from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_mail import Mail, Message
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from geopy.distance import geodesic
from datetime import datetime, date, timedelta
import hashlib
import json
from models import db, User, Admin, BloodRequest, DonationHistory, Hospital, AuditLog, SystemSettings, AIConfig, Notification, BloodBag, HospitalRequest, DonorAppointment, BloodDrive, Interest, ChatMessage, ConsentLog, HospitalBroadcast, InventoryHistory, DonorAssignment
from translations import TRANSLATIONS

app = Flask(__name__)
app.config['SECRET_KEY'] = 'lifelink_secret_key_change_in_production'

import os
from flask_socketio import SocketIO, emit
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# PostgreSQL readiness with SQLite fallback
default_db_path = os.path.join(app.instance_path, 'lifelink.db')
db_url = os.environ.get('DATABASE_URL')
# Render provides postgres:// but SQLAlchemy requires postgresql://
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or f"sqlite:///{default_db_path}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize Real-Time engine (using threading fallback if eventlet blocked on Windows)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize Security Rate Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["500 per day", "100 per hour"],
    storage_uri="memory://"
)

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

# Initialize Extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# Email Configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'your-email@gmail.com'
app.config['MAIL_PASSWORD'] = 'your-app-password'
app.config['MAIL_DEFAULT_SENDER'] = 'your-email@gmail.com'
mail = Mail(app)

@login_manager.user_loader
def load_user(user_id):
    # Check for namespaced IDs
    if user_id.startswith('admin_'):
        try:
            uid = int(user_id.split('_')[1])
            return db.session.get(Admin, uid)
        except (IndexError, ValueError):
            return None
            
    if user_id.startswith('user_'):
        try:
            uid = int(user_id.split('_')[1])
            user = db.session.get(User, uid)
            if user and not user.is_approved:
                return None # Block login if not approved
            return user
        except (IndexError, ValueError):
            return None

    if user_id.startswith('hospital_'):
        try:
            uid = int(user_id.split('_')[1])
            hosp = db.session.get(Hospital, uid)
            if hosp and not hosp.is_approved:
                return None
            return hosp
        except (IndexError, ValueError):
            return None

    # Fallback for legacy/numeric IDs (default to User first to maintain some backward compat if needed, 
    # though valid sessions should now have prefixes)
    try:
        max_legacy_check = int(user_id)
        user = db.session.get(User, max_legacy_check)
        if user:
            if not user.is_approved: return None
            return user
        hosp = db.session.get(Hospital, max_legacy_check)
        if hosp:
            return hosp
        return db.session.get(Admin, max_legacy_check)
    except ValueError:
        return None

@app.context_processor
def inject_translations():
    lang = session.get('lang', 'en')
    
    def _t(key, **kwargs):
        text = TRANSLATIONS.get(lang, TRANSLATIONS['en']).get(key, key)
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", str(v))
        return text

    return dict(_=_t, current_lang=lang, datetime=datetime)

@app.route('/set_lang/<lang_code>')
def set_lang(lang_code):
    if lang_code in TRANSLATIONS:
        session['lang'] = lang_code
        if current_user.is_authenticated and isinstance(current_user, User):
            current_user.language = lang_code
            db.session.commit()
    return redirect(request.referrer or url_for('home'))

# --- HELPER: Smart Matching Logic ---
def calculate_match_score(donor, request_lat, request_lon):
    """
    Match Score = 
    (Location Closeness * 40) + 
    (Recency * 30) + 
    (Health Score * 30)
    """
    # 1. Location (Max 40)
    donor_loc = (donor.latitude, donor.longitude)
    req_loc = (request_lat, request_lon)
    try:
        distance = geodesic(donor_loc, req_loc).km
    except:
        distance = 1000

    # Score decreases as distance increases. Max score for < 1km.
    # 50km radius consideration
    location_score = max(0, 40 - (distance * 0.8))

    # 2. Recency (Max 30)
    # Prefer donors who haven't donated in a long time
    if donor.last_donation_date:
        days_diff = (date.today() - donor.last_donation_date).days
    else:
        days_diff = 365 
        
    if days_diff < 90:
        return 0, distance # Blocked

    recency_score = min(30, (days_diff / 180) * 30)

    # 3. Health Score (Max 30)
    health_deduction = 0
    if donor.smoking: health_deduction += 10
    if donor.drinking: health_deduction += 10
    health_score = max(0, 30 - health_deduction)

    total_score = location_score + recency_score + health_score
    
    # 4. Reputation (Max 20, optional bonus)
    reputation_bonus = (donor.reputation_score / 100) * 20
    total_score += reputation_bonus
    
    return round(total_score, 2), round(distance, 2)

# --- ADVANCED SYSTEM LOGIC ---

@app.before_request
def run_background_tasks():
    """Simulate background processing on every request"""
    if not request.path.startswith('/static'):
        try:
            update_request_statuses()
            send_eligibility_reminders()
        except:
            pass # Keep app stable if DB is not ready

def update_request_statuses():
    """Expire old requests based on urgency/strict 24h rule"""
    try:
        now = datetime.utcnow()
        expiry_threshold = now - timedelta(hours=24)
        
        # 1. Expire BloodRequests older than 24 hours
        expired_reqs = BloodRequest.query.filter(
            BloodRequest.status == 'Pending',
            BloodRequest.created_at <= expiry_threshold
        ).all()
        
        for req in expired_reqs:
            req.status = 'Expired'
            
        # 2. Expire HospitalBroadcasts older than 24 hours
        expired_broadcasts = HospitalBroadcast.query.filter(
            HospitalBroadcast.status == 'Active',
            HospitalBroadcast.created_at <= expiry_threshold
        ).all()
        
        for b in expired_broadcasts:
            b.status = 'Expired'
            
        db.session.commit()
    except Exception as e:
        print(f"Error updating request statuses: {e}")
        db.session.rollback()

def send_sms_alert(mobile, message):
    """Twilio SMS Simulator"""
    settings = SystemSettings.query.first()
    if settings and settings.enable_sms:
        print(f"📱 [TWILIO SMS] To: {mobile} -> {message}")

def send_whatsapp_alert(mobile, message):
    """Twilio WhatsApp Simulator"""
    settings = SystemSettings.query.first()
    if settings and settings.enable_whatsapp:
        print(f"💬 [TWILIO WHATSAPP] To: {mobile} -> {message}")

def send_eligibility_reminders():
    """Smart Donation Reminder: Notify donors when they become eligible"""
    try:
        now = date.today()
        # Find users who were NOT available but whose 90-day gap has just passed
        # Simplification: Find users who reached 90 days today
        donors = User.query.filter_by(is_available=False).all()
        for donor in donors:
            if donor.last_donation_date:
                gap = (now - donor.last_donation_date).days
                if gap >= 90:
                    donor.is_available = True
                    # Create notification
                    notif = Notification(
                        user_id=donor.id,
                        message="You are eligible to donate blood again! Your contribution saves lives. ❤️",
                        type="REWARD"
                    )
                    db.session.add(notif)
                    # Simulate email logic here if needed
        db.session.commit()
    except:
        db.session.rollback()

def calculate_donor_reputation(donor):
    """
    Score = Success Rate + Consistency - Missed Commitments
    Initial: 100
    """
    history = donor.history
    if not history: return donor.reputation_score
    
    successful_donations = len([h for h in history if not h.notes or "Cancelled" not in h.notes])
    # Placeholder logic for missed commitments
    # In a real app, we'd track appointments and see if they were fulfilled
    # For now, we'll keep it simple
    return min(100.0, 70.0 + (successful_donations * 5))

def generate_donation_hash(donation):
    """Immutable Ledger Simulation"""
    prev = DonationHistory.query.filter(DonationHistory.id < donation.id).order_by(DonationHistory.id.desc()).first()
    prev_hash = prev.blockchain_hash if prev else "0" * 64
    
    data = f"{donation.donor_id}-{donation.donation_date}-{prev_hash}"
    return hashlib.sha256(data.encode()).hexdigest()

def check_donor_eligibility(user):
    """
    Returns (is_eligible, next_eligible_date, message)
    """
    if not user.is_available:
        return False, None, "You have set your status to Unavailable. Toggle it in your profile settings."
        
    # Check 90 day gap
    settings = SystemSettings.query.first()
    gap_days = settings.donation_gap_days if settings else 90
    
    if user.last_donation_date:
        next_eligible = user.last_donation_date + timedelta(days=gap_days)
        if date.today() < next_eligible:
            return False, next_eligible, f"Minimum {gap_days} days gap required between donations."
    else:
        next_eligible = date.today()

    # Health Checks
    if user.age < 18 or user.age > 65: return False, next_eligible, "Age restricted (18-65 allowed)."
    min_weight = 50.0 if user.gender == 'Male' else 45.0
    if not user.weight or user.weight < min_weight: return False, next_eligible, f"Minimum weight required: {min_weight}kg."
    
    min_hb = 13.0 if user.gender == 'Male' else 12.5
    if not user.hemoglobin or user.hemoglobin < min_hb: return False, next_eligible, f"Hemoglobin too low (Min: {min_hb} g/dL)."
    
    if user.systolic_bp and user.diastolic_bp:
        if not (100 <= user.systolic_bp <= 140) or not (60 <= user.diastolic_bp <= 90):
            return False, next_eligible, "Blood pressure out of safe range."

    if user.gender == 'Female':
        if user.is_pregnant or user.is_breastfeeding: return False, next_eligible, "Not eligible during pregnancy or breastfeeding."
        if getattr(user, 'menstrual_cycle_safe', True) == False: return False, next_eligible, "Not eligible due to menstrual cycle safety."
    
    # Habits
    if user.heart_disease or getattr(user, 'asthma', False): return False, next_eligible, "Safety block: Heart or Asthma conditions present."

    return True, next_eligible, "You are fully eligible and healthy to donate!"

# --- ROUTES ---

@app.route('/donor/requests', methods=['GET'])
@login_required
def donor_requests():
    bg = request.args.get('blood_group', current_user.blood_group)
    active_reqs = BloodRequest.query.filter(
        BloodRequest.status == 'Pending',
        BloodRequest.requester_id != current_user.id,
        (BloodRequest.blood_group == bg) if bg else True
    ).all()
    
    from geopy.distance import geodesic
    for r in active_reqs:
        try:
            r.distance = round(geodesic((current_user.latitude, current_user.longitude), (r.req_latitude, r.req_longitude)).km, 1)
        except:
            r.distance = 999
    
    active_reqs.sort(key=lambda x: x.distance)
    return render_template('donor/requests.html', requests=active_reqs)

@app.route('/donor/accept/<int:req_id>', methods=['POST'])
@login_required
def donor_accept_request(req_id):
    req = BloodRequest.query.get_or_404(req_id)
    if req.status == 'Pending':
        req.status = 'Assigned'
        req.fulfilled_by_id = current_user.id
        
        notif = Notification(user_id=req.requester_id, type="MATCH", message=f"{current_user.full_name} has ACCEPTED your request for {req.blood_group}!")
        db.session.add(notif)
        db.session.commit()
        
        # Real-Time Event & Notifications
        socketio.emit('request_accepted', {'req_id': req_id, 'blood_group': req.blood_group})
        send_sms_alert(current_user.mobile_number, f"Thank you for accepting the {req.blood_group} request! The hospital expects you.")
        
        flash("Request accepted successfully! Their contact details are now visible.", "success")
    return redirect(url_for('dashboard'))

@app.route('/donor/cancel-acceptance/<int:req_id>', methods=['POST'])
@login_required
def donor_cancel_acceptance(req_id):
    req = BloodRequest.query.get_or_404(req_id)
    if req.fulfilled_by_id == current_user.id and req.status == 'Assigned':
        req.status = 'Pending'
        req.fulfilled_by_id = None
        current_user.reputation_score = max(0.0, current_user.reputation_score - 10.0)
        
        notif = Notification(user_id=req.requester_id, type="ALERT", message=f"A donor cancelled their acceptance for {req.blood_group}. It is Pending again.")
        db.session.add(notif)
        db.session.commit()
        flash("Acceptance cancelled. Your reputation score was penalized by 10 points.", "warning")
    return redirect(url_for('dashboard'))

@app.route('/donor/map')
@login_required
def donor_map():
    hospitals = Hospital.query.filter_by(is_approved=True).all()
    pending_reqs = BloodRequest.query.filter_by(status='Pending').all()
    return render_template('donor/map.html', hospitals=hospitals, requests=pending_reqs)


@app.route('/')
def home():
    return render_template('index.html')

@app.route('/live-map')
def live_map():
    return render_template('live_map.html')

@app.route('/test')
def test_frontend():
    return "<h1>Backend is active</h1><p>If you see this, Flask is working.</p>"

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register():
    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        mobile = request.form.get('mobile_number', '').strip()
        password = request.form.get('password')
        blood_group = request.form.get('blood_group')
        age = request.form.get('age')
        city = request.form.get('city', '').strip()
        email = request.form.get('email', '').strip()
        lat = request.form.get('latitude', 0)
        lon = request.form.get('longitude', 0)
        
        # Health
        bp = 1 if request.form.get('bp') else 0
        sugar = 1 if request.form.get('sugar') else 0
        heart = 1 if request.form.get('heart_disease') else 0
        asthma = 1 if request.form.get('asthma') else 0
        smoking = 1 if request.form.get('smoking') else 0
        drinking = 1 if request.form.get('drinking') else 0
        
        last_donation = request.form.get('last_donation_date')
        
        # Validation
        if not mobile:
            return render_template('register.html', error="Mobile number is required.")
        if User.query.filter_by(mobile_number=mobile).first():
            return render_template('register.html', error="A user with this mobile number already exists.")
            
        # Registration logic update
        new_user = User(
            full_name=full_name, mobile_number=mobile, 
            email=email,
            password_hash=generate_password_hash(password),
            blood_group=blood_group, age=int(age), city=city,
            latitude=float(lat), longitude=float(lon),
            gender=request.form.get('gender', 'Male'),
            weight=float(request.form.get('weight', 0)),
            hemoglobin=float(request.form.get('hemoglobin', 0)),
            pulse_rate=int(request.form.get('pulse_rate', 0)),
            systolic_bp=int(request.form.get('systolic_bp', 0)),
            diastolic_bp=int(request.form.get('diastolic_bp', 0)),
            sugar_level=int(request.form.get('sugar_level', 0)),
            is_pregnant=request.form.get('is_pregnant') == 'on',
            is_breastfeeding=request.form.get('is_breastfeeding') == 'on',
            menstrual_cycle_safe=request.form.get('menstrual_safe') == 'on',
            bp=bp, sugar=sugar, heart_disease=heart, asthma=asthma,
            smoking=smoking, drinking=drinking,
            last_donation_date=datetime.strptime(last_donation, '%Y-%m-%d').date() if last_donation else None
        )
        db.session.add(new_user)
        db.session.commit()
        
        # Audit & Legal GDPR Consent Log
        if request.form.get('gdpr_consent') == 'on':
            c_log = ConsentLog(user_id=new_user.id, consent_given=True, ip_address=request.remote_addr or 'unknown')
            db.session.add(c_log)
            db.session.commit()
        
        # Initial eligibility check
        check_eligibility(new_user)
        
        login_user(new_user)
        return redirect(url_for('dashboard'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        mobile = request.form.get('mobile_number', '').strip()
        password = request.form.get('password')
        
        user = User.query.filter_by(mobile_number=mobile).first()
        if user and check_password_hash(user.password_hash, password):
            if not user.is_approved:
                return render_template('login.html', error="Account pending approval.")
            login_user(user)
            check_eligibility(user) # Auto-update status
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid credentials")
            
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

def check_eligibility(user):
    """Enhanced Medical Eligibility AI Check"""
    now = date.today()
    
    # 1. Donation Gap (90 days)
    if user.last_donation_date:
        if (now - user.last_donation_date).days < 90:
            user.is_available = False
            return False, "Donation gap not met (90 days required)."
            
    # 2. Age (18-65)
    if not (18 <= user.age <= 65):
        user.is_available = False
        return False, "Age must be between 18 and 65."
        
    # 3. Weight
    min_weight = 50.0 if user.gender == 'Male' else 45.0
    if not user.weight or user.weight < min_weight:
        user.is_available = False
        return False, f"Minimum weight required: {min_weight}kg."
        
    # 4. Hemoglobin
    min_hb = 13.0 if user.gender == 'Male' else 12.5
    if not user.hemoglobin or user.hemoglobin < min_hb:
        user.is_available = False
        return False, f"Hemoglobin too low (Min: {min_hb} g/dL)."
        
    # 5. Blood Pressure
    if user.systolic_bp and user.diastolic_bp:
        if not (100 <= user.systolic_bp <= 140) or not (60 <= user.diastolic_bp <= 90):
            user.is_available = False
            return False, "Blood pressure out of safe range."

    # 6. Female Specifics
    if user.gender == 'Female':
        if user.is_pregnant or user.is_breastfeeding:
            user.is_available = False
            return False, "Not eligible during pregnancy or breastfeeding."
        if not user.menstrual_cycle_safe:
            user.is_available = False
            return False, "Not eligible during certain phases of the menstrual cycle."

    user.is_available = True
    db.session.commit()
    return True, "Eligible to donate!"

@app.route('/cancel-request/<int:req_id>', methods=['POST'])
@login_required
def cancel_request(req_id):
    req = BloodRequest.query.get_or_404(req_id)
    if req.requester_id != current_user.id and not isinstance(current_user, Admin):
        flash("Unauthorized", "error")
        return redirect(url_for('dashboard'))
    
    req.status = 'Cancelled'
    db.session.commit()
    flash("Request cancelled successfully.")
    return redirect(url_for('dashboard'))

@app.route('/api/live-map')
def live_map_data():
    """Live Donor Availability Map API"""
    donors = User.query.filter_by(is_approved=True).all()
    data = []
    for d in donors:
        # Simple eligibility check for map
        eligible = d.is_available
        data.append({
            'name': d.full_name,
            'lat': d.latitude,
            'lon': d.longitude,
            'bg': d.blood_group,
            'status': 'Online' if d.is_online else 'Offline',
            'eligible': 'Yes' if eligible else 'No',
            'reputation': d.reputation_score
        })
    return jsonify(data)

@app.route('/my-history')
@login_required
def my_history():
    history = DonationHistory.query.filter_by(donor_id=current_user.id).order_by(DonationHistory.donation_date.desc()).all()
    return render_template('donor/history.html', history=history)

@app.route('/donor/certificate/<int:history_id>')
@login_required
def donor_certificate(history_id):
    donation = DonationHistory.query.get_or_404(history_id)
    if donation.donor_id != current_user.id:
        flash("Unauthorized access.", "error")
        return redirect(url_for('my_history'))
    return render_template('donor/certificate.html', donation=donation, user=current_user)

@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        mobile = request.form.get('mobile_number')
        user = User.query.filter_by(mobile_number=mobile).first()
        if user:
            new_pass = "LifeLink123"
            user.password_hash = generate_password_hash(new_pass)
            db.session.commit()
            flash(f'Your temporary password is: {new_pass}. Please login and change it immediately.', 'info')
            return redirect(url_for('login'))
        else:
            flash('Mobile number not found.', 'error')
    return render_template('forgot_password.html')

@app.route('/edit-profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        current_user.full_name = request.form.get('full_name', '').strip()
        current_user.age = int(request.form.get('age', current_user.age))
        current_user.city = request.form.get('city', '').strip()
        current_user.email = request.form.get('email', '').strip()
        current_user.blood_group = request.form.get('blood_group', current_user.blood_group)
        current_user.latitude = float(request.form.get('latitude', current_user.latitude))
        current_user.longitude = float(request.form.get('longitude', current_user.longitude))
        
        # Health & Habits updates
        current_user.smoking = request.form.get('smoking') == 'on'
        current_user.drinking = request.form.get('drinking') == 'on'
        current_user.bp = request.form.get('bp') == 'on'
        current_user.sugar = request.form.get('sugar') == 'on'
        current_user.heart_disease = request.form.get('heart_disease') == 'on'
        current_user.asthma = request.form.get('asthma') == 'on'
        
        # Advanced Vitals
        if request.form.get('weight'): current_user.weight = float(request.form.get('weight'))
        if request.form.get('hemoglobin'): current_user.hemoglobin = float(request.form.get('hemoglobin'))
        if request.form.get('pulse_rate'): current_user.pulse_rate = int(request.form.get('pulse_rate'))
        if request.form.get('systolic_bp'): current_user.systolic_bp = int(request.form.get('systolic_bp'))
        if request.form.get('diastolic_bp'): current_user.diastolic_bp = int(request.form.get('diastolic_bp'))
        if request.form.get('sugar_level'): current_user.sugar_level = int(request.form.get('sugar_level'))

        # Female Specifics
        current_user.is_pregnant = request.form.get('is_pregnant') == 'on'
        current_user.is_breastfeeding = request.form.get('is_breastfeeding') == 'on'
        current_user.menstrual_cycle_safe = request.form.get('menstrual_safe') == 'on'
        
        # Availability Toggle
        current_user.is_available = request.form.get('is_available') == 'on'
        
        db.session.commit()
        flash('Donor Profile updated successfully!', 'success')
        # Re-evaluate logic implicitly saves
        return redirect(url_for('profile'))
    return render_template('donor/edit_profile.html', user=current_user)

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_pass = request.form.get('current_password')
        new_pass = request.form.get('new_password')
        
        # Verify current password
        if check_password_hash(current_user.password_hash, current_pass):
            current_user.password_hash = generate_password_hash(new_pass)
            db.session.commit()
            flash('Password changed successfully!', 'success')
            return redirect(url_for('profile'))
        else:
            flash('Incorrect current password!', 'error')
            
    return render_template('donor/change_password.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if isinstance(current_user, Admin):
        return redirect(url_for('admin_dashboard'))
    if isinstance(current_user, Hospital):
        return redirect(url_for('hospital_dashboard'))
    
    # Collect Dashboard Metrics
    total_donations = DonationHistory.query.filter_by(donor_id=current_user.id).count()
    
    # My accepted requests
    accepted_requests = BloodRequest.query.filter_by(fulfilled_by_id=current_user.id, status='Assigned').all()
    
    from collections import OrderedDict
    
    # Calculate eligibility explicitly
    is_eligible, next_date, elig_message = check_donor_eligibility(current_user)
    
    nearby_requests = BloodRequest.query.filter(
        BloodRequest.requester_id != current_user.id,
        BloodRequest.status == 'Pending'
    ).order_by(BloodRequest.created_at.desc()).limit(5).all()
    
    # Notifications
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(3).all()
    
    # Assignments
    assignments = DonorAssignment.query.filter_by(donor_id=current_user.id, status='Pending').all()
    # attach request object to the assignment for context in template
    for a in assignments:
        a.req = BloodRequest.query.get(a.request_id)
    
    return render_template('donor/dashboard.html', 
                           user=current_user, 
                           accepted_requests=accepted_requests, 
                           nearby_requests=nearby_requests,
                           total_donations=total_donations,
                           is_eligible=is_eligible,
                           next_date=next_date,
                           elig_message=elig_message,
                           notifications=notifications,
                           assignments=assignments)

@app.route('/donor/notifications', methods=['GET', 'POST'])
@login_required
def donor_notifications():
    if not isinstance(current_user, User): return redirect(url_for('home'))
        
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'mark_all_read':
            unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).all()
            for n in unread:
                n.is_read = True
            db.session.commit()
            flash('All notifications marked as read', 'success')
            return redirect(url_for('donor_notifications'))
            
    # Fetch all notifications for the user
    all_notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    
    # Optional: Mark them as read just by visiting the page, 
    # but the manual mark_all_read is better UX. We will use manual.
    
    return render_template('donor/notifications.html', notifications=all_notifications)

@app.route('/request-blood', methods=['GET', 'POST'])
@login_required
def request_blood():
    if request.method == 'POST':
        req = BloodRequest(
            requester_id=current_user.id,
            patient_name=request.form.get('patient_name'),
            blood_group=request.form.get('blood_group'),
            hospital_name=request.form.get('hospital_name'),
            hospital_location=request.form.get('hospital_location'),
            req_latitude=float(request.form.get('req_latitude')),
            req_longitude=float(request.form.get('req_longitude')),
            urgency_level=request.form.get('urgency_level'),
            contact_number=current_user.mobile_number
        )
        db.session.add(req)
        db.session.commit()
        
        # Notify donors in the same city
        try:
            donors = User.query.filter_by(city=req.hospital_location, is_available=True).all()
            recipient_emails = [d.email for d in donors if d.email and d.id != current_user.id]
            
            if recipient_emails:
                msg = Message(
                    subject=f"URGENT: Blood Needed in {req.hospital_location}",
                    recipients=recipient_emails,
                    body=f"Patient {req.patient_name} needs {req.blood_group} blood at {req.hospital_name}.\n"
                         f"Location: {req.hospital_location}\n"
                         f"Urgency: {req.urgency_level}\n"
                         f"Please contact: {req.contact_number}\n\n"
                         f"Thank you for your help!"
                )
                mail.send(msg)
        except Exception as e:
            print(f"Error sending email: {e}") # Log error but don't break the flow
            
        return redirect(url_for('find_donors', req_id=req.id))
        
    return render_template('request.html')

@app.route('/find-donors')
@login_required
def find_donors():
    req_id = request.args.get('req_id')
    req = BloodRequest.query.get_or_404(req_id)
    
    # Filter Logic
    target_bg = req.blood_group
    search_bgs = [target_bg]
    if target_bg != 'O-':
        search_bgs.append('O-')
        
    candidates = User.query.filter(User.blood_group.in_(search_bgs), User.is_available==True).all()
    
    ranked = []
    for donor in candidates:
        if donor.id == current_user.id: continue # Don't suggest self
        
        score, dist = calculate_match_score(donor, req.req_latitude, req.req_longitude)
        if score > 0:
            donor.score = score
            donor.distance = dist
            donor.match_color = 'green' if score > 80 else 'orange'
            ranked.append(donor)
            
    ranked.sort(key=lambda x: x.score, reverse=True)
    return render_template('donors.html', donors=ranked[:5], blood_request=req)

@app.route('/express-interest/<int:req_id>', methods=['POST'])
@login_required
def express_interest(req_id):
    if isinstance(current_user, Hospital) or isinstance(current_user, Admin):
        return redirect(url_for('home'))
    
    req = BloodRequest.query.get_or_404(req_id)
    
    # Check if already interested
    existing = Interest.query.filter_by(request_id=req.id, donor_id=current_user.id).first()
    if not existing:
        interest = Interest(request_id=req.id, donor_id=current_user.id)
        db.session.add(interest)
        
        # Notify requester
        if req.requester_id:
            notif = Notification(
                user_id=req.requester_id,
                message=f"{current_user.full_name} is interested in donating for your request (Blood Group: {req.blood_group}). Contact them at: {current_user.mobile_number}",
                type='info'
            )
            db.session.add(notif)
            
        db.session.commit()
        flash('Interest expressed successfully!', 'success')
    else:
        flash('You have already expressed interest in this request.', 'info')
        
    return redirect(url_for('dashboard'))

@app.route('/api/notifications')
@login_required
def get_notifications():
    if not isinstance(current_user, User): return jsonify({'count': 0, 'notifications': []})
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(10).all()
    unread = sum(1 for n in notifs if not n.is_read)
    return jsonify({
        'count': unread,
        'notifications': [{'id': n.id, 'message': n.message, 'is_read': n.is_read, 'date': n.created_at.strftime('%Y-%m-%d %H:%M')} for n in notifs]
    })

@app.route('/api/notifications/clear', methods=['POST'])
@login_required
def clear_notifications():
    if not isinstance(current_user, User): return jsonify({'success': False})
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'success': True})

@app.route('/certificate/<int:donation_id>')
@login_required
def download_certificate(donation_id):
    if not isinstance(current_user, User): return redirect(url_for('home'))
    donation = DonationHistory.query.get_or_404(donation_id)
    if donation.donor_id != current_user.id:
        flash("You can only view your own certificates.", "error")
        return redirect(url_for('my_history'))
    return render_template('certificate.html', user=current_user, donation=donation)

@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        admin = Admin.query.filter_by(username=username).first()
            
        if admin and check_password_hash(admin.password_hash, password):
            login_user(admin)
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('admin_login.html', error="Invalid Admin Credentials")
            
    return render_template('admin_login.html')

@app.route('/clear-alert')
def clear_alert():
    session.pop('emergency_alert', None)
    return redirect(url_for('home'))



@app.route('/admin-dashboard')
@login_required
def admin_dashboard():
    if not isinstance(current_user, Admin):
        return redirect(url_for('dashboard'))
        
    donors = User.query.all()
    requests = BloodRequest.query.all()
    fulfilled_count = sum(1 for r in requests if r.status == 'Fulfilled')
    
    # Calculate Data for Charts
    bg_counts = {}
    for d in donors:
        bg_counts[d.blood_group] = bg_counts.get(d.blood_group, 0) + 1
        
    req_status = {}
    for r in requests:
        req_status[r.status] = req_status.get(r.status, 0) + 1
        
    # AI Feature 1: Blood Shortage Early Warning
    # Calculate simple shortage based on inventory vs recent requests
    shortage_warnings = []
    # Count available blood directly from BloodBag table
    available_stock = {}
    all_bags = BloodBag.query.filter_by(status='Available').all()
    for bag in all_bags:
        available_stock[bag.blood_group] = available_stock.get(bag.blood_group, 0) + bag.quantity
    
    # Compare with last 30 days demand
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_requests = BloodRequest.query.filter(BloodRequest.created_at >= thirty_days_ago).all()
    recent_demand = {}
    for r in recent_requests:
        recent_demand[r.blood_group] = recent_demand.get(r.blood_group, 0) + 1
        
    for bg in ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']:
        stock = available_stock.get(bg, 0)
        demand = recent_demand.get(bg, 0)
        # If stock is strictly less than demand over last 30 days, flag it
        if stock < demand and demand > 0:
            shortage_warnings.append({'blood_group': bg, 'stock': stock, 'demand_30d': demand})
            
    # AI Feature 2: Emergency Risk Prediction (Trends by City)
    city_risks = []
    city_demand = {}
    for r in recent_requests:
        if r.urgency_level == 'Critical':
            city = r.hospital_location or "Unknown"
            city_demand[city] = city_demand.get(city, 0) + 1
            
    # Find cities with high critical demands
    for city, count in sorted(city_demand.items(), key=lambda item: item[1], reverse=True)[:3]:
        city_risks.append({'city': city, 'critical_requests_30d': count})
        
    return render_template('admin/dashboard.html', 
                            donors=donors, 
                            requests=requests, 
                            helped=fulfilled_count,
                            bg_counts=bg_counts,
                            req_status=req_status,
                            hospitals_count=Hospital.query.count(),
                            shortage_warnings=shortage_warnings,
                            city_risks=city_risks)

# --- AUDIT LOG HELPER ---
def log_action(action, details=None):
    if current_user.is_authenticated and isinstance(current_user, Admin):
        log = AuditLog(
            admin_id=current_user.id,
            action=action,
            details=details,
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()

# --- ADMIN DONOR MANAGEMENT ---

@app.route('/admin/donors')
@login_required
def admin_donors():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    donors = User.query.all()
    return render_template('admin/donors.html', donors=donors)

@app.route('/admin/block-user/<int:user_id>/<action>')
@login_required
def block_user(user_id, action):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    user = User.query.get_or_404(user_id)
    if action == 'block':
        user.is_approved = False
        user.is_available = False
        user.is_locked = True
        log_action('BLOCK_USER', f'Blocked user {user.full_name} ({user.id})')
    elif action == 'unblock':
        user.is_approved = True
        user.is_locked = False
        log_action('UNBLOCK_USER', f'Unblocked user {user.full_name} ({user.id})')
    db.session.commit()
    return redirect(url_for('admin_donors'))

@app.route('/admin/delete-user/<int:user_id>')
@login_required
def delete_user(user_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    user = User.query.get_or_404(user_id)
    name = user.full_name
    
    # Cascade Delete: Remove related records first
    DonationHistory.query.filter_by(donor_id=user.id).delete()
    ChatMessage.query.filter((ChatMessage.sender_id==user.id) | (ChatMessage.receiver_id==user.id)).delete()
    # Requests will set requester_id to NULL automatically if configured, or we can leave them
    # For cleanliness, we should probably keep requests but show "Unknown User" or leave as is.
    
    db.session.delete(user)
    db.session.commit()
    log_action('DELETE_USER', f'Deleted user {name} ({user_id})')
    return redirect(url_for('admin_donors'))

@app.route('/admin/reset-password/<int:user_id>', methods=['POST'])
@login_required
def reset_user_password(user_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    user = User.query.get_or_404(user_id)
    new_pass = request.form.get('new_password')
    if new_pass:
        user.password_hash = generate_password_hash(new_pass)
        db.session.commit()
        log_action('RESET_PASSWORD', f'Reset password for user {user.full_name}')
        flash(f'Password reset for {user.full_name}')
    return redirect(url_for('admin_donors'))

# --- ADMIN REQUEST MANAGEMENT ---

@app.route('/admin/requests')
@login_required
def admin_requests():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    reqs = BloodRequest.query.order_by(BloodRequest.created_at.desc()).all()
    donors = User.query.filter_by(is_available=True, is_approved=True).all()
    
    fulfillers = {}
    for r in reqs:
        if r.fulfilled_by_id:
            db_user = db.session.get(User, r.fulfilled_by_id)
            if db_user:
                fulfillers[r.id] = db_user.full_name
                
    return render_template('admin/requests.html', requests=reqs, donors=donors, fulfillers=fulfillers)

@app.route('/admin/update-request/<int:req_id>', methods=['POST'])
@login_required
def update_request(req_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    req = BloodRequest.query.get_or_404(req_id)
    status = request.form.get('status')
    if status and status != req.status:
        old_status = req.status
        req.status = status
        db.session.commit()
        log_action('UPDATE_REQUEST', f'Changed request {req_id} status from {old_status} to {status}')
    return redirect(url_for('admin_requests'))

@app.route('/admin/assign-donor/<int:req_id>', methods=['POST'])
@login_required
def assign_donor(req_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    req = BloodRequest.query.get_or_404(req_id)
    donor_id = request.form.get('donor_id')
    if donor_id:
        try:
            donor = User.query.get(int(donor_id))
            if donor:
                req.fulfilled_by_id = donor.id
                req.status = 'Fulfilled'
                
                # Add to DonationHistory
                donation = DonationHistory(donor_id=donor.id, notes=f"Assigned to {req.patient_name} by Admin", donation_date=date.today())
                db.session.add(donation)
                db.session.flush() # Get ID
                
                # Blockchain Simulation
                donation.blockchain_hash = generate_donation_hash(donation)
                donation.verified = True
                
                # Reputation Bonus
                settings = SystemSettings.query.first()
                bonus = settings.success_donation_bonus if settings else 5.0
                donor.reputation_score = min(100.0, donor.reputation_score + bonus)
                
                log_action('ASSIGN_DONOR', f'Assigned donor {donor.full_name} to request {req_id}')
                flash(f'Assigned {donor.full_name} to request and marked as Fulfilled. Reputation increased!')
                db.session.commit()
        except (ValueError, TypeError):
            flash('Invalid Donor Selection', 'error')
            
            
    return redirect(url_for('admin_requests'))

@app.route('/admin/donor/<int:donor_id>')
@login_required
def admin_donor_profile(donor_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    donor = User.query.get_or_404(donor_id)
    
    donations = DonationHistory.query.filter_by(donor_id=donor.id).order_by(DonationHistory.donation_date.desc()).all()
    requests = BloodRequest.query.filter_by(requester_id=donor.id).order_by(BloodRequest.created_at.desc()).all()
    assignments = DonorAssignment.query.filter_by(donor_id=donor.id).order_by(DonorAssignment.created_at.desc()).all()
    # attach related blood request to assignments
    for a in assignments:
        a.req = BloodRequest.query.get(a.request_id)
        
    return render_template('admin/donor_profile.html', donor=donor, donations=donations, requests=requests, assignments=assignments)

@app.route('/admin/hospital/<int:hospital_id>')
@login_required
def admin_hospital_profile(hospital_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    hospital = Hospital.query.get_or_404(hospital_id)
    
    inventory_logs = InventoryHistory.query.filter_by(hospital_id=hospital.id).order_by(InventoryHistory.date.desc()).all()
    requests = BloodRequest.query.filter_by(hospital_name=hospital.name).order_by(BloodRequest.created_at.desc()).all()
    broadcasts = HospitalBroadcast.query.filter_by(hospital_id=hospital.id).order_by(HospitalBroadcast.created_at.desc()).all()
    active_bags = BloodBag.query.filter_by(hospital_id=hospital.id, status='Available').all()
    
    return render_template('admin/hospital_profile.html', 
                           hospital=hospital, 
                           inventory_logs=inventory_logs, 
                           requests=requests, 
                           broadcasts=broadcasts,
                           active_bags=active_bags)

@app.route('/admin/inventory')
@login_required
def admin_inventory():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    
    hospitals = Hospital.query.filter_by(is_approved=True).all()
    
    # Build inventory data per hospital — same BloodBag source hospitals use
    hospital_inventory = []
    stock_summary = {}
    today = date.today()
    for h in hospitals:
        bags = BloodBag.query.filter_by(hospital_id=h.id).all()
        # Annotate expiry info
        for bag in bags:
            # Safely handle datetime objects in case db returns datetime instead of date
            exp_d = bag.expiry_date.date() if isinstance(bag.expiry_date, datetime) else bag.expiry_date
            bag.days_to_expiry = (exp_d - today).days if exp_d else None
            
        available = [b for b in bags if b.status == 'Available']
        
        # Add to summary aggregate
        for b in available:
            stock_summary[b.blood_group] = stock_summary.get(b.blood_group, 0) + b.quantity
            
        hospital_inventory.append({
            'hospital': h,
            'bags': bags,
            'available_count': sum(b.quantity for b in available),
            'total_count': sum(b.quantity for b in bags)
        })
    
    return render_template('admin/inventory.html',
                           hospital_inventory=hospital_inventory,
                           stock_summary=stock_summary)


@app.route('/admin/audit-logs')
@login_required
def admin_audit_logs():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(100).all()
    return render_template('admin/audit_logs.html', logs=logs)

# Include manage_hospitals route (refactored to new template location)
@app.route('/admin/hospitals')
@login_required
def manage_hospitals():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    hospitals = Hospital.query.all()
    return render_template('admin/manage_hospitals.html', hospitals=hospitals)

@app.route('/admin/add-hospital', methods=['POST'])
@login_required
def add_hospital():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    name = request.form.get('name')
    city = request.form.get('city')
    address = request.form.get('address')
    contact = request.form.get('contact')
    if Hospital.query.filter_by(mobile_number=contact).first():
        flash('Hospital with this number already exists!', 'error')
        return redirect(url_for('manage_hospitals'))
    
    h = Hospital(name=name, city=city, address=address, mobile_number=contact)
    db.session.add(h)
    db.session.commit()
    log_action('ADD_HOSPITAL', f'Added hospital {name}')
    return redirect(url_for('manage_hospitals'))

@app.route('/admin/delete-hospital/<int:h_id>')
@login_required
def delete_hospital(h_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    h = Hospital.query.get_or_404(h_id)
    name = h.name
    db.session.delete(h)
    db.session.commit()
    log_action('DELETE_HOSPITAL', f'Deleted hospital {name}')
    return redirect(url_for('manage_hospitals'))

@app.route('/admin/reset-hospital-password/<int:h_id>', methods=['POST'])
@login_required
def reset_hospital_password(h_id):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    h = Hospital.query.get_or_404(h_id)
    new_pass = request.form.get('new_password')
    if new_pass:
        h.password_hash = generate_password_hash(new_pass)
        db.session.commit()
        log_action('RESET_HOSPITAL_PASSWORD', f'Reset password for hospital {h.name}')
        flash(f'Password reset for {h.name}')
    return redirect(url_for('manage_hospitals'))

@app.route('/admin/forecast')
@login_required
def admin_forecast():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    # ... (existing forecast logic)
    predictions = {'A+': 'High Demand', 'O+': 'Critical', 'B+': 'Stable', 'AB-': 'Low'}
    trend = [10, 15, 8, 12, 20, 25] # Mock
    return render_template('admin/forecast.html', predictions=predictions, trend=trend)

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    settings = SystemSettings.query.first()
    if not settings:
        settings = SystemSettings() # Create default if not exists
        db.session.add(settings)
        db.session.commit()
    
    if request.method == 'POST':
        settings.donation_gap_days = int(request.form.get('donation_gap'))
        settings.emergency_radius_km = float(request.form.get('radius'))
        settings.admin_contact_email = request.form.get('email')
        
        # New settings
        settings.critical_expiry_days = int(request.form.get('critical_expiry', settings.critical_expiry_days))
        settings.medium_expiry_days = int(request.form.get('medium_expiry', settings.medium_expiry_days))
        settings.low_expiry_days = int(request.form.get('low_expiry', settings.low_expiry_days))
        
        db.session.commit()
        log_action('UPDATE_SETTINGS', 'Updated system configuration')
        flash('Settings updated!')
    
    return render_template('admin/settings.html', settings=settings)

@app.route('/admin/ai-control', methods=['GET', 'POST'])
@login_required
def admin_ai_control():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    ai_config = AIConfig.query.first()
    if not ai_config:
        ai_config = AIConfig()
        db.session.add(ai_config)
        db.session.commit()
        
    if request.method == 'POST':
        ai_config.weight_blood_group = float(request.form.get('w_bg'))
        ai_config.weight_distance = float(request.form.get('w_dist'))
        ai_config.weight_recency = float(request.form.get('w_rec'))
        ai_config.weight_health = float(request.form.get('w_h'))
        db.session.commit()
        log_action('UPDATE_AI', 'Updated AI matching weights')
        flash('AI Strategy Updated!')
        
    return render_template('admin/ai_control.html', config=ai_config)

@app.route('/admin/communication', methods=['GET', 'POST'])
@login_required
def admin_communication():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    return render_template('admin/communication.html')

@app.route('/admin/send-broadcast', methods=['POST'])
@login_required
def send_broadcast():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    msg = request.form.get('message')
    channel = request.form.get('channel') # SMS, WhatsApp, Dashboard
    bg_filter = request.form.get('blood_group', 'All')
    city_filter = request.form.get('city', '').strip()
    
    # Smart Targeting Logic
    query = User.query.filter_by(is_available=True)
    if bg_filter != 'All':
        query = query.filter_by(blood_group=bg_filter)
    if city_filter:
        # Case insensitive city match
        query = query.filter(User.city.ilike(f"%{city_filter}%"))
        
    target_donors = query.all()
    count = len(target_donors)
    
    # Generate Notifications
    for donor in target_donors:
        notif = Notification(user_id=donor.id, type='ALERT', message=msg)
        db.session.add(notif)
        
    db.session.commit()
    
    # Simulate sending external
    details = f"Sent {channel} broadcast to {count} donors (Filter: {bg_filter}, {city_filter or 'All Cities'}). Msg: {msg[:20]}..."
    log_action('BROADCAST_SEND', details)
    session['emergency_alert'] = msg if channel == 'Dashboard' else None
    
    flash(f'{channel} Broadcast Sent Successfully to {count} targeted donors!')
    return redirect(url_for('admin_communication'))

@app.route('/admin/monitoring')
@login_required
def admin_monitoring():
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    # Simulating online users (in production, use Redis/LastActive)
    online_donors = User.query.filter_by(is_available=True).limit(5).all() 
    active_emergencies = BloodRequest.query.filter_by(urgency_level='High', status='Pending').all()
    return render_template('admin/monitoring.html', online=online_donors, emergencies=active_emergencies)

@app.route('/admin/export/<type>')
@login_required
def admin_export(type):
    if not isinstance(current_user, Admin): return "Unauthorized", 403
    import csv
    from io import StringIO
    from flask import make_response
    
    si = StringIO()
    cw = csv.writer(si)
    
    if type == 'donors':
        cw.writerow(['ID', 'Name', 'Mobile', 'Blood Group', 'City'])
        rows = User.query.all()
        for r in rows: cw.writerow([r.id, r.full_name, r.mobile_number, r.blood_group, r.city])
        filename = 'donors.csv'
    elif type == 'requests':
        cw.writerow(['ID', 'Patient', 'Group', 'Status', 'Date'])
        rows = BloodRequest.query.all()
        for r in rows: cw.writerow([r.id, r.patient_name, r.blood_group, r.status, r.created_at])
        filename = 'requests.csv'
    elif type == 'logs':
        cw.writerow(['Time', 'Action', 'Admin', 'Details', 'IP'])
        rows = AuditLog.query.all()
        for r in rows: cw.writerow([r.timestamp, r.action, r.admin_id, r.details, r.ip_address])
        filename = 'audit_logs.csv'
    elif type == 'hospitals':
        cw.writerow(['ID', 'Name', 'Email', 'City', 'Mobile'])
        rows = Hospital.query.all()
        for r in rows: cw.writerow([r.id, r.name, r.email, r.city, r.mobile_number])
        filename = 'hospitals_report.csv'
    elif type == 'donations':
        cw.writerow(['ID', 'Donor ID', 'Donation Date', 'Notes'])
        rows = DonationHistory.query.all()
        for r in rows: cw.writerow([r.id, r.donor_id, r.donation_date, r.notes])
        filename = 'donations_report.csv'
    elif type == 'inventory':
        cw.writerow(['ID', 'Hospital ID', 'Blood Group', 'Type', 'Qty', 'Expiry', 'Status'])
        rows = BloodBag.query.all()
        for r in rows: cw.writerow([r.id, r.hospital_id, r.blood_group, r.component_type, r.quantity, r.expiry_date, r.status])
        filename = 'blood_inventory_report.csv'
    else:
        return "Invalid Export Type", 400
        
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv"
    return output

# --- CHAT ROUTES ---

@app.route('/chat/<int:partner_id>')
@login_required
def chat(partner_id):
    partner = User.query.get_or_404(partner_id)
    messages = ChatMessage.query.filter(
        ((ChatMessage.sender_id == current_user.id) & (ChatMessage.receiver_id == partner.id)) |
        ((ChatMessage.sender_id == partner.id) & (ChatMessage.receiver_id == current_user.id))
    ).order_by(ChatMessage.timestamp.asc()).all()
    
    # Mark as read
    ChatMessage.query.filter_by(sender_id=partner.id, receiver_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    
    return render_template('chat.html', partner=partner, messages=messages)

@app.route('/my-chats')
@login_required
def my_chats():
    # Find all unique users I've chatted with
    sent_to = db.session.query(ChatMessage.receiver_id).filter_by(sender_id=current_user.id).distinct().all()
    received_from = db.session.query(ChatMessage.sender_id).filter_by(receiver_id=current_user.id).distinct().all()
    
    partner_ids = {uid[0] for uid in sent_to} | {uid[0] for uid in received_from}
    
    chats = []
    for pid in partner_ids:
        partner = User.query.get(pid)
        if not partner: continue
        
        last_msg = ChatMessage.query.filter(
            ((ChatMessage.sender_id == current_user.id) & (ChatMessage.receiver_id == pid)) |
            ((ChatMessage.sender_id == pid) & (ChatMessage.receiver_id == current_user.id))
        ).order_by(ChatMessage.timestamp.desc()).first()
        
        unread_count = ChatMessage.query.filter_by(sender_id=pid, receiver_id=current_user.id, is_read=False).count()
        
        chats.append({
            'user': partner,
            'last_msg': last_msg,
            'unread': unread_count
        })
    
    chats.sort(key=lambda x: x['last_msg'].timestamp, reverse=True)
    return render_template('my_chats.html', chats=chats)

@app.route('/api/send_message', methods=['POST'])
@login_required
def api_send_message():
    data = request.json
    receiver_id = data.get('receiver_id')
    text = data.get('message')
    
    if not receiver_id or not text:
        return jsonify({'success': False}), 400
        
    msg = ChatMessage(sender_id=current_user.id, receiver_id=receiver_id, message=text)
    db.session.add(msg)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/get_messages/<int:partner_id>')
@login_required
def api_get_messages(partner_id):
    messages = ChatMessage.query.filter(
        ((ChatMessage.sender_id == current_user.id) & (ChatMessage.receiver_id == partner_id)) |
        ((ChatMessage.sender_id == partner_id) & (ChatMessage.receiver_id == current_user.id))
    ).order_by(ChatMessage.timestamp.asc()).all()
    
    return jsonify([{
        'sender_id': m.sender_id,
        'message': m.message,
        'timestamp': m.timestamp.strftime('%H:%M')
    } for m in messages])

# --- HOSPITAL ROUTES ---

@app.route('/hospital/register', methods=['GET', 'POST'])
def hospital_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password')
        city = request.form.get('city', '').strip()
        address = request.form.get('address', '').strip()
        contact = request.form.get('contact', '').strip()
        
        if not contact or not email:
            return render_template('hospital_register.html', error="Email and Mobile number are required.")
            
        # Robust Uniqueness Check for both Mobile and Email
        existing_hosp = Hospital.query.filter(
            (Hospital.mobile_number == contact) | (Hospital.email == email)
        ).first()
        
        if existing_hosp:
            msg = "A hospital with this number exists." if existing_hosp.mobile_number == contact else "A hospital with this email exists."
            return render_template('hospital_register.html', error=msg)
            
        try:
            hosp = Hospital(
                name=name, email=email,
                password_hash=generate_password_hash(password),
                city=city, address=address, mobile_number=contact,
                latitude=0.0, longitude=0.0,
                is_approved=True # Auto-approve for now or set to False if admin review needed
            )
            db.session.add(hosp)
            db.session.commit()
            login_user(hosp)
            return redirect(url_for('hospital_dashboard'))
        except Exception as e:
            db.session.rollback()
            return render_template('hospital_register.html', error="Registration failed. Internal error.")
            
    return render_template('hospital_register.html')

@app.route('/hospital/login', methods=['GET', 'POST'])
def hospital_login():
    if request.method == 'POST':
        mobile = request.form.get('mobile_number', '').strip()
        password = request.form.get('password')
        
        hosp = Hospital.query.filter_by(mobile_number=mobile).first()
        if hosp and hosp.password_hash and check_password_hash(hosp.password_hash, password):
            if not hosp.is_approved:
                return render_template('hospital_login.html', error="Account pending approval.")
            login_user(hosp)
            return redirect(url_for('hospital_dashboard'))
        else:
            return render_template('hospital_login.html', error="Invalid credentials")
            
    return render_template('hospital_login.html')

@app.route('/api/heatmap-data')
@login_required
def heatmap_data():
    if not isinstance(current_user, (Admin, Hospital)): return jsonify([])
    reqs = BloodRequest.query.filter_by(status='Pending').all()
    # Return [lat, lng, intensity]
    data = [[r.req_latitude, r.req_longitude, min(1.0, 0.5 + 0.5 * (r.urgency_level == 'Critical'))] for r in reqs]
    return jsonify(data)

@app.route('/api/stock-predictor')
@login_required
def stock_predictor():
    """Predicts stock depletion in days based on 30-day burn rate"""
    if not isinstance(current_user, (Admin, Hospital)): return jsonify({})
    
    thirty_days_ago = datetime.utcnow().date() - timedelta(days=30)
    history = BloodBag.query.filter_by(hospital_id=current_user.id, status='Used') \
                            .filter(BloodBag.donation_date >= thirty_days_ago).all()
    
    current = BloodBag.query.filter_by(hospital_id=current_user.id, status='Available').all()
    
    from collections import Counter
    burn_rates = Counter([b.blood_group for b in history]) # Usage per 30 days
    stock = Counter([b.blood_group for b in current])
    
    predictions = {}
    for bg in ['A+', 'A-', 'B+', 'B-', 'O+', 'O-', 'AB+', 'AB-']:
        daily_burn = burn_rates.get(bg, 0.1) / 30.0
        current_amount = stock.get(bg, 0)
        days_left = current_amount / max(0.01, daily_burn)
        
        status = 'Stable'
        if days_left < 7: status = 'Critical'
        elif days_left < 14: status = 'Warning'
            
        predictions[bg] = {
            'days_left': round(days_left),
            'status': status,
            'burn_rate': round(daily_burn, 2)
        }
        
    return jsonify(predictions)

@app.route('/hospital/dashboard')
@login_required
def hospital_dashboard():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    
    total_stock = BloodBag.query.filter_by(hospital_id=current_user.id, status='Available').count()
    pending_reqs = BloodRequest.query.filter_by(hospital_name=current_user.name, status='Pending').count()
    emergency_reqs = BloodRequest.query.filter(BloodRequest.hospital_name==current_user.name, BloodRequest.status=='Pending', BloodRequest.urgency_level.in_(['Critical', 'High'])).count()
    
    bags = BloodBag.query.filter_by(hospital_id=current_user.id, status='Available').all()
    expiry_alerts = sum(1 for b in bags if (b.expiry_date - date.today()).days <= 7)
    
    # Analytics for Charts
    from collections import Counter
    blood_groups = [b.blood_group for b in bags]
    stock_by_group = dict(Counter(blood_groups))
    
    # Usage metrics
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    monthly_usage = BloodBag.query.filter_by(hospital_id=current_user.id, status='Used').filter(BloodBag.donation_date >= thirty_days_ago.date()).count()
    month_demand = BloodRequest.query.filter_by(hospital_name=current_user.name).filter(BloodRequest.created_at >= thirty_days_ago).count()
    
    recent_reqs = BloodRequest.query.filter_by(hospital_name=current_user.name).order_by(BloodRequest.created_at.desc()).limit(5).all()
    
    return render_template('hospital/dashboard.html', 
        total_stock=total_stock, pending_reqs=pending_reqs, 
        expiry_alerts=expiry_alerts, recent_reqs=recent_reqs,
        emergency_reqs=emergency_reqs, stock_by_group=stock_by_group,
        monthly_usage=monthly_usage, month_demand=month_demand)

@app.route('/hospital/broadcast', methods=['POST'])
@login_required
def hospital_broadcast():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    bg = request.form.get('blood_group')
    message = request.form.get('message')
    
    donors = User.query.filter_by(blood_group=bg, is_available=True).all()
    count = 0
    for d in donors:
        notif = Notification(user_id=d.id, type='Emergency', message=f"EMERGENCY from {current_user.name}: {message}")
        db.session.add(notif)
        count += 1
    db.session.commit()
    flash(f'Emergency Broadcast sent to {count} eligible {bg} donors in the network.', 'success')
    return redirect(url_for('hospital_dashboard'))

@app.route('/hospital/network', methods=['GET', 'POST'])
@login_required
def hospital_network():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    # Cross-hospital blood requests logic
    if request.method == 'POST':
        target_id = request.form.get('target_hospital_id')
        if target_id:
            req = HospitalRequest(
                requester_hospital_id=current_user.id,
                target_hospital_id=target_id,
                blood_group=request.form.get('blood_group'),
                quantity=int(request.form.get('quantity', 1)),
                urgency_level=request.form.get('urgency_level', 'High')
            )
            db.session.add(req)
            flash('Targeted request sent.', 'success')
        else:
            broadcast = HospitalBroadcast(
                hospital_id=current_user.id,
                blood_group=request.form.get('blood_group'),
                quantity=int(request.form.get('quantity', 1)),
                urgency=request.form.get('urgency_level', 'High'),
                expires_at=datetime.utcnow() + timedelta(hours=24)
            )
            db.session.add(broadcast)
            flash('Emergency Broadcast sent to all hospitals.', 'success')
            
        db.session.commit()
        return redirect(url_for('hospital_network'))
        
    other_hospitals = Hospital.query.filter(Hospital.id != current_user.id, Hospital.is_approved == True).all()
    
    # Existing targeted requests
    out_reqs = HospitalRequest.query.filter_by(requester_hospital_id=current_user.id).all()
    all_in = HospitalRequest.query.filter((HospitalRequest.target_hospital_id == current_user.id)).all()
    
    # Broadcast requests
    my_broadcasts = HospitalBroadcast.query.filter_by(hospital_id=current_user.id).all()
    active_broadcasts = HospitalBroadcast.query.filter(
        HospitalBroadcast.hospital_id != current_user.id,
        HospitalBroadcast.status == 'Active',
        HospitalBroadcast.expires_at > datetime.utcnow()
    ).all()
    
    # Filter out dismissed broadcasts
    hidden_broadcasts = session.get('hidden_broadcast_reqs', [])
    active_broadcasts = [b for b in active_broadcasts if b.id not in hidden_broadcasts]
    
    hidden_reqs = session.get('hidden_network_reqs', [])
    in_reqs = [r for r in all_in if r.id not in hidden_reqs and r.requester_hospital_id != current_user.id]
    
    return render_template('hospital/network.html', 
        hospitals=other_hospitals, 
        out_reqs=out_reqs, 
        in_reqs=in_reqs,
        my_broadcasts=my_broadcasts,
        active_broadcasts=active_broadcasts
    )

@app.route('/hospital/network/reply/<int:req_id>', methods=['POST'])
@login_required
def hospital_network_reply(req_id):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    action = request.form.get('action')
    req = HospitalRequest.query.get_or_404(req_id)
    
    if req.status != 'Pending':
        flash('This request has already been processed by another hospital.', 'warning')
        return redirect(url_for('hospital_network'))
        
    if action == 'accept':
        req.status = 'Fulfilled'
        req.target_hospital_id = current_user.id
        db.session.commit()
        flash(f'You accepted the network request for {req.blood_group}!', 'success')
    elif action == 'reject':
        if req.target_hospital_id == current_user.id:
            req.status = 'Rejected'
            db.session.commit()
            flash('Targeted request was rejected.', 'info')
        else:
            hidden = session.get('hidden_network_reqs', [])
            hidden.append(req.id)
            session['hidden_network_reqs'] = hidden
            session.modified = True
            flash('You declined the broadcast request.', 'info')
            
    return redirect(url_for('hospital_network'))

@app.route('/hospital/broadcast/reply/<int:broadcast_id>', methods=['POST'])
@login_required
def hospital_broadcast_reply(broadcast_id):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    action = request.form.get('action')
    broadcast = HospitalBroadcast.query.get_or_404(broadcast_id)
    
    if broadcast.status != 'Active':
        flash('This emergency broadcast has already been fulfilled or expired.', 'warning')
        return redirect(url_for('hospital_network'))
        
    if action == 'accept':
        broadcast.status = 'Accepted'
        broadcast.accepted_by_id = current_user.id
        db.session.commit()
        flash(f'You accepted the emergency broadcast for {broadcast.blood_group}!', 'success')
    elif action == 'reject':
        hidden = session.get('hidden_broadcast_reqs', [])
        hidden.append(broadcast.id)
        session['hidden_broadcast_reqs'] = hidden
        session.modified = True
        flash('You dismissed the broadcast request.', 'info')
            
    return redirect(url_for('hospital_network'))

@app.route('/hospital/export/<report_type>')
@login_required
def hospital_export(report_type):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    import io, csv
    from flask import Response
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    if report_type == 'inventory':
        bags = BloodBag.query.filter_by(hospital_id=current_user.id).all()
        writer.writerow(['Bag ID', 'Blood Group', 'Component', 'Quantity', 'Donation Date', 'Expiry Date', 'Status'])
        for b in bags:
            writer.writerow([b.id, b.blood_group, b.component_type, b.quantity, b.donation_date, b.expiry_date, b.status])
        filename = "inventory_report.csv"
        
    elif report_type == 'requests':
        reqs = BloodRequest.query.filter_by(hospital_name=current_user.name).all()
        writer.writerow(['Request ID', 'Patient', 'Blood Group', 'Urgency', 'Date', 'Status'])
        for r in reqs:
            writer.writerow([r.id, r.patient_name, r.blood_group, r.urgency_level, r.created_at.strftime('%Y-%m-%d'), r.status])
        filename = "requests_report.csv"
    else:
        return "Invalid report type", 400
        
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response

@app.route('/hospital/map')
@login_required
def hospital_map():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    donors = User.query.filter_by(is_available=True).all()
    hospitals = Hospital.query.filter_by(is_approved=True).all()
    reqs = BloodRequest.query.filter_by(status='Pending').all()
    return render_template('hospital/map.html', donors=donors, hospitals=hospitals, reqs=reqs)

@app.route('/hospital/profile', methods=['GET', 'POST'])
@login_required
def hospital_profile():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    if request.method == 'POST':
        current_user.city = request.form.get('city', current_user.city)
        current_user.address = request.form.get('address', current_user.address)
        
        new_password = request.form.get('new_password')
        if new_password:
            from werkzeug.security import generate_password_hash
            current_user.password_hash = generate_password_hash(new_password)
            
        db.session.commit()
        flash('Profile and security details updated successfully!', 'success')
        return redirect(url_for('hospital_profile'))
        
    return render_template('hospital/profile.html')

@app.route('/hospital/inventory', methods=['GET', 'POST'])
@login_required
def hospital_inventory():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    
    if request.method == 'POST':
        bg = request.form.get('blood_group')
        ctype = request.form.get('component_type')
        qty = int(request.form.get('quantity'))
        d_date = datetime.strptime(request.form.get('donation_date'), '%Y-%m-%d').date()
        e_date = d_date + timedelta(days=42)
        
        bag = BloodBag(hospital_id=current_user.id, blood_group=bg, component_type=ctype, quantity=qty, donation_date=d_date, expiry_date=e_date)
        db.session.add(bag)
        
        hist = InventoryHistory(
            hospital_id=current_user.id,
            action='Added',
            blood_group=bg,
            quantity=qty,
            date=datetime.utcnow()
        )
        db.session.add(hist)
        
        db.session.commit()
        flash('Stock added successfully!', 'success')
        return redirect(url_for('hospital_inventory'))
        
    all_bags = BloodBag.query.filter_by(hospital_id=current_user.id).order_by(BloodBag.expiry_date.desc()).all()
    today_date = date.today()
    
    # Auto-expire available bags that passed expiration
    changed = False
    for b in all_bags:
        b.days_to_expiry = (b.expiry_date - today_date).days
        if b.status == 'Available' and b.days_to_expiry < 0:
            b.status = 'Expired'
            changed = True
            
            # Log Expiration
            hist = InventoryHistory(
                hospital_id=current_user.id,
                action='Expired',
                blood_group=b.blood_group,
                quantity=b.quantity,
                date=datetime.utcnow()
            )
            db.session.add(hist)
            
    if changed:
        db.session.commit()
        
    active_bags = [b for b in all_bags if b.status == 'Available']
    history_bags = [b for b in all_bags if b.status != 'Available']
    # sort active bags by closest expiry
    active_bags.sort(key=lambda x: x.days_to_expiry)
    
    bg_counts = {}
    for b in active_bags:
        bg_counts[b.blood_group] = bg_counts.get(b.blood_group, 0) + b.quantity
    low_stock_alerts = [bg for bg in ['A+', 'A-', 'B+', 'B-', 'O+', 'O-', 'AB+', 'AB-'] if bg_counts.get(bg, 0) < 3]
        
    return render_template('hospital/inventory.html', bags=active_bags, history_bags=history_bags, low_stock_alerts=low_stock_alerts)

@app.route('/hospital/use-bag/<int:bag_id>', methods=['POST'])
@login_required
def hospital_use_bag(bag_id):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    bag = BloodBag.query.get_or_404(bag_id)
    if bag.hospital_id == current_user.id and bag.status == 'Available':
        bag.status = 'Used'
        
        hist = InventoryHistory(
            hospital_id=current_user.id,
            action='Used',
            blood_group=bag.blood_group,
            quantity=bag.quantity,
            date=datetime.utcnow()
        )
        db.session.add(hist)
        
        db.session.commit()
        flash('Bag marked as used.', 'success')
    return redirect(url_for('hospital_inventory'))

@app.route('/hospital/history', methods=['GET'])
@login_required
def hospital_history():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
        
    query = InventoryHistory.query.filter_by(hospital_id=current_user.id)
    
    # Search Filters
    bg_filter = request.args.get('blood_group')
    action_filter = request.args.get('action')
    date_filter = request.args.get('date')
    
    if bg_filter:
        query = query.filter(InventoryHistory.blood_group == bg_filter)
    if action_filter:
        query = query.filter(InventoryHistory.action == action_filter)
    if date_filter:
        try:
            filter_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
            # Assuming date field is DateTime, we need to filter by date part
            query = query.filter(db.func.date(InventoryHistory.date) == filter_date)
        except ValueError:
            pass
            
    history_logs = query.order_by(InventoryHistory.date.desc()).all()
    
    return render_template('hospital/inventory_history.html', history_logs=history_logs)

@app.route('/hospital/edit-bag/<int:bag_id>', methods=['POST'])
@login_required
def hospital_edit_bag(bag_id):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    bag = BloodBag.query.get_or_404(bag_id)
    if bag.hospital_id == current_user.id:
        bag.blood_group = request.form.get('blood_group', bag.blood_group)
        bag.component_type = request.form.get('component_type', bag.component_type)
        bag.quantity = int(request.form.get('quantity', bag.quantity))
        if request.form.get('donation_date'):
            bag.donation_date = datetime.strptime(request.form.get('donation_date'), '%Y-%m-%d').date()
        if request.form.get('expiry_date'):
            bag.expiry_date = datetime.strptime(request.form.get('expiry_date'), '%Y-%m-%d').date()
        db.session.commit()
        flash('Stock updated successfully!', 'success')
    return redirect(url_for('hospital_inventory'))

@app.route('/hospital/mark-bag/<int:bag_id>/<action>')
@login_required
def hospital_mark_bag(bag_id, action):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    bag = BloodBag.query.get_or_404(bag_id)
    if bag.hospital_id == current_user.id:
        if action in ['Used', 'Expired']:
            bag.status = action
            flash(f'Bag marked as {action}', 'success')
        elif action == 'Delete':
            db.session.delete(bag)
            flash('Bag permanently deleted', 'success')
        db.session.commit()
    return redirect(url_for('hospital_inventory'))

@app.route('/hospital/requisition', methods=['GET', 'POST'])
@login_required
def hospital_requisition():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    
    if request.method == 'POST':
        req = BloodRequest(
            patient_name=request.form.get('patient_name'),
            blood_group=request.form.get('blood_group'),
            hospital_name=current_user.name,
            hospital_location=current_user.city,
            req_latitude=current_user.latitude or 0.0,
            req_longitude=current_user.longitude or 0.0,
            urgency_level=request.form.get('urgency_level'),
            contact_number=current_user.mobile_number
        )
        db.session.add(req)
        db.session.commit()
        flash('Requisition created.', 'success')
        return redirect(url_for('hospital_requisition'))
        
    reqs = BloodRequest.query.filter_by(hospital_name=current_user.name).order_by(BloodRequest.created_at.desc()).all()
    return render_template('hospital/requisition.html', reqs=reqs)

@app.route('/hospital/fulfill-req/<int:req_id>', methods=['POST'])
@login_required
def fulfill_requisition(req_id):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    req = BloodRequest.query.get_or_404(req_id)
    if req.hospital_name == current_user.name:
        req.status = 'Fulfilled'
        
        # Enforce Cooldown & Create History Record if fulfilled by a donor
        from datetime import date
        if req.fulfilled_by_id:
            donor = User.query.get(req.fulfilled_by_id)
            if donor:
                donor.last_donation_date = date.today()
                donor.is_available = False # Triggers gap cooldown logic automatically based on SystemSettings via check_donor_eligibility
                
                # Add to history
                from models import DonationHistory
                hist = DonationHistory(donor_id=donor.id, donation_date=date.today(), notes=f"Donated for {req.patient_name} at {req.hospital_name}")
                db.session.add(hist)
                
        db.session.commit()
        flash('Requisition marked as fulfilled. Donor cooldown applied.', 'success')
    return redirect(url_for('hospital_requisition'))

@app.route('/hospital/find-donors/<int:req_id>')
@login_required
def hospital_find_donors(req_id):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    req = BloodRequest.query.get_or_404(req_id)
    if req.hospital_name != current_user.name:
        return "Unauthorized", 403
    
    from geopy.distance import geodesic
    ai_conf = AIConfig.query.first()
    
    # Defaults in case DB empty
    w_dist = ai_conf.weight_distance if ai_conf else 30.0
    w_health = ai_conf.weight_health if ai_conf else 10.0
    w_rep = ai_conf.weight_reputation if ai_conf else 20.0
    
    # Filter by group, availability and eligibility
    candidates = User.query.filter(User.blood_group==req.blood_group, User.is_available==True, User.is_approved==True).all()
    
    scored = []
    # Dynamic AI match scoring simulator 
    for c in candidates:
        if current_user.latitude and current_user.longitude and c.latitude and c.longitude:
            dist = geodesic((current_user.latitude, current_user.longitude), (c.latitude, c.longitude)).km
        else:
            dist = 999
            
        # Dynamic weighting implementation
        score = 0.0
        
        # Distance (Closer = higher score up to w_dist factor)
        if dist < 10: score += w_dist
        elif dist < 30: score += (w_dist * 0.5)
        elif dist < 50: score += (w_dist * 0.2)
        
        # Reputation
        rep_ratio = min(100.0, float(c.reputation_score or 100)) / 100.0
        score += (rep_ratio * w_rep)
        
        # Perfect Health Check 
        if not c.bp and not c.sugar and not c.heart_disease and not c.asthma:
            score += w_health
        
        scored.append({
            'donor': c,
            'distance': round(dist, 1),
            'score': round(score, 1)
        })
        
    # Prioritize by AI score descending
    scored.sort(key=lambda x: x['score'], reverse=True)
    return render_template('hospital/find_donors.html', req=req, donors=scored)

@app.route('/hospital/assign-donor/<int:req_id>/<int:donor_id>', methods=['POST'])
@login_required
def hospital_assign_donor(req_id, donor_id):
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    req = BloodRequest.query.get_or_404(req_id)
    if req.hospital_name == current_user.name:
        req.status = 'Assigned'
        donor = User.query.get(donor_id)
        if donor:
            req.fulfilled_by_id = donor.id
            
            # Create a formal assignment record
            assignment = DonorAssignment(
                request_id=req.id,
                donor_id=donor.id,
                status='Pending'
            )
            db.session.add(assignment)
             
            # Notify the donor
            notif = Notification(
                user_id=donor.id, 
                type='Assignment', 
                message=f"You have been assigned to an emergency request from {current_user.name} for {req.blood_group} blood. Please respond in your dashboard."
            )
            db.session.add(notif)
            
        db.session.commit()
        flash(f'Donor Assigned successfully! Notification sent.', 'success')
    return redirect(url_for('hospital_requisition'))

@app.route('/donor/assignment/reply/<int:assignment_id>', methods=['POST'])
@login_required
def donor_assignment_reply(assignment_id):
    if not isinstance(current_user, User): return redirect(url_for('home'))
    action = request.form.get('action')
    assignment = DonorAssignment.query.get_or_404(assignment_id)
    
    if assignment.donor_id != current_user.id or assignment.status != 'Pending':
        flash('Invalid action.', 'danger')
        return redirect(url_for('donor_dashboard'))
        
    req = BloodRequest.query.get(assignment.request_id)
    
    if action == 'accept':
        assignment.status = 'Accepted'
        db.session.commit()
        flash(f'You have accepted the assignment for {req.hospital_name}. Thank you!', 'success')
    elif action == 'reject':
        assignment.status = 'Rejected'
        req.fulfilled_by_id = None
        db.session.commit()
        
        # Auto-reassign logic
        from geopy.distance import geodesic
        ai_conf = AIConfig.query.first()
        w_dist = ai_conf.weight_distance if ai_conf else 30.0
        w_health = ai_conf.weight_health if ai_conf else 10.0
        w_rep = ai_conf.weight_reputation if ai_conf else 20.0
        
        candidates = User.query.filter(User.blood_group==req.blood_group, User.is_available==True, User.is_approved==True).all()
        # filter out the donor who just rejected
        candidates = [c for c in candidates if c.id != current_user.id]
        
        best_donor = None
        best_score = -1
        
        if candidates:
            # Re-fetch the hospital to get its location (we only stored its name/location strings in request initially)
            hosp = Hospital.query.filter_by(name=req.hospital_name).first()
            h_lat = hosp.latitude if hosp else req.req_latitude
            h_lng = hosp.longitude if hosp else req.req_longitude
            
            for c in candidates:
                if h_lat and h_lng and c.latitude and c.longitude:
                    dist = geodesic((h_lat, h_lng), (c.latitude, c.longitude)).km
                else:
                    dist = 999
                    
                score = 0.0
                if dist < 10: score += w_dist
                elif dist < 30: score += (w_dist * 0.5)
                elif dist < 50: score += (w_dist * 0.2)
                
                rep_ratio = min(100.0, float(c.reputation_score or 100)) / 100.0
                score += (rep_ratio * w_rep)
                
                if not c.bp and not c.sugar and not c.heart_disease and not c.asthma:
                    score += w_health
                    
                if score > best_score:
                    best_score = score
                    best_donor = c
                    
        if best_donor:
            req.status = 'Assigned'
            req.fulfilled_by_id = best_donor.id
            
            new_assignment = DonorAssignment(request_id=req.id, donor_id=best_donor.id, status='Pending')
            db.session.add(new_assignment)
            
            notif = Notification(user_id=best_donor.id, type='Assignment', message=f"You have been auto-assigned to an emergency request from {req.hospital_name} for {req.blood_group} blood. Please respond in your dashboard.")
            db.session.add(notif)
            
            flash('You have declined the assignment. The system has automatically reassigned a new donor.', 'info')
        else:
            req.status = 'Pending'
            flash('You have declined the assignment. The hospital will be notified to find another donor.', 'info')
            
        db.session.commit()
        
    return redirect(url_for('donor_dashboard'))

@app.route('/hospital/appointments')
@login_required
def hospital_appointments():
    if not isinstance(current_user, Hospital): return redirect(url_for('home'))
    apts = DonorAppointment.query.filter_by(hospital_id=current_user.id).order_by(DonorAppointment.appointment_date.desc()).all()
    for a in apts: a.donor = User.query.get(a.donor_id)
    return render_template('hospital/appointments.html', appointments=apts)

# --- ADMIN ROUTES ENHANCEMENTS ---

@app.route('/admin/edit-user/<int:user_id>', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    if not isinstance(current_user, Admin):
        return "Unauthorized", 403
        
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.full_name = request.form.get('full_name')
        user.mobile_number = request.form.get('mobile_number')
        new_pass = request.form.get('password')
        if new_pass:
            user.password_hash = generate_password_hash(new_pass)
        
        db.session.commit()
        return redirect(url_for('admin_dashboard'))
        
    return render_template('edit_user.html', user=user)

# Database Initialization moved to separate scripts or CLI
# with app.app_context():
#     db.create_all()

@app.route('/leaderboard')
def leaderboard():
    """Donor Leaderboard showcasing Top 10 users by reputation score & total donations"""
    top_donors = User.query.filter_by(is_approved=True).order_by(User.reputation_score.desc()).limit(10).all()
    # Adding badge logic via template or dynamic processing
    for d in top_donors:
        ds = len(d.history)
        if ds >= 10: d.badge = '🏆 Gold'
        elif ds >= 5: d.badge = '🥇 Silver'
        elif ds >= 1: d.badge = '🥉 Bronze'
        else: d.badge = '🌱 Rookie'
    return render_template('leaderboard.html', donors=top_donors)

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')

@socketio.on('connect')
def test_connect():
    print('Client connected to Live Engine')

if __name__ == '__main__':
    socketio.run(app, debug=True)

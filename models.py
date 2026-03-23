from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=True) # Optional for existing users
    mobile_number = db.Column(db.String(15), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    blood_group = db.Column(db.String(5), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    city = db.Column(db.String(50), nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    language = db.Column(db.String(10), default='en')
    
    # Health & Habits
    bp = db.Column(db.Boolean, default=False)
    sugar = db.Column(db.Boolean, default=False)
    heart_disease = db.Column(db.Boolean, default=False)
    asthma = db.Column(db.Boolean, default=False)
    smoking = db.Column(db.Boolean, default=False)
    drinking = db.Column(db.Boolean, default=False)
    
    # Advanced Health Vitals
    gender = db.Column(db.String(10), default='Male')
    weight = db.Column(db.Float, nullable=True)
    hemoglobin = db.Column(db.Float, nullable=True)
    pulse_rate = db.Column(db.Integer, nullable=True)
    systolic_bp = db.Column(db.Integer, nullable=True)
    diastolic_bp = db.Column(db.Integer, nullable=True)
    sugar_level = db.Column(db.Integer, nullable=True) # mg/dL
    
    # Female Specific
    is_pregnant = db.Column(db.Boolean, default=False)
    is_breastfeeding = db.Column(db.Boolean, default=False)
    menstrual_cycle_safe = db.Column(db.Boolean, default=True)
    
    last_donation_date = db.Column(db.Date, nullable=True)
    is_available = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=True) # Admin approval
    qr_code_data = db.Column(db.String(500), nullable=True) # Data for QR ID
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Security & Tracking
    last_active = db.Column(db.DateTime, nullable=True)
    is_online = db.Column(db.Boolean, default=False)
    failed_login_attempts = db.Column(db.Integer, default=0)
    is_locked = db.Column(db.Boolean, default=False)
    reputation_score = db.Column(db.Float, default=100.0) # 0-100
    risk_score = db.Column(db.Float, default=0.0)

    # Manual backrefs definition to avoid collisions if any
    # (Using overlaps or strictly defining in one place is better)

    def get_id(self):
        return f"user_{self.id}"

class Hospital(UserMixin, db.Model):
    __tablename__ = 'hospitals'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=True)
    password_hash = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(50), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    mobile_number = db.Column(db.String(20), unique=True, nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    blood_stock = db.Column(db.JSON, default={})
    is_approved = db.Column(db.Boolean, default=True)

    def get_id(self):
        return f"hospital_{self.id}"

class Admin(UserMixin, db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='SuperAdmin')

    def get_id(self):
        return f"admin_{self.id}"

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, nullable=True)
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.String(255), nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class SystemSettings(db.Model):
    __tablename__ = 'system_settings'
    id = db.Column(db.Integer, primary_key=True)
    donation_gap_days = db.Column(db.Integer, default=180)
    emergency_radius_km = db.Column(db.Float, default=50.0)
    admin_contact_email = db.Column(db.String(100), default='admin@lifelink.com')
    
    # Comms & Twilio configuration
    enable_sms = db.Column(db.Boolean, default=False)
    enable_whatsapp = db.Column(db.Boolean, default=False)
    
    # Expiry Settings
    critical_expiry_days = db.Column(db.Integer, default=2)
    medium_expiry_days = db.Column(db.Integer, default=5)
    low_expiry_days = db.Column(db.Integer, default=10)
    
    # Reputation Settings
    missed_commitment_penalty = db.Column(db.Float, default=10.0)
    success_donation_bonus = db.Column(db.Float, default=5.0)

class AIConfig(db.Model):
    __tablename__ = 'ai_config'
    id = db.Column(db.Integer, primary_key=True)
    weight_blood_group = db.Column(db.Float, default=40.0)
    weight_distance = db.Column(db.Float, default=30.0)
    weight_recency = db.Column(db.Float, default=20.0)
    weight_health = db.Column(db.Float, default=10.0)
    weight_reputation = db.Column(db.Float, default=20.0)
    weight_response = db.Column(db.Float, default=10.0)

class BloodRequest(db.Model):
    __tablename__ = 'blood_requests'
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    patient_name = db.Column(db.String(100), nullable=False)
    blood_group = db.Column(db.String(5), nullable=False)
    hospital_name = db.Column(db.String(100), nullable=False)
    hospital_location = db.Column(db.String(255), nullable=True)
    req_latitude = db.Column(db.Float, nullable=False)
    req_longitude = db.Column(db.Float, nullable=False)
    urgency_level = db.Column(db.String(20), default='Medium')
    contact_number = db.Column(db.String(15), nullable=False)
    status = db.Column(db.String(20), default='Pending') # Pending, Fulfilled, Cancelled, Expired
    fulfilled_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    expiry_days = db.Column(db.Integer, default=5)
    expiry_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Removed duplicate created_at and relationship (User.requests is sufficient if defined there or backref)
    requester = db.relationship('User', foreign_keys=[requester_id], backref='requests')
    fulfilled_by = db.relationship('User', foreign_keys=[fulfilled_by_id], backref='fulfilled_requests')

    @property
    def status_color(self):
        return '#eee' if self.status == 'Pending' else '#d4edda'

class DonationHistory(db.Model):
    __tablename__ = 'donation_history'
    id = db.Column(db.Integer, primary_key=True)
    donor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    donation_date = db.Column(db.Date, nullable=False)
    notes = db.Column(db.String(200), nullable=True)
    
    # Blockchain Simulation
    blockchain_hash = db.Column(db.String(64), nullable=True)
    previous_hash = db.Column(db.String(64), nullable=True)
    verified = db.Column(db.Boolean, default=False)
    
    donor = db.relationship('User', backref='history')

class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    priority = db.Column(db.Integer, default=3) # 1=Critical, 2=High, 3=Medium
    status = db.Column(db.String(20), default='Unread') # Unread, Sent, Escalated
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class BloodBag(db.Model):
    __tablename__ = 'blood_bags'
    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=False)
    blood_group = db.Column(db.String(5), nullable=False)
    component_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    donation_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='Available')

class HospitalRequest(db.Model):
    __tablename__ = 'hospital_requests'
    id = db.Column(db.Integer, primary_key=True)
    requester_hospital_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=False)
    target_hospital_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=True)
    blood_group = db.Column(db.String(5), nullable=False)
    component_type = db.Column(db.String(50), default='Whole Blood')
    quantity = db.Column(db.Integer, default=1)
    urgency_level = db.Column(db.String(20), default='Medium')
    status = db.Column(db.String(20), default='Pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DonorAppointment(db.Model):
    __tablename__ = 'donor_appointments'
    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=False)
    donor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    appointment_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='Scheduled')
    post_care_notes = db.Column(db.Text, nullable=True)

class BloodDrive(db.Model):
    __tablename__ = 'blood_drives'
    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False) # Namespaced or simple ID? Sticking to app.py logic
    receiver_id = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

class Interest(db.Model):
    __tablename__ = 'interests'
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('blood_requests.id'), nullable=False)
    donor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ConsentLog(db.Model):
    __tablename__ = 'consent_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    consent_given = db.Column(db.Boolean, nullable=False)
    ip_address = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class HospitalBroadcast(db.Model):
    __tablename__ = 'hospital_broadcasts'
    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=False)
    blood_group = db.Column(db.String(5), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    urgency = db.Column(db.String(20), default='High')
    status = db.Column(db.String(20), default='Active') # Active, Accepted, Expired
    accepted_by_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)

class InventoryHistory(db.Model):
    __tablename__ = 'inventory_history'
    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.Integer, db.ForeignKey('hospitals.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False) # Added, Used, Expired
    blood_group = db.Column(db.String(5), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

class DonorAssignment(db.Model):
    __tablename__ = 'donor_assignments'
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('blood_requests.id'), nullable=False)
    donor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(20), default='Pending') # Pending, Accepted, Rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

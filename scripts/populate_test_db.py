# Verify Scripts for Database Setup
import os
from datetime import datetime, timedelta
from app import app, db
from models import User, Hospital, BloodRequest, BloodBag, HospitalBroadcast, Notification, DonationHistory, DonorAssignment

def populate_test_data():
    with app.app_context():
        # First ensure DB tables exist
        db.create_all()
        
        # 1. Create a Hospital if none exists
        hospital = Hospital.query.first()
        if not hospital:
            print("Creating test hospital...")
            hospital = Hospital(
                name="Central AI Health",
                email="ai@centralhealth.com",
                city="Metropolis",
                mobile_number="555-0100",
                is_approved=True
            )
            hospital.set_password("mypass123")
            db.session.add(hospital)
            db.session.commit()
            
        # 2. Add some inventory to hospital to simulate AI shortage calculation
        print("Adding Blood Bags for Shortage calculations...")
        for i in range(2):
            bag = BloodBag(
                hospital_id=hospital.id, 
                blood_group="A+", 
                component_type="Whole Blood",
                quantity=1, 
                status='Available',
                donation_date=datetime.utcnow().date(),
                expiry_date=datetime.utcnow().date() + timedelta(days=42)
            )
            db.session.add(bag)
        
        # 3. Add historical requests (Last 30 days) to trigger 'Shortage' and 'Emergency Risk' 
        print("Adding Historical High-Risk Requests...")
        thirty_days_ago = datetime.utcnow() - timedelta(days=5)
        for i in range(5):
            req = BloodRequest(
                patient_name=f"Urgent Patient {i}",
                blood_group="A+",
                urgency_level="Critical", # This feeds AI Emergency Risk (by city)
                hospital_name=hospital.name,
                hospital_location=hospital.city,
                status="Pending",
                req_latitude=40.7128,
                req_longitude=-74.0060,
                contact_number="555-0000"
            )
            req.created_at = thirty_days_ago # Set old date to ensure it's in the 30-day window
            db.session.add(req)
            
        # 4. Create a Donor for Assignment/Notifications
        donor = User.query.filter_by(blood_group="A+").first()
        if not donor:
            donor = User(
                full_name="AI Test Donor",
                email="test_donor@example.com",
                mobile_number="555-0200",
                blood_group="A+",
                city="Metropolis",
                is_available=True
            )
            donor.set_password("mypass123")
            db.session.add(donor)
            db.session.commit()
            
        # 5. Add a Broadcast
        broadcast = HospitalBroadcast(
            hospital_id=hospital.id,
            blood_group="O-",
            quantity=3,
            urgency="High",
            status="Active",
            expires_at=datetime.utcnow() + timedelta(days=1)
        )
        db.session.add(broadcast)

        db.session.commit()
        print("Data Population Complete! The Admin Dashboard should now feature A+ AI Shortage Warnings, and Metropolis AI Risk Predictions.")

if __name__ == '__main__':
    populate_test_data()

from app import app, db
import models
from werkzeug.security import generate_password_hash
import os
from sqlalchemy import inspect
from datetime import datetime, date, timedelta

def reset_db():
    print("Resetting database...")
    with app.app_context():
        # 1. Close connections
        db.session.remove()
        db.engine.dispose()
        
        db_uri = app.config['SQLALCHEMY_DATABASE_URI']
        if db_uri.startswith('sqlite:///'):
            db_path = db_uri.replace('sqlite:///', '')
            if not os.path.isabs(db_path):
                db_path = os.path.join(app.instance_path, db_path)
            
            if os.path.exists(db_path):
                print(f"Deleting: {db_path}")
                try:
                    os.remove(db_path)
                except Exception as e:
                    print(f"Error deleting: {e}")

        # 2. Verify metadata
        print(f"Metadata tables: {db.metadata.tables.keys()}")
        
        # 3. Create tables
        print("Creating tables...")
        db.create_all()
        
        # 4. Verify creation
        inspector = inspect(db.engine)
        actual_tables = inspector.get_table_names()
        print(f"Actual tables created: {actual_tables}")
        
        if 'system_settings' not in actual_tables:
            print("CRITICAL: system_settings table NOT created!")
            # Force create it if possible
            # But let's see why first.
            
        # 5. Seed
        print("Seeding...")
        try:
            settings = models.SystemSettings(
                donation_gap_days=90,
                emergency_radius_km=50.0,
                admin_contact_email='admin@lifelink.com',
                critical_expiry_days=2,
                medium_expiry_days=5,
                low_expiry_days=10
            )
            db.session.add(settings)
            
            ai = models.AIConfig()
            db.session.add(ai)
            
            # Admin must now be created via CLI or protected secure route
            print("System seeded. Please create an Admin account securely.")
            
            db.session.commit()
            print("Successfully seeded.")
        except Exception as e:
            print(f"Seed Error: {e}")
            db.session.rollback()

if __name__ == '__main__':
    reset_db()

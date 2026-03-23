# LifeLink – Smart Blood Donation Management System 🩸

**LifeLink** is a comprehensive, enterprise-level web application designed to revolutionize blood donation management. It bridges the gap between donors, hospitals, and patients in need during emergencies. Unlike traditional directories, LifeLink uses real-time WebSockets, a **Smart Matching Algorithm**, precise Geolocation, and advanced medical eligibility checks to find the nearest, safest, and most compatible donors within seconds.

## 🌟 Key Features

### 🧠 Smart Donor Matching
Algorithms filter and rank donors based on:
- **Location Closeness:** Calculated using precise `geopy` distance metrics.
- **Recency of Donation:** Enforces a strict 90-day cooldown period.
- **Health Safety:** Analyzes habits (smoking, drinking) and vitals.
- **Reputation Score:** Dynamic scoring based on past fulfilled commitments.

### ⚡ Real-Time & Live Capabilities
- **WebSockets (Flask-SocketIO):** Instant "Request Accepted" and "New Blood Demand" alerts without refreshing the page.
- **Live Interactive Map:** Visualizes donor availability, pending requests, and approved hospitals dynamically.

### 🛡️ Enterprise-Grade Security & Compliance
- **Rate Limiting:** Protects critical endpoints like login and registration against brute force attacks using `Flask-Limiter`.
- **Audit & GDPR Logging:** Consent logs track user data agreements and IP addresses securely.
- **Immutable Ledger Simulation:** Donation records are secured with SHA-256 blockchain-style hashing to maintain integrity.

### 🏥 Multi-Role Architecture
- **Donor Dashboard:** Manage health vitals, handle nearby requests, view donation history & certificates, and toggle availability.
- **Hospital Portal:** Broadcast urgent demands, manage inventory searches, fulfill requisitions, and query donor availability.
- **Admin Panel:** Complete system management, user/hospital approval workflows, and system settings overrides.

### 💌 Robust Notification Engine
- **Automated Email Notifications:** Handles critical urgent requests securely via `Flask-Mail`.
- **Scaffolded SMS & WhatsApp Alerts:** Prepared for Twilio integration to ensure immediate reachability.
- **Internal App Notifications:** Rewards, matches, requests, and system-level alerts directly inside the portal.

## 🛠️ Tech Stack

- **Backend:** Python 3, Flask, Flask-SocketIO
- **Database:** SQLAlchemy ORM (PostgreSQL Ready / SQLite Fallback)
- **Frontend:** HTML5, CSS3 (Modern Dark Theme with Red Accents), JavaScript
- **Security:** Werkzeug (Password Hashing), Flask-Login (Session Management), Flask-Limiter
- **Geospatial:** Geopy API 

## 📦 Installation & Setup

1. **Clone the Repository**
   ```bash
   git clone https://github.com/yourusername/lifelink.git
   cd lifelink
   ```

2. **Create a Virtual Environment**
   ```bash
   python -m venv venv
   # On Windows
   venv\Scripts\activate
   # On Mac/Linux
   source venv/bin/activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment variables (Optional)**
   While development defaults to SQLite and mock configurations, you can update `app.py` or your system environment variables to configure production databases (like PostgreSQL) or SMTP credentials for your email.

5. **Initialize the Database & Run**
   The application will automatically build the necessary database tables on the first run.
   ```bash
   python app.py
   ```
   *Visit `http://127.0.0.1:5000` in your web browser.*

*Note: For populating test data, you can run the `populate_test_db.py` script provided in the repository.*

## 🔐 Default Admin Credentials
To access the administrative panel upon a fresh installation:
- **Mobile Number / Username:** `admin`
- **Password:** `admin123`



## 🤝 Contributing
Contributions, issues, and feature requests are welcome! 

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
*Built to save lives.*

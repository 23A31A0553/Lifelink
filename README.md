# LifeLink – Smart Blood Donation Management System

**LifeLink** is a smart, web-based blood donation management system designed to bridge the gap between donors and patients in need during emergencies. Unlike traditional directories, LifeLink utilizes **Geolocation API** and a **Smart Matching Algorithm** to find the nearest, safest, and most compatible donors within seconds.

## 🚀 Features

-   **Smart Donor Matching**: Algorithms filter donors based on blood group compatibility, distance (Geolocation), recency of donation, and health safety.
-   **Email Notifications**: Automated alerts sent to donors in the same city when an urgent blood request is posted.
-   **Real-time Geolocation**: Uses browser `navigator.geolocation` and `geopy` to calculate precise distances.
-   **User Dashboard**: Donors can manage their profile, view history, and receive requests.
-   **Admin Panel**: Comprehensive management for Users, Requests, Hospitals, and System Settings.
-   **Health Safety Checks**: strict filtering for eligibility (e.g., last donation > 90 days, no critical health flags).
-   **Security**: Password hashing (Werkzeug) and session protection (Flask-Login).

## 🛠️ Tech Stack

-   **Backend**: Python (Flask)
-   **Database**: SQLite (SQLAlchemy ORM)
-   **Frontend**: HTML5, CSS3, JavaScript
-   **Email Service**: Flask-Mail (SMTP)
-   **Distance Calculation**: Geopy

## 📦 Installation

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/yourusername/lifelink.git
    cd lifelink
    ```

2.  **Create a Virtual Environment**
    ```bash
    python -m venv .venv
    # Windows
    .venv\Scripts\activate
    # Mac/Linux
    source .venv/bin/activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Initialize the Database**
    The app will automatically create the database on the first run.

5.  **Run the Application**
    ```bash
    python app.py
    ```
    The app will assume `debug=True` by default. Visit `http://127.0.0.1:5000` in your browser.

## 🔐 Usage

### Default Admin Credentials
-   **Username**: `admin`
-   **Password**: `admin123`

### Donors
-   Register with your location enabled.
-   Update your health status in the dashboard.
-   Wait for blood requests nearby!

## 🤝 Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

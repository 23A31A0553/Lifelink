CREATE DATABASE IF NOT EXISTS lifelink;
USE lifelink;

-- Donors Table
CREATE TABLE IF NOT EXISTS donors (
    id INT AUTO_INCREMENT PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    mobile_number VARCHAR(15) UNIQUE NOT NULL,
    blood_group VARCHAR(5) NOT NULL,
    age INT NOT NULL,
    city VARCHAR(50) NOT NULL,
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    last_donation_date DATE,
    
    -- Health Conditions (1 = Yes, 0 = No)
    bp BOOLEAN DEFAULT 0,
    sugar BOOLEAN DEFAULT 0,
    heart_disease BOOLEAN DEFAULT 0,
    asthma BOOLEAN DEFAULT 0,
    
    -- Habits
    smoking BOOLEAN DEFAULT 0,
    drinking BOOLEAN DEFAULT 0,
    
    is_available BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Emergency Blood Requests Table
CREATE TABLE IF NOT EXISTS blood_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_name VARCHAR(100) NOT NULL, 
    blood_group VARCHAR(5) NOT NULL,
    hospital_name VARCHAR(100) NOT NULL,
    hospital_location VARCHAR(255), -- Text description or Geocoded
    req_latitude DECIMAL(10, 8),
    req_longitude DECIMAL(11, 8),
    urgency_level ENUM('Low', 'Medium', 'High') DEFAULT 'Medium',
    contact_number VARCHAR(15) NOT NULL,
    status ENUM('Pending', 'Fulfilled', 'Cancelled') DEFAULT 'Pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Admin Table
CREATE TABLE IF NOT EXISTS admins (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast search
CREATE INDEX idx_blood_group ON donors(blood_group);
CREATE INDEX idx_city ON donors(city);
CREATE INDEX idx_availability ON donors(is_available);

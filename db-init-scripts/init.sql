CREATE TABLE IF NOT EXISTS Users (
    user_id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    username VARCHAR(255),
    first_name VARCHAR(255) NOT NULL,
    last_name VARCHAR(255),
    language VARCHAR(255) NOT NULL DEFAULT 'EN'
);

CREATE TABLE IF NOT EXISTS Applications (
    application_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES Users(user_id),
    application_number VARCHAR(255) NOT NULL,
    application_suffix VARCHAR(255),
    application_type VARCHAR(255) NOT NULL,
    application_year INT NOT NULL,
    current_status VARCHAR(1000) DEFAULT 'Unknown',
    application_state VARCHAR(50) NOT NULL DEFAULT 'UNKNOWN',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_at TIMESTAMP,
    last_updated TIMESTAMP,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS Reminders (
    reminder_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES Users(user_id),
    application_id INT REFERENCES Applications(application_id) ON DELETE CASCADE,
    reminder_time TIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


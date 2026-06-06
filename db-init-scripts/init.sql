-- Telegram users known to the bot
CREATE TABLE IF NOT EXISTS Users (
    user_id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    username VARCHAR(255),
    first_name VARCHAR(255) NOT NULL,
    last_name VARCHAR(255),
    language VARCHAR(255) NOT NULL DEFAULT 'EN',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMP NULL,
    deactivation_reason VARCHAR(255) NULL
);

-- MVCR applications tracked per user
CREATE TABLE IF NOT EXISTS Applications (
    application_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES Users(user_id),
    application_number VARCHAR(255) NOT NULL,
    application_suffix VARCHAR(255),
    application_type VARCHAR(255) NOT NULL,
    application_year INT NOT NULL,
    current_status TEXT DEFAULT 'Unknown',
    application_state VARCHAR(50) NOT NULL DEFAULT 'UNKNOWN',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_at TIMESTAMP,
    last_updated TIMESTAMP,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE
);

-- Per-application user-scheduled refresh times
CREATE TABLE IF NOT EXISTS Reminders (
    reminder_id SERIAL PRIMARY KEY,
    user_id INT REFERENCES Users(user_id),
    application_id INT REFERENCES Applications(application_id) ON DELETE CASCADE,
    reminder_time TIME NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Transactional outbox for system-initiated user-facing messages
CREATE TABLE IF NOT EXISTS Notifications (
    id              BIGSERIAL PRIMARY KEY,
    chat_id         BIGINT    NOT NULL REFERENCES Users(chat_id),
    kind            VARCHAR(64) NOT NULL,
    text            TEXT      NOT NULL,
    origin_ref      BIGINT    NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at    TIMESTAMP NULL,
    attempts        INT       NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_error      TEXT      NULL
);

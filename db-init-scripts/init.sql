CREATE TABLE IF NOT EXISTS Applications (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    application_number VARCHAR(255) NOT NULL,
    application_suffix VARCHAR(255),
    application_type VARCHAR(255) NOT NULL,
    application_year INT NOT NULL,
    current_status VARCHAR(255),
    last_updated TIMESTAMP,
    is_resolved BOOLEAN NOT NULL DEFAULT FALSE,
    language VARCHAR(255) NOT NULL DEFAULT 'EN'
);

CREATE TABLE IF NOT EXISTS Applications (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    application_number VARCHAR(255) NOT NULL,
    application_suffix VARCHAR(255),
    application_type VARCHAR(255) NOT NULL,
    application_year INT NOT NULL,
    current_status VARCHAR(255),
    status_changed BOOLEAN,
    last_notified TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signup_verifications (
    email VARCHAR(254) PRIMARY KEY,
    code_hmac CHAR(64) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    failed_attempts SMALLINT NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0 AND failed_attempts <= 5)
);

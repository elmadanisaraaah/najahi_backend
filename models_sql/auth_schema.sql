CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE,
    password_hash TEXT,
    phone_number VARCHAR(30) UNIQUE,
    role VARCHAR(30) NOT NULL DEFAULT 'student',
    is_email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    is_phone_verified BOOLEAN NOT NULL DEFAULT FALSE,
    auth_provider VARCHAR(30) NOT NULL DEFAULT 'email',
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    last_login_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS student_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    nom VARCHAR(120),
    prenom VARCHAR(120),
    niveau VARCHAR(120),
    filiere VARCHAR(120),
    profile_photo_url TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL,
    device_info TEXT,
    ip_address VARCHAR(80),
    user_agent TEXT,
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    code VARCHAR(10),
    token_hash TEXT,
    is_used BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    is_used BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oauth_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider VARCHAR(30) NOT NULL,
    provider_user_id VARCHAR(255) NOT NULL,
    provider_email VARCHAR(255),
    access_token TEXT,
    refresh_token TEXT,
    id_token TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_provider_account UNIQUE (provider, provider_user_id)
);

CREATE TABLE IF NOT EXISTS phone_otp_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number VARCHAR(30) NOT NULL,
    otp_code_hash TEXT NOT NULL,
    purpose VARCHAR(30) NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    is_used BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_phone_number ON users(phone_number);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_email_tokens_user_id ON email_verification_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_reset_tokens_user_id ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_accounts_user_id ON oauth_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_phone_otp_phone ON phone_otp_codes(phone_number);

-- Ensure all student_profiles columns exist (safe to run multiple times)
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS telephone        VARCHAR(30);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS date_naissance   DATE;
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS ville            VARCHAR(120);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS etablissement    VARCHAR(200);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS annee_scolaire   VARCHAR(20);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS moyenne_generale NUMERIC(5,2);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS avatar_url       TEXT;
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS type_bac         VARCHAR(120);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS note_bac         NUMERIC(4,2);
ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS filiere_actuelle VARCHAR(120);
-- Back-fill filiere_actuelle from the original filiere column for existing rows
UPDATE student_profiles SET filiere_actuelle = filiere
WHERE filiere_actuelle IS NULL AND filiere IS NOT NULL;
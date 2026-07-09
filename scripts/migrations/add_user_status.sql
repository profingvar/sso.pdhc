-- S9 (#411): activation gate. Professionals created from an approved
-- sign-on request start 'pending' (zero access) until an SU completes
-- the guided assignment and activates. Every pre-existing row defaults
-- to 'active' so nothing changes for current users.
--
-- Apply (inside the sso_db container):
--   psql -U <user> -d <db> -f add_user_status.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_status_enum') THEN
        CREATE TYPE user_status_enum AS ENUM ('active', 'pending', 'suspended');
    END IF;
END$$;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status user_status_enum NOT NULL DEFAULT 'active';

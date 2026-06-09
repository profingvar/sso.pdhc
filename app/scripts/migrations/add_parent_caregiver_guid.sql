-- #187: add Organisation.parent_caregiver_guid (self-referential FK).
--
-- Encodes the PDL vårdgivare / vårdenhet hierarchy that PDL Ch 4 §§2,4
-- and Lag (2022:913) §2 presume.
--
-- Semantics:
--   NULL     → this row IS a caregiver (vårdgivare, legal entity).
--   non-NULL → this row is a vårdenhet whose parent caregiver is the
--              referenced row. Cycles are illegal (enforced in code).
--
-- This ticket only adds the column. Backfill of existing rows lives
-- in #189; consumer rollout lives in #188 / #190.
--
-- Idempotent: each step checks current state first. Safe to re-run on
-- an environment that is already partially migrated.
--
-- Usage:
--   docker exec sso_db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--     -f /tmp/add_parent_caregiver_guid.sql
-- (copy in via `docker cp` first, or pipe via stdin).

BEGIN;

-- ---------------------------------------------------------------
-- Add column (nullable, no default — existing rows stay NULL).
-- ---------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'organisations'
          AND column_name = 'parent_caregiver_guid'
    ) THEN
        ALTER TABLE organisations
            ADD COLUMN parent_caregiver_guid varchar(36);
    END IF;
END$$;

-- ---------------------------------------------------------------
-- FK constraint to organisations(guid). Naming matches SQLAlchemy's
-- default so Base.metadata reflects cleanly on a fresh init.
-- ---------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'organisations'
          AND constraint_name =
              'organisations_parent_caregiver_guid_fkey'
    ) THEN
        ALTER TABLE organisations
            ADD CONSTRAINT organisations_parent_caregiver_guid_fkey
            FOREIGN KEY (parent_caregiver_guid)
            REFERENCES organisations(guid);
    END IF;
END$$;

-- ---------------------------------------------------------------
-- Index for fast roll-up queries (used by #188 blob assembly and
-- by future caregiver-scope reads).
-- ---------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'organisations'
          AND indexname = 'ix_organisations_parent_caregiver_guid'
    ) THEN
        CREATE INDEX ix_organisations_parent_caregiver_guid
            ON organisations (parent_caregiver_guid);
    END IF;
END$$;

COMMIT;

-- ---------------------------------------------------------------
-- Sanity checks (non-transactional; print for the operator).
-- ---------------------------------------------------------------
SELECT 'parent_caregiver_guid column present' AS check,
       EXISTS (
           SELECT 1 FROM information_schema.columns
           WHERE table_name = 'organisations'
             AND column_name = 'parent_caregiver_guid'
       ) AS ok;

SELECT 'parent_caregiver_guid FK present' AS check,
       EXISTS (
           SELECT 1 FROM information_schema.table_constraints
           WHERE table_name = 'organisations'
             AND constraint_name =
                 'organisations_parent_caregiver_guid_fkey'
       ) AS ok;

SELECT 'parent_caregiver_guid index present' AS check,
       EXISTS (
           SELECT 1 FROM pg_indexes
           WHERE tablename = 'organisations'
             AND indexname = 'ix_organisations_parent_caregiver_guid'
       ) AS ok;

SELECT 'existing rows unchanged' AS check,
       (SELECT count(*) FROM organisations
        WHERE parent_caregiver_guid IS NOT NULL) = 0
       AS ok_initial_state;

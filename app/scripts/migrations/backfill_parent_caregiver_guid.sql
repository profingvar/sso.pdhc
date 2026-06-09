-- #189: backfill parent_caregiver_guid for existing organisations.
--
-- Legal mapping per 2026-06-04: PDHC Clinic = PDL vårdenhet, every
-- vårdenhet must point at its vårdgivare. Anything that is NOT a
-- vårdenhet (a vårdgivare itself, an external partner, a tech vendor)
-- stays with parent_caregiver_guid IS NULL.
--
-- Inspection of prod orgs (2026-06-09):
--   Region Uppsala  — vårdgivare (legal entity)         → NULL
--   UAS             — vårdenhet under Region Uppsala    → Region Uppsala
--   KS              — vårdenhet under Region Stockholm  → SKIP (region not in DB)
--   Test Clinic     — dev fixture                       → SKIP (no real caregiver)
--   1177            — national service / Inera         → NULL (own caregiver)
--   Medituner       — tech vendor                      → NULL
--   Abbott          — sensor manufacturer              → NULL
--   cgm_provider    — tech vendor                      → NULL
--   MeditunerAB     — external partner (is_external=t) → NULL
--
-- Idempotent: each UPDATE re-checks current state. Re-runs are safe.
--
-- Usage:
--   docker cp .../backfill_parent_caregiver_guid.sql sso_db:/tmp/
--   docker exec sso_db sh -c \
--     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/backfill_parent_caregiver_guid.sql'

BEGIN;

-- ---------------------------------------------------------------
-- Confirmed mapping: UAS → Region Uppsala.
-- ---------------------------------------------------------------
UPDATE organisations
   SET parent_caregiver_guid = (
       SELECT guid FROM organisations WHERE name = 'Region Uppsala'
   )
 WHERE name = 'UAS'
   AND parent_caregiver_guid IS NULL;

COMMIT;

-- ---------------------------------------------------------------
-- Run report — shown to the operator.
-- ---------------------------------------------------------------
SELECT '--- post-backfill state ---' AS info;
SELECT
    name,
    is_external,
    parent_caregiver_guid IS NOT NULL AS has_parent,
    parent_caregiver_guid
FROM organisations
ORDER BY parent_caregiver_guid NULLS FIRST, name;

SELECT '--- follow-up list for operator ---' AS info;
SELECT
    name AS skipped_org,
    'no matching vårdgivare in DB; operator clarification needed' AS reason
FROM organisations
WHERE name IN ('KS', 'Test Clinic')
  AND parent_caregiver_guid IS NULL;

SELECT '--- consistency check: every non-NULL points at a real row ---' AS info;
SELECT
    o.name AS clinic,
    p.name AS parent_caregiver,
    p.guid AS parent_guid
FROM organisations o
LEFT JOIN organisations p ON o.parent_caregiver_guid = p.guid
WHERE o.parent_caregiver_guid IS NOT NULL;

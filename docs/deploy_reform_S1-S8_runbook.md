# Deploy runbook — SSO access-model reform S1–S8 (rollup #396)

**Scope:** ship the reform code (commits `13adeb4`→`3c30cac`) live on the
macmini, create the 3 new SSO tables + backfill affiliations, and apply the
ips D1 column ALTER. Backwards-compatible (dual-emit) — no consumer needs to
change first.

**Blast radius: HIGHEST on the platform.** Every service validates bearer
tokens against `sso.pdhc /api/auth/me/service`. While `sso_app` restarts,
downstream services briefly 401. Keep the rebuild+health window tight; do it
in a low-traffic slot. The blob change is *additive* (new `affiliations[]`,
`active_affiliation_guid`, `session_phases` alongside every legacy field), so
a stale consumer keeps working.

**Authorisation:** graceful rebuild/restart of sso's *own* compose project +
`docker exec` migrations are inside Claude's envelope (CLAUDE.md §15). All
`sudo` is operator-only. Get an explicit go before step 3.

---

## Facts this runbook is built on

| Thing | Value |
|---|---|
| Prod dir | `/usr/local/www/sso.pdhc/` (app under `app/`) |
| Model | **containerised** (Option C #153): `sso_app` (build `.`, `COPY . .`) + `sso_db` |
| Compose project | `sso` (`name: sso` in `app/docker-compose.yml`) |
| DB volume | `app_sso_pgdata` (external) |
| Ports | app `9000`, db `9003:5432` |
| DB creds | `sso_user` / `sso_db` |
| Migration path | `docker exec sso_app python scripts/backfill_affiliations.py` (create_all + seed_roles + backfill, all idempotent) |
| ips D1 | `ips-db-1` — `gateway/migrations/add_reform_patient_flags.sql` (idempotent ALTER) |

**Do NOT use** `app/safe_restart.sh` — its `PORTS=(9000..9003)` `kill -9`s the
host side of Colima's `sso_db` forward and the DB goes "unavailable"
([[infra_sso_safe_restart_bug]]). Rebuild via `docker compose` only.

### Remote-command preamble (every ssh block)

Non-interactive ssh has a minimal PATH and no docker context. Prefer running
docker *inside the VM* via `colima ssh --` (survives the host-forward dying):

```bash
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  colima ssh -- docker ps --format "{{.Names}}" | sort'
```

Host-side `docker compose` (needed for `--build`, which uses the build context
on disk) needs the context set instead:

```bash
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  docker context use colima >/dev/null; cd /usr/local/www/sso.pdhc/app && docker compose ps'
```

---

## 0. Pre-flight — baseline + backups (reversible, do first)

```bash
# 0a. Capture the CURRENT running image id (rollback target) + git sha.
colima ssh -- docker inspect --format '{{.Image}} {{.Config.Image}}' sso_app
colima ssh -- docker exec sso_app sh -c 'git rev-parse HEAD 2>/dev/null || echo no-git-in-image'

# 0b. Baseline health — expect 200 + database: connected.
curl -s https://sso.pdhc.se/api/health

# 0c. DB backup — BOTH sso and ips (D1 touches ips). Predeploy dir, not ~.
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  mkdir -p ~/backups/predeploy/sso.pdhc; TS=$(date -u +%Y-%m-%dT%H-%M-%SZ); \
  colima ssh -- docker exec sso_db pg_dump -U sso_user sso_db > ~/backups/predeploy/sso.pdhc/sso_db_$TS.sql; \
  colima ssh -- docker exec ips-db-1 pg_dump -U ips_user ips_db > ~/backups/predeploy/sso.pdhc/ips_db_$TS.sql; \
  ls -lh ~/backups/predeploy/sso.pdhc/*_$TS.sql'
```

Confirm both dumps are non-zero. (Move/prune the predeploy tars at session end
per [[feedback_predeploy_tarball_placement]].)

---

## 1. Sync reform code to the prod build context

The `COPY . .` image is built from `/usr/local/www/sso.pdhc/app` on the server,
so the new code must land there **before** the rebuild. Git was made
authoritative for sso in the `13adeb4` reconcile, but re-verify — prod dirs can
carry on-server-only edits ([[feedback_prod_vs_repo_divergence]]).

```bash
# 1a. Is the prod dir a git checkout?
ssh miserver@192.168.1.154 'test -d /usr/local/www/sso.pdhc/.git && echo checkout || echo not-a-checkout'
```

**If `checkout`:** diff-check, then pull.

```bash
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  cd /usr/local/www/sso.pdhc && git fetch origin && git status --porcelain && \
  git log --oneline -1 && echo "--- files that differ from origin/main ---" && \
  git diff --stat origin/main'
```

- Working tree clean + only expected reform files behind → `git pull --ff-only origin main`.
- **Any uncommitted server-side edit** (look for `*.bak.<date>` tells) → STOP,
  mirror the edit back to local first, re-tag; do not clobber it.

**If `not-a-checkout`:** ship just the reform files by tar (no venv, no `.env`):

```bash
# From local mac — the four code areas the reform touched:
cd ~/T7_sidewinder/sso.pdhc
tar czf /tmp/sso_reform.tgz app/src app/scripts app/tests
scp /tmp/sso_reform.tgz miserver@192.168.1.154:~/backups/predeploy/sso.pdhc/
ssh miserver@192.168.1.154 'cd /usr/local/www/sso.pdhc && tar xzf ~/backups/predeploy/sso.pdhc/sso_reform.tgz'
```

Either way, `.env` is untouched (operator-owned, gitignored).

---

## 2. Confirm no new `.env` keys are required

The reform adds **no** new env vars (no feature flag — dual-emit is always on).
Sanity-check the SSO client pairs are still present (unrelated but cheap):

```bash
ssh miserver@192.168.1.154 'grep -c "^SSO_CLIENT_ID_" /usr/local/www/sso.pdhc/app/.env'
```

---

## 3. Build the image + migrate BEFORE swapping traffic  ⟵ needs operator go

**Ordering is load-bearing.** The new blob builder queries `affiliations` with
no missing-table guard (`affiliation_service.active_affiliations`), and in prod
(Postgres) boot does *not* run `create_all` (that's gated to the SQLite test
path, `src/app.py:55`). So if the new serving container starts before the
tables exist, every professional `/api/auth/me` 500s — and since all services
validate against sso, that breaks auth platform-wide. Avoid the window by
creating + backfilling the tables from a **throwaway container of the new
image while the OLD container still serves old code** (old code never touches
`affiliations`), then swap.

```bash
# 3a. Build the new image only — do NOT `up` yet. (COPY . . needs a real build,
#     [[infra_docker_copy_dot_needs_build_flag]].)
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  docker context use colima >/dev/null; cd /usr/local/www/sso.pdhc/app && \
  docker compose build app'
```

If the build fails with `SyntaxError: null bytes` / corrupt layer (post
power-cut), use `docker compose build --no-cache app`
([[infra_power_cut_null_byte_image_corruption]]).

```bash
# 3b. DRY-RUN the migration from a throwaway container of the freshly built
#     image. `run --rm` reuses the running `db` service (depends_on healthy),
#     creates NOTHING serving-side. Prints created/skipped/flagged, commits
#     nothing.
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  docker context use colima >/dev/null; cd /usr/local/www/sso.pdhc/app && \
  docker compose run --rm app python scripts/backfill_affiliations.py --dry-run'
```

Read the output:
- `created` = one Affiliation per existing UserOrganisation.
- `flagged (defaulted to 'other_care')` = people with no `Professional` row or
  an unmapped legacy role. **Note these person/unit guids** — SU reassigns the
  correct role after backfill (step 7).

```bash
# 3c. REAL migration (still on old serving code). Creates the 3 tables, seeds
#     the 7 roles, backfills affiliations. Idempotent — safe to re-run.
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  docker context use colima >/dev/null; cd /usr/local/www/sso.pdhc/app && \
  docker compose run --rm app python scripts/backfill_affiliations.py'

# 3d. Verify table contents BEFORE swapping traffic.
colima ssh -- docker exec sso_db psql -U sso_user -d sso_db -c \
  "SELECT (SELECT count(*) FROM roles) roles, \
          (SELECT count(*) FROM affiliations) affiliations, \
          (SELECT count(*) FROM research_projects) research_projects;"
```

Expect `roles = 7`, `affiliations ≈ count(user_organisations)`,
`research_projects = 0` (SU adds these later via the registry API).

```bash
# 3e. NOW swap the serving container to the new (already-built) image. Tables
#     exist + are populated, so there is no 500 window.
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  docker context use colima >/dev/null; cd /usr/local/www/sso.pdhc/app && \
  docker compose up -d app'

# Bounded health wait (do not hang):
for i in $(seq 1 30); do
  s=$(curl -s -o /dev/null -w '%{http_code}' https://sso.pdhc.se/api/health); \
  echo "try $i -> $s"; [ "$s" = "200" ] && break; sleep 2; done
curl -s https://sso.pdhc.se/api/health   # expect status ok, database connected
```

---

## 4. ips D1 column ALTER (separate service, `ips-db-1`)

Idempotent (`ADD COLUMN IF NOT EXISTS`). ips's `db.create_all()` never alters
existing tables, so this is required for the 3 new `patient_index` columns.

```bash
# Ship the SQL and apply it.
scp ~/T7_sidewinder/ips.pdhc/gateway/migrations/add_reform_patient_flags.sql \
    miserver@192.168.1.154:~/backups/predeploy/sso.pdhc/
ssh miserver@192.168.1.154 'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"; \
  colima ssh -- docker exec -i ips-db-1 psql -U ips_user -d ips_db' \
  < ~/T7_sidewinder/ips.pdhc/gateway/migrations/add_reform_patient_flags.sql

# Verify the 3 columns exist.
colima ssh -- docker exec ips-db-1 psql -U ips_user -d ips_db -c \
  "\d patient_index" | grep -E "ehds_opt_out|quality_registry_opt_out|consented_research_projects"
```

The ips **app** image already carries the new model code only if ips was
redeployed with `--build` after the D1 commit. If ips prod predates the D1
commit, redeploy ips the same way (`cd /usr/local/www/ips.pdhc/... && docker
compose up -d --build`) — otherwise the columns exist in the DB but the ORM
won't read/write them. Check: `colima ssh -- docker exec ips-db-1 ...` shows
columns AND `curl -s https://ips.pdhc.se/api/v1/health` is 200.

---

## 5. Post-deploy verification (the DoD)

```bash
# 5a. Blob shape — reform fields present ALONGSIDE legacy (dual-emit).
#     Use a real professional token, or the internal service check.
curl -s https://sso.pdhc.se/api/health          # 200, database connected

# 5b. A downstream service still authenticates (proves /me/service unbroken).
curl -s https://request.pdhc.se/api/health       # 200
curl -s https://gateway.pdhc.se/api/v1/health    # 200 (HMAC path, independent)

# 5c. Care-hierarchy hygiene — should report 0 violations.
colima ssh -- docker exec sso_app python scripts/verify_care_hierarchy.py

# 5d. Registry authz spot-check — non-SU write must 403, read must 200.
#     (run with a non-SU professional bearer token $T)
curl -s -o /dev/null -w '%{http_code}\n' https://sso.pdhc.se/api/registry/roles -H "Authorization: Bearer $T"          # 200
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://sso.pdhc.se/api/registry/roles -H "Authorization: Bearer $T"  # 403
```

Also confirm on services.html that sso stays green and no sibling flipped red.
(Note: services.html greens are `no-cors` opaque and unreliable — trust the
curl 200s above, [[infra_disk_full_cascade_2026-05-22]].)

---

## 6. Rollback

The migrations are **additive and safe to leave** (new tables, new nullable/
defaulted columns) — rollback is code-only:

```bash
# Code rollback: check out the pre-reform sha in the prod dir, rebuild.
ssh miserver@192.168.1.154 'export PATH=...; cd /usr/local/www/sso.pdhc && \
  git checkout 13adeb4 && cd app && docker context use colima && \
  docker compose up -d --build app'
# (or, if image id was captured in 0a, retag/run that image)
```

The `roles`/`affiliations`/`research_projects` tables and the ips columns can
stay — the old code simply ignores them. Only drop them if you are abandoning
the reform entirely, and only after a fresh `sso_db` dump.

Full data restore (last resort): stop app, `psql < ~/backups/predeploy/sso.pdhc/sso_db_<TS>.sql`.

---

## 7. After a clean deploy — unblocks

- **SU task:** reassign the correct role to every affiliation flagged in 3b.
- **#408 X2** and **#409 M0** are now unblocked (consumers can start reading
  `affiliations[]` / `session_phases`). Kick those off next.
- Update `progress.md` + close a deploy note on rollup #396.
- Prune `~/backups/predeploy/sso.pdhc/*` once verified stable a day or two.
```

# SSO External Partners — Plan

A workflow for registering **external** organisations (third parties, integrators, regional health authorities, vendors) as authenticated callers of the PDHC platform, with their relationships referenced from FHIR Contracts in `contract.pdhc`.

This plan covers schema, admin UI, auth integration, contract pointers, and rollout. **No code is written until the operator approves.**

---

## 1) Scope and goals

### 1.1 What this plan delivers

- A **Partner** entity inside `sso.pdhc` (DB table + ORM model) that represents an external organisation registered to call PDHC services.
- A **SU-only admin page** (`/admin/partners`) for operators to register, edit, rotate credentials, and revoke access for partners.
- A stable **`partner_guid`** that `contract.pdhc` references inside FHIR Contract resources, so a Contract has a clean machine-readable pointer to "who this contract is with".
- A **fix for the broken Service Key Management** UI noted in the screenshot (currently emits "No keyed services configured. Set KEYAUTH_SERVICE_*_URL and KEYAUTH_SERVICE_*_KEY in .env." on every load because nothing wires those env vars).

### 1.2 Non-goals

- A CRM with notes / billing / SLA tracking. If that's needed, partners eventually splits into its own service. SSO stays the system-of-record for **identity + license**, nothing more.
- Per-partner rate limiting, quota tracking, observability dashboards. Defer.
- Self-service partner onboarding. Initial flow is SU-driven only.
- Changing the existing **internal** Service Registry table (the public.html / sso.pdhc dashboard table). That stays as-is — internal services are a different shape.

### 1.3 Why SSO and not a new service

- Identity (who is allowed to call us) belongs in the auth authority. Splitting it across services creates two systems of truth.
- The existing SSO request-loader at every PDHC service already validates `X-Source-Service` + `X-Service-Key` against `sso.pdhc`'s `_<SERVICE>` map. Partners reuse that path — no new auth surface.
- Separation between **internal services** (the 13 services in the registry) and **external partners** (third parties) is preserved by giving them different DB tables and different admin pages, while sharing the auth path.

---

## 2) Current state

### 2.1 What exists today

- **Internal Service Registry** — frontend-only table on `/su/admin` rendering health probes for the 13 PDHC services. No DB table; the list is hardcoded in the template.
- **Service Key Management UI** — placeholder section that reads from `KEYAUTH_SERVICE_*_URL` / `_KEY` env-pairs in sso.pdhc's `.env`. Currently empty in production; the UI displays its empty-state message permanently.
- **SSO `User` model** — covers human users (professionals, patients, SU admins). No service-account model.
- **`contract.pdhc` `ContractRecord` model** — single table holding FHIR Contract resources as JSON (`fhir_contract: JSONB`). No referential link from Contract to any external organisation.

### 2.2 What's missing

- **No DB row** anywhere on the platform that says "Partner X is allowed to call us, here is their credential, here are their allowed scopes".
- **No FHIR-clean way** for a Contract to reference "the external organisation this contract is with". Currently the JSON blob can carry whatever the operator types into `signer[].party`, but nothing validates or normalises it.
- **No rotation / revocation flow** for partner credentials.

---

## 3) Data model

### 3.1 New table: `external_partner`

Lives in `sso.pdhc`'s database. Shape:

| Column | Type | Notes |
|---|---|---|
| `partner_guid` | UUID v4 | primary key, generated server-side, never reused |
| `display_name` | varchar(120) | human-readable label, e.g. "Region Stockholm Diabetes Registry" |
| `org_number` | varchar(32) | Swedish organisationsnummer or equivalent legal identifier; nullable for non-Swedish partners |
| `country_code` | varchar(2) | ISO 3166-1 alpha-2 |
| `description` | text | free text — what this partner does, why they're integrated |
| `contact_name` | varchar(120) | primary point-of-contact human name |
| `contact_email` | varchar(120) | required |
| `contact_phone` | varchar(40) | optional |
| `auth_kind` | enum | `oauth_client` \| `api_key` \| `none` |
| `client_id` | varchar(64) | populated when `auth_kind=oauth_client` |
| `client_secret_hash` | varchar(128) | argon2 / bcrypt of the secret; secret itself shown once on creation, never stored cleartext |
| `api_key_hash` | varchar(128) | populated when `auth_kind=api_key`; same one-shot rule |
| `allowed_scopes` | jsonb | array of strings; e.g. `["fhir.observation.read", "fhir.patient.read:cdr2"]` |
| `allowed_services` | jsonb | array of service names (`["cdr.pdhc","gateway.pdhc"]`) the partner can call |
| `status` | enum | `active` \| `suspended` \| `revoked` |
| `created_at` | timestamptz | |
| `created_by_user_guid` | UUID | FK to `users.guid` (the SU who registered them) |
| `last_rotated_at` | timestamptz | for credential rotation tracking |
| `expires_at` | timestamptz | nullable; auto-revoke when reached |
| `revoked_at` | timestamptz | nullable; set on revocation |
| `revoke_reason` | text | nullable |

### 3.2 New table: `external_partner_audit`

Append-only audit log of every state change. Shape: `id`, `partner_guid`, `actor_user_guid`, `event` (enum: created / edited / rotated / suspended / reactivated / revoked), `at`, `before_json`, `after_json`. Driven by ORM hooks on `external_partner`.

### 3.3 Why these specific fields

- `partner_guid` is the stable handle for cross-service references. Once issued, it never changes — even when display_name or contact info changes. Contracts pin to the GUID.
- `allowed_services` + `allowed_scopes` give two layers: a coarse "this partner can talk to gateway.pdhc" gate, and a fine "and only for the observation.read scope" gate. The fine gate is checked at the target service; the coarse gate is checked at SSO.
- Hashing client_secret + api_key matches how passwords are stored — operators see the cleartext only at creation and rotation, then it's gone.
- `created_by_user_guid` + `external_partner_audit` give Rule 24 / GDPR auditability: every change is attributable to a named SU.

---

## 4) Auth integration

### 4.1 Extending the existing service-key path

The current SSO request loader at every PDHC service inspects:

```
X-Source-Service: <name>
X-Service-Key: <secret>
```

…and looks the source up in a hardcoded `KNOWN_FHIR_SERVICES` dict. Partners reuse this path. The shape:

```
X-Source-Service: partner:<partner_guid>
X-Service-Key: <api_key_or_client_secret>
```

The `partner:` prefix tells the request loader "look this up in the partner table, not the internal-service table". The lookup hits `sso.pdhc`'s `/api/internal/partner/<guid>/validate` endpoint (new), which:

1. Verifies the partner exists and `status=active`.
2. Verifies the supplied secret matches `client_secret_hash` or `api_key_hash`.
3. Verifies `expires_at` not passed.
4. Returns a synthetic access blob: `{ user_type: "partner", partner_guid, allowed_scopes, allowed_services, is_su_admin: false, organization_ids: [] }`.

The receiving service (cdr.pdhc, gateway.pdhc, etc.) checks:

- `partner_guid` ∈ `allowed_services` for *its own service name* — refuse if not.
- The endpoint being called is in `allowed_scopes` — refuse if not.

### 4.2 OAuth flow (optional, per partner)

For partners that need browser-redirect flow (e.g. a third-party portal where end-users land), `auth_kind=oauth_client` enables the existing OAuth 2.0 + PKCE flow. The partner is just an SSO client at that point; the flow is the same as for internal browser apps. `client_secret` is the confidential-client secret used in the token endpoint.

### 4.3 Replacing the broken `KEYAUTH_SERVICE_*` env-pair scheme

The current scheme requires the operator to add env-pairs (`KEYAUTH_SERVICE_FOO_URL`, `KEYAUTH_SERVICE_FOO_KEY`) and restart sso.pdhc per partner. That's why the UI is empty in production — nobody set the env vars.

The new scheme moves this into the DB. The Service Key Management UI is rewritten to read from `external_partner` (filtered by `auth_kind=api_key`). The env-pair scheme is **removed** in the same change.

---

## 5) Admin page UX

### 5.1 Route

`GET/POST /admin/partners` — SU-only, gated by `is_su_admin=True`. Adjacent to the existing `/su/admin` page; possibly a tab or a sub-page link from there.

### 5.2 Layout

Three blocks on the page, top to bottom:

**Block A — Register new partner (form)**

Fields: display_name, org_number, country_code, description, contact name / email / phone, auth_kind (radio), allowed_services (multi-select from internal service list), allowed_scopes (free text per scope, with autocomplete from a known scope catalogue), expires_at (optional date).

On submit: server generates `partner_guid`, generates the client_secret or api_key (a 43-char URL-safe random token), hashes and stores, **shows the cleartext secret once on the response page** with a copy-to-clipboard button and a clear "this will never be shown again" warning. Same UX as GitHub's personal access tokens.

**Block B — Existing partners (table)**

Columns: display_name, partner_guid (truncated, click to copy), country, contact, auth_kind, status, expires, last_rotated. Per-row actions: View / Edit / Rotate credential / Suspend / Revoke.

**Block C — Audit log (collapsed by default)**

Shows last 50 audit entries with a "load more" button. Each row: timestamp, actor SU, event, partner display_name, before→after diff.

### 5.3 Edit / Rotate / Suspend / Revoke

- **Edit** — change display_name, contact info, description, allowed_services, allowed_scopes, expires_at. Cannot change partner_guid, country_code, org_number after creation (these would invalidate signed contracts; require revoke + re-register).
- **Rotate credential** — generates a new secret, hashes it, sets `last_rotated_at`. Old secret stops working immediately. New secret shown once.
- **Suspend** — sets `status=suspended`. Auth checks fail until reactivated. Reversible.
- **Revoke** — sets `status=revoked`, `revoked_at`, `revoke_reason`. Irreversible at the SSO layer (you'd register a new partner with a new GUID if you wanted to bring them back); contracts referencing the revoked partner stay readable but flagged.

### 5.4 Frontend stack

Plain server-rendered HTML, matching the existing `su_admin.html` style. No new SPA framework. The page extends `base.html` and follows `repo_css.md` design tokens.

---

## 6) How `contract.pdhc` references partners

### 6.1 FHIR Contract.signer.party pointer

FHIR R5 Contract has a `signer[]` array with `signer[].party.reference` (a Reference). Today operators put whatever string they want there; no validation.

The new convention:

```json
{
  "resourceType": "Contract",
  "signer": [
    {
      "type": { "code": "AGNT" },
      "party": {
        "reference": "https://sso.pdhc.se/Partner/<partner_guid>",
        "display": "Region Stockholm Diabetes Registry"
      }
    }
  ]
}
```

The `reference` URI — `https://sso.pdhc.se/Partner/<partner_guid>` — is the canonical handle. `contract.pdhc` validates on insert/update that any reference matching that pattern resolves to a real partner via SSO's lookup endpoint.

### 6.2 New SSO endpoint: `GET /api/public/partner/<guid>`

Read-only public endpoint returning a sparse partner record:

```json
{
  "partner_guid": "<uuid>",
  "display_name": "...",
  "country_code": "SE",
  "status": "active|suspended|revoked",
  "description": "..."
}
```

Public because `contract.pdhc` (and any UI that renders a contract) needs to look up "what does this partner_guid mean" without service-key auth. Sensitive fields (contact info, scopes, secrets) are NOT exposed here — those require SU auth at the `/admin/partners/<guid>` route.

### 6.3 Contract list / detail UI in contract.pdhc

When `contract.pdhc` renders a Contract record, for each `signer[].party.reference` matching the `https://sso.pdhc.se/Partner/<guid>` shape, it fetches the public partner record and shows the display_name + country + status badge inline. Lookup is cached in-process for ~5 min so listing doesn't hammer SSO.

### 6.4 Contract creation flow (later, optional)

A future enhancement: contract.pdhc's contract-creation form has a "Partner" autocomplete that hits `GET /api/public/partner?search=` to let the operator pick a registered partner directly. The form then populates `signer[].party.reference` automatically. **Out of scope for this plan** — the canonical reference URI is what matters; the form UX is iterable.

---

## 7) Service Key Management fix

### 7.1 What's broken

The existing UI (lower section of the SSO admin page) reads from `app.config.SERVICES_REGISTRY`, which is built from `KEYAUTH_SERVICE_*_URL` / `_KEY` env-pair scanning at boot. Production deploys never set those env vars, so the registry is always empty and the UI permanently shows "No keyed services configured."

### 7.2 What replaces it

The "Service Key Management" UI is **deleted** from the SSO admin page. Its function is subsumed by the new `/admin/partners` page (filtered to `auth_kind=api_key` partners, if anyone wants the same view). The `KEYAUTH_SERVICE_*` config-scanning code path in `src/config.py` is removed.

### 7.3 Migration concern

If any internal service was relying on a `KEYAUTH_SERVICE_*` row in production (none currently — the registry is empty), it would need to be reissued as a partner record. We'll grep the prod env on miserver for any `KEYAUTH_SERVICE_*=...` pairs before deletion to be safe.

---

## 8) Rollout plan

### 8.1 Order of operations

1. **Migration** — alembic migration adds `external_partner` + `external_partner_audit` tables to sso.pdhc DB. Idempotent. Empty rollout — no data yet.
2. **Backend** — add ORM models, `/api/internal/partner/<guid>/validate` (service-to-service), `/api/public/partner/<guid>` (public read), `/api/admin/partners/*` (CRUD + rotate/suspend/revoke; SU-only). Unit tests. No UI yet.
3. **Frontend** — add `/admin/partners` server-rendered page. Manual smoke from local SU login.
4. **Integrate** — extend the existing service-key request-loader on each PDHC service to recognise `partner:<guid>` source format and call SSO's validate endpoint. Per-service deploy.
5. **Contract.pdhc** — add reference-validation to ContractRecord insert/update. Add inline partner display in list/detail. Per-service deploy.
6. **Cleanup** — remove the `KEYAUTH_SERVICE_*` env-scanning code path and its UI block. Remove the empty-state message that's confusing operators.
7. **Documentation** — `docs/external_partners.md` describing onboarding flow for SUs. `docs/api/partners.md` describing the public lookup endpoint for partner UIs.

### 8.2 Each step gates on the previous

No step ships to production until the preceding one's tests are green and the operator has run smoke checks. The plan deliberately puts schema + backend + UI in three separate deploys so a regression in any layer is rolled back surgically.

### 8.3 Operator-only steps (per Rule 19)

- Running the alembic migration on the live sso.pdhc DB.
- Restarting sso.pdhc gunicorn after each backend deploy.
- The first-ever partner registration (before any external party gets a credential).

I (Claude) prepare the migration, code, and instructions; the operator runs them on the server.

---

## 9) Open questions

These need an explicit operator decision before step 1:

1. **Credential lifetime defaults** — should partner credentials default to non-expiring, 90-day, or 1-year? My suggestion: 1-year, with auto-rotation reminders 30 days before expiry.
2. **Scope catalogue** — should `allowed_scopes` be free-text or constrained to a known catalogue? My suggestion: constrained — define a `SCOPE_CATALOGUE` constant in sso.pdhc that the form's autocomplete reads from. Adding a new scope is a code change reviewed in PR.
3. **Multi-tenancy** — should one partner be able to act on behalf of multiple `org_guid`s (e.g. a vendor managing 5 healthcare regions)? My suggestion: yes — `allowed_org_guids: jsonb` (nullable) gives that without changing the table shape.
4. **Contract reference shape** — keep the `https://sso.pdhc.se/Partner/<guid>` URL convention, or use a FHIR `urn:uuid:<guid>` style? URL is more discoverable; `urn:uuid` is shorter. My suggestion: URL, it's more standard for FHIR external references.
5. **Public endpoint rate limiting** — the `GET /api/public/partner/<guid>` is reachable without auth. We probably want a per-IP rate limit. Use existing nginx `limit_req` zone, or in-app middleware? My suggestion: nginx, consistent with other public endpoints on sso.pdhc.

---

## 10) Out of scope (for clarity)

- Self-service partner onboarding (partners registering themselves).
- Per-partner usage analytics / billing.
- Cross-partner trust delegation (partner A vouching for partner B).
- A separate `partners.pdhc` service. Reconsider only when partner business metadata grows beyond what fits in SSO.
- Replacing the **internal** Service Registry on the SSO landing — that's a different feature for a different audience.

---

## 11) References

- `sso.pdhc/app/src/templates/su_admin.html` — current admin page; new partners section sits next to existing service registry.
- `sso.pdhc/app/src/config.py` — `KEYAUTH_SERVICE_*` env-pair scanning; remove in step 6.
- `contract.pdhc/app/backend/app/models.py:27` — `ContractRecord` model; add validation in step 5.
- FHIR R5 Contract: <https://hl7.org/fhir/R5/contract.html#Contract.signer>
- Rule 8 (CLAUDE.md) — API key storage / rotation / expiry / revocation procedures must be in deployment plans. Section 5 + 7 satisfy this.
- Rule 18 — internal references via GUID. `partner_guid` is the stable cross-service handle.
- Rule 24 — full operation log. `external_partner_audit` satisfies this.

# External Partners — onboarding guide

For SU admins. How to register a third-party organisation as an authenticated caller of the PDHC platform, what each field on the form means, and how the partner's credential is delivered.

Companion to `plans/external_partners_plan.md` (the design doc) and `api-reference.md` (the wire-level endpoint docs).

---

## 1) When to use this

Use the External Partners flow when an outside organisation needs to **call PDHC services** — push observations, query a cohort, fetch terminology, etc. Examples:

- A regional health authority pushing observations into `cdr.pdhc`
- A CGM vendor's portal pulling its own patients' data via `dashboard.pdhc`
- An integrator translating an external EHR into FHIR for `gateway.pdhc`

Do **not** use it for:

- Internal PDHC services (those live in the Service Registry on the SSO landing).
- Individual humans (use the user-management flow under `/su/admin`).
- One-off researcher access (use the analysis-phase grant on a professional user).

---

## 2) The flow

### 2.1 Before the form

Have ready:

- The partner's **legal name** and **organisation number** (Swedish orgnr or equivalent).
- A **named technical contact** with email and phone.
- A short **description** of what the integration does — appears next to every Contract that references this partner.
- The list of **services** the partner needs to call (e.g. `cdr.pdhc`, `gateway.pdhc`).
- The **scopes** within each service (e.g. `fhir.observation.read`, `fhir.patient.write`).
- An **expiry date** if the integration is time-bounded.

### 2.2 Filling the form

1. Sign in to the SSO admin page as an SU (`/su/admin`).
2. Scroll to the **External Partners** card.
3. Click **+ Register partner**.
4. Fill the fields. Defaults are usually right:
   - **Auth kind** — leave at `api_key` unless the partner integrates a browser-redirect OAuth flow (then use `oauth_client`).
   - **Country** — ISO-2 (`SE`, `DK`, `NO`, etc.).
   - **Allowed services / scopes** — start with the narrowest set that still lets the partner do their job. Always easy to grant more later via Edit; revoking is more disruptive.
   - **Expires** — optional. If set, the credential auto-rejects after that date.
5. Click **Register**.

### 2.3 After register

The form returns a yellow banner with the **secret** in cleartext.

> **Copy this immediately and deliver it to the partner via a secure channel.** The secret is shown exactly once, never stored in cleartext on our side, and never displayed again. If you miss it or lose it later, click **Rotate** on the partner's row — that mints a new one (and invalidates the old one).

Secure-channel options, in order of preference:

1. The partner's already-encrypted operator portal (e.g. their own SSO).
2. End-to-end-encrypted messenger (Signal, Wire) used only between the two named technical contacts.
3. Encrypted ZIP with password delivered out-of-band.

Do **not** email it in the clear, paste it into a ticket system without rotation, or store it in a shared password manager that other operators can read.

---

## 3) The partner's GUID

When a partner is registered, SSO mints a **`partner_guid`** — a stable UUID v4. This GUID:

- Never changes for the lifetime of the partner record.
- Is the canonical handle used in **FHIR Contract** resources (see §4).
- Is **not** secret — it appears in the public lookup endpoint and in any UI that renders a Contract. It's a stable identifier, like a URL.

The cleartext secret is what's secret. The GUID is just the partner's name.

---

## 4) Referencing a partner in a Contract

`contract.pdhc` stores FHIR R5 Contract resources. To pin a Contract to a registered partner, use this convention in `signer[].party.reference`:

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

The reference URI is **machine-readable** — `contract.pdhc`'s UI fetches the public partner record and shows the partner's display name + country + status badge inline. If a partner is suspended or revoked, the Contract still renders but flags it.

Multiple signers on the same Contract are fine; the SU can add the PDHC platform's own party alongside the partner.

---

## 5) Lifecycle actions

| Action | Use when | Reversible? |
|---|---|---|
| Edit | display name, contact, description, expires_at, allowed services / scopes need updating | yes — the change is logged and effective immediately |
| Rotate | partner reports a leaked secret, scheduled credential refresh, contractor change at the partner's side | yes — old secret invalid immediately, new secret shown once |
| Suspend | temporary pause (e.g. during incident triage, contract renegotiation, suspected abuse) | yes — Reactivate restores access |
| Revoke | permanent termination — partnership ended, breach, legal obligation to disconnect | **no** — to bring them back, register a fresh partner with a new `partner_guid` |

Country code, organisation number, and auth kind **cannot be edited** after registration — those changes would silently invalidate any contract pinned to the partner. To change them, revoke the existing record and register a new one (or argue with us if you have a strong reason).

---

## 6) The audit log

Every state change is recorded in `external_partner_audit`:

- Who did it (SU user GUID).
- What kind of change (`created`, `edited`, `rotated`, `suspended`, `reactivated`, `revoked`).
- The full record before and after.

Visible in the admin page (collapsed by default; expand the audit panel under the partner's row). Persists for the life of the partner record. Used for the platform's GDPR / Rule-24 audit obligations.

---

## 7) Programmatic API surface

For SU automation (e.g. CSV bulk-onboarding via a script):

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /api/admin/partners` | SU bearer token | list all |
| `POST /api/admin/partners` | SU bearer token | register; returns `secret_cleartext` once |
| `GET /api/admin/partners/<guid>` | SU bearer token | full record |
| `PATCH /api/admin/partners/<guid>` | SU bearer token | edit mutable fields |
| `POST /api/admin/partners/<guid>/rotate` | SU bearer token | new credential; old invalidated |
| `POST /api/admin/partners/<guid>/suspend` | SU bearer token | |
| `POST /api/admin/partners/<guid>/reactivate` | SU bearer token | |
| `POST /api/admin/partners/<guid>/revoke` | SU bearer token | body: `{"reason": "..."}` |
| `GET /api/admin/partners/<guid>/audit` | SU bearer token | last 200 audit entries |
| `GET /api/admin/partners/_meta/catalogue` | SU bearer token | scope + service catalogues |
| `GET /api/public/partner/<guid>` | none | sparse public record (display name, country, status) |
| `POST /api/internal/partner/<guid>/validate` | service-key | service-to-service credential check; returns access blob |

Schemas: see `api-reference.md`.

---

## 8) When something goes wrong

**Partner says "I'm getting 401 / 403".** Check the partner's row:

- Status is **suspended** or **revoked** → reactivate or re-register.
- **Expires** has passed → edit to extend, or accept the integration is over.
- The partner is sending the wrong **`X-Source-Service`** header — it must be exactly `partner:<their_guid>`, not the display name or org number.
- The partner's secret was rotated; they're using the old one. Rotate again, deliver the new secret freshly.

**The form rejects an unknown scope.** The scope catalogue is closed (`SCOPE_CATALOGUE` in `src/routes/partners.py`). Adding a new scope is a code change, not a config change. File a ticket; reviewer should add it to both SCOPE_CATALOGUE and the receiving service's enforcement.

**A partner's GUID was already burned (revoked) but they want to come back.** Register them as a fresh partner. The new GUID is different by design — any Contract referencing the old GUID stays readable but is visually flagged as pointing to a revoked partner.

**The audit log shows an SU action you don't recognise.** That's a security issue. Bump the SU's `token_revocation_epoch` to log them out everywhere immediately, then investigate.

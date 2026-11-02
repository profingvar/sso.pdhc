# EPS C3a — an identity for a private individual acting on a patient's behalf

Ticket **#890** (`sso.pdhc`). Companion to **EPS C3** (#889, `ips.pdhc`), whose
policy branch this blocks. Assessment context: `ips.pdhc/plans/eps_state_reform.md` §3.5.
Privilege model ratified by the operator in **EPS A3** (#879).

This document answers the four questions #890 poses. Every claim below cites the
file and line it was measured from on 2026-11-02; nothing here is inferred from
configuration or from a docstring.

---

## 0. The measurement that decides the shape

`user_type` is a Postgres ENUM with exactly **two** members:

```python
# app/src/models/user.py:18
user_type = Column(Enum('patient', 'professional', name='user_type_enum'), nullable=False)
```

But the access **blob** already carries a third value the column cannot hold:

```python
# app/src/routes/partners.py:281-282
blob = {
    'user_type': 'partner',
```

That partner blob is **synthetic** — it is assembled from an `Organisation` row
after a client-secret check (`partners.py:277`), with no `users` row anywhere.
So the blob's vocabulary is deliberately wider than the column's, and a third
identity type *could* be built the same way, with no enum migration at all.

**It must not be.** A partner is a machine: it authenticates with
`client_secret_hash` / `api_key_hash` and has no password, no session, no reset
flow. A relative is a human being who logs in. Everything a human login needs
lives on the `users` row and nowhere else:

| column | `user.py` | what it does |
|---|---|---|
| `password_hash` | :17 | the login itself |
| `status` | :26 | `active` / `pending` / `suspended` |
| `force_change_on_next_login` | :35 | SU-triggered reset (#43) |
| `password_changed_at` | :36 | ditto |
| `token_revocation_epoch` | :41 | bulk session flush (#44) |

A synthetic-blob relative would have to reimplement all five, plus the
`revoked_tokens` list, outside the one place the platform already gets them
right. So:

> **Decision: `'relative'` becomes a third member of `user_type_enum`,**
> with a `Relative` profile table mirroring `patient.py` / `professional.py`.

This is a schema change. Per sso.pdhc Rule 12/19 the migration is written here
and **applied by the operator** — no ssh or scp from this plan.

---

## 1. What `user_type` a private individual gets, and how it differs

`'relative'`. The three types differ along one axis that matters more than any
other — **where the authority comes from**:

| | `patient` | `professional` | `relative` |
|---|---|---|---|
| whose data | their own | someone else's | someone else's |
| authorised by | *being* the subject | an affiliation to an organisation | **a grant from the patient** |
| organisation | one (`organisation_guid`, their clinic) | many (`organization_ids`) | **none** |
| default access | their own record | their org's records, via phases | **nothing at all** |
| who can widen it | — | an SU, via affiliation + activation | **only the patient** |

The last row is the operator's A3 ruling and is the whole design:

> **Both parties may REDUCE access; only the patient may INCREASE it.**

A professional's authority is institutional and an SU administers it. A
relative's authority is personal and the patient administers it. That is why
this cannot be modelled as a professional with an odd affiliation.

### What the blob looks like

The blob is built per `user_type` branch in `build_access_blob`
(`app/src/services/auth_service.py:64`). The patient branch (:93-100) emits
`patient_guid`, `organisation_guid`, `in_registry`, `registries`.

**CORRECTED 2026-11-02 while implementing #889.** This section first said the
blob should carry `patient_guid` = P's guid, on the assumption — made in §5
below — that sso held the grant. #889 requires the grant to sit *beside*
`patient_consents`, which is an ips table, and that requirement wins. sso
therefore does not know which patients a relative may act for, and **the blob
carries identity only**:

```python
{'user_type': 'relative', 'user_guid': <the relative's users.guid>}
```

ips resolves the subject patient from its own `patient_proxy_grants` table and
sets `g.patient_guid` to it, so A3's "read as the patient" is still nearly
free — every consumer's existing patient path runs unchanged and
`care_access_policy` needed **no new zone**. The reduction against #889's
8–15 day estimate holds; only the location of the grant changed.

Three reasons the correction is also the better architecture:

- patient self-determination over blocks and consents already lives in ips, and
  granting a relative access is the same kind of act;
- sso is the bottom of the dependency stack. Had the grant lived there, either
  sso would have to ask ips — a downstream dependency — or the patient's grants
  would be administered in a service that owns none of their other
  self-determination rows;
- the revocation property below is unaffected, only relocated: ips resolves the
  grant from its own database on every request.

**But the blob must also say who is actually reading, and this is not
optional.** The blob's own docstring (`auth_service.py:76-81`) cites
Lag (2022:913) chain-of-custody as the reason `session_id` exists. An audit row
keyed only on `patient_guid` cannot tell a relative's read from the patient's
own. Two fields already separate actor from subject and must both be honoured:

- `user_guid` — the **relative's** own `users.guid` (the actor)
- `patient_guid` — **P's** guid (the subject)

**No new blob field is needed for this after all** (corrected with the above).
sso cannot name the subject, so the actor/subject distinction is made where the
subject is known: ips sets `g.proxy_user_guid` (actor) alongside
`g.patient_guid` (subject), and its `AuditLog` already carries `actor_guid`,
`actor_type` and `patient_guid` as separate columns — so the chain-of-custody
requirement needed no schema change at all. Measured in
`ips.pdhc/gateway/app/models/audit_log.py:25-28`.

---

## 2. How they are identified and verified

#890 states the constraint precisely: **the relationship is asserted by the
PATIENT, not by the relative.** So the account and the relationship are two
separate problems, and only the second is actually load-bearing.

### The account: exactly as weak as a patient's, and that is the honest baseline

`POST /api/patient/register` is **unauthenticated** — `@rate_limit(10/60s)` is
its only decorator (`app/src/routes/patient.py:15-16`). It takes an email, a
password and a 12-digit string, checks `^\d{12}$` with no checksum anywhere in
the codebase, and creates the account. Its docstring promises *"Dev-mode only
flag checked via config"*; `config` is assigned at :27 and **never read again**
in the function. Unchanged since the initial commit, 2026-03-18.

So the platform does not verify that a patient is who they say they are, and
there is no existing identity-proofing path for a relative to reuse. Designing
a stronger verification for relatives than for patients would invent a standard
the platform does not meet. *(Filed separately — see §6.)*

### The relationship: this is where the trust actually sits

Because the account is weak, the grant must not be addressable by a typed email
address — the patient would be granting access to a mailbox nobody verified.
Use the pattern the platform already has for exactly this problem.
`onboard.pdhc`'s provider window (platform CLAUDE.md §11) admits an external
party with no pre-existing account via three independent gates: a 256-bit
invite token hashed at rest with a flat 404 on a miss, a room code spoken on
the **telephone** and never sent in the same channel as the link, and a
liveness gate.

The relative case needs the first two:

1. The patient creates a grant. It mints a 256-bit invite token, stored hashed,
   and a short code the patient reads to the relative **out of band** — in
   person or by telephone.
2. The relative redeems the token, creating (or attaching) their `relative`
   account.
3. The grant binds to the resulting `users.guid` at redemption, and the token
   is spent.

The authority is then "**the patient chose this person and told them a code**",
which is a true statement, rather than "the platform verified this identity",
which would not be.

---

## 3. Whether they belong to an organisation at all

**No.** A relative belongs to no organisation, and the blob must reflect that
by **omitting** `organization_ids` entirely rather than sending `[]`.

This is not a stylistic choice — it matches the measured patient branch, which
never sets `organization_ids` at all (`auth_service.py:93-100`; only the
professional branch sets it, at :131). Consumers therefore already tolerate the
key being absent for a non-professional blob. Sending `[]` would be new
behaviour; omitting it is existing behaviour.

#890 says the usual Rule 24 scoping then says "see nothing", which is correct
by default and must be overridden only by an explicit grant. Confirmed — and
the override is **not** an organisation:

> The grant does not widen the relative's org scope. It resolves the relative
> to patient P, and P's own access path runs unchanged. Nothing is scoped by
> organisation, because a patient's access to their own record never was.

That distinction is what keeps this out of Rule 24's blast radius: no new
org-scoping rule is introduced anywhere.

### The dangerous corollary

`is_su_admin` stays `False`, and nothing must ever grant a relative an
affiliation. A relative with an affiliation would be a professional wearing the
wrong `user_type` — institutional authority obtained through a personal grant.
The `relative` branch of `build_access_blob` must not call
`build_affiliations_for_blob` at all (contrast :209), so the field cannot
appear even by accident.

---

## 4. How revocation propagates

#890 requires that the platform's no-caching rule hold for this type too.
It does, and three of the four mechanisms already exist unchanged:

| mechanism | where | revokes |
|---|---|---|
| `revoked_tokens` | `models/revoked_token.py` | one token, by guid |
| `user.token_revocation_epoch` | `user.py:40` | every session of that user (#44) |
| `user.status = 'suspended'` | `user.py:26` | the account |
| **ending the grant** | new | the *relationship*, leaving the account intact |

The fourth is the one EPS needs, and A3 ratified who may use it: **both parties
may end it.** The patient may revoke; the relative may terminate their own
access. Only the patient may create or widen one.

The property #890 asks for holds **only under one design constraint**, which is
therefore the load-bearing sentence of this section:

> **The grant is resolved from the database inside `build_access_blob`, on every
> request. It is never baked into the JWT.**

Because the blob is rebuilt per request (`auth_service.py:64`) and consumers are
forbidden to cache it, a grant ended at 10:00:00 is gone from the next request
at 10:00:01, with no token revocation, no logout and no service-side
invalidation. Put `patient_guid` in the token instead and revocation would wait
for expiry — the exact failure the no-caching rule exists to prevent.

Ending a grant must also be **expressible as history**, not as a terminal
state. This is #891's lesson applied: use a partial unique index over *open*
rows, so the same patient and relative can grant, end, and grant again.

```sql
CREATE UNIQUE INDEX uq_patient_relative_active
    ON patient_relative_grants (patient_guid, relative_user_guid)
    WHERE ended_at IS NULL;
```

A plain `UNIQUE(patient_guid, relative_user_guid)` would make "ended" terminal
for that pair for ever. And as with #891: `ended_at` is not `expires_at` — a
non-NULL `ended_at` always means over, so a future value is refused.

---

## 5. Schema sketch (operator applies — Rule 12/19)

```sql
-- 1. the enum gains a member. Postgres cannot drop one, so this is one-way.
ALTER TYPE user_type_enum ADD VALUE IF NOT EXISTS 'relative';

-- 2. the profile row, mirroring patients/professionals
CREATE TABLE relatives (
    id              SERIAL PRIMARY KEY,
    guid            VARCHAR(36) UNIQUE NOT NULL,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name    VARCHAR(255),
    fhir_resource_type VARCHAR(50) NOT NULL DEFAULT 'RelatedPerson',
    created_at      TIMESTAMP NOT NULL DEFAULT now()
);

-- 3. THE GRANT DOES NOT LIVE HERE. Corrected 2026-11-02: #889 requires it
--    beside patient_consents, an ips table. It is
--    ips.pdhc/gateway/migrations/add_patient_proxy_grants.sql, shipped with
--    #889, and sso holds identity only.
--
--    The invite-token flow of §2 still belongs on the sso side, because it is
--    how the relative's ACCOUNT comes into being; the grant that account is
--    then bound to is ips's row.
```

**What #889 actually built, for cross-reference:** `patient_proxy_grants` with
`valid_from`/`valid_to` (both ends, EPS C1), `revoked_at`/`revoked_by_role`
(either party, A3), and **no** database UNIQUE over
(patient, proxy) — uniqueness of the ACTIVE grant is enforced at the API layer,
because a UNIQUE would make revocation terminal for that pair for ever (#891).

`fhir_resource_type` is `RelatedPerson`, which is FHIR R5's own resource for
exactly this role — consistent with `Patient.FHIR_RESOURCE_TYPE` (`patient.py:23`)
and with EPS A1's R5 ruling.

`scope` carries A3's second clause (the relative may receive and give separate
information through a coming app) without building that app now: the enum has
room for it and `read_as_patient` is the only value EPS C3 needs.

### Why `ADD VALUE` deserves a sentence of its own

Postgres cannot remove an enum value. `ALTER TYPE ... ADD VALUE` also cannot run
inside a transaction block in older versions, so the migration must not be
wrapped in one. Alembic wraps by default — the migration needs
`op.execute("COMMIT")` first or an autocommit block, or it fails on the server
and not locally if the two differ in version.

---

## 6. Incidental finding — filed as #904

`POST /api/patient/register` being unauthenticated with a never-read `config`
gate (§2) is not part of #890 and is not fixed here. It is **#904**
rather than a caveat in this document, because a caveat is a finding nobody has
looked at yet. The ticket was filed before this line cited it.

**Stated uncertainty:** this was measured in the **repo**, not against the
running service, and no probe of `sso.pdhc.se` was made. Whether the deployed
code matches this checkout is unverified — which is the distinction #844/#845
turned on. The ticket says so in those words.

---

## 7. What this unblocks

- **#889 (EPS C3)** — its policy branch now has an answer, and the estimate
  should come down: read-as-patient needs no new branch in
  `care_access_policy`, only the blob fields in §1 and the audit distinction.
- **#879 (EPS A3)** — answered by the operator; this document records how.
- **#876** — the epic, closes last.

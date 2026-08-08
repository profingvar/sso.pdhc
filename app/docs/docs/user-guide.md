# User Guide

This guide covers day-to-day usage for professionals and patients. It reflects
the current access model, in which what you can do is decided by your
**affiliations** (which clinic you work at and in which role), the **phases**
an SU admin has granted you, and whether your account has been **activated**.

## Professional Workflow

### Logging In

1. Go to the SSO login page (`/login`)
2. Enter your email and password
3. Click **Login**
4. You will be redirected to your dashboard

If you arrived from another service (SSO handshake), you will be automatically
redirected back after login.

### How your access is decided

Three independent things combine to decide what you can do in each PDHC
service:

- **Affiliations** — each affiliation binds you to **one care unit (clinic)**
  in **one role** (for example *Doctor at Oncology Clinic*). You can hold more
  than one — e.g. Doctor at one clinic and Researcher at another. Your role
  determines which phases you are *allowed* to work in.
- **Phases** — the service areas you have been granted: *planning* (Plan),
  *request*, *provider* (Receive), and *analysis*. Phases are granted
  explicitly by an SU admin; they are not implied by anything else.
- **Activation** — your account must be **activated** by an SU admin before
  any of the above takes effect.

At the moment you use a service, the system works out your **session phases** —
the phases you have been granted *and* that your current role is allowed to use.
For example, an *Other care professional* role does not permit the *analysis*
phase, so even if you were granted *analysis*, it would not apply while you act
in that role. (The *planning*/Plan phase is special: it involves no patient
data and applies whenever it is granted, regardless of role.)

!!! warning "Being granted a phase is not enough — your account must be activated"
    A brand-new professional account starts **unactivated**. Until an SU admin
    activates it, every phase-gated service treats you as having **no access**,
    even if phases have already been granted. An SU can only activate your
    account once your profile is complete: at least one affiliation, at least
    one granted phase your role can actually use, and — for any *Researcher*
    affiliation — at least one research project attached. If something is
    missing, the SU is shown exactly what to fix.

### Dashboard

After login, the dashboard shows:

- **Your affiliations** — the clinic + role bindings you currently hold.
- **Your phases** — which service phases you have access to.
- **Your groups** — approved group memberships (with category and admin
  status). Groups are organisational/category metadata — they list who you work
  with, not what you can do.

### Requesting Group Membership

To join an existing group:

1. From the dashboard or `/request-join`, select the group you want to join
2. Submit the request
3. A group admin or SU admin will review your request
4. You will appear in the group once approved

!!! note "Joining a group does not grant phase access"
    Group membership shows who you are associated with (e.g. "Oncology
    Planning"). Its category is a free-form label only; it does not by itself
    give you access to any service phase. If you need access to a new phase,
    ask an SU admin to grant it.

### Joining by Invite

If you received an invite token from a group admin:

1. Go to `/join`
2. Enter the invite token
3. Submit — this creates a pending membership
4. A group admin must approve your membership

### Requesting Group Admin Role

To request admin privileges for a group you belong to:

1. Navigate to `/request-join` or use the dashboard
2. Select the group and request the admin role
3. An SU admin will review and decide

### Suggesting a New Group

1. Go to `/suggest-group`
2. Enter the proposed group name
3. Select the group category: `planning`, `request`, `provider`, or `analysis`
4. Submit — an SU admin will review your proposal

### Changing Your Password

1. Go to `/change-password` (also accessible from the dashboard)
2. Enter your current password
3. Enter a new password (minimum 8 characters)
4. Confirm — you remain logged in

### When an Admin Resets Your Password

An SU administrator can force you to pick a new password (for example, after
you report a forgotten password or a suspected leak). When this happens:

1. The admin communicates a **temporary password** to you out-of-band (by phone
   or in person)
2. The next time you open *any* PDHC service (dashboard, plan, contract,
   request, cgm, rosetta, etc.) you will be **automatically redirected** to the
   SSO change-password page — you cannot use those services until you set a new
   password
3. Log in with the temporary password if prompted, then set a new one on
   `sso.pdhc.se/change-password`
4. Once you submit a new password successfully, every service unblocks on your
   next click — no need to log back in everywhere

If you are instead told that **all your sessions have been cleared** (a separate
admin action, typically for a security incident), you will simply be logged out
everywhere and asked to log in again with your existing password. No automatic
redirect to change-password in that case.

---

## Patient Workflow

### Registration

1. Go to `/register-patient`
2. Fill in:
   - **Email** — your email address
   - **Password** — minimum 8 characters
   - **Personnummer** — exactly 12 digits (Swedish personal identity number)
   - **Organisation** — select from the dropdown
3. Submit — your account is created immediately
4. You can now log in

### Logging In

1. Go to `/login`
2. Enter your email and password
3. Click **Login**
4. You will see your patient dashboard

### Dashboard

The patient dashboard shows:

- **Registry status** — whether you are enrolled in any registries
- **Registries** — list of registries you participate in (e.g., INCA)
- **Organisation** — your affiliated organisation

### Viewing Registry Status

Your registry enrolment status is shown on the dashboard. This information comes
from the IPS (Integrated Patient Summary) system and reflects your current
participation in quality registries.

---

## Access Request (New Professionals)

If you are a new professional who does not yet have an account:

1. Go to `/request-access`
2. Fill in the form:
   - **Email** — your professional email
   - **Password** — choose a strong password (min 8 characters)
   - **First name** and **Last name**
   - **Role** — the professional role you are requesting
   - **Organisation** — select from the dropdown
   - **Requested phases** — which service phases you need (this is a *request*;
     an SU admin decides which to grant)
   - **Chosen leader** — select a group leader or SU admin who can endorse you
3. Submit your request
4. Wait for review:
   - A leader may **endorse** your request
   - An SU admin **approves** it, which creates your account and sets up your
     affiliation(s). Phase grants and activation are separate, explicit SU
     actions.
5. Once your account exists, an SU admin grants your phases and then
   **activates** your account. Until it is activated, phase-gated services
   return no access even after phases are granted.

---

## Common Tasks

### Accessing Downstream Services

When you click a link to another PDHC service:

1. If you are already logged into SSO, you are redirected seamlessly
2. If not, you see the SSO login form
3. After login, you are sent back to the original service

Your access level in each service depends on your **session phases** (your
granted phases narrowed by the role of your active affiliation), your affiliated
care unit / caregiver (for scope), and your group memberships (for category).
Each service composes its own policy from these inputs; the SSO supplies the
facts.

### Viewing Documentation

Service documentation is available at `/docs`. This page provides access to
downloadable documentation files.

### Landing Page

The landing page (`/`) shows all registered services in the PDHC ecosystem,
pulled from the service registry.
</content>

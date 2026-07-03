# User Guide

This guide covers day-to-day usage for professionals and patients.

## Professional Workflow

### Logging In

1. Go to the SSO login page (`/login`)
2. Enter your email and password
3. Click **Login**
4. You will be redirected to your dashboard

If you arrived from another service (SSO handshake), you will be automatically redirected back after login.

### Dashboard

After login, the dashboard shows:

- **Your role** — professional type (doctor, nurse, other)
- **Your groups** — approved group memberships (with category and admin status). Groups are organisational/category metadata — they list who you work with, not what you can do.
- **Effective phases** — which service phases you have access to. Phases are granted independently by an SU admin and are orthogonal to group membership.
- **Organisation** — your affiliated organisation(s)

### Requesting Group Membership

To join an existing group:

1. From the dashboard or `/request-join`, select the group you want to join
2. Submit the request
3. A group admin or SU admin will review your request
4. You will appear in the group once approved

!!! note "Joining a group does not grant phase access"
    Group membership shows who you are associated with (e.g. "Oncology Planning"). It does not by itself give you access to any service phase. If you need access to a new phase, ask an SU admin to grant it via `Admin → User phases`.

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
3. Select the group type: `planning`, `request`, `provider`, or `analysis`
4. Submit — an SU admin will review your proposal

### Changing Your Password

1. Go to `/change-password` (also accessible from the dashboard)
2. Enter your current password
3. Enter a new password (minimum 8 characters)
4. Confirm — you remain logged in

### When an Admin Resets Your Password

An SU administrator can force you to pick a new password (for example, after you report a forgotten password or a suspected leak). When this happens:

1. The admin communicates a **temporary password** to you out-of-band (by phone or in person)
2. The next time you open *any* PDHC service (dashboard, plan, contract, request, cgm, rosetta, etc.) you will be **automatically redirected** to the SSO change-password page — you cannot use those services until you set a new password
3. Log in with the temporary password if prompted, then set a new one on `sso.pdhc.se/change-password`
4. Once you submit a new password successfully, every service unblocks on your next click — no need to log back in everywhere

If you are instead told that **all your sessions have been cleared** (a separate admin action, typically for a security incident), you will simply be logged out everywhere and asked to log in again with your existing password. No automatic redirect to change-password in that case.

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

Your registry enrolment status is shown on the dashboard. This information comes from the IPS (Integrated Patient Summary) system and reflects your current participation in quality registries.

---

## Access Request (New Professionals)

If you are a new professional who does not yet have an account:

1. Go to `/request-access`
2. Fill in the form:
   - **Email** — your professional email
   - **Password** — choose a strong password (min 8 characters)
   - **First name** and **Last name**
   - **Professional role** — doctor, nurse, or other
   - **Organisation** — select from the dropdown
   - **Requested phases** — which service phases you need access to (this is a *request*; an SU admin decides which to grant)
   - **Chosen leader** — select a group leader or SU admin who can endorse you
3. Submit your request
4. Wait for review:
   - A leader may **endorse** your request
   - An SU admin **approves** and creates your account. Group membership(s) are created at this step; phase grants are **not** — the SU reviews your requested phases and grants them explicitly as a separate action (#57).
5. Once approved, log in with the credentials you provided. If your SU has not yet granted the phases you requested, phase-gated services will return 403 until they do.

---

## Common Tasks

### Accessing Downstream Services

When you click a link to another PDHC service:

1. If you are already logged into SSO, you are redirected seamlessly
2. If not, you see the SSO login form
3. After login, you are sent back to the original service

Your access level in each service depends on your phases (for action gating) and your groups + organisation (for scope and category). Each service composes its own policy from these independent inputs.

### Viewing Documentation

Service documentation is available at `/docs`. This page provides access to downloadable documentation files.

### Landing Page

The landing page (`/`) shows all registered services in the PDHC ecosystem, pulled from the service registry.

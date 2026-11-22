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

### If you cannot log in: the 15-minute lock

After **five wrong passwords in a row**, password login for that account is
locked for **15 minutes**. This applies to everyone: patients, professionals
and administrators.

- **What you see:** the same message as for any failed login, *"Invalid email
  or password"* — also when you now type the right password. The page does not
  say that the account is locked. That is deliberate: it must not tell a
  stranger which email addresses have accounts.
- **What to do:** wait 15 minutes and try again with the right password. The
  lock ends by itself; trying again during the wait does not make it longer.
- **In a hurry:** ask an SU admin to unlock you.
- A successful login clears the count, and wrong attempts more than 15 minutes
  apart do not add up.
- If you see *"Too many requests"* instead, too many logins came from your
  network address within a minute. Wait a minute.

Om du är patient: efter fem felaktiga lösenord i rad är inloggningen låst i
15 minuter. Du ser bara "Invalid email or password", även med rätt lösenord.
Vänta 15 minuter och försök igen, eller kontakta din vårdenhet.

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

1. Go to `/register-patient` (the patient app's **Registrera dig** button sends
   you there, with `?next=` set so the login afterwards returns to the app).
2. Fill in (the page is in Swedish, #1049):
   - **Förnamn**, **Efternamn**
   - **Personnummer** — 10 or 12 digits with a real date. *In the pilot only
     test numbers are accepted*: a number whose check digit is correct is
     refused with "Piloten tar bara emot testpersonnummer."
   - **E-post** — this is your login
   - **Mobilnummer** — optional; `07…` is stored as `+467…` (in eps, not in SSO)
   - **Din vårdenhet** — select from the list
   - **Lösenord** — minimum 8 characters
3. Submit. The page then says *"Om uppgifterna kunde registreras kan du logga
   in nu. Har du redan ett konto loggar du in med det."* — the SAME text whether
   your account was just created or you already had one (so the page cannot be
   used to find out who is registered).
4. Choose **Logga in**. At the first login you add a passkey before you reach
   the patient app.

Behind the page, SSO asks eps (the patient register) to create you as a
patient at the unit you chose. If eps did, your account is linked to your care
unit straight away. If eps already knew you, the account is created but not
linked: use **Skapa länkkod** (below). If eps cannot be reached, nothing is
created and the page says "Försök igen om en stund."

If your care unit has **invited** you, use the link they gave you instead — see
the section below.

### Logging In

1. Go to `/login`
2. Enter your email and password
3. Click **Login**
4. **Second factor (#1040).** A patient account logs in with two things: the
   password, and a second factor.
    - The first time, you are taken to **Andra faktor** (`/second-factor`) to
      add one. Choose a **passkey** (recommended) or an **authenticator app**.
    - Every time after that, you confirm with the passkey or type the six-digit
      code from the app before you are let in.
5. You will see your patient dashboard

### Second factor: passkey or authenticator app

| | Passkey (recommended) | Authenticator app (reserve) |
|---|---|---|
| What it is | A key saved in your phone, tablet or computer | An app showing a six-digit code that changes every 30 seconds |
| How you use it | Face ID, fingerprint or the device's code | Type the code |
| Add it | **Lägg till passkey** on `/second-factor` | **Koppla autentiseringsapp**, enter the key in the app, then type one code to confirm |

- You can add more than one passkey (for example phone and computer).
- You can remove a factor on the same page, but **not the last one**.
- A code can be used once. After five wrong codes the app login is locked for
  15 minutes; a passkey still works.
- **Lost your device?** Contact your care unit. An administrator can reset your
  second factor; you then add a new one at your next login.

### Dashboard

The patient dashboard shows:

- **Registry status** — whether you are enrolled in any registries
- **Registries** — list of registries you participate in (e.g., INCA)
- **Organisation** — your affiliated organisation
- **Patientappen** — whether a second factor is added, whether your account is
  linked to your care unit, the **Skapa länkkod** button while it is not, and
  the link to the patient app once both are done

### Så kommer du som patient in i patientappen

Patientappen finns på <https://patientapp.pdhc.se>. Där svarar du på frågor och
formulär från din vårdenhet. För att komma in behöver du tre saker: ett konto,
en andra faktor och en koppling till din vårdenhet. Det finns två vägar dit.

**Väg 1: Din vårdenhet bjuder in dig (vanligast)**

1. Du får en länk av din vårdenhet. Länken fungerar en gång och gäller i sju dagar.
2. Öppna länken. Fyll i e-post, personnummer och ett lösenord på minst åtta tecken.
3. Välj **Skapa konto**. Kontot är nu kopplat till dig hos vårdenheten.
4. Lägg till en andra faktor. Välj **Lägg till passkey** och bekräfta med
   Face ID, fingeravtryck eller enhetens kod. Har du ingen passkey kan du i
   stället koppla en autentiseringsapp.
5. Gå till <https://patientapp.pdhc.se> och logga in.

**Väg 2: Du registrerar dig själv i appen**

1. Öppna <https://patientapp.pdhc.se> och välj **Registrera dig**. Fyll i
   förnamn, efternamn, personnummer, e-post, gärna mobilnummer, välj din
   vårdenhet och ett lösenord på minst åtta tecken.
   *Under piloten går det bara att använda testpersonnummer.*
2. Sidan säger: "Om uppgifterna kunde registreras kan du logga in nu. Har du
   redan ett konto loggar du in med det." Välj **Logga in**.
3. Logga in med e-post och lösenord. Första gången lägger du till en passkey
   (eller en autentiseringsapp) innan du kommer vidare. Sedan kommer du till
   patientappen.
4. Oftast är kontot då redan kopplat till din vårdenhet. Säger patientappen att
   kontot inte är kopplat, gör du så här: logga in på <https://sso.pdhc.se> och
   välj **Skapa länkkod** på din sida. Du får en kod som ser ut så här:
   `ABCD-EFGH`. Koden visas bara en gång och gäller i sju dagar.
5. **Ge koden till din vårdenhet**, till exempel vid ett besök eller i telefon.
   Vårdenheten kopplar då ditt konto till dig som patient.
6. När vårdenheten har gjort det går du till <https://patientapp.pdhc.se> och
   loggar in.

**Om det inte fungerar**

- *Patientappen säger att kontot inte är kopplat.* Vårdenheten har inte lagt in
  din kod ännu. Skapa en ny kod om den gamla har gått ut, och ge den till
  vårdenheten. Vårdenheten kan bara koppla dig om du är listad hos just den
  vårdenheten.
- *Länken från vårdenheten gäller inte.* Den är redan använd eller för gammal.
  Be vårdenheten om en ny.
- *Sidan säger "Försök igen om en stund."* Patientregistret svarade inte.
  Inget konto skapades. Försök igen senare.
- *Sidan säger "Piloten tar bara emot testpersonnummer."* Under piloten tar vi
  inte emot riktiga personnummer.
- *Du har bytt eller tappat din telefon.* Kontakta din vårdenhet, så får du
  lägga till en ny andra faktor.

Varje gång du loggar in gör du det i två steg: först lösenordet, sedan din
passkey eller koden från appen. Sedan kommer du till <https://patientapp.pdhc.se>.

### For care staff: linking a patient account

On your **Dashboard**, the card **Patient app: link a patient account** has two
forms. Both need the patient's GUID in eps.pdhc; check it carefully, because
the account will see that patient's tasks.

- **Invite a patient** — creates a one-time link (valid 7 days). It is shown
  once; give it to the patient.
- **Patient gave you a link code** — enter the code and the patient guid.

You can do this for the care units you are affiliated to. A link cannot be
changed afterwards except by an SU admin.

Before anything is linked, the guid is checked against eps: the patient must
exist there and be listed at the care unit. If it is refused:

- **No patient with this guid in eps** — the guid is wrong; copy it again.
- **This patient is not listed at this care unit in eps** — wrong patient or
  wrong unit, or the listing is missing in eps.
- **The patient register (eps) did not answer** — nothing was linked; try again
  later. The patient's link code is not used up.

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

### Finding your way back to the platform

Every page carries the platform ribbon in the top left:

    PDHC / sso.pdhc

`PDHC` returns you to the service index at <https://www.pdhc.se/services.html>;
`sso.pdhc` is this service's own home. It is the same ribbon every PDHC service
carries, so it means the same thing everywhere (#784 A1/A2).

### Downloading documentation

Documentation files are served from `/docs/download/<name>`.

The landing page no longer carries a Documentation pane — it was removed in
in #784 (D1) along with the Service Registry pane, because neither told a
anything they could act on. The download routes themselves are unchanged and
still work; only the pane that listed them is gone.

### Landing Page

The landing page (`/`) offers two things, and nothing else:

| card | who it is for |
|---|---|
| **Register as a patient to join the health data registry** | a private person creating their own account |
| **Request access as a healthcare professional** | a clinician who needs an account created for them |

Each card is titled with the **action it performs** rather than the audience it
names, so a first-time visitor can tell which one applies to them without
reading further (#784 A4/A5).

What it no longer shows, and why, since the earlier version of this guide
described it: the page used to render a **Platform Services** table and a set
of **Connected Services** cards, built by polling every service's health
endpoint and reading `oath_overview.csv`. Both were removed in #784 (A3).

That removal was deliberate rather than tidying. A visitor arriving at the
sign-in page of an identity provider is there to sign in; a live status board
of twenty services is noise at best, and when the prober could not reach a
service the page implied an outage that the service itself would have denied.
Platform status belongs on <https://www.pdhc.se/services.html>, which is
built for it. The ribbon above is the way there.

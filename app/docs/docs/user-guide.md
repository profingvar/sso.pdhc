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

### Joining: by invitation from your care unit (#1050)

A patient joins the patient app **only by invitation from their care unit**.
Open self-registration is closed (#1050 step 1): `/register-patient` shows
"Du behöver en inbjudan" unless the operator
turns `PATIENT_SELF_REGISTRATION` on, which is off by default.

1. Your care unit registers you and gives you a one-time link
   (`/patient-invite/<token>`). It works once and for seven days.
2. The page says **Inbjudan från <your care unit>** and shows your name.
3. Fill in (Swedish page):
   - **Personnummer** — 10 or 12 digits. It must be the SAME number your care
     unit registered; SSO asks eps (the patient register) and is told only
     yes or no. A wrong number gives "Personnumret stämmer inte med
     inbjudan"; after five wrong numbers the link closes and you need a new
     one. *In the pilot only test numbers are accepted.*
   - **E-post** — this is your login
   - **Lösenord** — minimum 8 characters
4. **Skapa konto.** The account is created already linked to you at the care
   unit, with your name. Next you add a **passkey** (or an authenticator app),
   and then you are taken straight to the patient app's login.

If eps cannot be reached the page says "Försök igen om en stund." and nothing
is created.

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

Patientappen finns på <https://patientapp.pdhc.se>. Där fyller du i ditt
patientkort och svarar på frågor från din vårdenhet. Du går med **bara genom en
inbjudan från din vårdenhet**. Det går inte att registrera sig själv.

**Så går det till**

1. Vårdenheten registrerar dig och ger dig en länk. Länken fungerar en gång och
   gäller i sju dagar. (Sms kommer senare. Tills vidare får du länken på annat
   sätt, till exempel vid ett besök.)
2. Öppna länken. Sidan visar **Inbjudan från** din vårdenhet och ditt namn.
3. Skriv ditt personnummer. Det måste vara samma nummer som vårdenheten har
   registrerat. *Under piloten går det bara att använda testpersonnummer.*
4. Skriv din e-postadress och välj ett lösenord på minst åtta tecken.
   Välj **Skapa konto**.
5. Lägg till en passkey: välj **Lägg till passkey** och bekräfta med Face ID,
   fingeravtryck eller enhetens kod. Har du ingen passkey kan du i stället
   koppla en autentiseringsapp.
6. Du kommer direkt till patientappens inloggning och är inne.

**Har du redan ett konto som inte är kopplat?** Det gäller bara konton som
skapades innan inbjudan blev det enda sättet. Logga in på
<https://sso.pdhc.se> och välj **Skapa länkkod**. Ge koden (`ABCD-EFGH`, gäller
i sju dagar) till din vårdenhet, som kopplar kontot.

**Om det inte fungerar**

- *"Personnumret stämmer inte med inbjudan."* Kontrollera siffrorna. Efter fem
  felaktiga försök slutar länken att fungera, och du behöver en ny.
- *Länken gäller inte.* Den är redan använd, för gammal eller stängd. Be
  vårdenheten om en ny.
- *Sidan säger "Försök igen om en stund."* Patientregistret svarade inte.
  Inget konto skapades. Försök igen senare.
- *Sidan säger "Piloten tar bara emot testpersonnummer."* Under piloten tar vi
  inte emot riktiga personnummer.
- *Du har redan ett konto.* Logga in med det i patientappen. Ingen ny
  inbjudan behövs.
- *Du har bytt eller tappat din telefon.* Kontakta din vårdenhet, så får du
  lägga till en ny andra faktor.

Varje gång du loggar in gör du det i två steg: först lösenordet, sedan din
passkey eller koden från appen. Sedan kommer du till <https://patientapp.pdhc.se>.

### For care staff: linking a patient account

**The usual way is dashboard.pdhc's "Bjud in patient" (#1050)**: enter the
patient's name and personnummer, choose your care unit, and the dashboard
registers the patient in eps and creates the invite in one go, with the name
on it. The forms below remain for an SU admin and for the link-code case.

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

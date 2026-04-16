## Regler för rättigheter och auktorisering (gäller genomgående)

Den här SSO/OATH-tjänsten bygger åtkomst och auktorisering av tjänster i en mikroservicemiljö. Den ska både hantera inloggning och auktoriseringsnycklar som medger att hela systemet kan använda standardiserade regler för åtkomst till bland annat hälsodata. 

När man vill logga in på en undertjänst skall man hänvisas till denna tjänsten för inloggning. När man loggat in ska SSOtjänsten hantera tillbakahänsvisning tillsammans med de nycklar som sätter privilegier. När man vill logga in på en annan tjänst ska det autoimatiskt kontrolleras om man är inloggad och skjutas tillbaka till tjänst den andra tjänsten med rätt credentials. 
Det ska finnas en 'oath_overview.csv' där dels olika tjänster kan läggas till dels där privilegienivå enligt de regler som finns kan sättas per tjänst. Likså ska den tabellen innehålla url tjänst, url api health, url capability statement, url endpoints per tjänst. 
På samma sätt ska finna en översikt över användare med alla uppgifter i ett komprimerat utförande som är synligt/editerbart av SU

Ett central dokument är det som ger instruktioner till nedströms tjänster för att effektivt kunna inordnas under SSO/OATH. Landningssidan på SSO ska lista alla tjänster som ges service.

Denna tjänsten bygger på ett litet antal **auktoriseringsregler** som kombineras per listad tjänst. 

En person är antingen patient eller professionell (olika inlogg om man råkar vara båda)

- **Identitetstyp 1:** (`user_type`)En patient har tillgång till sina data (överordnad regel) Om patientens personnummer finns i tjänsten har inloggad patient automatiskt tillgång till data i hela systemet via en lighthousefunktion. En patient har en tillhörighet till en organisation som ansvarar för patientens data
- **Patientens egen data**: patienter ska i princip bara kunna agera på resurser som är knutna till **deras `patient_guid`**, och vissa extra läsregler kan bero på om patienten finns i register (`in_registry`, `registries`).

Övriga regler gäller professionella per tjänst.

- **Identitetstyp 2(`user_type`)**: Professionell (Läk, SSK, Övr).

- **Organisation (datascope)**: en individ kopplas till en **organisation** via åtkomst-/endorsmentflöden. Organisation används som **synlighets- och scopes-gräns** för data i nedströmstjänster (”du ser bara data från patienter registrerade på din organisation”). Man kan vara medlem i flera organisationer.

- **Fas-behörighet (direkta grants)**: privilegier ges per fas (**planning**, **request**, **provider**, **analysis**) genom **direkta `UserPhase`-grants** som SU tilldelar per användare via `POST /api/admin/users/<guid>/phases`. Resultatet exponeras som listan `effective_phases` i access-blobben. Gruppmedlemskap **ger inte** fas-behörighet (beslut #57, 2026-04-15): en godkänd medlem i en grupp med `category = "planning"` får **inte** automatiskt `planning`-fasen — SU måste grant:a den explicit. Man kan ha flera faser samtidigt.

- **Grupper (organisatorisk metadata)**: grupper är en organisatorisk/kategorisk indelning som är **oberoende** av fas-behörighet (#57). Varje grupp har en `category` (fri textsträng efter #60, tidigare en 4-värdes-enum `group_type`) som nedströmstjänster kan använda för UI-gruppering eller org-admin-checkar — men **aldrig** som en fas-access-signal. Endast medlemskap med **`status == 'approved'`** ska räknas vid gruppbaserad logik.

- **Gruppadmin**: ett godkänt gruppmedlemskap kan ha flaggan **`is_admin`**. Det ger administratörsbehörighet **inom den specifika gruppen** — att godkänna/avslå väntande medlemsansökningar, skapa invite-tokens, osv. Det är **inte** fas-scopad admin och ger inga extra fas-grants.
- **SU admin (superuser)**: om användaren är markerad som **SU admin** (`is_su_admin`) ska den behandlas som systemadministratör med breda rättigheter enligt policy/matris (t.ex. `oath_overview.csv` i helheten), och kan dessutom utföra administrativa funktioner i SSO-tjänsten.

- **Tjänst-till-tjänst-läsning**: vissa profiluppslag kan kräva extra “service credentials” via headers (`X-SSO-Client-Id`, `X-SSO-Client-Secret`) utöver användarens Bearer-token.

Tjänsten levererar alltså en **”access blob”** (profil + grupper/faser) och det är tänkt att **nedströms microservices** mappar detta till sina egna domänroller och CRUD-rättigheter, men alltid med dessa byggstenar som grund.

En professionell har alltså följande attribut:
Namn
Yrke
Fastillhörigheter
Organisationstillhörigheter
Grupptillhörigheter
Privilegierställning: Användare/gruppledare/SU

En patient som vill logga in på patientportalen Använder sitt personnummer och får systemwide sina data listade. Patienterna med tillträde listas i en annan tjänst (ips.pdhc). De loggar in med (mock) Bankid i patientportalen (minadata.pdhc). 

## Översikt: vad tjänsten är och vad den gör

Tjänsten är en central **Flask-baserad SSO/OATH-server** med en PostgreSQL-databas. Den fungerar som **enda källa** för användare, professional-identiteter, grupper, medlemskap och administrativa beslut (endorsment/approval). Den utfärdar en **JWT-baserad access token** (session-token) med begränsad livslängd och tillhandahåller API:er för att slå upp aktuell användare, lista grupper och hantera ansökningar/medlemskap. Den har även en fristående frontend webbfunktion (HTML-sidor) för inloggning, admin och onboarding.

## Webbsidor (UI) och dokumentvisning

Tjänsten exponerar ett antal UI-sidor som stödjer flödena (start, login, dashboard, su-admin, group-admin, join, request-join, register-patient, suggest-group, request-access, change-password). Syftet är att ge ett operativt gränssnitt för inloggning och administration utan att konsumenttjänster behöver bygga egna adminverktyg för SSO-delen.

Det finns även en docs-sida där en fast allowlist av dokument kan laddas ned. Nedladdningen är designad för att förhindra path traversal genom att endast tillåta specifika filnamn.
För SU finns en översiktssida där alla användare listas inklusive deras status för alla regler. Den översiktssidan ska kunna exporteras som .csv och likaså ska SU kunna importera en .csv med nya användare.

## Autentisering: tokenutfärdande och sessionmodell

När en användare loggar in med e-post och lösenord verifieras lösenordet mot lagrad hash. Vid lyckad inloggning skapas en **JWT (HS256)** med minst `sub = user_guid` och en `exp` baserad på konfiguration (standard ~24 timmar). Den tokenen används som **Bearer-token** i `Authorization`-headern mot både SSO-tjänstens egna API:er och mot nedströmstjänster som i sin tur anropar SSO för att resolva användaren.

Utöver “ren API-login” finns ett särskilt browser-orienterat SSO-handshake som är gjort för att en extern applikation ska kunna skicka användaren till SSO för inloggning och sedan få en redirect tillbaka med token.

## Handshake för extern tjänst (H1–H4 med `next`, `state`, `token`)

Tjänsten stöder ett **icke-standardiserat men tydligt kontrakt** för webbläsarbaserad SSO mellan tjänster:

- En extern tjänst skickar användaren till SSO:s login-sida och bifogar en **callback-URL** i parametern `next` (och valfritt `state`).
- SSO validerar att `next` är **allowlistad** (antingen via exakt callback-URL-lista eller via godkända origins).
- Efter lyckad inloggning gör SSO en **302 redirect** tillbaka till `next` och bifogar **`token=<jwt>`** (och `state` om den fanns) i query string. Vid fel bifogas istället `error` och ev. `error_description`.
- Den externa tjänsten tar emot callbacken och gör sedan server-side uppslag mot SSO (typiskt `GET /api/auth/me`) för att bygga sin egen session och auktorisera användaren.

Detta är centralt för “SSO mellan tjänster” i den här miljön och förklarar varför `next`/`token`-mönstret återkommer i dokumentationen.

## Profiluppslag: “vem är användaren?” (`/api/auth/me`)

För att nedströmstjänster ska kunna fatta beslut om rättigheter erbjuder SSO en “me”-endpoint som tar en Bearer-token och returnerar en profil:

- Grundfält: användarens GUID, e-post, `user_type`, samt `is_su_admin`.
- För patienter: `patient_guid`, `in_registry` och lista över `registries`.
- För professionals: `professional_guid`, `professional_role`, `organization_ids` (plus `organisation_warning` om listan är tom), en lista av `groups` (med `group_guid`, `group_name`, `category`, `status`, `is_admin`) samt en **`effective_phases`**-lista som **uteslutande** kommer från direkta `UserPhase`-grants (#57). Grupper och faser är oberoende kriterier — konsumenten kontrollerar fas-behörighet via `phase in blob["effective_phases"]` och läser `groups` bara för organisatorisk/kategorisk metadata.

I praktiken är detta den primära mekanismen som konsumenttjänster använder för att översätta SSO-identitet till lokala roller/rättigheter.

## Profiluppslag med tjänstautentisering (`/api/auth/me/service`)

Det finns även en variant där samma typ av profil returneras, men där SSO dessutom kräver att anroparen skickar service-credentials i headers. Den är tänkt för scenarier där man vill kunna skilja på “vilken intern tjänst gör uppslaget”, eller tvinga fram att bara kända tjänster får göra vissa uppslag i kombination med en användartoken. Konfigurationen av godkända service-klienter är env-baserad och matchar `client_id -> secret`.

## Patientflöden: registrering och registerstatus

Tjänsten stödjer patientrelaterade funktioner som är avsedda för enkel onboarding i miljöer utan BankID:

- **Patient self-enrollment**: skapar både en `user` av typen patient och en `patient`-rad med personnummer (12 siffror) och ett dev-lösenord. Detta är tydligt markerat som en förenklad modell.
- **Registry status**: en patient som är inloggad kan fråga om den egna patienten finns i något register och få tillbaka `in_registry` samt vilka registrynamn som matchar.

Dessa funktioner är knutna till regeln “patient får bara göra patient-saker” och kräver Bearer-token.

## Professionalflöden: medlemskap, gruppledare och lösenordsbyte

För professionals finns funktioner som beskriver hur man blir behörig i en fas:

- En professional kan **begära medlemskap** i en grupp, vilket skapar ett ”pending” medlemskap. Detta är ett sätt att säga ”jag vill ingå i denna organisatoriska gruppering”. Observera: medlemskap ger **inte** automatiskt fas-behörighet (#57) — fas-grants hanteras separat av SU.
- En professional kan också **begära att bli gruppledare** (admin) för en grupp. Den begäran måste sedan beslutas av SU admin. Gruppledar-rollen är scopad till administration **inom gruppen** (godkänna medlemmar, skapa invites) — den ger inga fas-grants.
- Det finns ett endpoint för **lösenordsbyte** för inloggade användare (med kontroll av current password och minsta längd på nytt lösenord).

Gemensamt är att de här endpoints:en utgår från att användaren redan har en giltig token och att auktoriseringen görs utifrån `user_type` och rollen (professional).

## Gruppfunktioner: lista egna grupper, admin-beslut och inbjudningar

Grupper är en organisatorisk/kategorisk indelning (oberoende av fas-behörighet efter #57) och tjänsten har därför flera gruppnära funktioner:

- **Lista egna godkända grupper**: användaren kan få en lista över grupper där medlemskapet är ”approved”. Klienter använder detta för UI-gruppering eller org-admin-checkar — **inte** för att avgöra fas-behörighet (det görs via `effective_phases`).
- **Group admin: lista pending membership requests**: en gruppadmin kan se väntande medlemsansökningar för sin grupp.
- **Group admin: godkänna/avslå medlemskap**: en gruppadmin kan sätta status på ett pending medlemskap till approved eller rejected.
- **Inbjudningar (invite links)**: gruppadmin eller SU admin kan skapa en tidsbegränsad invite-token. Den kan lösas in av en professional för att skapa ett pending medlemskap via “join-by-invite”. Detta är ett kontrollerat sätt att distribuera onboarding till grupper utan att användaren måste hitta gruppen manuellt.

## SU admin-funktioner: systemadministration och beslutsflöden

SU admin är den högsta administrativa nivån och kan:

- **Lista användare/professionals** med mer komplett information (inklusive medlemskap), vilket behövs för att administrera.
- **Promota SU admin**: höja en professional till superuser, med extra verifiering (kräver att den som gör åtgärden bekräftar med sitt eget lösenord).
- **Ta bort användare** och **ta bort grupper**, med kaskad-/städlogik i databasen så att referenser som pekar på “decided_by” blir nullade innan borttag.
- **Tillsätta gruppadmin**: säkerställa att en viss användare blir admin i en grupp (approved + is_admin).
- **Hantera gruppförslag**: users kan föreslå nya grupper; SU admin kan godkänna (skapar grupp) eller avslå.
- **Hantera gruppledarförfrågningar**: SU admin kan godkänna (ger adminstatus) eller avslå.
- **Hantera åtkomstansökningar**: SU admin (och i vissa fall vald gruppledare) kan lista, uppdatera, endorsa eller avvisa access requests.
- **Hantera organisationer**: lista och skapa organisationer (organisationer är en viktig dimension för datascope i nedströmssystem).

Det här är tjänstens “governance”-del: den skapar och förvaltar de strukturer (organisationer, grupper, adminskap, medlemskap) som nedströmstjänster sedan använder för auktorisering.

## Publika (oautentiserade) katalog- och onboarding-endpoints

För att kunna bygga onboardingflöden utan att först ha ett konto finns en “public” API-del utan login:

- Lista organisationsnamn för dropdowns.
- Lista grupper (read-only katalog).
- Lista group leaders (inklusive SU admins) för att kunna välja vem som ska endorsa en åtkomstansökan.
- Skicka in en **access request**: en professional kan ange e-post, ett lösenord (minst 8 tecken), personuppgifter, organisation, vilka faser som önskas, samt välja en group leader/SU admin som ska besluta. Observera (#57): godkännande av en access request skapar användaren men ger **inte** automatiskt de önskade faserna — SU måste grant:a varje fas explicit via `POST /api/admin/users/<guid>/phases` som ett separat steg.

Dessa endpoints har enkla rate limits och kort cache-TTL för att vara robusta mot överdriven last.

## Datamodellens roll: GUID som kontrakt och “access blob” som leverans

En viktig funktion i tjänsten är att den standardiserar **identifierare**: API:er och integrationer ska använda GUID/UUID för användare, patienter, grupper, medlemskap etc. Interna DB-id:n är just interna. Det gör att konsumenttjänster kan lagra referenser stabilt utan att exponera interna id:n och utan att kräva gemensam DB.

Tjänsten sammanställer dessutom ”access blobben” (inkl. grupper, faser, SU-flagga) så att konsumenttjänster kan fatta beslut enligt en extern rättighetsmatris (t.ex. `oath_overview.csv`) men med SSO som sanningskälla för identiteten. Efter #57 är `effective_phases` och `groups` **oberoende** fält i blobben — konsumenter som tidigare härledde fas-access från gruppmedlemskap måste uppdateras att läsa `effective_phases` direkt.

## Tekniska överväganden (samlat)

- **Icke-standard SSO-kontrakt**: `next` + `token` i redirect är inte OIDC. Det fungerar i kontrollerade miljöer, men kräver tydlig allowlist och tydliga felkoder. Det är viktigt att konsumenter alltid gör server-side validering via `GET /api/auth/me`.
- **Redirect-säkerhet**: allowlist implementeras både som “origin allowlist” och en valfri strikt “exakt URL”-allowlist. Det minskar open-redirect-risk men kräver korrekt driftkonfig när nya tjänster onboardas.
- **Token i URL**: token skickas i query string vid redirect. Det är praktiskt men innebär risk för loggning i proxies/CDN och i browser-historik. Man behöver därför vara strikt med logghantering och helst hålla callback-endpoints “rena” (snabbt byta token till server-side session och redirecta vidare utan token i URL).
- **JWT och nyckelhantering**: tokens signeras med en delad hemlighet (`SECRET_KEY`, HS256). Om konsumenter validerar lokalt måste de dela samma hemlighet (hög risk/operativt dyrt). Rekommenderat är i stället att konsumenter anropar `/api/auth/me`.
- **Token-livslängd**: standard är ~24 timmar (`SESSION_EXPIRY_HOURS`). Konsumenter måste hantera `token_expired` och skicka användaren till ny inloggning.
- **Rate limiting och cache**: publika endpoints och login med `next` har in-memory rate limiting. Det fungerar för en enskild process men blir inkonsekvent vid flera workers/instances och efter omstart. Kort cache för katalogdata minskar last men bör synkas med hur ofta data ändras.
- **Behörighetsbeslut splittras medvetet**: SSO levererar identitet/grupper/faser; nedströmstjänster måste kombinera detta med sin egen domänmodell (resursägarskap, organisationstillhörighet, kontrakt) för slutligt beslut. Det gör SSO generellt men kräver metodisk rollmapping i varje tjänst. Instruktioner ska ges i säsrkilt dokument för varje systemutveckling av nedströms tjänster.
- **DB-transaktioner och konsistens**: DB-lagret använder en cursor-context som committar per request. Många “approve/reject”-operationer bygger på “status == pending” som precondition, vilket är bra för idempotens men kräver att clients hanterar “already decided”.
- **Drift och dokumentation**: tjänsten innehåller omfattande drift- och integrationsdokument. Eftersom kontraktet (t.ex. handshake-parametrar och access blob) är centralt bör den dokumentationen hållas uppdaterad i takt med kodens faktiska beteende.

Kolla nedanstående och se om det är tillämpligt: 

Här är ett rent beslutsträd som du kan använda i dina microservices. Jag delar upp det i tre nivåer: entry (gemensamt) → patient-flöde → professionell-flöde.

⸻

🌳 1. ROOT – gemensamt beslutsträd

START

IF is_su_admin == true:
    → ALLOW (enligt policy-matris per action)
    
ELSE IF user_type == "patient":
    → Gå till PATIENT FLOW
    
ELSE IF user_type == "professional":
    → Gå till PROFESSIONAL FLOW

ELSE:
    → DENY


⸻

👤 2. PATIENT FLOW (egen data, överordnad regel)

# Steg 1 – verifiera identitet i systemet
IF patient exists in IPS (via personnummer):
    lighthouse_access = true
ELSE:
    → DENY

# Steg 2 – ownership (huvudregel)
IF subject.patient_guid != resource.patient_guid:
    → DENY

# Steg 3 – organisation (valfri, beroende på domän)
IF resource.organization_id exists:
    IF subject.organization_id != resource.organization_id:
        → DENY

# Steg 4 – registry-regler (endast vissa resurser)
IF resource.requires_registry == true:
    IF subject.in_registry != true:
        → DENY
    IF resource.registry NOT IN subject.registries:
        → DENY

# Steg 5 – action filtering
IF action IN ["read", "write", "delete"]:
    → ALLOW (default för egen data, om ovan passerar)

END

👉 Tolkning:
	•	patient_guid = absolut gatekeeper
	•	IPS/lighthouse = “entry ticket”
	•	registry = extra filter (inte grundregel)

⸻

🧑‍⚕️ 3. PROFESSIONAL FLOW

# Steg 1 – organisation scope (ALLTID först)
IF resource.patient.organization_id NOT IN subject.organization_ids:
    → DENY

# Steg 2 – fas-behörighet (direkta UserPhase-grants, #57)
REQUIRED_PHASE = map_action_to_phase(action)

IF REQUIRED_PHASE NOT IN subject.effective_phases:
    → DENY
# OBS: Gruppmedlemskap kontrolleras INTE här. Grupper är
# organisatorisk metadata efter #57 — en "planning"-kategoriserad
# grupp ger inte planning-fasen automatiskt, SU måste grant:a den.

# Steg 3 – gruppadmin (inom-grupp-administration, inte fas-override)
# Flaggan `is_admin` på ett godkänt medlemskap ger administratörs-
# rättigheter INOM den specifika gruppen (godkänna medlemmar,
# skapa invites). Den ger inga extra fas-grants eller
# resurs-access utanför gruppens egna admin-endpoints.

# Steg 4 – standard permissions (per tjänst)
IF service_policy_allows(subject.role, action, resource):
    → ALLOW

ELSE:
    → DENY


⸻

🔄 4. Service-to-service (extra lager)

IF request has X-SSO-Client-Id + Secret:
    validate service credentials
    
    IF valid:
        → allow profile enrichment / read
    ELSE:
        → DENY

👉 Detta körs parallellt, inte istället för user auth.

⸻

🧩 5. Lighthouse / Access Blob (input till alla beslut)

Alla services får detta:

{
  "user_guid": "...",
  "email": "...",
  "user_type": "patient | professional",
  "is_su_admin": false,
  "must_change_password": false,

  // Patient-specifika fält
  "patient_guid": "...",
  "organisation_guid": "...",
  "in_registry": true,
  "registries": [...],

  // Professional-specifika fält
  "professional_guid": "...",
  "professional_role": "doctor | nurse | other",
  "organization_ids": [...],
  "groups": [
    {
      "group_guid": "...",
      "group_name": "Oncology Planning",
      "category": "planning",   // fri textsträng efter #60; bara metadata
      "status": "approved",
      "is_admin": false          // inom-grupp-admin, inte fas-admin
    }
  ],
  // Fas-behörighet — ENDA källa är direkta UserPhase-grants (#57).
  // Nedströms: `if "planning" in blob["effective_phases"]: …`
  "effective_phases": ["planning", "analysis"]
}


⸻

🧠 6. Hur microservices ska tänka (viktigt)

Varje service gör:

1. Validera token + access blob
2. Kör beslutsträdet (ovan)
3. Mappa → lokala CRUD-regler

👉 De ska inte uppfinna egen auth, bara mappa.

⸻

💡 Min tydliga rekommendation
	•	Gör detta som en shared policy engine / middleware
	•	Definiera:
	•	map_action_to_phase()
	•	resource.requires_registry
	•	Undvik att varje service tolkar regler olika

⸻
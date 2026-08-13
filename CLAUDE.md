# Omen — guild-landingpage

Landingpage for WoW Classic Fresh-guilden **Omen** (EU-Spineshatter, guild-id `809023`)
på `fresh.warcraftlogs.com`. Siden skal svare på ét spørgsmål og gøre det med data.

Den fulde spec med hele beslutningshistorikken ligger i
`..\..\Brainstorms\12-08-2026-omen-guild-landingpage.md`. **Læs den, før du ændrer
noget produktmæssigt** — den forklarer, hvorfor tingene er, som de er.

## Sidens ene spørgsmål

> "Kan de clear alt inden for 3 timer?"

Alt andet er understøttende. Rank er afvist som støj.

## De fem regler, alt andet følger af

1. **Hovedtallet er vægur-clear-tid for hele aftenen** — første pull *overhovedet*
   → sidste boss død *overhovedet*, inkl. trash, wipes og tiden mellem zonerne.
   Ikke sum af bosskampe, ikke sum af zone-segmenter, ikke rekord-pull.
   Zone-summen findes stadig i data (`zones_wallclock_ms`), men den er ~20 min
   for lav, fordi summons og flyveture falder ud — og det er rigtig raidtid.
2. **Median, aldrig rekord.** Rekord må stå som en lille sidelinje. Simons ord om
   at hero'e rekorden: *"bondefangeri"*. Siden må ikke kunne overselges.
3. **Raid-størrelse står altid ved siden af en tid.** "2t58 · 21 mand" forklarer
   sommerdykket bedre end en undskyldning. Data forklarer sig selv.
4. **Nøgler rører aldrig browseren.** WCL-nøgle og Discord-webhook er secrets i
   GitHub Actions / Cloudflare. Frontend læser kun `data.json`.
5. **Ansøgningsformularen er en samtalestarter, ikke en screening.** Optimeret for
   at blive *sendt*. Ingen essays, ingen lange fritekstfelter.

## Arkitektur

```
GitHub Actions (cron, fredag morgen)
  └─ scripts/fetch_data.py  ──> WarcraftLogs v2 GraphQL
        └─ skriver site/data.json  (committes tilbage til repoet)

Cloudflare Pages
  ├─ site/            statisk, læser data.json — kan ikke gå ned
  └─ functions/       én serverless-funktion: formular → Discord-webhook
```

Kun **formularen** er levende. Alt andet er en fil og et cron-job. Det er
grunden til, at "simpelt" og "live data" kan være sandt på samme tid.

## Vigtigt om WarcraftLogs-API'et

- **Host er `fresh.warcraftlogs.com`, ikke `www`.** WCL kører separate databaser
  pr. site. Bruger man `www`, får man tomme svar i stedet for en fejl.
- Auth: OAuth2 client credentials → `POST {host}/oauth/token`.
- GraphQL: `{host}/api/v2/client`. Rate limit: 3600 points/time.
- `fight.startTime` / `endTime` er millisekunder **relativt til `report.startTime`**.
- WCL regner allerede zone-segmentet ud (`1:12:35`-tallet), men lægger aldrig
  zonerne sammen. **Sammenlægningen er sidets opfindelse** — den sker i
  `analyse_report()` i `scripts/fetch_data.py`.
- **Samme raid logges tit af flere raidere.** 66 reports viste sig at være 32
  aftener. Uden `deduplicate_nights()` tælles en aften dobbelt i medianen.
- Zonen hedder **"The Eye"** i API'et, ikke "Tempest Keep".

## Kendt datarisiko

Hovedtallets troværdighed hænger på **logging-disciplin**: loggen skal startes før
første pull og stoppes ved sidste kill. I eksempel-loggen manglede Al'ar, altså
ca. 15 min. Glemt stop = tallet for højt. Sen start = tallet for lavt.
Det er en aftale med raidlederne, ikke et kodeproblem — men koden bør markere
mistænkelige logs frem for stiltiende at regne på dem.

## Sprog — vigtigt, og en undtagelse fra workspacets regler

Omen er en **international guild**. Alt, en besøgende ser, er **engelsk**:
begge sider, formularens fejlbeskeder, Discord-beskeden fra funktionen.

Kommentarer i koden, commit-beskeder og scriptets konsoloutput er stadig **dansk**
— de er til Simon, ikke til besøgende.

Det betyder også, at **datoformatet på siden er engelsk** ("21 May", "23 Jul 2026"),
ikke workspacets DD/MM/ÅÅÅÅ. Det er bevidst: `06-08` læses som 8. juni af
halvdelen af et internationalt publikum. Workspace-reglen gælder Simons egne
tekster og filnavne — ikke dette produkts brugerflade. **Lav det ikke om.**

## Arbejdsform i dette projekt

Simon er uddannet datamatiker, Java-backend-tung, rusten på frontend og har ikke
brugt VS Code i nogle år. Så: **jeg stilladserer, han bygger videre.** Spring
grundforklaringer over på arkitektur, API'er og datamodeller — men vær konkret om
værktøj, kommandoer og syntaks.

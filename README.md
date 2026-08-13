# Omen — landingpage

Viser guilden Omens rigtige clear-tider fra WarcraftLogs og har en ansøgningsknap
til Discord.

## Kom i gang

### 1. Hent API-nøgler (engangsopgave)

Gå til <https://www.warcraftlogs.com/api/clients/>, log ind, og opret en client:

- **Name:** `Omen landingpage`
- **Redirect URL:** `http://localhost` (bruges ikke, men feltet er påkrævet)

Du får et **Client ID** og et **Client Secret**.

### 2. Læg nøglerne i .env

```bash
cp .env.example .env
```

Åbn `.env` og indsæt de to værdier. Filen er i `.gitignore` og bliver **aldrig**
committet.

### 3. Kør scriptet

```bash
# Tjek at forbindelsen virker, og se den nyeste raid regnet igennem
.venv/Scripts/python.exe scripts/fetch_data.py --probe

# Byg site/data.json af alle raids
.venv/Scripts/python.exe scripts/fetch_data.py
```

## Mappestruktur

| Sti | Hvad |
|-----|------|
| `scripts/wcl.py` | tynd API-klient: token + GraphQL. Ingen forretningslogik |
| `scripts/fetch_data.py` | henter raids og regner vægur-clear-tider ud |
| `scripts/shot.py` | screenshots af siden til udvikling. Ikke en del af produktet |
| `site/index.html` | forsiden — hovedtal, konsistensstribe, tier-kort |
| `site/apply.html` | ansøgningsformularen, seks felter |
| `site/style.css` | fælles fundament: farver, baggrund, knap |
| `site/data.json` | genereret data. Committes, så siden altid har noget at vise |
| `functions/api/apply.js` | serverless: formular → Discord. Sidens eneste levende del |
| `.github/workflows/` | cron-jobbet der opdaterer data efter torsdagsraidet |

## Se siden mens du bygger

```bash
# Screenshots af begge skærmstørrelser -> .shots/
.venv/Scripts/python.exe scripts/shot.py

# Eller kør en rigtig server og åbn den i browseren
.venv/Scripts/python.exe -m http.server -d site 8000
```

## Secrets — hvad skal sættes hvor

Ingen af dem hører hjemme i koden.

| Navn | Hvor | Til hvad |
|------|------|----------|
| `WCL_CLIENT_ID` | `.env` lokalt · GitHub repo secrets | henter data |
| `WCL_CLIENT_SECRET` | `.env` lokalt · GitHub repo secrets | henter data |
| `DISCORD_WEBHOOK_URL` | Cloudflare Pages → Environment variables (Secret) | modtager ansøgninger |
| `OFFICER_ROLE_ID` | Cloudflare Pages (valgfri) | pinger officers i beskeden |

## Kommandoer du bruger igen og igen

```bash
git status                  # hvad har jeg ændret?
git add -A && git commit -m "besked"
git log --oneline           # historikken
```

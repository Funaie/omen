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
| `site/` | den statiske hjemmeside — læser `data.json` |
| `site/data.json` | genereret data. Committes, så siden altid har noget at vise |
| `.github/workflows/` | cron-jobbet der opdaterer data efter torsdagsraidet |

## Kommandoer du bruger igen og igen

```bash
git status                  # hvad har jeg ændret?
git add -A && git commit -m "besked"
git log --oneline           # historikken
```

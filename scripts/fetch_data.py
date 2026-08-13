"""Henter Omens raids fra WarcraftLogs og skriver site/data.json.

Hovedtallet, hele siden bygger på, er **vægur-clear-tid for hele aftenen**:
fra første pull overhovedet til sidste boss er død — inkl. trash, wipes og
tiden mellem zonerne. De ~20 minutter med summons og flyveture er rigtig
raidtid for den, der sidder og venter på at kunne logge af.

WarcraftLogs regner et tal pr. zone (det er "1:12:35"-tallet på en report),
men lægger aldrig zonerne sammen og tæller aldrig mellemrummene med. Begge
dele er sidets egen opfindelse, og de sker her.

To ting, rigtige data afslørede, og som koden ikke må glemme:
  * Samme raid logges tit af flere raidere — 66 reports var 32 aftener.
  * En aften tæller kun i medianen, hvis HELE tieren faldt. Ellers ville en
    7-minutters Gruul-log stå som en clear.

Kørsel:
    python scripts/fetch_data.py --probe     # tjek forbindelse + se én report rå
    python scripts/fetch_data.py             # byg site/data.json
"""

import argparse
import json
import pathlib
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from wcl import WCLClient, WCLError

GUILD_ID = 809023  # Omen, EU-Spineshatter, på fresh.warcraftlogs.com

# Logs, der IKKE er registreret på guilden, men er rigtige Omen-raids.
# Sker når en raider uploader fra sin egen konto uden at sætte guild på
# rapporten — så findes den ikke via reports(guildID:), uanset at raidet
# var det samme.
#
# REGLEN, og den er ikke til forhandling: her tilføjes kun **hele aftener,
# guilden faktisk raidede**. Aldrig for at vælge den pæneste af flere logs
# af samme aften — det klarer deduplicate_nights() efter faste kriterier.
# Hver kode er en påstand om, at raidet fandt sted; den står i git og kan
# efterprøves af enhver på fresh.warcraftlogs.com/reports/<kode>.
#
# Den rigtige løsning er at få raideren til at sætte guild på rapporten på
# WarcraftLogs. Så forsvinder behovet for linjen her.
EXTRA_REPORT_CODES = [
    "DALQ1RCdtj738Gw6",  # 13-08-2026 — guild-loggen stoppede midt i SSC
]

# Hvor data.json lander. Siden læser den herfra.
OUTPUT_PATH = pathlib.Path(__file__).parent.parent / "site" / "data.json"

# Zonerne som WCL faktisk navngiver dem — bekræftet mod rigtige data 13-08-2026.
# Bemærk: zonen hedder "The Eye" i API'et, ikke "Tempest Keep". Tempest Keep er
# hele komplekset; The Eye er den 25-mands raid, vi måler på.
#
# "bosses" er det forventede antal bosser i en fuld clear. Det bruges kun til at
# markere logs, hvor der mangler kills — se flag_incomplete().
ZONES = {
    "Serpentshrine Cavern": {"tier": "T5", "bosses": 6},
    "The Eye": {"tier": "T5", "bosses": 4},
    "Gruul's Lair": {"tier": "T4", "bosses": 2},
    "Magtheridon's Lair": {"tier": "T4", "bosses": 1},
    # 11 og ikke 10: Nightbane er optionel i teorien, men Omen tager ham hver
    # gang, så en "fuld Karazhan" uden ham ville være en halv sandhed.
    "Karazhan": {"tier": "T4", "bosses": 11},
}

# Hvad "alt" betyder lige nu. En aften tæller kun med i hovedtallet, hvis DISSE
# zoner er clearet helt. Tog de Gruul/Magtheridon med oveni, tæller den tid med
# i aftenens vægur — men de kvalificerer ikke en aften på egen hånd.
# Når T6 åbner, er det her linjen, der skal ændres.
CURRENT_TIER = "T5"
CURRENT_TIER_ZONES = ["Serpentshrine Cavern", "The Eye"]

# Forrige tier bruges til én linje på siden: "det her tog X uger at lære, og
# endte som en farm-clear på Y". Det er beviset for, hvad T5 ender med at blive.
PREVIOUS_TIER = "T4"
PREVIOUS_TIER_ZONES = ["Karazhan"]

# Rullende vindue for hovedtallet: guilden bliver hurtigere, og et tal fra maj
# beskriver ikke den raid, en ansøger møder i august.
WINDOW_DAYS = 56  # 8 uger
# ... men et vindue med to aftener i er en tilfældighed, ikke en median. Er der
# færre end så mange, udvides vinduet, indtil der er nok. Siden fortæller selv,
# hvor mange aftener tallet står på.
MIN_NIGHTS = 4


GUILD_REPORTS_QUERY = """
query GuildReports($guildID: Int!, $page: Int!) {
  reportData {
    reports(guildID: $guildID, limit: 25, page: $page) {
      current_page
      has_more_pages
      data {
        code
        title
        startTime
        endTime
        zone { id name }
      }
    }
  }
}
"""

REPORT_FIGHTS_QUERY = """
query ReportFights($code: String!) {
  reportData {
    report(code: $code) {
      code
      title
      startTime
      endTime
      fights {
        id
        name
        encounterID
        kill
        size
        difficulty
        startTime
        endTime
        gameZone { id name }
        friendlyPlayers
      }
    }
  }
}
"""


def fetch_reports(client: WCLClient, max_pages: int = 4) -> list[dict]:
    """Guildens reports plus de manuelt tilføjede, nyeste først.

    Koderne i EXTRA_REPORT_CODES tilføjes kun, hvis de ikke allerede kom med
    fra guilden — bliver en rapport senere knyttet til guilden på WCL, skal
    den ikke tælles to gange.
    """
    reports: list[dict] = []
    page = 1
    while page <= max_pages:
        data = client.query(GUILD_REPORTS_QUERY, {"guildID": GUILD_ID, "page": page})
        block = data["reportData"]["reports"]
        reports.extend(block["data"])
        if not block["has_more_pages"]:
            break
        page += 1

    kendte = {r["code"] for r in reports}
    for code in EXTRA_REPORT_CODES:
        if code in kendte:
            print(f"  {code} er nu paa guilden — fjern den fra EXTRA_REPORT_CODES")
            continue
        reports.append({"code": code})
        print(f"  + manuelt tilfoejet log: {code}")

    return reports


def analyse_report(report: dict) -> dict | None:
    """Regn vægur-tider ud for én raidaften.

    Returnerer både tiden pr. zone og den samlede tid for aftenen.
    Fights' start/end er millisekunder RELATIVT til report.startTime —
    det er derfor tallene bliver små og pæne uden omregning.
    """
    fights = report.get("fights") or []
    if not fights:
        return None

    zones: dict[str, dict] = defaultdict(
        lambda: {"first_pull": None, "last_kill": None, "bosses": [], "raid_size": 0}
    )

    for fight in fights:
        zone_name = (fight.get("gameZone") or {}).get("name")
        if not zone_name:
            continue  # fights uden zone kan ikke placeres — springes over
        zone = zones[zone_name]

        # Første pull = tidligste fight i zonen, trash tæller med.
        if zone["first_pull"] is None or fight["startTime"] < zone["first_pull"]:
            zone["first_pull"] = fight["startTime"]

        if fight.get("kill") and fight.get("encounterID"):
            # Kun rigtige bosskills flytter sluttidspunktet — en wipe til
            # sidst afslutter ikke en clear.
            if zone["last_kill"] is None or fight["endTime"] > zone["last_kill"]:
                zone["last_kill"] = fight["endTime"]
            zone["bosses"].append(
                {
                    "name": fight.get("name"),
                    "duration_ms": fight["endTime"] - fight["startTime"],
                }
            )
            # Raidstørrelse aflæses på bosskills — trash har ofte skæve tal.
            players = fight.get("friendlyPlayers") or []
            zone["raid_size"] = max(zone["raid_size"], len(players))

    cleared_zones = []
    for name, zone in zones.items():
        if zone["last_kill"] is None:
            continue  # ingen kills i zonen — ikke en clear, kun forsøg
        expected = ZONES.get(name, {}).get("bosses")
        kills = len(zone["bosses"])
        cleared_zones.append(
            {
                "zone": name,
                "tier": ZONES.get(name, {}).get("tier"),
                "wallclock_ms": zone["last_kill"] - zone["first_pull"],
                "boss_time_ms": sum(b["duration_ms"] for b in zone["bosses"]),
                "boss_kills": kills,
                "boss_count_expected": expected,
                # En delvis clear er ikke sammenlignelig med en fuld. Vi regner
                # stadig tiden ud, men markerer den, så den kan holdes udenfor.
                "complete": expected is not None and kills >= expected,
                "raid_size": zone["raid_size"],
                "bosses": zone["bosses"],
            }
        )

    if not cleared_zones:
        return None

    session_start = min(f["startTime"] for f in fights)
    session_end = max(
        f["endTime"] for f in fights if f.get("kill") and f.get("encounterID")
    )

    started_at = datetime.fromtimestamp(report["startTime"] / 1000, tz=timezone.utc)

    return {
        "code": report["code"],
        "title": report.get("title"),
        "date": started_at.strftime("%d-%m-%Y"),
        # ISO ved siden af den danske dato: den ene er til øjne, den anden til
        # sortering og vinduesberegning. Aldrig parse den danske tilbage.
        "started_at": started_at.isoformat(timespec="seconds"),
        # HOVEDTALLET: første pull overhovedet -> sidste kill overhovedet.
        # Tiden mellem zonerne (summons, flyveture, pauser) tæller MED — det er
        # rigtig raidtid for den, der sidder ved skærmen og venter på at logge af.
        "session_wallclock_ms": session_end - session_start,
        # Summen af zonerne hver for sig. Mindre end ovenstående, netop fordi
        # mellemrummene falder ud. Bruges til opdelingen, ikke til hovedtallet.
        "zones_wallclock_ms": sum(z["wallclock_ms"] for z in cleared_zones),
        "raid_size": max(z["raid_size"] for z in cleared_zones),
        "boss_kills": sum(z["boss_kills"] for z in cleared_zones),
        # Én ufuldstændig zone gør hele aftenen ufuldstændig — tiden er så for lav.
        "complete": all(z["complete"] for z in cleared_zones),
        # Kvalifikationen til hovedtallet: er HELE den nuværende tier ryddet?
        # En Gruul-only aften på 7 minutter er "complete", men ikke en clear.
        "full_tier_clear": all(
            any(z["zone"] == name and z["complete"] for z in cleared_zones)
            for name in CURRENT_TIER_ZONES
        ),
        "zones": sorted(cleared_zones, key=lambda z: -z["wallclock_ms"]),
    }


def format_duration(ms: int) -> str:
    """2h24 — kort, uden sekunder. Sekunder er støj på et hovedtal.

    Engelsk "h", ikke dansk "t": Omen er en international guild, og alt
    brugervendt på siden er engelsk. Kommentarer og konsoloutput er stadig
    dansk — de er til Simon, ikke til besøgende.
    """
    minutes = round(ms / 60000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}" if hours else f"{minutes} min"


def deduplicate_nights(raids: list[dict]) -> list[dict]:
    """Én aften = én log.

    To raidere uploader tit hver sin log af samme raid — 21-05-2026 findes fx
    som både `9gtD4HbB` (3t35) og `ZG6gRQLD` (3t25). Uden det her tæller aftenen
    dobbelt i medianen.

    Vi beholder den log, der så mest af aftenen: flest kills, og ved lige mange
    den længste vægur. Den, der startede loggen sent, har det pænere tal — og
    netop derfor er det ikke den, vi må vælge.
    """
    by_date: dict[str, list[dict]] = defaultdict(list)
    for raid in raids:
        by_date[raid["date"]].append(raid)

    return [
        max(same_night, key=lambda r: (r["boss_kills"], r["session_wallclock_ms"]))
        for same_night in by_date.values()
    ]


def select_window(nights: list[dict]) -> tuple[list[dict], int]:
    """De aftener, hovedtallet regnes på. Nyeste først ind.

    Returnerer (aftener, faktisk_vinduesbredde_i_dage) — bredden kan være større
    end WINDOW_DAYS, hvis der ikke var aftener nok. Siden skal kunne sige det højt.
    """
    ordered = sorted(nights, key=lambda r: r["started_at"], reverse=True)
    if not ordered:
        return [], WINDOW_DAYS

    # Ankeret er NU, ikke nyeste log. Ellers ville en guild på tre måneders pause
    # stadig få tallet præsenteret som "seneste 8 uger".
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WINDOW_DAYS)
    inside = [r for r in ordered if datetime.fromisoformat(r["started_at"]) >= cutoff]

    if len(inside) < MIN_NIGHTS:
        inside = ordered[:MIN_NIGHTS]

    if len(inside) > 1:
        oldest = datetime.fromisoformat(inside[-1]["started_at"])
        span = max(WINDOW_DAYS, (now - oldest).days)
    else:
        span = WINDOW_DAYS
    return inside, span


def build_progression(nights: list[dict]) -> dict:
    """Hvor langt er de nået pr. zone — "10/10 T5".

    Progression er "nogensinde dræbt", ikke "dræbt i går". Én boss talt én gang,
    uanset hvor mange gange han er lagt ned, derfor et set af navne.
    """
    killed: dict[str, set[str]] = defaultdict(set)
    for night in nights:
        for zone in night["zones"]:
            killed[zone["zone"]].update(b["name"] for b in zone["bosses"])

    zones = {}
    for name, config in ZONES.items():
        zones[name] = {
            "tier": config["tier"],
            "killed": len(killed.get(name, ())),
            "total": config["bosses"],
        }

    def tier_line(tier_zones: list[str]) -> dict:
        return {
            "killed": sum(zones[z]["killed"] for z in tier_zones),
            "total": sum(zones[z]["total"] for z in tier_zones),
        }

    return {
        "zones": zones,
        CURRENT_TIER: tier_line(CURRENT_TIER_ZONES),
        PREVIOUS_TIER: tier_line(PREVIOUS_TIER_ZONES),
    }


def summarise_tier(nights: list[dict], tier_zones: list[str]) -> dict | None:
    """Median og rekord for en tier, der ikke er den nuværende.

    Bruges til forrige-tier-linjen. Ingen vindue her — hele tieren er forbi,
    så det er hele historikken, der er det interessante.
    """
    qualified = [
        n
        for n in nights
        if all(
            any(z["zone"] == name and z["complete"] for z in n["zones"])
            for name in tier_zones
        )
    ]
    if not qualified:
        return None

    wallclocks = [n["session_wallclock_ms"] for n in qualified]
    median = int(statistics.median(wallclocks))
    return {
        "clear_count": len(qualified),
        "median_wallclock_ms": median,
        "median_display": format_duration(median),
        "best_display": format_duration(min(wallclocks)),
    }


def build_dataset(raids: list[dict]) -> dict:
    """Saml det, siden skal bruge. Median — ikke rekord.

    Hovedtallet står kun på aftener, hvor hele den nuværende tier blev clearet.
    En aften, hvor Al'ar mangler, er hurtigere af den forkerte grund, og at lade
    den trække medianen ned ville være præcis den overselling, siden ikke må kunne.
    """
    nights = deduplicate_nights(raids)
    # Nyeste først — både days_since_last_full_clear og vinduet regner på det.
    qualified = sorted(
        (r for r in nights if r["full_tier_clear"]),
        key=lambda r: r["started_at"],
        reverse=True,
    )
    window, span_days = select_window(qualified)

    wallclocks = [r["session_wallclock_ms"] for r in window]
    median = int(statistics.median(wallclocks)) if wallclocks else 0
    best = min(wallclocks) if wallclocks else 0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "guild": {"name": "Omen", "realm": "Spineshatter", "region": "EU", "id": GUILD_ID},
        "summary": {
            "tier": CURRENT_TIER,
            "tier_zones": CURRENT_TIER_ZONES,
            # Hvad tallet står på. Siden må gerne skrive "median over 5 aftener"
            # — det er stærkere end et nøgent tal, ikke svagere.
            "nights_in_window": len(window),
            "window_days": span_days,
            "window_extended": span_days > WINDOW_DAYS,
            "median_wallclock_ms": median,
            "median_display": format_duration(median),
            # Rekorden er en sidelinje. Den må aldrig være hovedtallet.
            "best_wallclock_ms": best,
            "best_display": format_duration(best),
            # Til gennemsigtighed: hvor meget blev sorteret fra, og hvorfor.
            "report_count": len(raids),
            "night_count": len(nights),
            "full_clear_count": len(qualified),
            # Hvor gammelt er tallet? Bliver det stort, er enten guilden gået i
            # stå, eller også er logging-disciplinen skredet. Begge dele skal ses.
            "days_since_last_full_clear": (
                (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(qualified[0]["started_at"])
                ).days
                if qualified
                else None
            ),
        },
        "progression": build_progression(nights),
        "previous_tier": {
            "tier": PREVIOUS_TIER,
            "zones": PREVIOUS_TIER_ZONES,
            **(summarise_tier(nights, PREVIOUS_TIER_ZONES) or {}),
        },
        # Kun de aftener, hovedtallet står på — i den rækkefølge, de skal vises.
        "window": [r["code"] for r in window],
        "raids": sorted(nights, key=lambda r: r["started_at"], reverse=True),
    }


INDEX_PATH = OUTPUT_PATH.parent / "index.html"


def update_og_tags(dataset: dict) -> bool:
    """Skriv hovedtallet ind i index.html's OG-tags.

    Discords link-preview ER sidens reelle forside — folk ser den, før de ser
    siden. Men Discord læser den statiske HTML og kører ikke vores JavaScript,
    så tallet dér skal skrives ind på forhånd. Uden det her ville previewet
    fastfryse ved det tal, der stod, da filen blev skrevet i hånden.

    Returnerer True, hvis filen faktisk blev ændret.
    """
    if not INDEX_PATH.exists():
        return False

    summary = dataset["summary"]
    progress = dataset["progression"][summary["tier"]]

    title = (
        f"Omen · Spineshatter — {progress['killed']}/{progress['total']} "
        f"{summary['tier']}, median clear {summary['median_display']}"
    )
    description = (
        "Thursdays 20:30-23:30 server time. Wall-clock clear time from first "
        "pull to last boss, trash and wipes included. Median across "
        f"{summary['nights_in_window']} raid nights — never the record."
    )

    html = original = INDEX_PATH.read_text(encoding="utf-8")
    for prop, value in (("og:title", title), ("og:description", description)):
        # Kun content-attributten på præcis den ene meta-tag røres.
        html = re.sub(
            rf'(<meta property="{prop}" content=")[^"]*(">)',
            lambda m: m.group(1) + value.replace("\\", "\\\\") + m.group(2),
            html,
            count=1,
        )

    if html == original:
        return False
    INDEX_PATH.write_text(html, encoding="utf-8")
    return True


def unchanged(dataset: dict, path: pathlib.Path) -> bool:
    """Er alt bortset fra tidsstemplet det samme som i filen paa disken?"""
    if not path.exists():
        return False
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False  # ulaeselig fil skal skrives om, ikke bevares

    return {k: v for k, v in existing.items() if k != "generated_at"} == {
        k: v for k, v in dataset.items() if k != "generated_at"
    }


def main() -> int:
    # Windows-terminaler er cp1252 som standard, og så knækker "·" og "æøå".
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Tjek forbindelsen og dump én report rå, så vi kan se de faktiske felter",
    )
    args = parser.parse_args()

    try:
        client = WCLClient()
        print(f"Rate limit: {client.rate_limit()}")

        reports = fetch_reports(client)
        print(f"Fandt {len(reports)} reports for guild {GUILD_ID}")

        if args.probe:
            newest = reports[0]
            print(f"\nNyeste report: {newest['code']} — {newest.get('title')}")
            data = client.query(REPORT_FIGHTS_QUERY, {"code": newest["code"]})
            full = data["reportData"]["report"]
            print(json.dumps(analyse_report(full), indent=2, ensure_ascii=False))
            return 0

        raids = []
        for report in reports:
            data = client.query(REPORT_FIGHTS_QUERY, {"code": report["code"]})
            analysed = analyse_report(data["reportData"]["report"])
            if analysed:
                raids.append(analysed)
                missing = [
                    f"{z['zone']} {z['boss_kills']}/{z['boss_count_expected']}"
                    for z in analysed["zones"]
                    if not z["complete"]
                ]
                mark = "   [delvis: " + ", ".join(missing) + "]" if missing else ""
                print(
                    f"  {analysed['date']}  {format_duration(analysed['session_wallclock_ms'])}"
                    f"  ·  {analysed['raid_size']} mand"
                    f"  ·  {analysed['boss_kills']} bosser{mark}"
                )

        if not raids:
            print("Ingen raids med kills fundet — noget er galt.")
            return 1

        dataset = build_dataset(raids)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

        # generated_at aendrer sig ved HVER koersel. Skrev vi ubetinget, ville
        # cron-jobbet lave en commit hver fredag, ogsaa i uger hvor intet er
        # sket — og saa druknede de rigtige aendringer i stoej. Derfor
        # sammenlignes alt UNDTAGEN tidsstemplet.
        if unchanged(dataset, OUTPUT_PATH):
            print("\nTallene er uaendrede — data.json roeres ikke.")
            # OG-tags tjekkes alligevel: de kan vaere bagud, hvis nogen har
            # redigeret index.html i haanden.
            if update_og_tags(dataset):
                print("OG-tags i index.html rettet ind efter tallene.")
            return 0

        OUTPUT_PATH.write_text(
            json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if update_og_tags(dataset):
            print("OG-tags i index.html opdateret.")
        # "11/10" betyder ikke en overpræstation, det betyder at ZONES lyver.
        for zone_name, progress in dataset["progression"]["zones"].items():
            if progress["killed"] > progress["total"]:
                print(
                    f"ADVARSEL: {zone_name} viser {progress['killed']}/{progress['total']}"
                    f" — bossantallet i ZONES er for lavt.",
                    file=sys.stderr,
                )

        summary = dataset["summary"]
        print(
            f"\n{summary['report_count']} reports -> {summary['night_count']} aftener"
            f" -> {summary['full_clear_count']} fulde {summary['tier']}-clears"
        )
        print(
            f"HOVEDTAL: {summary['median_display']}"
            f"  (median over {summary['nights_in_window']} aftener,"
            f" seneste {summary['window_days']} dage"
            f"{', vindue udvidet' if summary['window_extended'] else ''})"
        )
        print(f"Rekord: {summary['best_display']}   ->  Skrev {OUTPUT_PATH}")
        return 0

    except WCLError as error:
        print(f"FEJL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

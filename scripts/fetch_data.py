"""Henter Omens raids fra WarcraftLogs og skriver site/data.json.

Hovedtallet, hele siden bygger på, er **vægur-clear-tid**: fra første pull
i en zone til sidste boss i zonen er død — inkl. trash og wipes.

WarcraftLogs regner allerede det tal pr. zone (det er "1:12:35"-tallet på
en report), men lægger aldrig zonerne sammen. Sammenlægningen på tværs af
zoner til ét ugetal er sidets egentlige opfindelse, og den sker her.

Kørsel:
    python scripts/fetch_data.py --probe     # tjek forbindelse + se én report rå
    python scripts/fetch_data.py             # byg site/data.json
"""

import argparse
import json
import pathlib
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv

from wcl import WCLClient, WCLError

GUILD_ID = 809023  # Omen, EU-Spineshatter, på fresh.warcraftlogs.com

# Hvor data.json lander. Siden læser den herfra.
OUTPUT_PATH = pathlib.Path(__file__).parent.parent / "site" / "data.json"

# Zoner grupperet i tiers. Navnene bekræftes mod rigtige data ved --probe;
# gameZone-id'er indsættes, når vi har set dem.
TIERS = {
    "T5": ["Serpentshrine Cavern", "Tempest Keep", "Gruul's Lair", "Magtheridon's Lair"],
    "T4": ["Karazhan", "Gruul's Lair", "Magtheridon's Lair"],
}


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
    """Alle guildens reports, nyeste først."""
    reports: list[dict] = []
    page = 1
    while page <= max_pages:
        data = client.query(GUILD_REPORTS_QUERY, {"guildID": GUILD_ID, "page": page})
        block = data["reportData"]["reports"]
        reports.extend(block["data"])
        if not block["has_more_pages"]:
            break
        page += 1
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
        cleared_zones.append(
            {
                "zone": name,
                "wallclock_ms": zone["last_kill"] - zone["first_pull"],
                "boss_time_ms": sum(b["duration_ms"] for b in zone["bosses"]),
                "boss_kills": len(zone["bosses"]),
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

    return {
        "code": report["code"],
        "title": report.get("title"),
        "date": datetime.fromtimestamp(
            report["startTime"] / 1000, tz=timezone.utc
        ).strftime("%d-%m-%Y"),
        "session_wallclock_ms": session_end - session_start,
        "zones_wallclock_ms": sum(z["wallclock_ms"] for z in cleared_zones),
        "raid_size": max(z["raid_size"] for z in cleared_zones),
        "zones": sorted(cleared_zones, key=lambda z: -z["wallclock_ms"]),
    }


def format_duration(ms: int) -> str:
    """2t24 — dansk, kort, uden sekunder. Sekunder er støj på et hovedtal."""
    minutes = round(ms / 60000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}t{minutes:02d}" if hours else f"{minutes} min"


def build_dataset(raids: list[dict]) -> dict:
    """Saml det, siden skal bruge. Median — ikke rekord."""
    wallclocks = [r["zones_wallclock_ms"] for r in raids]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "guild": {"name": "Omen", "realm": "Spineshatter", "region": "EU", "id": GUILD_ID},
        "summary": {
            "raid_count": len(raids),
            "median_wallclock_ms": int(statistics.median(wallclocks)),
            "median_display": format_duration(int(statistics.median(wallclocks))),
            "best_wallclock_ms": min(wallclocks),
            "best_display": format_duration(min(wallclocks)),
        },
        "raids": raids,
    }


def main() -> int:
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
                print(
                    f"  {analysed['date']}  {format_duration(analysed['zones_wallclock_ms'])}"
                    f"  ·  {analysed['raid_size']} mand  ·  {analysed['title']}"
                )

        if not raids:
            print("Ingen raids med kills fundet — noget er galt.")
            return 1

        dataset = build_dataset(raids)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(
            json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nSkrev {OUTPUT_PATH} — median {dataset['summary']['median_display']}")
        return 0

    except WCLError as error:
        print(f"FEJL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

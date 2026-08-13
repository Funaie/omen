"""Tager screenshots af site/ — udviklingsværktøj, ikke en del af produktet.

Siden henter data.json med fetch(), og det blokerer browseren på file://.
Derfor starter scriptet en rigtig HTTP-server på en ledig port og skyder
derfra. Billederne lander i .shots/ (gitignored).

Kørsel:
    .venv/Scripts/python.exe scripts/shot.py            # desktop + mobil
    .venv/Scripts/python.exe scripts/shot.py --wide     # kun desktop
"""

import argparse
import contextlib
import functools
import http.server
import pathlib
import socket
import socketserver
import threading

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).parent.parent
SITE_DIR = ROOT / "site"
SHOT_DIR = ROOT / ".shots"

# Bredde, højde, navn. Højden er kun viewport — der skydes full page.
VIEWPORTS = {
    "desktop": (1440, 900),
    "mobil": (390, 844),
}


@contextlib.contextmanager
def serve(directory: pathlib.Path):
    """Statisk HTTP-server på en ledig port, kun så længe blokken kører."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    # allow_reuse_address: ellers hænger porten i TIME_WAIT mellem kørsler.
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wide", action="store_true", help="kun desktop")
    parser.add_argument("--page", default="index.html", help="hvilken fil (default index.html)")
    args = parser.parse_args()

    if not (SITE_DIR / args.page).exists():
        print(f"Findes ikke: {SITE_DIR / args.page}")
        return 1

    SHOT_DIR.mkdir(exist_ok=True)
    targets = {"desktop": VIEWPORTS["desktop"]} if args.wide else VIEWPORTS

    with serve(SITE_DIR) as base_url, sync_playwright() as p:
        browser = p.chromium.launch()
        for name, (width, height) in targets.items():
            page = browser.new_page(viewport={"width": width, "height": height})

            # Fejl i konsollen er tit forklaringen på et tomt eller skævt layout,
            # så de samles op og printes frem for at forsvinde i browseren.
            problems: list[str] = []
            page.on("console", lambda m: m.type == "error" and problems.append(m.text))
            page.on("pageerror", lambda e: problems.append(str(e)))

            page.goto(f"{base_url}/{args.page}", wait_until="networkidle")
            out = SHOT_DIR / f"{name}.png"
            page.screenshot(path=str(out), full_page=True)
            print(f"{out}  ({width}x{height})")
            for problem in problems:
                print(f"   FEJL I KONSOLLEN: {problem}")
            page.close()
        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Öffnet die Streamlit-App in einem Headless-Browser, damit Streamlit Community Cloud sie nicht schlafen legt.

Schläft die App schon, wird «Yes, get this app back up!» geklickt und gewartet, bis sie wieder läuft.
Bricht mit Fehler ab (GitHub schickt dann eine E-Mail), wenn die App nicht aufwacht.
"""
from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL", "").strip()
WECK_TEXT = "get this app back up"


def schlaeft(page) -> bool:
    return page.get_by_text(WECK_TEXT, exact=False).count() > 0


def main() -> int:
    if not URL.startswith("http"):
        print("APP_URL fehlt: in GitHub unter Settings → Secrets and variables → Actions → Variables eintragen.")
        return 1
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=90_000)
        time.sleep(10)
        if schlaeft(page):
            print("App schläft – wecke sie auf …")
            page.get_by_text(WECK_TEXT, exact=False).first.click()
            for _ in range(36):  # bis 6 Minuten warten
                time.sleep(10)
                if not schlaeft(page):
                    break
            if schlaeft(page):
                print("App ist nicht aufgewacht.")
                browser.close()
                return 1
            print("App wurde aufgeweckt.")
        else:
            print("App ist wach.")
        # Ein Moment auf der Seite bleiben, damit die Verbindung zur App als Besuch zählt
        try:
            page.wait_for_load_state("networkidle", timeout=60_000)
        except PWTimeout:
            pass
        time.sleep(30)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Scrapes WWIN Liga BiH odds from meridianbet.ba.

Usage:
    python meridian_kvote.py            # headless (no window)
    python meridian_kvote.py --vidljivo # visible browser (for testing)

For the bots, call preuzmi_kvote(): it returns a list of dicts with odds
as float (or None when an odd isn't available). find_match_odds() picks
the row for one match by team names.
"""

import csv
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

URL = "https://meridianbet.ba/sr/kladjenje/fudbal/bih/premier-liga"
OUTPUT = Path("meridian_kvote.csv").resolve()
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# Runs inside the page: reads every match row and its odds cells from the rendered HTML
EXTRACT = """
() => {
    const text = (root, selector) =>
        root.querySelector(selector)?.innerText.trim() || "";

    return Array.from(document.querySelectorAll(".c-event"))
        .filter(row => row.querySelector(".c-event__rivals--home"))
        .map(row => ({
            home: text(row, ".c-event__rivals--home"),
            away: text(row, ".c-event__rivals--away"),
            day: text(row, ".c-event__period-min"),
            time: text(row, ".c-event__period-time"),
            url: row.querySelector("a.c-event-action__bottom")?.href || "",
            groups: Array.from(row.querySelectorAll(".c-event__game")).map(
                group => Array.from(group.querySelectorAll(".c-selection")).map(
                    cell => ({
                        title: cell.getAttribute("title") || "",
                        value: cell.innerText.trim(),
                        limit: cell.classList.contains("c-selection--limit"),
                        classes: cell.className
                    })
                )
            )
        }));
}
"""

LOCKED = re.compile(r"lock|disable|suspend|inactive", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def kvota(cell):
    """Returns the odd as float; locked or unavailable odds return None."""
    if LOCKED.search(cell["classes"]):
        return None
    value = cell["value"].replace(",", ".").strip()
    return float(value) if NUMBER.fullmatch(value) else None


def obradi(event, preuzeto):
    row = {
        "Preuzeto": preuzeto,
        "Domacin": event["home"],
        "Gost": event["away"],
        "Dan": event["day"],
        "Vrijeme_Sarajevo": event["time"],
        "1": None,
        "X": None,
        "2": None,
        "Granica_golova": None,
        "Manje_golova": None,
        "Vise_golova": None,
        "URL": event["url"],
    }
    home = event["home"].casefold()
    away = event["away"].casefold()

    for group in event["groups"]:
        cells = [c for c in group if not c["limit"]]
        titles = [c["title"].casefold() for c in cells]

        # 1X2
        x_idx = next(
            (i for i, t in enumerate(titles) if "nerešen ishod na meču" in t),
            None,
        )
        if x_idx is not None:
            row["X"] = kvota(cells[x_idx])
            if len(cells) == 3 and x_idx == 1:
                # standard 1 | X | 2 layout – doesn't depend on the exact club name
                row["1"] = kvota(cells[0])
                row["2"] = kvota(cells[2])
            else:
                for cell, t in zip(cells, titles):
                    if "pobeđuje na meču" in t:
                        if t.startswith(home + " "):
                            row["1"] = kvota(cell)
                        elif t.startswith(away + " "):
                            row["2"] = kvota(cell)

        # total goals under / over
        limits = [c for c in group if c["limit"]]
        under = [c for c, t in zip(cells, titles) if "ukupno golova na meču manje" in t]
        over = [c for c, t in zip(cells, titles) if "ukupno golova na meču više" in t]
        if len(limits) == len(under) == len(over) == 1:
            row["Granica_golova"] = kvota(limits[0])
            row["Manje_golova"] = kvota(under[0])
            row["Vise_golova"] = kvota(over[0])

    return row


def ucitaj_sve(page):
    """Scrolls until the number of matches stops growing (lazy loading)."""
    prethodno = -1
    for _ in range(15):
        broj = page.locator(".c-event__rivals--home").count()
        if broj == prethodno:
            break
        prethodno = broj
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(700)


def preuzmi_kvote(headless=True):
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1500, "height": 1000},
            timezone_id="Europe/Sarajevo",
            locale="sr-RS",
            user_agent=USER_AGENT,
        )
        page = context.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_selector(".c-event__rivals--home", timeout=30000)
                page.wait_for_selector(".c-event__game .c-selection", timeout=15000)
            except PWTimeout:
                # show what the page actually returned (blocked, empty, changed layout...)
                print(f"Meridian: utakmice se nisu pojavile. URL: {page.url}")
                print(f"Naslov stranice: {page.title()!r}")
                body = page.locator("body").inner_text(timeout=5000)[:600].replace("\n", " | ")
                print(f"Tekst stranice: {body!r}")
                page.screenshot(path="meridian_debug.png", full_page=False)
                return []
            page.wait_for_timeout(1500)  # let all odds fill in
            ucitaj_sve(page)
            events = page.evaluate(EXTRACT)
        finally:
            context.close()
            browser.close()

    preuzeto = datetime.now().astimezone().isoformat(timespec="seconds")
    return [obradi(e, preuzeto) for e in events]


# ---------- matching a FotMob match to a Meridian row ----------
def _norm(name):
    """'HŠK Zrinjski Mostar' -> {'zrinjski', 'mostar'} (no diacritics, no club prefixes)."""
    s = unicodedata.normalize("NFKD", name.replace("đ", "dj").replace("Đ", "Dj"))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    words = re.findall(r"[a-z0-9]+", s)
    return {w for w in words if w not in {"fk", "nk", "hsk", "hnk", "sk", "fc"}}


# city words alone don't identify a club (FK Sarajevo vs Željezničar Sarajevo, Borac vs BSK Banja Luka)
CITIES = {"sarajevo", "mostar", "banja", "luka", "zenica", "doboj", "bijeljina"}


def _same_team(a, b):
    na, nb = _norm(a) - CITIES, _norm(b) - CITIES
    if na and nb:
        return bool(na & nb)
    return _norm(a) == _norm(b)          # e.g. "FK Sarajevo" vs "Sarajevo"


def find_match_odds(rows, home, away):
    """Returns the Meridian row for home – away (FotMob names), or None."""
    for r in rows:
        if _same_team(r["Domacin"], home) and _same_team(r["Gost"], away):
            return r
    return None


# ---------- standalone use ----------
def za_excel(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}".replace(".", ",")
    return v


def sacuvaj_csv(rows, path=OUTPUT):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: za_excel(v) for k, v in row.items()})


def main():
    headless = "--vidljivo" not in sys.argv
    print("Preuzimam kvote sa Meridiana...")
    rows = preuzmi_kvote(headless=headless)

    if not rows:
        print("Nisu pronadjene utakmice (stranica se nije ucitala ili je blokirana).")
        if headless:
            print("Probaj: python meridian_kvote.py --vidljivo")
        return

    if not any(row[k] for row in rows for k in ("1", "X", "2")):
        print("Utakmice su pronadjene, ali kvote nisu prepoznate. "
              "Moguce je da je struktura stranice promijenjena.")
        return

    for row in rows:
        print(f'{row["Dan"]} {row["Vrijeme_Sarajevo"]}  '
              f'{row["Domacin"]} - {row["Gost"]}: '
              f'1={row["1"]} | X={row["X"]} | 2={row["2"]}')

    sacuvaj_csv(rows)
    print(f"\nSacuvano {len(rows)} utakmica u {OUTPUT}")


if __name__ == "__main__":
    main()

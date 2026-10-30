"""
Test izvora – provjerava koji izvori podataka puštaju GitHub servere.
Pokreni preko GitHub Actions (workflow "Test izvora") i pošalji log.
"""
from curl_cffi import requests

IZVORI = {
    "SofaScore (app domena)": "https://api.sofascore.app/api/v1/team/5240/events/next/0",
    "SofaScore (web)": "https://www.sofascore.com/api/v1/team/5240/events/next/0",
    "FotMob API": "https://www.fotmob.com/api/teams?id=10108",
    "FotMob API (data)": "https://www.fotmob.com/api/data/teams?id=10108",
    "FotMob stranica": "https://www.fotmob.com/teams/10108/overview/zeljeznicar",
    "TheSportsDB naredne": "https://www.thesportsdb.com/api/v1/json/3/eventsnext.php?id=137934",
    "TheSportsDB zadnje": "https://www.thesportsdb.com/api/v1/json/3/eventslast.php?id=137934",
}
TRAZI = ["Zrinjski", "Zeljeznicar", "Željezničar", "__NEXT_DATA__"]

for ime, url in IZVORI.items():
    for nacin in ("chrome", None):
        try:
            r = requests.get(url, impersonate=nacin, timeout=30) if nacin else \
                requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            nadjeno = [t for t in TRAZI if t in r.text]
            print(f"{'✅' if r.status_code == 200 else '❌'} {ime} [{nacin or 'obican'}] "
                  f"status={r.status_code} duzina={len(r.text)} nadjeno={nadjeno}")
            if r.status_code == 200:
                print("   ", r.text[:200].replace("\n", " "))
        except Exception as e:
            print(f"❌ {ime} [{nacin or 'obican'}] greska: {e}")
    print()

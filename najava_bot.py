"""
Najava bot – FK Željezničar
Svako jutro u 08:00 (bh. vrijeme) objavi najavu naredne utakmice na Discord,
čim se utakmica pojavi na SofaScoreu. Podaci: SofaScore (bez registracije).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
TEAM_ID = 5240            # FK Željezničar
POST_HOUR = 8             # objava u 08:00 po bh. vremenu
# ručno pokretanje (GitHub "Run workflow" ili FORCE=1) objavljuje odmah
FORCE = os.environ.get("FORCE") == "1" or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
BASE = "https://www.sofascore.com/api/v1/"
STATE_FILE = Path("zadnja_najava.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}
FORM_EMOJI = {"W": "🟢", "D": "🟡", "L": "🔴"}
DANI = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]


def get(path):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None          # nema podataka (npr. kvote, glasanje)
        if e.code == 403:
            sys.exit("SofaScore je odbio zahtjev (403). Pokreni bota sa svog računara.")
        raise


# ---------- vrijeme u Sarajevu (CET/CEST) bez dodatnih paketa ----------
def _last_sunday(year, month):
    d = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def sarajevo_time(ts):
    utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    dst_start = _last_sunday(utc.year, 3).replace(hour=1)
    dst_end = _last_sunday(utc.year, 10).replace(hour=1)
    offset = 2 if dst_start <= utc < dst_end else 1
    return utc + timedelta(hours=offset)


# ---------- dijelovi najave ----------
def table_line(standings, team_id):
    for row in standings["standings"][0]["rows"]:
        if row["team"]["id"] == team_id:
            return (f"{row['position']}. mjesto, {row['points']} bodova "
                    f"({row['wins']}P {row['draws']}N {row['losses']}I, "
                    f"golovi {row['scoresFor']}:{row['scoresAgainst']})")
    return "-"


def full_table(standings, highlight_ids):
    """Cijela tabela u code bloku (Discord ne podržava prave tabele)."""
    lines = ["#  Klub            U  Gol    Bod"]
    for row in standings["standings"][0]["rows"]:
        name = row["team"]["shortName"][:14]
        mark = "▶" if row["team"]["id"] in highlight_ids else " "
        goals = f"{row['scoresFor']}:{row['scoresAgainst']}"
        lines.append(f"{row['position']:<2}{mark}{name:<15}{row['matches']:>2}  {goals:<6}{row['points']:>3}")
    return "```\n" + "\n".join(lines) + "\n```"


def form_line(form):
    return " ".join(FORM_EMOJI.get(f, f) for f in form) if form else "-"


def h2h_lines(h2h, limit=5):
    if not h2h or not h2h.get("events"):
        return "Nema podataka"
    played = [e for e in h2h["events"] if e.get("status", {}).get("type") == "finished"]
    played.sort(key=lambda e: e["startTimestamp"], reverse=True)
    lines = []
    for e in played[:limit]:
        date = sarajevo_time(e["startTimestamp"]).strftime("%d.%m.%Y")
        hs = e.get("homeScore", {}).get("current", "?")
        as_ = e.get("awayScore", {}).get("current", "?")
        lines.append(f"{date}: {e['homeTeam']['shortName']} {hs}:{as_} {e['awayTeam']['shortName']}")
    return "\n".join(lines) or "Nema podataka"


def votes_line(votes):
    v = (votes or {}).get("vote")
    if not v:
        return None
    total = v.get("vote1", 0) + v.get("voteX", 0) + v.get("vote2", 0)
    if not total:
        return None
    pct = lambda n: round(100 * n / total)
    return f"1: {pct(v['vote1'])}%  X: {pct(v['voteX'])}%  2: {pct(v['vote2'])}%  ({total} glasova)"


def build_embed(event, form, standings, h2h, votes):
    home, away = event["homeTeam"], event["awayTeam"]
    start = sarajevo_time(event["startTimestamp"])
    venue = home.get("venue", {})
    stadion = venue.get("name", "-")
    if venue.get("capacity"):
        stadion += f" ({venue['capacity']:,} mjesta)".replace(",", ".")

    fields = [
        {"name": "🗓️ Termin",
         "value": f"{DANI[start.weekday()]}, {start:%d.%m.%Y} u {start:%H:%M}", "inline": True},
        {"name": "🏟️ Stadion", "value": stadion, "inline": True},
        {"name": "🔢 Kolo", "value": str(event.get("roundInfo", {}).get("round", "-")), "inline": True},
        {"name": "👔 Treneri",
         "value": f"{home['shortName']}: {home.get('manager', {}).get('name', '-')}\n"
                  f"{away['shortName']}: {away.get('manager', {}).get('name', '-')}", "inline": False},
        {"name": "📊 Tabela",
         "value": f"{home['shortName']}: {table_line(standings, home['id'])}\n"
                  f"{away['shortName']}: {table_line(standings, away['id'])}", "inline": False},
        {"name": "🏆 Tabela lige",
         "value": full_table(standings, {home["id"], away["id"]}), "inline": False},
        {"name": "📈 Forma (zadnjih 5)",
         "value": f"{home['shortName']}: {form_line((form or {}).get('homeTeam', {}).get('form'))}\n"
                  f"{away['shortName']}: {form_line((form or {}).get('awayTeam', {}).get('form'))}",
         "inline": False},
        {"name": "⚔️ Međusobni dueli", "value": h2h_lines(h2h), "inline": False},
    ]
    vl = votes_line(votes)
    if vl:
        fields.append({"name": "🗳️ Navijači SofaScorea tipuju", "value": vl, "inline": False})

    return {
        "title": f"⚽ Najava: {home['name']} – {away['name']}",
        "url": f"https://www.sofascore.com/football/match/{event['slug']}/{event['customId']}",
        "color": 0x0066CC,
        "fields": fields,
        "footer": {"text": "WWIN liga BiH • podaci: SofaScore"},
    }


def post(embed):
    body = json.dumps({"embeds": [embed]}).encode()
    req = urllib.request.Request(WEBHOOK, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "najava-bot"})
    urllib.request.urlopen(req, timeout=30)


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    now_local = sarajevo_time(time.time())
    today = now_local.strftime("%Y-%m-%d")

    if not FORCE:
        if state.get("datum") == today:
            print(f"Najava za danas ({today}) je već objavljena.")
            return
        if now_local.hour < POST_HOUR:
            print(f"Sada je {now_local:%H:%M} u Sarajevu – objava tek od {POST_HOUR:02d}:00.")
            return

    events = (get(f"team/{TEAM_ID}/events/next/0") or {}).get("events", [])
    events = [e for e in events if e["startTimestamp"] > time.time()]
    if not events:
        print("Naredna utakmica još nije objavljena na SofaScoreu.")
        return
    event = min(events, key=lambda e: e["startTimestamp"])
    eid = event["id"]

    details = get(f"event/{eid}")["event"]          # treneri, stadion
    season = details["season"]["id"]
    tournament = details["tournament"]["uniqueTournament"]["id"]
    embed = build_embed(
        details,
        get(f"event/{eid}/pregame-form"),
        get(f"unique-tournament/{tournament}/season/{season}/standings/total"),
        get(f"event/{details['customId']}/h2h/events"),
        get(f"event/{eid}/votes"),
    )

    if not WEBHOOK:
        print(json.dumps(embed, indent=2, ensure_ascii=False))   # test bez Discorda
        return
    post(embed)
    STATE_FILE.write_text(json.dumps({"datum": today, "utakmica": eid}))
    print(f"Objavljena najava: {embed['title']}")


if __name__ == "__main__":
    main()

"""
Najava bot – FK Željezničar
Svako jutro u 08:00 (bh. vrijeme) objavi najavu naredne utakmice na Discord.
Podaci: FotMob (bez registracije, radi i s GitHub Actions).
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
TEAM_ID = 10108           # FK Željezničar na FotMobu
POST_HOUR = 8             # objava u 08:00 po bh. vremenu
# ručno pokretanje (GitHub "Run workflow" ili FORCE=1) objavljuje odmah
FORCE = os.environ.get("FORCE") == "1" or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
BASE = "https://www.fotmob.com"
STATE_FILE = Path("zadnja_najava.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}
FORM_EMOJI = {"W": "🟢", "D": "🟡", "L": "🔴"}
DANI = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]


def get(path, required=True):
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, ValueError) as e:
        if required:
            sys.exit(f"FotMob greška za {path}: {e}")
        return None


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


def parse_utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def short(name):
    return name[3:] if name.startswith("FK ") else name


# ---------- dijelovi najave ----------
def table_rows(team):
    try:
        return team["table"][0]["data"]["table"]["all"]
    except (KeyError, IndexError, TypeError):
        return []


def table_line(rows, team_id):
    for r in rows:
        if r["id"] == team_id:
            return (f"{r['idx']}. mjesto, {r['pts']} bodova "
                    f"({r['wins']}P {r['draws']}N {r['losses']}I, "
                    f"golovi {r['scoresStr'].replace('-', ':')})")
    return "-"


def full_table(rows, highlight_ids):
    """Cijela tabela u code bloku (Discord ne podržava prave tabele)."""
    lines = ["#  Klub              U  Gol    Bod"]
    for r in rows:
        name = short(r["name"])[:16]
        mark = "▶" if r["id"] in highlight_ids else " "
        goals = r["scoresStr"].replace("-", ":")
        lines.append(f"{r['idx']:<2}{mark}{name:<17}{r['played']:>2}  {goals:<6}{r['pts']:>3}")
    return "```\n" + "\n".join(lines) + "\n```"


def form_line(team, team_id):
    try:
        form = team["table"][0]["teamForm"][str(team_id)]
    except (KeyError, IndexError, TypeError):
        return "-"
    return " ".join(FORM_EMOJI.get(f["resultString"], "⚪") for f in form) or "-"


def stats_lines(match):
    nazivi = {"Goals per match": "Golovi po utakmici",
              "Goals conceded per match": "Primljeni golovi po utakmici"}
    lines = []
    for s in (match.get("stats") or {}).get("stats", []):
        if s.get("title") in nazivi and len(s.get("stats", [])) == 2:
            h, a = s["stats"]
            lines.append(f"{nazivi[s['title']]}: {h} – {a}")
    return "\n".join(lines)


def h2h_lines(match, team, limit=5):
    """Međusobni dueli: prvo iz detalja utakmice, inače iz ove sezone."""
    results = []
    details = get(f"/api/data/matchDetails?matchId={match['id']}", required=False)
    try:
        for m in details["content"]["h2h"]["matches"]:
            utc = (m.get("time") or {}).get("utcTime") or (m.get("status") or {}).get("utcTime")
            score = (m.get("status") or {}).get("scoreStr")
            if utc and score:
                results.append((parse_utc(utc), m["home"]["name"], score, m["away"]["name"]))
    except (KeyError, TypeError):
        results = []

    if not results:
        opp_ids = {match["home"]["id"], match["away"]["id"]}
        for f in team["fixtures"]["allFixtures"]["fixtures"]:
            st = f.get("status", {})
            if st.get("finished") and {f["home"]["id"], f["away"]["id"]} == opp_ids:
                results.append((parse_utc(st["utcTime"]), f["home"]["name"],
                                st["scoreStr"], f["away"]["name"]))

    results.sort(reverse=True)
    lines = [f"{sarajevo_time(ts):%d.%m.%Y}: {short(h)} {s.replace(' - ', ':')} {short(a)}"
             for ts, h, s, a in results[:limit]]
    return "\n".join(lines) or "Nema podataka"


TEZINA = {"easy": "lagan", "moderate": "srednji", "medium": "srednji",
          "hard": "težak", "tough": "težak", "brutal": "brutalan"}
TEZINA_MECA = {1: "lako", 2: "srednje", 3: "teško"}


def last_match_line(team):
    m = (team.get("overview") or {}).get("lastMatch")
    if not m or not (m.get("status") or {}).get("scoreStr"):
        return None
    date = sarajevo_time(parse_utc(m["status"]["utcTime"]))
    res = {1: "🟢", 0: "🟡", -1: "🔴"}.get(m.get("result"), "")
    score = m["status"]["scoreStr"].replace(" - ", ":")
    return f"{date:%d.%m.}: {short(m['home']['name'])} {score} {short(m['away']['name'])} {res}".strip()


def next_matches_lines(team, match, limit=3):
    fd = {f["matchId"]: f.get("difficulty")
          for f in ((team.get("overview") or {}).get("fixtureDifficulty") or {}).get("fixtures", [])}
    upcoming = [f for f in team["fixtures"]["allFixtures"]["fixtures"]
                if f.get("notStarted") and f["id"] != match["id"]]
    upcoming.sort(key=lambda f: f["status"]["utcTime"])
    lines = []
    for f in upcoming[:limit]:
        date = sarajevo_time(parse_utc(f["status"]["utcTime"]))
        home = f["home"]["id"] == TEAM_ID
        opp = short(f["away"]["name"] if home else f["home"]["name"])
        tez = TEZINA_MECA.get(fd.get(f["id"]))
        lines.append(f"{date:%d.%m.} {opp} ({'D' if home else 'G'})" + (f" – {tez}" if tez else ""))
    return "\n".join(lines)


def difficulty_line(team):
    fd = (team.get("overview") or {}).get("fixtureDifficulty") or {}
    if not fd.get("difficulty"):
        return None
    label = TEZINA.get(fd["difficulty"].lower(), fd["difficulty"])
    n = fd.get("difficultyMatches") or 3
    opps = [short(f["opponentName"]) for f in fd.get("fixtures", [])[:n]]
    return f"**{label.capitalize()}** – naredne {n}: " + ", ".join(opps)


def round_number(team, match):
    """Kolo = redni broj utakmice u ligi ove sezone (FotMob ga ne daje direktno)."""
    league = match.get("tournament", {}).get("leagueId")
    league_fx = [f for f in team["fixtures"]["allFixtures"]["fixtures"]
                 if f.get("tournament", {}).get("leagueId") == league]
    league_fx.sort(key=lambda f: f["status"]["utcTime"])
    for i, f in enumerate(league_fx, 1):
        if f["id"] == match["id"]:
            return str(i)
    return None


def venue_text(team, match):
    host = team
    if match["home"]["id"] != TEAM_ID:          # gostovanje – stadion domaćina
        host = get(f"/api/data/teams?id={match['home']['id']}", required=False) or {}
    v = (host.get("overview") or {}).get("venue") or {}
    name = (v.get("widget") or {}).get("name")
    if not name:
        return "-"
    cap = dict(v.get("statPairs") or []).get("Capacity")
    city = (v.get("widget") or {}).get("city")
    text = name + (f", {city}" if city else "")
    if cap:
        text += f" ({cap:,} mjesta)".replace(",", ".")
    return text


def build_embed(team, match):
    home, away = match["home"], match["away"]
    start = sarajevo_time(parse_utc(match["status"]["utcTime"]))
    rows = table_rows(team)
    hn, an = short(home["name"]), short(away["name"])
    competition = match.get("tournament", {}).get("name", "")
    if competition == "Premier League":
        competition = "WWIN liga BiH"

    fields = [
        {"name": "🗓️ Termin",
         "value": f"{DANI[start.weekday()]}, {start:%d.%m.%Y} u {start:%H:%M}", "inline": True},
        {"name": "🏟️ Stadion", "value": venue_text(team, match), "inline": True},
    ]
    kolo = round_number(team, match)
    if kolo:
        fields.append({"name": "🔢 Kolo", "value": kolo, "inline": True})
    if rows:
        fields += [
            {"name": "📊 Tabela",
             "value": f"{hn}: {table_line(rows, home['id'])}\n{an}: {table_line(rows, away['id'])}",
             "inline": False},
            {"name": "🏆 Tabela lige",
             "value": full_table(rows, {home["id"], away["id"]}), "inline": False},
            {"name": "📈 Forma (zadnjih 5, najnovija desno)",
             "value": f"{hn}: {form_line(team, home['id'])}\n{an}: {form_line(team, away['id'])}",
             "inline": False},
        ]
    sl = stats_lines(match)
    if sl:
        fields.append({"name": f"🎯 Statistika ({hn} – {an})", "value": sl, "inline": False})
    fields.append({"name": "⚔️ Međusobni dueli", "value": h2h_lines(match, team), "inline": False})

    lm = last_match_line(team)
    if lm:
        fields.append({"name": "🔙 Zadnja utakmica", "value": lm, "inline": False})
    nm = next_matches_lines(team, match)
    if nm:
        fields.append({"name": "📅 Naredne utakmice (D = doma, G = gosti)", "value": nm, "inline": False})
    dl = difficulty_line(team)
    if dl:
        fields.append({"name": "🧗 Težina rasporeda", "value": dl, "inline": False})

    return {
        "title": f"⚽ Najava: {home['name']} – {away['name']}",
        "url": BASE + match.get("pageUrl", ""),
        "color": 0x0066CC,
        "fields": fields,
        "footer": {"text": f"{competition} • podaci: FotMob"},
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

    team = get(f"/api/data/teams?id={TEAM_ID}")
    match = (team.get("overview") or {}).get("nextMatch")
    if not match or parse_utc(match["status"]["utcTime"]) < time.time():
        print("Naredna utakmica još nije objavljena na FotMobu.")
        return

    embed = build_embed(team, match)

    if not WEBHOOK:
        print(json.dumps(embed, indent=2, ensure_ascii=False))   # test bez Discorda
        return
    post(embed)
    STATE_FILE.write_text(json.dumps({"datum": today, "utakmica": match["id"]}))
    print(f"Objavljena najava: {embed['title']}")


if __name__ == "__main__":
    main()

"""
Match preview bot – FK Željezničar
1) Posts the RESULT of each match as soon as FotMob marks it finished.
2) Posts ONE preview per match: at 08:00 (Sarajevo time) on the first
   morning after the previous match.
Data: FotMob (no registration, works from GitHub Actions).
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
TEAM_ID = 10108           # FK Željezničar on FotMob
POST_HOUR = 8             # post at 08:00 Sarajevo time
MAX_WAIT_MIN = 60         # if started early, wait at most 60 min until 08:00
RESULT_MAX_AGE_H = 36     # post a result only if the match ended at most 36 h ago
# a manual run (GitHub "Run workflow" or FORCE=1) posts immediately;
# RUCNO chooses what to post: "najava", "rezultat" or "oboje"
MANUAL = os.environ.get("FORCE") == "1" or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
MANUAL_MODE = os.environ.get("RUCNO") or "najava"
# manual runs also skip anything already posted, unless PRISILNO=true
MANUAL_FORCE = (os.environ.get("PRISILNO") or "").lower() == "true"
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


# ---------- Sarajevo time (CET/CEST) without extra packages ----------
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


# ---------- preview sections ----------
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
    """Full table in a code block (Discord has no real tables)."""
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
    """Head-to-head: from match details first, otherwise from this season's fixtures."""
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


# FotMob difficulty labels -> Bosnian
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
    # difficulty per match (1–3) from FotMob's fixture difficulty widget
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
    """Round = match number in the league this season (FotMob doesn't provide it directly)."""
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
    if match["home"]["id"] != TEAM_ID:          # away match – use the home team's stadium
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


def next_match(team):
    match = (team.get("overview") or {}).get("nextMatch")
    if not match or parse_utc(match["status"]["utcTime"]) < time.time():
        return None
    return match


def match_today_not_finished(team, today):
    """True if Želja has a match today that hasn't finished yet (or one is live)."""
    if (team.get("overview") or {}).get("hasOngoingMatch"):
        return True
    for f in team["fixtures"]["allFixtures"]["fixtures"]:
        st = f.get("status", {})
        if st.get("cancelled") or st.get("finished"):
            continue
        if sarajevo_time(parse_utc(st["utcTime"])).strftime("%Y-%m-%d") == today:
            return True
    return False


# ---------- result post ----------
def scorers(match_id):
    """Goal scorers from FotMob match details; empty if not available."""
    details = get(f"/api/data/matchDetails?matchId={match_id}", required=False)
    out = {}
    try:
        events = details["header"]["events"]
        for side in ("homeTeamGoals", "awayTeamGoals"):
            goals = []
            for name, evs in (events.get(side) or {}).items():
                for ev in evs:
                    minute = ev.get("timeStr") or ev.get("time")
                    who = ev.get("fullName") or ev.get("nameStr") or name
                    extra = " (pen.)" if ev.get("isPenalty") or ev.get("goalDescription") == "Penalty" else ""
                    extra = " (a.g.)" if ev.get("ownGoal") else extra
                    goals.append((int(str(ev.get("time", 0)).split("+")[0] or 0), f"{who} {minute}'{extra}"))
            goals.sort()
            out[side] = [g for _, g in goals]
    except (KeyError, TypeError, AttributeError, ValueError):
        return {}
    return out


def league_played(team, league_id):
    return sum(1 for f in team["fixtures"]["allFixtures"]["fixtures"]
               if f.get("tournament", {}).get("leagueId") == league_id
               and f.get("status", {}).get("finished"))


def build_result_embed(team, m):
    home, away = m["home"], m["away"]
    score = m["status"]["scoreStr"].replace(" - ", ":")
    res = m.get("result")
    verdict = {1: "🟢 Pobjeda", 0: "🟡 Remi", -1: "🔴 Poraz"}.get(res, "")
    color = {1: 0x2ECC71, 0: 0xF1C40F, -1: 0xE74C3C}.get(res, 0x0066CC)
    start = sarajevo_time(parse_utc(m["status"]["utcTime"]))
    competition = m.get("tournament", {}).get("name", "")
    if competition == "Premier League":
        competition = "WWIN liga BiH"

    fields = []
    if verdict:
        fields.append({"name": "Ishod", "value": verdict, "inline": True})
    kolo = round_number(team, m)
    if kolo and competition == "WWIN liga BiH":
        fields.append({"name": "🔢 Kolo", "value": kolo, "inline": True})
    fields.append({"name": "🗓️ Odigrano", "value": f"{start:%d.%m.%Y}", "inline": True})

    sc = scorers(m["id"])
    if sc.get("homeTeamGoals") or sc.get("awayTeamGoals"):
        fields.append({"name": f"⚽ {short(home['name'])}",
                       "value": "\n".join(sc.get("homeTeamGoals") or []) or "-", "inline": True})
        fields.append({"name": f"⚽ {short(away['name'])}",
                       "value": "\n".join(sc.get("awayTeamGoals") or []) or "-", "inline": True})

    # only show the table if FotMob has already counted this match
    rows = table_rows(team)
    league = m.get("tournament", {}).get("leagueId")
    for r in rows:
        if r["id"] == TEAM_ID and r["played"] == league_played(team, league):
            fields.append({"name": "📊 Željezničar na tabeli",
                           "value": table_line(rows, TEAM_ID), "inline": False})

    return {
        "title": f"🏁 Kraj: {home['name']} {score} {away['name']}",
        "url": BASE + m.get("pageUrl", ""),
        "color": color,
        "fields": fields,
        "footer": {"text": f"{competition} • podaci: FotMob"},
    }


def send(embed, label):
    if not WEBHOOK:
        print(json.dumps(embed, indent=2, ensure_ascii=False))   # test without Discord
    else:
        post(embed)
        print(f"{label}: {embed['title']}")


def save(state):
    # written right after every post, so a later error can't cause a repost
    if WEBHOOK:
        STATE_FILE.write_text(json.dumps(state))


def do_result(team, state, now):
    """Post the result of the last match once, as soon as FotMob marks it finished."""
    m = (team.get("overview") or {}).get("lastMatch") or {}
    st = m.get("status") or {}
    if not st.get("finished") or not st.get("scoreStr") or st.get("cancelled"):
        return False
    if state.get("rezultat") == m["id"]:
        print("Rezultat zadnje utakmice je već objavljen.")
        return False
    # don't post old results (e.g. right after first setup)
    if now - parse_utc(st["utcTime"]) > RESULT_MAX_AGE_H * 3600:
        print("Zadnja utakmica je prestara za objavu rezultata.")
        state["rezultat"] = m["id"]
        save(state)          # remember it so we don't check again
        return True
    send(build_result_embed(team, m), "Objavljen rezultat")
    state["rezultat"] = m["id"]
    save(state)
    return True


def do_preview(team, state, now_local, today):
    """Post one preview per match, the morning after the previous match."""
    target = now_local.replace(hour=POST_HOUR, minute=0, second=0, microsecond=0)
    wait = (target - now_local).total_seconds()
    if wait > MAX_WAIT_MIN * 60:
        print(f"Sada je {now_local:%H:%M} u Sarajevu – najava tek u {POST_HOUR:02d}:00.")
        return False

    match = next_match(team)
    if not match:
        print("Naredna utakmica još nije objavljena na FotMobu.")
        return False
    if state.get("utakmica") == match["id"]:
        print(f"Najava za {match['home']['name']} – {match['away']['name']} je već objavljena.")
        return False
    # a match today that isn't over yet (or live) – no preview for the next one yet
    if match_today_not_finished(team, today) and \
            sarajevo_time(parse_utc(match["status"]["utcTime"])).strftime("%Y-%m-%d") != today:
        print("Danas se još igra utakmica – najava za narednu ide poslije.")
        return False
    # GitHub can run very late: if the previous match was played today,
    # don't post in the evening – the preview goes out the next morning
    last = (team.get("overview") or {}).get("lastMatch") or {}
    last_utc = (last.get("status") or {}).get("utcTime")
    if last_utc and sarajevo_time(parse_utc(last_utc)).strftime("%Y-%m-%d") >= today:
        print("Prethodna utakmica je odigrana danas – najava ide sutra u 08:00.")
        return False
    # new match to announce: wait until exactly 08:00, then refresh the data
    if wait > 0:
        print(f"Sada je {now_local:%H:%M} – čekam do {POST_HOUR:02d}:00 ({wait / 60:.0f} min).")
        time.sleep(wait)
        team = get(f"/api/data/teams?id={TEAM_ID}")
        match = next_match(team)
        if not match:
            print("Naredna utakmica nije dostupna.")
            return False
    # re-check right before posting (the data may have been refreshed after waiting)
    if state.get("utakmica") == match["id"]:
        print("Najava je već objavljena.")
        return False
    send(build_embed(team, match), "Objavljena najava")
    state["utakmica"] = match["id"]
    state["datum"] = today
    save(state)
    return True


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    now = time.time()
    now_local = sarajevo_time(now)
    today = now_local.strftime("%Y-%m-%d")

    team = get(f"/api/data/teams?id={TEAM_ID}")

    if MANUAL:
        # manual run: post what was chosen in GitHub ("najava", "rezultat" or "oboje"),
        # but never something already posted – unless PRISILNO is ticked
        if MANUAL_MODE in ("rezultat", "oboje"):
            m = (team.get("overview") or {}).get("lastMatch")
            if not m:
                print("Nema odigrane utakmice.")
            elif state.get("rezultat") == m["id"] and not MANUAL_FORCE:
                print("Rezultat zadnje utakmice je već objavljen – preskačem.")
            else:
                send(build_result_embed(team, m), "Objavljen rezultat (ručno)")
                state["rezultat"] = m["id"]
                save(state)
        if MANUAL_MODE in ("najava", "oboje"):
            match = next_match(team)
            if not match:
                print("Naredna utakmica još nije objavljena na FotMobu.")
            elif state.get("utakmica") == match["id"] and not MANUAL_FORCE:
                print("Najava za narednu utakmicu je već objavljena – preskačem.")
            else:
                send(build_embed(team, match), "Objavljena najava (ručno)")
                state["utakmica"] = match["id"]
                state["datum"] = today
                save(state)
        return

    # 1) result first, 2) then the preview (never the other way round)
    do_result(team, state, now)
    do_preview(team, state, now_local, today)


if __name__ == "__main__":
    main()

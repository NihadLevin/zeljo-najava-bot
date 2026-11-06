"""
Odds bot – WWIN Liga BiH (Meridianbet)
Run on every refresh (triggered by cron-job.org):
  1) new matches on Meridian (e.g. a new round each week) -> one post listing them with all odds
  2) an odd changed for a known match -> a separate post with just the changed odds
The last seen odds are stored in kvote_stanje.json.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

from meridian_kvote import preuzmi_kvote, _norm

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
STATE_FILE = Path("kvote_stanje.json")
# manual option in GitHub: post the full list again even if nothing is new
POST_ALL = (os.environ.get("OBJAVI_SVE") or "").lower() == "true"
ODDS_KEYS = ["1", "X", "2", "Granica_golova", "Manje_golova", "Vise_golova"]
LABELS = {"1": "1", "X": "X", "2": "2", "Granica_golova": "Granica golova",
          "Manje_golova": "Manje golova", "Vise_golova": "Više golova"}
FOOTER = {"text": "WWIN liga BiH • Meridianbet • kvote se mogu promijeniti • 18+"}


def k(v):
    """2.05 -> '2,05'; missing odd -> '–'."""
    return f"{v:.2f}".replace(".", ",") if v else "–"


def match_key(r):
    # the event URL is unique per match; fall back to the team names
    return r.get("URL") or f"{r['Domacin']}|{r['Gost']}"


def is_zeljo(r):
    return "zeljeznicar" in _norm(r["Domacin"]) | _norm(r["Gost"])


def odds_line(o):
    text = f"`1: {k(o['1'])} │ X: {k(o['X'])} │ 2: {k(o['2'])}`"
    if o.get("Granica_golova"):
        g = f"{o['Granica_golova']:g}".replace(".", ",")
        text += f"\n{g} golova: manje {k(o['Manje_golova'])} · više {k(o['Vise_golova'])}"
    return text


def match_name(r):
    star = "🔵 " if is_zeljo(r) else ""
    return f"{star}{r['Domacin']} – {r['Gost']}  ·  {r['Dan']} {r['Vrijeme_Sarajevo']}".strip()


# ---------- Discord ----------
def post(embeds):
    """Sends up to 10 embeds per message (Discord limit)."""
    for i in range(0, len(embeds), 10):
        body = json.dumps({"embeds": embeds[i:i + 10]}).encode()
        req = urllib.request.Request(WEBHOOK, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "kvote-bot"})
        urllib.request.urlopen(req, timeout=30)


def send(embeds, label):
    if not embeds:
        return
    if not WEBHOOK:
        print(json.dumps(embeds, indent=2, ensure_ascii=False))   # test without Discord
    else:
        post(embeds)
    print(f"{label}: {len(embeds)} poruka/embed")


def list_embeds(rows, title):
    """Full odds list; split into several embeds if there are more than 25 matches."""
    rows = sorted(rows, key=lambda r: not is_zeljo(r))      # Željo's match first
    embeds = []
    for i in range(0, len(rows), 25):                       # max 25 fields per embed
        embeds.append({
            "title": title if i == 0 else f"{title} (nastavak)",
            "color": 0x1E4B9B,
            "fields": [{"name": match_name(r), "value": odds_line(r), "inline": False}
                       for r in rows[i:i + 25]],
            "footer": FOOTER,
        })
    return embeds


def change_embed(r, changes):
    lines = []
    for key, old, new in changes:
        if key == "Granica_golova":
            lines.append(f"**Granica golova:** {old or '–'} → {new}".replace(".", ","))
            continue
        arrow = "🔺" if (old and new > old) else "🔻" if old else "🆕"
        lines.append(f"**{LABELS[key]}:** {k(old)} → {k(new)} {arrow}")
    return {
        "title": f"🔄 Promjena kvota: {r['Domacin']} – {r['Gost']}",
        "description": "\n".join(lines),
        "color": 0x0066CC if is_zeljo(r) else 0x95A5A6,
        "fields": [{"name": f"Trenutne kvote · {r['Dan']} {r['Vrijeme_Sarajevo']}",
                    "value": odds_line(r), "inline": False}],
        "footer": FOOTER,
    }


# ---------- comparing with the last run ----------
def compare(old, new):
    """Changed odds as (key, old, new). A missing/locked odd (None) is not a change."""
    changes = []
    for key in ODDS_KEYS:
        if new.get(key) is not None and new.get(key) != old.get(key):
            changes.append((key, old.get(key), new[key]))
    return changes


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    known = state.get("utakmice", {})

    rows = preuzmi_kvote()
    if not rows:
        # never wipe the saved odds just because Meridian didn't load this time
        sys.exit("Meridian nije vratio utakmice (stranica se nije učitala ili je blokirana).")
    if not any(r[x] for r in rows for x in ("1", "X", "2")):
        sys.exit("Utakmice su pronađene, ali kvote nisu prepoznate – možda se promijenila stranica.")
    print(f"Meridian: {len(rows)} utakmica.")

    new_matches = [r for r in rows if match_key(r) not in known]
    changed = []
    for r in rows:
        old = known.get(match_key(r))
        if old:
            ch = compare(old, r)
            if ch:
                changed.append((r, ch))

    # 1) new matches (or the full list on first run / when asked)
    if POST_ALL:
        send(list_embeds(rows, "📋 Kvote – WWIN liga BiH"), "Objavljena lista kvota")
    elif new_matches:
        title = "📋 Kvote – WWIN liga BiH" if not known else "🆕 Nove utakmice – kvote"
        send(list_embeds(new_matches, title), "Objavljene nove utakmice")
    else:
        print("Nema novih utakmica.")

    # 2) changed odds – one embed per match, only the odds that changed
    if changed:
        send([change_embed(r, ch) for r, ch in changed], "Objavljene promjene kvota")
    else:
        print("Nema promjena kvota.")

    # save: current matches only (played/removed ones drop out); keep the last
    # known value of an odd that is temporarily locked (None)
    new_state = {}
    for r in rows:
        old = known.get(match_key(r), {})
        entry = {kk: r[kk] for kk in ("Domacin", "Gost", "Dan", "Vrijeme_Sarajevo")}
        for key in ODDS_KEYS:
            entry[key] = r[key] if r[key] is not None else old.get(key)
        new_state[match_key(r)] = entry
    if WEBHOOK:
        STATE_FILE.write_text(json.dumps({"utakmice": new_state}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

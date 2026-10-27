"""Final scores from ESPN's public scoreboard JSON (unofficial, no key needed)."""
import json
from datetime import datetime, timezone

import requests

from . import config

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
# College scoreboards default to ranked teams only; these group IDs return all of D-I / FBS.
EXTRA_PARAMS = {"NCAAF": {"groups": "80"}, "NCAAB": {"groups": "50"}}


def parse_scoreboard(data):
    """Reduce ESPN's scoreboard JSON to a simple list of games."""
    games = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        sides = {}
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            names = {t.get(k, "") for k in ("displayName", "shortDisplayName", "name", "abbreviation", "location", "nickname")}
            sides[c.get("homeAway")] = {
                "name": t.get("displayName", ""),
                "names": [n for n in names if n],
                "score": float(c["score"]) if c.get("score") not in (None, "") else None,
            }
        if "home" not in sides or "away" not in sides:
            continue
        status = (comp.get("status") or ev.get("status") or {}).get("type", {})
        games.append({
            "id": ev.get("id"),
            "start": ev.get("date"),
            "home": sides["home"],
            "away": sides["away"],
            "completed": bool(status.get("completed")),
            "state": status.get("name", ""),  # e.g. STATUS_FINAL, STATUS_POSTPONED, STATUS_CANCELED
        })
    return games


def get_games(conn, league, date_str):
    """Games for a league on a YYYYMMDD date. Days whose games are all final are cached."""
    path = config.LEAGUES.get(league)
    if not path:
        return []
    row = conn.execute("SELECT data FROM scoreboards WHERE league=? AND date=?", (league, date_str)).fetchone()
    if row:
        return json.loads(row["data"])
    params = {"dates": date_str, "limit": 500, **EXTRA_PARAMS.get(league, {})}
    r = requests.get(ESPN.format(path=path), params=params, timeout=30)
    r.raise_for_status()
    games = parse_scoreboard(r.json())
    if games and all(g["completed"] or "POSTPONED" in g["state"] or "CANCELED" in g["state"] for g in games):
        conn.execute(
            "INSERT OR REPLACE INTO scoreboards(league, date, data, fetched_at) VALUES (?,?,?,?)",
            (league, date_str, json.dumps(games), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    return games

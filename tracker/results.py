"""Final scores from ESPN's public scoreboard JSON (unofficial, no key needed)."""
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


_cache = {}


def get_games(league, date_str):
    """Games for a league on a YYYYMMDD date (fetched once per run)."""
    path = config.LEAGUES.get(league)
    if not path:
        return []
    key = (league, date_str)
    if key not in _cache:
        params = {"dates": date_str, "limit": 500, **EXTRA_PARAMS.get(league, {})}
        r = requests.get(ESPN.format(path=path), params=params, timeout=30)
        r.raise_for_status()
        _cache[key] = parse_scoreboard(r.json())
    return _cache[key]

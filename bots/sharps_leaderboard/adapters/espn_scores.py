"""ScoresProvider adapter: final scores from ESPN's public scoreboard JSON (unofficial, no key needed)."""
import requests

from ..ports import ScoresProvider

ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
# League -> ESPN scoreboard path
LEAGUES = {
    "NFL": "football/nfl",
    "NCAAF": "football/college-football",
    "NBA": "basketball/nba",
    "WNBA": "basketball/wnba",
    "NCAAB": "basketball/mens-college-basketball",
    "MLB": "baseball/mlb",
    "NHL": "hockey/nhl",
}
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


class EspnScores(ScoresProvider):
    """Each league/day is fetched once per run."""

    def __init__(self, get=requests.get):
        self._get = get
        self._cache = {}

    def games(self, league, day):
        path = LEAGUES.get(league)
        if not path:
            return []
        key = (league, day)
        if key not in self._cache:
            params = {"dates": day, "limit": 500, **EXTRA_PARAMS.get(league, {})}
            r = self._get(ESPN.format(path=path), params=params, timeout=30)
            r.raise_for_status()
            self._cache[key] = parse_scoreboard(r.json())
        return self._cache[key]

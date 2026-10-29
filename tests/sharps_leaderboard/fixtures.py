"""Games and helpers shared by the leaderboard tests, in the domain's own shape
(what a ScoresProvider returns), so domain tests don't depend on any adapter."""
from datetime import datetime, timezone


def team(display, short, abbr, location):
    return {"name": display, "names": [display, short, abbr, location]}


def game(gid, start, home, hs, away, as_, state="STATUS_FINAL"):
    return {
        "id": gid, "start": start,
        "home": {**home, "score": float(hs)},
        "away": {**away, "score": float(as_)},
        "completed": state == "STATUS_FINAL",
        "state": state,
    }


LAKERS = team("Los Angeles Lakers", "Lakers", "LAL", "Los Angeles")
CLIPPERS = team("LA Clippers", "Clippers", "LAC", "LA")
SUNS = team("Phoenix Suns", "Suns", "PHX", "Phoenix")
CELTICS = team("Boston Celtics", "Celtics", "BOS", "Boston")
KNICKS = team("New York Knicks", "Knicks", "NY", "New York")
JAZZ = team("Utah Jazz", "Jazz", "UTAH", "Utah")
CHIEFS = team("Kansas City Chiefs", "Chiefs", "KC", "Kansas City")
RAIDERS = team("Las Vegas Raiders", "Raiders", "LV", "Las Vegas")

NBA_DAY = [
    game("1", "2026-10-25T23:30Z", LAKERS, 112, SUNS, 105),     # Lakers win by 7, total 217
    game("2", "2026-10-26T00:00Z", CELTICS, 100, KNICKS, 97),   # Celtics win by 3, total 197
    game("3", "2026-10-26T02:00Z", CLIPPERS, 99, JAZZ, 101, "STATUS_POSTPONED"),
]
NFL_SUNDAY = [game("9", "2026-11-01T17:00Z", CHIEFS, 27, RAIDERS, 20)]


def games_for(league, day):
    if league == "NBA" and day == "20261025":
        return NBA_DAY
    if league == "NFL" and day == "20261101":
        return NFL_SUNDAY
    return []


NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)
POSTED = "2026-10-25T16:00:00Z"  # noon ET on game day

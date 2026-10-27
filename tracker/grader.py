"""Grade picks against final scores.

Results:
  win / loss / push   graded automatically
  void                game postponed or canceled
  manual              can't be graded automatically (player props, unmatched teams, ambiguous games)
  pending             game not final yet
"""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config
from .results import get_games

ET = ZoneInfo("America/New_York")
# How many days after the post to look for the game (football picks often go out days early).
LOOKAHEAD_DAYS = {"NFL": 7, "NCAAF": 7}
DEFAULT_LOOKAHEAD = 2


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def team_matches(query, side):
    q = _norm(query)
    if not q:
        return False
    names = [_norm(n) for n in side["names"]]
    if q in names:
        return True
    # "Chiefs" in "Kansas City Chiefs", but avoid tiny fragments matching everything
    full = _norm(side["name"])
    return len(q) >= 4 and re.search(rf"\b{re.escape(q)}\b", full) is not None


def which_side(game, team):
    """'home' / 'away' if the team name matches exactly one side of the game."""
    home, away = team_matches(team, game["home"]), team_matches(team, game["away"])
    if home and not away:
        return "home"
    if away and not home:
        return "away"
    return None


def _parse_time(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def find_game(bet, posted_at, league, games_for):
    """Return (game, side_of_team, note) for the first matching game at or after the post.

    games_for(league, 'YYYYMMDD') -> list of games.
    """
    team = bet.get("team") or bet.get("opponent")
    if not team:
        return None, None, "no team in pick"
    posted = _parse_time(posted_at)
    start_day = posted.astimezone(ET).date()
    days = LOOKAHEAD_DAYS.get(league, DEFAULT_LOOKAHEAD)
    for d in range(days + 1):
        day = (start_day + timedelta(days=d)).strftime("%Y%m%d")
        matches = []
        for g in games_for(league, day):
            side = which_side(g, team)
            if side and bet.get("opponent") and bet.get("team"):
                other = "away" if side == "home" else "home"
                if not team_matches(bet["opponent"], g[other]):
                    continue  # opponent given and doesn't fit this game
            if side:
                matches.append((g, side))
        upcoming = [m for m in matches if not m[0].get("start") or _parse_time(m[0]["start"]) >= posted - timedelta(hours=4)]
        if len(upcoming) > 1:
            return None, None, f"ambiguous: {len(upcoming)} games match '{team}' on {day}"
        if upcoming:
            g, side = upcoming[0]
            note = None
            if g.get("start") and _parse_time(g["start"]) < posted:
                note = "posted after game start (live bet?)"
            return g, side, note
    return None, None, f"no {league} game found for '{team}'"


def grade_bet(bet, game, side):
    """win / loss / push for a single straight bet against a final game, or None if not gradeable."""
    if "POSTPONED" in game["state"] or "CANCELED" in game["state"]:
        return "void"
    if not game["completed"]:
        return None
    other = "away" if side == "home" else "home"
    mine, theirs = game[side]["score"], game[other]["score"]
    if mine is None or theirs is None:
        return None
    kind = bet.get("bet_type")
    if kind == "moneyline":
        diff = mine - theirs
    elif kind == "spread":
        if bet.get("line") is None:
            return "manual"
        diff = mine + float(bet["line"]) - theirs
    elif kind == "total":
        if bet.get("line") is None or bet.get("side") not in ("over", "under"):
            return "manual"
        diff = (mine + theirs) - float(bet["line"])
        if bet["side"] == "under":
            diff = -diff
    else:
        return "manual"
    return "win" if diff > 0 else "loss" if diff < 0 else "push"


def american_to_decimal(odds):
    odds = int(odds)
    return 1 + (odds / 100 if odds > 0 else 100 / abs(odds))


def units_net(result, odds, units):
    """Profit in units, treating units as the amount risked."""
    units = units or 1.0
    if result == "win":
        return round(units * (american_to_decimal(odds) - 1), 3)
    if result == "loss":
        return -units
    return 0.0


PERIOD_LABELS = {
    "first_half": "1st half", "second_half": "2nd half", "first_quarter": "1st quarter",
    "second_quarter": "2nd quarter", "third_quarter": "3rd quarter", "fourth_quarter": "4th quarter",
    "first_period": "1st period", "second_period": "2nd period", "third_period": "3rd period",
    "first_five_innings": "first 5 innings", "other": "partial-game",
}


def game_info(game, posted_at):
    """What gets stored about the matched game."""
    start = game.get("start")
    return {
        "id": game.get("id"),
        "start": start,
        "home_team": game["home"]["name"],
        "away_team": game["away"]["name"],
        "home_score": game["home"]["score"] if game.get("completed") else None,
        "away_score": game["away"]["score"] if game.get("completed") else None,
        "before_start": None if not start else _parse_time(start) > _parse_time(posted_at),
    }


def grade_pick(pick, games_for, now=None):
    """Return (result, units_net, note, game).

    result None means still pending. game is the matched game's details (or None),
    including whether the pick was posted before it started.
    """
    now = now or datetime.now(timezone.utc)
    league = pick.get("league") or "OTHER"
    if league not in config.LEAGUES:
        return "manual", None, f"league {league} not covered", None
    kind = pick.get("bet_type")
    period = pick.get("period") or "full_game"
    posted = pick["posted_at"]
    window_closed = now > _parse_time(posted) + timedelta(days=LOOKAHEAD_DAYS.get(league, DEFAULT_LOOKAHEAD) + 1)

    if kind in ("moneyline", "spread", "total"):
        game, side, note = find_game(pick, posted, league, games_for)
        if not game:
            pending = not (window_closed or note.startswith("ambiguous"))
            return (None, None, note, None) if pending else ("manual", None, note, None)
        info = game_info(game, posted)
        if period != "full_game":  # only full-game scores are checked automatically
            return "manual", None, f"{PERIOD_LABELS.get(period, period)} bet: grade by hand", info
        result = grade_bet(pick, game, side)
        if result is None:
            return None, None, note, info
        odds = pick.get("odds") or config.DEFAULT_ODDS
        net = units_net(result, odds, pick.get("units")) if result in ("win", "loss", "push") else None
        return result, net, note, info

    if kind == "parlay":
        legs = pick.get("legs") or []
        if isinstance(legs, str):
            legs = json.loads(legs)
        if not legs:
            return "manual", None, "parlay with no legs", None
        outcomes, decimal, pending, manual, starts = [], 1.0, False, False, []
        for leg in legs:
            leg_league = (leg.get("league") or league).upper()
            if (leg.get("bet_type") not in ("moneyline", "spread", "total") or leg_league not in config.LEAGUES
                    or (leg.get("period") or "full_game") != "full_game"):
                manual = True
                continue
            game, side, _ = find_game(leg, posted, leg_league, games_for)
            if game and game.get("start"):
                starts.append(game["start"])
            r = grade_bet(leg, game, side) if game else None
            if r is None:
                pending = True
            elif r == "manual":
                manual = True
            else:
                outcomes.append(r)
                if r == "win":
                    decimal *= american_to_decimal(leg.get("odds") or config.DEFAULT_ODDS)
        # A parlay counts as posted in time only if it went out before its first leg started.
        info = None
        if starts:
            first = min(starts, key=_parse_time)
            info = {"start": first, "before_start": _parse_time(first) > _parse_time(posted)}
        if "loss" in outcomes:  # one losing leg settles it
            return "loss", -(pick.get("units") or 1.0), None, info
        if pending and not window_closed:
            return None, None, None, info
        if manual or pending:
            return "manual", None, "parlay has legs that can't be graded automatically", info
        if all(o in ("push", "void") for o in outcomes):
            return "push", 0.0, None, info
        if pick.get("odds") and all(o == "win" for o in outcomes):
            net = units_net("win", pick["odds"], pick.get("units"))
        else:  # pushed legs drop out; price the remaining legs
            net = round((pick.get("units") or 1.0) * (decimal - 1), 3)
        return "win", net, None, info

    return "manual", None, f"{kind} needs manual grading", None


# Database bet types -> the grader's own names
_TYPE_MAP = {"over_under": "total", "prop": "player_prop"}


def to_grader(p, posted_at=None):
    """Convert a stored pick (or parlay leg) to the shape grade_pick works with."""
    g = {
        "league": (p.get("sport") or "OTHER").upper(),
        "bet_type": _TYPE_MAP.get(p.get("type"), p.get("type")),
        "team": p.get("team"), "opponent": p.get("opponent"), "side": p.get("side"),
        "line": p.get("point"),   # grader's "line" is the spread/total number
        "odds": p.get("line"),    # the table's "line" is the odds
        "units": p.get("units"),
        "period": p.get("period") or "full_game",
        "posted_at": posted_at or p.get("created_at"),
    }
    if p.get("legs"):
        g["legs"] = []
        for leg in p["legs"]:
            lg = to_grader(leg, g["posted_at"])
            if lg["league"] == "OTHER":  # leg didn't name a league: assume the parlay's
                lg["league"] = g["league"]
            g["legs"].append(lg)
    return g


def grade_pending(db):
    from . import store

    def games_for(league, day):
        try:
            return get_games(league, day)
        except Exception as e:
            print(f"  could not load {league} scores for {day}: {e}")
            return []

    rows = store.pending_picks(db)
    counts = {}
    late = 0
    for row in rows:
        result, net, note, game = grade_pick(to_grader(row), games_for)
        if game:
            store.set_game(db, row["id"], game)
            late += game.get("before_start") is False
        if result is None:
            continue
        store.set_result(db, row["id"], result, net, note)
        counts[result] = counts.get(result, 0) + 1
    print(f"  graded {sum(counts.values())} of {len(rows)} pending picks {counts or ''}"
          f"{f', {late} posted after the game started' if late else ''}")
    return counts

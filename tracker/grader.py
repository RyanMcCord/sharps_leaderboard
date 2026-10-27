"""Grade picks against final scores.

Results:
  win / loss / push   graded automatically
  void                game postponed or canceled
  manual              can't be graded automatically (player props, unmatched teams, ambiguous games)
  NULL                still pending (game not final yet)
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


def grade_pick(pick, games_for, now=None):
    """Return (result, units_net, note). result None means still pending."""
    now = now or datetime.now(timezone.utc)
    league = pick.get("league") or "OTHER"
    if league not in config.LEAGUES:
        return "manual", None, f"league {league} not covered"
    kind = pick.get("bet_type")
    posted = pick["posted_at"]
    window_closed = now > _parse_time(posted) + timedelta(days=LOOKAHEAD_DAYS.get(league, DEFAULT_LOOKAHEAD) + 1)

    if kind in ("moneyline", "spread", "total"):
        game, side, note = find_game(pick, posted, league, games_for)
        if not game:
            return ("manual", None, note) if window_closed or note.startswith("ambiguous") else (None, None, note)
        result = grade_bet(pick, game, side)
        if result is None:
            return None, None, note
        odds = pick.get("odds") or config.DEFAULT_ODDS
        return result, (units_net(result, odds, pick.get("units")) if result in ("win", "loss", "push") else None), note

    if kind == "parlay":
        legs = pick.get("legs") or []
        if isinstance(legs, str):
            legs = json.loads(legs)
        if not legs:
            return "manual", None, "parlay with no legs"
        outcomes, decimal, pending, manual = [], 1.0, False, False
        for leg in legs:
            leg_league = (leg.get("league") or league).upper()
            if leg.get("bet_type") not in ("moneyline", "spread", "total") or leg_league not in config.LEAGUES:
                manual = True
                continue
            game, side, _ = find_game(leg, posted, leg_league, games_for)
            r = grade_bet(leg, game, side) if game else None
            if r is None:
                pending = True
            elif r == "manual":
                manual = True
            else:
                outcomes.append(r)
                if r == "win":
                    decimal *= american_to_decimal(leg.get("odds") or config.DEFAULT_ODDS)
        if "loss" in outcomes:  # one losing leg settles it
            return "loss", -(pick.get("units") or 1.0), None
        if pending and not window_closed:
            return None, None, None
        if manual or pending:
            return "manual", None, "parlay has legs that can't be graded automatically"
        if all(o in ("push", "void") for o in outcomes):
            return "push", 0.0, None
        if pick.get("odds") and all(o == "win" for o in outcomes):
            net = units_net("win", pick["odds"], pick.get("units"))
        else:  # pushed legs drop out; price the remaining legs
            net = round((pick.get("units") or 1.0) * (decimal - 1), 3)
        return "win", net, None

    return "manual", None, f"{kind} needs manual grading"


def grade_pending(conn):
    def games_for(league, day):
        try:
            return get_games(conn, league, day)
        except Exception as e:
            print(f"  could not load {league} scores for {day}: {e}")
            return []

    rows = conn.execute("SELECT * FROM picks WHERE result IS NULL").fetchall()
    counts = {}
    for row in rows:
        pick = dict(row)
        result, net, note = grade_pick(pick, games_for)
        if result is None:
            continue
        conn.execute(
            "UPDATE picks SET result=?, units_net=?, note=?, graded_at=? WHERE id=?",
            (result, net, note, datetime.now(timezone.utc).isoformat(), row["id"]),
        )
        counts[result] = counts.get(result, 0) + 1
    conn.commit()
    print(f"  graded {sum(counts.values())} of {len(rows)} pending picks {counts or ''}")
    return counts

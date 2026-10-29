"""What a pick is, and how to tell whether a post might contain one. Pure domain logic: no I/O.

A pick is a dict with these keys (the shape the PickReader port returns and the
PickRepository port stores):

    sport            one of SPORTS
    type             one of BET_TYPES
    line             the odds as posted, American format (+150 -> 150, -105 -> -105), or None
    bet_description  the pick in plain words, e.g. "Lakers -4.5 vs Suns"
    team, opponent   the side taken / the other team (for over_under: the two teams)
    side             "over" / "under" for totals and props, else None
    point            the spread or total number (-4.5, 215.5), or None
    units            units stated, or None
    period           one of PERIODS
    legs             for parlays and teasers: a list of picks
"""
import json
import re

SPORTS = ["NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "MLB", "NHL", "OTHER"]
BET_TYPES = ["moneyline", "spread", "over_under", "prop", "parlay", "teaser", "future", "other"]
PERIODS = ["full_game", "first_half", "second_half", "first_quarter", "second_quarter", "third_quarter",
           "fourth_quarter", "first_period", "second_period", "third_period", "first_five_innings", "other"]
RESULTS = ["pending", "win", "loss", "push", "void", "manual"]

# Leagues whose final scores we can look up, so picks in them can be graded automatically
AUTO_GRADED_LEAGUES = {"NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "MLB", "NHL"}
# Odds assumed when a tipster doesn't post any
DEFAULT_ODDS = -110


def clean_pick(p):
    """Make a pick from an outside source safe to store: valid enums, sensible types."""
    sport = str(p.get("sport") or "OTHER").upper()
    kind = str(p.get("type") or "other").lower()
    line = p.get("line")
    try:
        line = int(line) if line is not None else None
    except (TypeError, ValueError):
        line = None
    side = p.get("side") if p.get("side") in ("over", "under") else None
    period = str(p.get("period") or "full_game").lower()
    out = {
        "sport": sport if sport in SPORTS else "OTHER",
        "type": kind if kind in BET_TYPES else "other",
        "line": line,
        "bet_description": (p.get("bet_description") or "").strip() or "Pick",
        "team": p.get("team") or None,
        "opponent": p.get("opponent") or None,
        "side": side,
        "point": p.get("point"),
        "units": p.get("units"),
        "period": period if period in PERIODS else "other",
    }
    if out["type"] in ("parlay", "teaser"):
        out["legs"] = [clean_pick(leg) for leg in (p.get("legs") or [])]
    return out


# Cheap pre-check so obvious non-picks ("good morning fam", "link in bio") never cost a Claude call.
PICK_HINTS = re.compile(
    r"[+-]\s?\d|\b[ou]\s?\d|\d\s?u\b|\bml\b|\bover\b|\bunder\b|\bpk\b|\bpick|\block|\bparlay|\bspread|\bplay\b|\bbet\b|\bvs\.?\b|\s@\s",
    re.IGNORECASE,
)


def might_be_pick(post):
    """post: anything with .text and .media_urls (or a dict with those keys)."""
    get = post.get if isinstance(post, dict) else lambda k: getattr(post, k)
    media = get("media_urls")
    if isinstance(media, str):
        media = json.loads(media or "[]")
    if media:
        return True  # images are often bet slips; always look
    return bool(PICK_HINTS.search(get("text") or ""))

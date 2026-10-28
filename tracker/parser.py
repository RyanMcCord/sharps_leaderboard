"""Turn raw posts (text + bet-slip images) into structured picks with Claude."""
import json
import re

from . import config, store

SPORTS = ["NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "MLB", "NHL", "OTHER"]
BET_TYPES = ["moneyline", "spread", "over_under", "prop", "parlay", "teaser", "future", "other"]
PERIODS = ["full_game", "first_half", "second_half", "first_quarter", "second_quarter", "third_quarter",
           "fourth_quarter", "first_period", "second_period", "third_period", "first_five_innings", "other"]

SYSTEM = """You read posts from sports-betting tipster accounts on X and extract the betting picks they make.

Rules:
- Only extract actual picks the account is making (a bet they are recommending or placing).
  Ignore promos for paid packages, recaps of past results, hype with no specific bet, and replies to fans.
- Read attached images too - bet slips and pick graphics often hold the actual pick.
- Teams: use the team's common name as it would appear on a scoreboard (e.g. "Lakers", "Kansas City Chiefs", "Duke").
- type:
  - moneyline: `team` is the side taken. bet_description like "Lakers moneyline".
  - spread: `team` is the side taken, `point` is their spread (e.g. -4.5 or 3).
  - over_under: a game total. `side` is over/under, `point` is the total, `team` and `opponent` are the two teams.
  - prop: a player or game prop. bet_description says what it is, e.g. "Tatum over 27.5 points";
    `side` over/under and `point` the number when there is one.
  - parlay / teaser: one pick, with each leg in `legs` (same fields as a pick).
  - future: season-long bets (e.g. "Chiefs to win the Super Bowl").
- line: the odds exactly as posted, in American format, as an integer: +150 -> 150, -105 -> -105.
  Convert decimal odds to American. Null if no odds are shown. For parlays, the parlay odds.
- units: number of units if stated (e.g. "2u" -> 2), else null.
- period: which part of the game the bet covers. full_game unless the post says otherwise:
  "1H"/"first half" -> first_half, "2H" -> second_half, "1Q".."4Q" -> first_quarter..fourth_quarter,
  hockey "P1"/"1st period" -> first_period (etc.), MLB "F5"/"first 5 innings" -> first_five_innings.
  Put the period in bet_description too, e.g. "Chiefs 1H -3.5".
- sport: the league, one of NFL, NCAAF, NBA, WNBA, NCAAB, MLB, NHL, or OTHER.
- If a post has no picks, return an empty list."""

PICK_PROPS = {
    "sport": {"type": "string", "enum": SPORTS},
    "type": {"type": "string", "enum": BET_TYPES},
    "line": {"type": ["integer", "null"], "description": "American odds as posted"},
    "bet_description": {"type": "string", "description": "The pick in plain words"},
    "team": {"type": ["string", "null"]},
    "opponent": {"type": ["string", "null"]},
    "side": {"type": ["string", "null"], "enum": ["over", "under", None]},
    "point": {"type": ["number", "null"], "description": "Spread or total number"},
    "units": {"type": ["number", "null"]},
    "period": {"type": "string", "enum": PERIODS},
}

TOOL = {
    "name": "record_picks",
    "description": "Record every betting pick found in the post.",
    "input_schema": {
        "type": "object",
        "properties": {
            "picks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        **PICK_PROPS,
                        "legs": {"type": "array", "items": {"type": "object", "properties": PICK_PROPS}},
                    },
                    "required": ["sport", "type", "bet_description"],
                },
            }
        },
        "required": ["picks"],
    },
}


def clean_pick(p):
    """Make a parsed pick safe to store: valid enums, sensible types."""
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


def _client():
    import anthropic
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it as a GitHub repo secret (or to .env when running locally).")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def extract_picks(client, post):
    content = [{"type": "image", "source": {"type": "url", "url": url}} for url in post["media_urls"][:4]]
    content.append({
        "type": "text",
        "text": f"Account: @{post['x_handle']}\nPosted (UTC): {post['posted_at']}\n\nPost text:\n{post['text']}",
    })
    msg = client.messages.create(
        model=config.PARSER_MODEL,
        max_tokens=2000,
        system=SYSTEM,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "record_picks"},
        messages=[{"role": "user", "content": content}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            return [clean_pick(p) for p in block.input.get("picks", [])]
    return []


# Cheap pre-check so obvious non-picks ("good morning fam", "link in bio") never cost an API call.
PICK_HINTS = re.compile(
    r"[+-]\s?\d|\b[ou]\s?\d|\d\s?u\b|\bml\b|\bover\b|\bunder\b|\bpk\b|\bpick|\block|\bparlay|\bspread|\bplay\b|\bbet\b|\bvs\.?\b|\s@\s",
    re.IGNORECASE,
)


def might_be_pick(post):
    media = post["media_urls"]
    if isinstance(media, str):
        media = json.loads(media or "[]")
    if media:
        return True  # images are often bet slips; always look
    return bool(PICK_HINTS.search(post["text"] or ""))


def parse_pending(db, limit=500):
    posts = store.unparsed_posts(db, limit)
    found = skipped = 0
    client = None
    for post in posts:
        if not might_be_pick(post):
            store.mark_parsed(db, post["id"])
            skipped += 1
            continue
        client = client or _client()
        try:
            picks = extract_picks(client, post)
        except Exception as e:  # leave unparsed so the next run retries it
            print(f"  could not read post {post['id']}: {e}")
            continue
        for p in picks:
            store.insert_pick(db, post["sharp_account"], post["id"], post["posted_at"], p)
        store.mark_parsed(db, post["id"])
        found += len(picks)
    print(f"  checked {len(posts)} posts ({skipped} skipped as not picks), found {found} picks")
    return found

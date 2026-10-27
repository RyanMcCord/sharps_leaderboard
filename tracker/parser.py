"""Turn raw posts (text + bet-slip images) into structured picks with Claude."""
import json
import re

from . import config
from .db import dumps

SYSTEM = """You read posts from sports-betting tipster accounts on X and extract the betting picks they make.

Rules:
- Only extract actual picks the account is making (a bet they are recommending or placing).
  Ignore promos for paid packages, recaps of past results, hype with no specific bet, and replies to fans.
- Read attached images too - bet slips and pick graphics often hold the actual pick.
- Teams: use the team's common name as it would appear on a scoreboard (e.g. "Lakers", "Kansas City Chiefs", "Duke").
- spread: `team` is the side taken, `line` is their spread (e.g. -4.5 or +3).
- moneyline: `team` is the side taken, `line` is null.
- total: `side` is over/under, `line` is the total, `team` and `opponent` are the two teams in the game.
- player_prop: put the player and stat in `description`, `side` over/under, `line` the number.
- parlay: one pick with bet_type "parlay" and each leg in `legs` (same fields as a pick). Odds = parlay odds if shown.
- odds: American odds as an integer if stated (e.g. -110, 150), else null. Convert decimal odds to American.
- units: number of units if stated (e.g. "2u" -> 2), else null.
- league: one of NFL, NCAAF, NBA, WNBA, NCAAB, MLB, NHL, or OTHER.
- If a post has no picks, return an empty list."""

PICK_PROPS = {
    "league": {"type": "string"},
    "bet_type": {"type": "string", "enum": ["moneyline", "spread", "total", "player_prop", "parlay", "other"]},
    "team": {"type": ["string", "null"]},
    "opponent": {"type": ["string", "null"]},
    "side": {"type": ["string", "null"], "enum": ["over", "under", None]},
    "line": {"type": ["number", "null"]},
    "odds": {"type": ["integer", "null"]},
    "units": {"type": ["number", "null"]},
    "description": {"type": "string", "description": "The pick in plain words, e.g. 'Lakers -4.5 vs Suns'"},
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
                    "required": ["league", "bet_type", "description"],
                },
            }
        },
        "required": ["picks"],
    },
}


def _client():
    import anthropic
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def extract_picks(client, post):
    content = []
    for url in json.loads(post["media_urls"] or "[]")[:4]:
        content.append({"type": "image", "source": {"type": "url", "url": url}})
    content.append({
        "type": "text",
        "text": f"Account: @{post['handle']}\nPosted (UTC): {post['created_at']}\n\nPost text:\n{post['text']}",
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
            return block.input.get("picks", [])
    return []


# Cheap pre-check so obvious non-picks ("good morning fam", "link in bio") never cost an API call.
PICK_HINTS = re.compile(
    r"[+-]\s?\d|\b[ou]\s?\d|\d\s?u\b|\bml\b|\bover\b|\bunder\b|\bpk\b|\bpick|\block|\bparlay|\bspread|\bplay\b|\bbet\b|\bvs\.?\b|\s@\s",
    re.IGNORECASE,
)


def might_be_pick(post):
    if json.loads(post["media_urls"] or "[]"):
        return True  # images are often bet slips; always look
    return bool(PICK_HINTS.search(post["text"] or ""))


def parse_pending(conn, limit=500):
    posts = conn.execute("SELECT * FROM posts WHERE parsed=0 ORDER BY created_at LIMIT ?", (limit,)).fetchall()
    found = skipped = 0
    client = None
    for post in posts:
        if not might_be_pick(post):
            conn.execute("UPDATE posts SET parsed=1 WHERE id=?", (post["id"],))
            skipped += 1
            continue
        client = client or _client()
        try:
            picks = extract_picks(client, post)
        except Exception as e:  # leave unparsed so the next run retries it
            print(f"  could not parse post {post['id']}: {e}")
            continue
        for p in picks:
            conn.execute(
                "INSERT INTO picks(post_id, handle, posted_at, league, bet_type, team, opponent, side, line, odds, units, legs, description) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (post["id"], post["handle"], post["created_at"], (p.get("league") or "OTHER").upper(), p.get("bet_type"),
                 p.get("team"), p.get("opponent"), p.get("side"), p.get("line"), p.get("odds"), p.get("units"),
                 dumps(p.get("legs") or []), p.get("description")),
            )
        conn.execute("UPDATE posts SET parsed=1 WHERE id=?", (post["id"],))
        conn.commit()
        found += len(picks)
    conn.commit()
    print(f"  checked {len(posts)} posts ({skipped} skipped as not picks), found {found} picks")
    return found

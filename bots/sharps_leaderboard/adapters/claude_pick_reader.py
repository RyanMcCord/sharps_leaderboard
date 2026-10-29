"""PickReader adapter: Claude reads the picks out of a post's text and bet-slip images."""
from xbots.adapters import claude

from ..domain.picks import BET_TYPES, PERIODS, SPORTS, clean_pick
from ..ports import PickReader

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
- If a post has no picks, return an empty list.

Always answer by calling the record_picks tool exactly once, even when there are no picks
(then call it with an empty list). Don't reply in plain text."""

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


class ClaudePickReader(PickReader):
    """The Claude client is created on first use, so runs with nothing to read don't need a key."""

    def __init__(self, client=None, model=None):
        self._client, self.model = client, model

    def read(self, post):
        if self._client is None:
            self._client = claude.client()
        return extract_picks(self._client, post, self.model or claude.model())


def extract_picks(client, post, model):
    content = [{"type": "image", "source": {"type": "url", "url": url}} for url in list(post["media_urls"])[:4]]
    content.append({
        "type": "text",
        "text": f"Account: @{post['x_handle']}\nPosted (UTC): {post['posted_at']}\n\nPost text:\n{post['text']}",
    })
    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM,
        tools=[TOOL],
        # Newer models don't accept a forced tool choice, so the instructions ask for the tool instead
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": content}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record_picks":
            return [clean_pick(p) for p in (block.input or {}).get("picks", [])]
    # No tool call: the model answered in plain text instead. Treat it as "no picks found".
    return []

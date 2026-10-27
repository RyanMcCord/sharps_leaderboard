# Tipster Tracker

Tracks the free picks that sports-betting accounts post on X and grades them against final scores. The output is a leaderboard with each account's record, units won and ROI.

```
X API (posts + bet-slip images)  →  Claude (extract picks)  →  ESPN scores (grade)  →  leaderboard.html + picks.csv
```

Everything is stored in one SQLite file (`tracker.db`). That includes a copy of every post, so a pick still counts if the tipster deletes it later.

## Setup (about 15 minutes)

1. **Python 3.11+**, then:
   ```
   pip install -r requirements.txt
   ```
2. **X API access.** Sign in at developer.x.com, create a project and an app, and add credits. The API is pay-per-use. Copy the app's **Bearer Token**.
3. **Anthropic API key.** Get one at console.anthropic.com. It's used to read picks out of post text and bet-slip images.
4. Copy `.env.example` to `.env` and paste in both keys.
5. Edit `accounts.txt` to add or remove handles. Your nine accounts are already in it.

## Running it

```
python run.py all        # fetch → parse → grade → report
```

Then open `leaderboard.html`. Each step can also run on its own (`fetch`, `parse`, `grade`, `report`).

**Run it on a schedule.** Tipsters often delete losing picks, so poll often enough to catch posts before games start. Every 2 hours from 9am to 11pm ET is a good default. With cron on Mac or Linux:

```
0 9-23/2 * * *  cd /path/to/tipster_tracker && /usr/bin/python3 run.py all >> tracker.log 2>&1
```

## What gets graded automatically

| Bet type | Graded? |
|---|---|
| Moneyline, spread, total | Yes, from final scores |
| Parlays of those | Yes. One losing leg makes it a loss, and pushed legs drop out |
| Player props, futures, anything else | Marked `manual` |
| Postponed or canceled games | `void` (not counted) |
| League outside NFL, NCAAF, NBA, WNBA, NCAAB, MLB, NHL | `manual` |

Picks are also marked `manual` when the team can't be matched to a game, or when more than one game fits. To grade those by hand:

```
python run.py review                 # lists each one with a link to the post
python run.py set 42 win             # or loss / push / void
python run.py set 42 win 2.5         # override the units won
```

## How the numbers work

- **Units:** treated as the amount risked. "2u" on a -110 winner is +1.82u. A pick with no stated units counts as 1u.
- **Odds:** taken from the post when shown. Otherwise the tracker assumes `DEFAULT_ODDS` (-110), which is generous for underdog-heavy tipsters.
- **ROI:** units won divided by units risked, with pushes excluded.
- **Small samples:** accounts with fewer than 30 graded picks are faded out on the leaderboard. 55% over 20 picks means very little.
- **Game matching:** a pick is matched to the first game for that team on or after the post, looking ahead 2 days (7 for football, since those picks often go out midweek). A pick posted after tip-off is still graded, with the note "live bet?".

## Costs

- **X API:** about $0.005 per post read, plus a one-time user lookup per account. Nine accounts × up to 20 new posts per run × 8 runs a day is the worst case. In practice it's much lower, because each run only asks for posts newer than the last one seen (`since_id`). Lower `MAX_POSTS_PER_ACCOUNT` or run less often to save money.
- **Anthropic API:** one short call per post, usually a fraction of a cent each.
- **Scores:** free. ESPN's public scoreboard feed is unofficial and undocumented, so it could change. If it breaks, swap `tracker/results.py` for a paid feed.

## Files

```
run.py               command line
accounts.txt         handles to track
tracker/xapi.py      X API fetching
tracker/parser.py    pick extraction (Claude, with images)
tracker/results.py   ESPN scores, with finished days cached
tracker/grader.py    grading logic
tracker/report.py    leaderboard HTML + CSV
tests/               offline tests: python -m unittest discover tests
```

## Notes

- The parser is the part most likely to need tuning. After the first few runs, look at `picks.csv` and see whether it's catching real picks and skipping promos. You can adjust the rules in `SYSTEM` in `tracker/parser.py`.
- This uses only the official X API. No site scraping.

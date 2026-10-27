# Sharps Leaderboard

Tracks the free picks that sports-betting accounts post on X and grades them against final scores. Once a day it updates a leaderboard website showing who's actually making money: each account's record, win %, units won and ROI.

```
X API (posts + bet-slip images)  →  Claude (read picks)  →  ESPN scores (grade)  →  leaderboard site + picks.csv
```

It runs on GitHub's servers once a day, so nothing needs to stay on at home. Everything is stored in one SQLite file (`tracker.db`) in this repo. That includes a copy of every post, so a pick still counts if the tipster deletes it later.

## Turning on the daily updates (about 15 minutes)

1. **Get an X API token.** Sign in at developer.x.com, create a project and an app, and add credits. The API is pay-per-use. Copy the app's **Bearer Token**.
2. **Get an Anthropic API key** at console.anthropic.com. It's used to read picks out of post text and bet-slip images.
3. **Add both as repo secrets.** In this repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `X_BEARER_TOKEN`
   - `ANTHROPIC_API_KEY`

   Secrets stay hidden even though the repo is public. Never put keys in a file in the repo.
4. **Turn on the website.** Go to **Settings → Pages**, set *Source* to **Deploy from a branch**, pick **main** and the **/docs** folder, then save. The leaderboard goes live at `https://ryanmccord.github.io/sharps_leaderboard/`.
5. **Run it once now.** Go to the **Actions** tab, open **Daily update**, and click **Run workflow**. After that it runs by itself every day at about 5 PM Eastern.

To track different accounts, edit `accounts.txt` and commit. The next daily run picks it up.

## How the daily run works

Each day at about 5 PM Eastern the run does four things:

1. Fetches each account's posts since the last run.
2. Reads the picks out of them.
3. Grades every pick whose game has finished. Last night's games are final by then.
4. Rebuilds the site and saves the results back to the repo.

If one step fails, for example because X credits ran out, the other steps still run and the site still updates. The run shows as failed on the Actions tab with the reason.

**Trade-off of running once a day:** if a tipster posts a pick and deletes it before the next run, the tracker never sees it. To catch more of those, add a second time to the schedule in `.github/workflows/daily-update.yml`, for example `- cron: "7 15 * * *"` for about 11 AM Eastern. Each extra run only costs the posts it actually finds.

## The leaderboard

Accounts are ranked by units won. Each account's bar extends right from the zero line if they're up and left if they're down. You can filter to the last 30 or 7 days or to a single league, and click an account to see its results by league and its latest picks, with links to the original posts.

## Running it on your own computer (optional)

```
pip install -r requirements.txt
cp .env.example .env          # then paste in both keys
python run.py all             # fetch → read picks → grade → build site
```

Then open `docs/index.html`. Each step can also run on its own (`fetch`, `parse`, `grade`, `report`). If you run it locally, commit `tracker.db` afterwards so the daily run builds on your data.

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

Rough monthly figures at one run a day for nine accounts:

- **X API:** about $0.005 per post fetched. Only posts since the last run come back, so the cost is set by how much these accounts post, not by how often the tracker runs. At 5 to 15 posts per account per day, that's about $7 to $20 a month. The very first run is capped at 20 posts per account.
- **Anthropic API:** posts that obviously aren't picks ("good morning fam", "link in bio") are skipped without an API call. Posts with images or betting language get one short call each. Expect a few dollars a month at most.
- **GitHub:** free. The run takes a couple of minutes a day, and both Actions and Pages are free for public repos.
- **Scores:** free. ESPN's public scoreboard feed is unofficial and undocumented, so it could change. If it breaks, swap `tracker/results.py` for a paid feed.

To spend less, track fewer accounts. Running less often doesn't save money on X, because each post is billed once either way.

## Files

```
run.py               command line
accounts.txt         handles to track
tracker/xapi.py      X API fetching
tracker/parser.py    pick extraction (Claude, with images)
tracker/results.py   ESPN scores, with finished days cached
tracker/grader.py    grading logic
tracker/report.py    builds the site + CSV
tracker/site_template.html   the leaderboard page
.github/workflows/daily-update.yml   the daily run
docs/index.html      the published leaderboard (rebuilt each run)
tests/               offline tests: python -m unittest discover tests
```

## Notes

- The parser is the part most likely to need tuning. After the first few runs, look at `picks.csv` and see whether it's catching real picks and skipping promos. You can adjust the rules in `SYSTEM` in `tracker/parser.py`.
- This uses only the official X API. No site scraping.

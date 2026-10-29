# Sharps leaderboard

Tracks the free picks that sports-betting accounts post on X and grades them against final scores. Once a day it updates a [leaderboard website](https://ryanmccord.github.io/sharps_leaderboard/) showing who's actually making money: each account's record, win %, units won and ROI.

```
X API (posts + bet-slip images) → Claude (read picks) → Aurora Postgres (store) → ESPN scores (grade) → leaderboard site
```

Visitors to the site never touch the database: the daily run queries it once and bakes the results into the page. Setup is shared with the other bots; see the [main README](../../README.md#setup).

## How it's put together

| Layer | Where | What |
|---|---|---|
| Domain | `domain/picks.py`, `domain/grading.py` | what a pick is, the "might this be a pick" pre-check, grading, units and ROI |
| Ports | `ports.py` | `PickRepository`, `ScoresProvider`, `PickReader`, `SitePublisher` (plus the shared `PostSource`) |
| Use cases | `application.py` | the daily pipeline, accounts, hand grading |
| Adapters | `adapters/` | Postgres (`sharps` schema), ESPN, Claude, the static page |
| Composition root | `app.py` | which adapter goes behind each port |
| Command line | `cli.py` | `python -m xbots sharps-leaderboard <command>` |
| Schedule | `.github/workflows/sharps-leaderboard.yml` | daily at 21:07 UTC |

## The database

Everything lives in the `sharps` schema. Before the repo hosted several bots, these tables were in `public`. The first run after the change moves them, with all their data, into `sharps`.

### sharps.accounts

| Column | Type | |
|---|---|---|
| `id` | uuid | primary key |
| `x_handle` | text | unique, case-insensitive |
| `created_at` | timestamptz | when the account was added |
| `active` | boolean | only active accounts are fetched and shown |
| `x_user_id`, `followers`, `last_seen_post_id` | | filled in from the X API |

### sharps.picks

| Column | Type | |
|---|---|---|
| `id` | uuid | primary key |
| `sharp_account` | uuid | foreign key to `accounts.id` |
| `sport` | enum | `NFL`, `NCAAF`, `NBA`, `WNBA`, `NCAAB`, `MLB`, `NHL`, `OTHER` |
| `type` | enum | `moneyline`, `spread`, `over_under`, `prop`, `parlay`, `teaser`, `future`, `other` |
| `line` | integer | the odds as posted: +150 is stored as `150`, -105 as `-105` |
| `bet_description` | text | e.g. "Lakers moneyline", "Tatum over 27.5 points" |
| `created_at` | timestamptz | when the pick was posted |
| `period` | enum | which part of the game: `full_game`, `first_half`, `second_half`, `first_quarter` … `fourth_quarter`, `first_period` … `third_period`, `first_five_innings`, `other` |
| `team`, `opponent`, `side`, `point` | | what grading needs: the side taken, over/under, and the spread or total number (-4.5, 215.5) |
| `units`, `legs` | | units stated, and the legs of a parlay or teaser |
| `result`, `units_net`, `graded_at`, `note` | | `pending`, `win`, `loss`, `push`, `void` or `manual`, plus units won or lost |
| `game_id`, `game_start`, `home_team`, `away_team`, `home_score`, `away_score` | | the game the pick was matched to (ESPN's game id) and its final score |
| `posted_before_start` | boolean | whether the pick went out before the game started. Empty until a game is matched |
| `post_id` | text | the X post it came from |

There's also `sharps.posts`, which keeps a copy of every post so a pick still counts if it's deleted later. Its `deleted_at` column records when the tracker noticed a post was gone. The `sharps.leaderboard` view has each active account's standings, leaving out late picks, plus counts of late and deleted picks.

**Looking at the data by hand:** the RDS console's Query editor doesn't work with express clusters. Use `standings` and `export` below instead, or a desktop SQL tool: connect to `DB_HOST` as `postgres` over SSL, with a token from `aws rds generate-db-auth-token` as the password.

## Day to day

From a computer with a `.env` file set up (see the main README):

```
python -m xbots sharps-leaderboard accounts                   # list accounts
python -m xbots sharps-leaderboard accounts add <handle>       # start tracking someone
python -m xbots sharps-leaderboard accounts pause <handle>     # stop tracking (history is kept)
python -m xbots sharps-leaderboard accounts resume <handle>    # track again
python -m xbots sharps-leaderboard standings                   # print the leaderboard
python -m xbots sharps-leaderboard review                      # picks that need a hand grade, with links
python -m xbots sharps-leaderboard set 3f9a1c2e win            # grade one by hand (win / loss / push / void)
python -m xbots sharps-leaderboard set 3f9a1c2e win 2.5        # ...and override the units won
python -m xbots sharps-leaderboard export picks.csv            # every pick to a spreadsheet
```

You can also add accounts by adding handles to `bots/sharps_leaderboard/accounts.txt` and committing. The next run picks them up. Hand grades and account changes show up on the site after the next daily run.

## How the daily run works

Each day at 21:07 UTC (5:07 PM Eastern during daylight saving time, 4:07 PM otherwise), the run:

1. Makes sure its tables exist and adds any new handles from `accounts.txt`.
2. Fetches each active account's posts since the last run, up to 500 per account. Starting the run wakes the database, which takes about 15–30 seconds.
3. Reads the picks out of new posts. Posts that obviously aren't picks are skipped without a Claude call. A post that fails to read stays queued for the next run.
4. Matches picks to their games, records whether each went out before the start, and grades the ones whose games have finished. Last night's games are final by then.
5. Checks each graded pick post once, at least 12 hours after grading, to see whether the tipster deleted it.
6. Rebuilds `docs/index.html`, which GitHub Pages publishes.

If one step fails, for example because X credits ran out, the rest still run and the site still updates. The run shows as failed on the Actions tab with the reason.

**Trade-off of running once a day:** a pick that's posted and then deleted before the next run is never seen. To catch more of those, add a second time in `.github/workflows/sharps-leaderboard.yml`, for example `- cron: "7 15 * * *"` for about 11 AM Eastern.

## The leaderboard

Accounts are ranked by units won. Each account's bar extends right from the zero line if they're up and left if they're down. You can filter to the last 30 or 7 days or to one league, and click an account to see its results by league and its latest picks, with links to the original posts.

## What gets graded automatically

| Bet type | Graded? |
|---|---|
| Full-game moneyline, spread, over/under | Yes, from final scores |
| Parlays of those | Yes. One losing leg makes it a loss, and pushed legs drop out |
| First half, quarters, periods, first 5 innings | Marked `manual`. The game is still matched, so the timing check works |
| Props, futures, teasers, other | Marked `manual` |
| Postponed or canceled games | `void` (not counted) |
| Sport `OTHER` | `manual` |

Picks are also marked `manual` when the team can't be matched to a game, or when more than one game fits.

## How the numbers work

- **Units:** treated as the amount risked. "2u" on a -110 winner is +1.82u. A pick with no stated units counts as 1u.
- **Odds:** the `line` from the post. When it's missing, the tracker assumes `DEFAULT_ODDS` (-110), which is generous for underdog-heavy tipsters.
- **ROI:** units won divided by units risked, with pushes excluded.
- **Small samples:** accounts with fewer than 30 graded picks are faded out on the leaderboard. 55% over 20 picks means very little.
- **Game matching:** a pick is matched to the first game for that team on or after the post, looking ahead 2 days (7 for football, since those picks often go out midweek).
- **Late picks:** a pick posted after its game started is graded but marked late and left out of the standings, since there's no way to tell a live bet from a pick posted once the result was clear. A parlay counts as late if it went out after its first leg started.
- **Deleted picks:** still count. The leaderboard shows how many each account deleted.

## Settings

Set these as environment variables (or in `.env`). All are optional.

| Setting | Default | |
|---|---|---|
| `DEFAULT_ODDS` | `-110` | odds assumed when a post doesn't show any |
| `CHECK_DELETIONS` | `true` | re-read each pick post once after grading to catch deletions (one X read per post) |
| `MAX_POSTS_PER_ACCOUNT` | `100` | most new posts per page per account (you're billed per post returned, so it's a ceiling, not a cost) |
| `FIRST_RUN_POSTS` | `20` | backfill for an account's first fetch |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | the model that reads picks |

## Costs

Rough monthly figures at one run a day for nine accounts:

- **Aurora:** on the AWS Free plan, an express cluster within the free limits (up to 4 ACUs, 1 GB of storage) costs nothing. This bot stores well under 1 GB a year. The Free plan ends after 6 months or when its credits run out. After that, a cluster that's awake about 10 minutes a day comes to roughly $1 a month.
- **X API:** about $0.005 per post fetched. Only posts since the last run come back, so the cost depends on how much these accounts post. At 5 to 15 posts per account per day, that's about $7 to $20 a month.
- **Deleted-post checks:** one more X read per pick post, once, roughly $7 a month. Set `CHECK_DELETIONS=false` to turn it off.
- **Anthropic API:** a few dollars a month at most.
- **GitHub and ESPN:** free. ESPN's scoreboard feed is unofficial and undocumented, so it could change. If it breaks, replace `adapters/espn_scores.py` with another `ScoresProvider`.

To spend less, track fewer accounts.

## Notes

- The pick reader is the part most likely to need tuning. Run `export` and check whether it's catching real picks and skipping promos. The rules are in `SYSTEM` in `adapters/claude_pick_reader.py`.
- Many picks are player props and same-game parlays, which need a hand grade today. Grading props automatically would need box scores: a new `ScoresProvider` method and a domain rule, with no change to the rest.

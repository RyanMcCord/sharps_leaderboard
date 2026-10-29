# Sharps Leaderboard

Tracks the free picks that sports-betting accounts post on X and grades them against final scores. Once a day it updates a leaderboard website showing who's actually making money: each account's record, win %, units won and ROI.

```
X API (posts + bet-slip images) → Claude (read picks) → Aurora Postgres (store) → ESPN scores (grade) → leaderboard site
```

The daily job runs on GitHub's servers, with its keys stored as GitHub secrets. The data lives in an Aurora Serverless Postgres database in your AWS account, which pauses itself when idle. The database has no password: on each run, the job's AWS access key signs a sign-in token that's good for 15 minutes. Visitors to the site never touch the database: the daily job queries it once and bakes the results into the page.

## Setup

You'll need about 20 minutes. Steps 1–3 happen in the AWS console and step 4 in GitHub. Create everything in one AWS region. This project's database is in **us-east-2 (Ohio)**, so check the region selector in the top-right of the AWS console says Ohio before each step.

### 1. Create the database

1. In the AWS console, open **RDS → Create database** and choose **Aurora (PostgreSQL Compatible)**.
2. Choose **Express configuration**. On the AWS Free plan it's the only option. Full configuration is greyed out until you upgrade.
3. Name it `sharps-leaderboard`. If the page offers capacity settings, set the **minimum to 0 ACUs** and **pause after 5 minutes**, so it sleeps between runs. Leave everything else as it is.
4. Create it and wait until its status says **Available**.
5. Open the cluster and go to the **Connectivity & security** tab. Under **Endpoints**, copy the endpoint whose type is **Writer**. It looks like `sharps-leaderboard.cluster-xxxxxxxx.us-east-2.rds.amazonaws.com`. This is your `DB_HOST`.

Express clusters have no password and can't be given one. Sign-in is by IAM token only. They also have no initial database, so the tracker uses the built-in `postgres` database. You don't need to enable the RDS Data API, and you don't need a Secrets Manager secret.

### 2. Get the X and Anthropic keys

- **X:** sign in at developer.x.com, create a project and an app, and add credits. Copy the app's **Bearer Token**.
- **Anthropic:** sign in at platform.claude.com, add credits under **Billing**, then create a key under **API Keys**. It's used to read picks out of post text and bet-slip images.

### 3. Create a limited AWS user for the daily job

This user can sign in to the database as `postgres`, and do nothing else in your AWS account.

1. Open **IAM → Users → Create user** and name it `sharps-leaderboard-bot`. Don't give it console access, and skip permissions for now.
2. Open the new user, then choose **Add permissions → Create inline policy**. Switch to **JSON** and paste this, replacing `REGION` and `ACCOUNT_ID` with yours. For this project that's `us-east-2` and `331384580180`, and your account ID is also shown in the top-right menu of the console.
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "rds-db:connect",
         "Resource": "arn:aws:rds-db:REGION:ACCOUNT_ID:dbuser:*/postgres"
       }
     ]
   }
   ```
   Name the policy `sharps-leaderboard-db`. The `*` stands for the cluster's resource ID. It changes if you ever recreate the cluster, so the wildcard means the policy keeps working.
3. On the user's **Security credentials** tab, choose **Create access key → Application running outside AWS**. Copy the **access key ID** and the **secret access key**. The secret key is shown only once.

This access key doesn't expire. If you think it may have leaked, deactivate it on the same tab, create a new one, and update the two GitHub secrets. Rotating it every few months is good practice either way.

### 4. Add the secrets to GitHub and turn on the site

1. In this repo, open **Settings → Secrets and variables → Actions** and add these repository secrets:

   | Secret | Value |
   |---|---|
   | `X_BEARER_TOKEN` | X bearer token |
   | `ANTHROPIC_API_KEY` | Anthropic API key |
   | `AWS_ACCESS_KEY_ID` | from step 3 |
   | `AWS_SECRET_ACCESS_KEY` | from step 3 |
   | `AWS_REGION` | the region your database is in: `us-east-2` |
   | `DB_HOST` | the writer endpoint from step 1 |
   | `DB_NAME` | optional. Leave it out, or set it to `postgres` |

   Secrets stay hidden even though the repo is public. Never put keys in a file in the repo. If you added `DB_CLUSTER_ARN` or `DB_SECRET_ARN` under an earlier version of these steps, delete them. They're only for the full-configuration setup described at the end of this section.
2. Open **Settings → Pages**, set *Source* to **Deploy from a branch**, then choose **main** and **/docs**.
3. On the **Actions** tab, open **Daily update** and click **Run workflow**. The first run creates the tables and adds the accounts from `accounts.txt`. After that it runs by itself every day at 21:07 UTC, which is 5:07 PM Eastern during daylight saving time and 4:07 PM otherwise.

The leaderboard is at `https://ryanmccord.github.io/sharps_leaderboard/`.

**Changing a key later:** update the matching repo secret. The next run uses it.

**If the first run fails,** open the run on the Actions tab and expand **Run the tracker**. Database errors print a line starting with `->` that says what to check. The usual causes are:

- **"PAM authentication failed".** AWS didn't accept the sign-in token. Check the user's policy from step 3, the access key secrets, and that `AWS_REGION` matches the cluster.
- **"could not translate host name".** `DB_HOST` isn't the writer endpoint.
- **"database ... does not exist".** `DB_NAME` should be `postgres`, or left out.

X and Anthropic errors name the secret to check. The first database step can take an extra 15–30 seconds while the database wakes up. That's normal.

### Using a full-configuration cluster instead

On a paid AWS plan you can create a regular (full-configuration) cluster with a password instead. In that case:

- Enable the RDS Data API on the cluster.
- Store the password in a Secrets Manager secret of type *Credentials for Amazon RDS database*.
- Set `DB_CLUSTER_ARN` and `DB_SECRET_ARN` as GitHub secrets instead of `DB_HOST`.
- Give the AWS user `rds-data:ExecuteStatement` on the cluster and `secretsmanager:GetSecretValue` on the secret.

The tracker picks whichever setup the secrets point to.

## The database

### accounts

| Column | Type | |
|---|---|---|
| `id` | uuid | primary key |
| `x_handle` | text | unique, case-insensitive |
| `created_at` | timestamptz | when the account was added |
| `active` | boolean | only active accounts are fetched and shown |
| `x_user_id`, `followers`, `last_seen_post_id` | | filled in from the X API |

### picks

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

There's also a `posts` table, which keeps a copy of every post so a pick still counts if it's deleted later. Its `deleted_at` column records when the tracker noticed a post was gone. The `leaderboard` view has each active account's standings, leaving out late picks, plus counts of late and deleted picks.

**Looking at the data by hand:** the RDS console's Query editor doesn't work with express clusters. Use the commands below instead. `python run.py standings` prints the leaderboard view and `python run.py export picks.csv` saves every pick to a spreadsheet. A desktop SQL tool also works: connect to `DB_HOST` as `postgres` over SSL, with a token from `aws rds generate-db-auth-token` as the password.

## Day to day

Run these from a computer with a `.env` file set up (see *Running it yourself* below):

```
python run.py accounts                      # list accounts
python run.py accounts add <handle>         # start tracking someone
python run.py accounts pause <handle>       # stop tracking (history is kept)
python run.py accounts resume <handle>      # track again
python run.py standings                     # print the leaderboard
python run.py review                        # picks that need a hand grade, with links
python run.py set 3f9a1c2e win              # grade one by hand (win / loss / push / void)
python run.py set 3f9a1c2e win 2.5          # ...and override the units won
python run.py export picks.csv              # every pick to a spreadsheet
```

You can also add accounts by adding handles to `accounts.txt` and committing. The next run picks them up. Hand grades and account changes show up on the site after the next daily run.

## How the daily run works

Each day at about 5 PM Eastern (4 PM outside daylight saving time), the run:

1. Makes sure the tables exist and adds any new handles from `accounts.txt`.
2. Fetches each active account's posts since the last run, up to 500 per account. Starting the run wakes the database, which takes about 15–30 seconds.
3. Reads the picks out of new posts.
4. Matches picks to their games, records whether each went out before the start, and grades the ones whose games have finished. Last night's games are final by then.
5. Checks each graded pick post once, at least 12 hours after grading, to see whether the tipster deleted it.
6. Queries the results and rebuilds `docs/index.html`, which GitHub Pages publishes.

If one step fails, for example because X credits ran out, the rest still run and the site still updates. The run shows as failed on the Actions tab with the reason.

**Trade-off of running once a day:** a pick that's posted and then deleted before the next run is never seen. To catch more of those, add a second time in `.github/workflows/daily-update.yml`, for example `- cron: "7 15 * * *"` for about 11 AM Eastern.

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

## Costs

Rough monthly figures at one run a day for nine accounts:

- **Aurora:** on the AWS Free plan, an express cluster within the free limits (up to 4 ACUs, 1 GB of storage) costs nothing. This project stores well under 1 GB a year. The Free plan ends after 6 months or when its credits run out. After that, a cluster that's awake about 10 minutes a day (the run plus 5 idle minutes before it pauses) comes to roughly $1 a month.
- **Secrets Manager:** not used, so no cost.
- **X API:** about $0.005 per post fetched. Only posts since the last run come back, so the cost depends on how much these accounts post, not how often the tracker runs. At 5 to 15 posts per account per day, that's about $7 to $20 a month. The very first run is capped at 20 posts per account.
- **Deleted-post checks:** one more X read per pick post, once. At 5 pick posts per account per day, that's roughly $7 a month for nine accounts. Set `CHECK_DELETIONS=false` to turn it off.
- **Anthropic API:** posts that obviously aren't picks are skipped without an API call. Expect a few dollars a month at most.
- **GitHub:** free for a public repo.
- **Scores:** free. ESPN's public scoreboard feed is unofficial and undocumented, so it could change. If it breaks, swap `tracker/results.py` for a paid feed.

To spend less, track fewer accounts.

## Running it yourself (optional)

```
pip install -r requirements.txt
cp .env.example .env          # then fill in the same values as the GitHub secrets
python run.py all             # the full daily run
```

The local run uses the same Aurora database, so anything you do shows up in the next daily run. To work against a local Postgres instead, set `DATABASE_URL` in `.env` and leave `DB_HOST` blank.

The code can also read the keys from a Secrets Manager secret if you set `APP_SECRET_ID`, but the daily run doesn't use that.

## Files

```
run.py                               command line
accounts.txt                         starter list of handles
tracker/schema.py                    tables, enums and the leaderboard view
tracker/db.py                        database connection (IAM token, RDS Data API, or plain Postgres)
tracker/store.py                     all the SQL
tracker/xapi.py                      X API fetching
tracker/parser.py                    reading picks (Claude, with images)
tracker/results.py                   ESPN scores
tracker/grader.py                    grading logic
tracker/report.py                    builds the site
tracker/site_template.html           the leaderboard page
.github/workflows/daily-update.yml   the daily run
docs/index.html                      the published leaderboard (rebuilt each run)
tests/                               python -m unittest discover tests
                                     (set PG_TEST_URL to also run the Postgres tests)
```

## Notes

- The pick reader is the part most likely to need tuning. After the first few runs, run `python run.py export picks.csv` and check whether it's catching real picks and skipping promos. You can adjust the rules in `SYSTEM` in `tracker/parser.py`.
- This uses only the official X API. No site scraping.

# Sharps Leaderboard

Tracks the free picks that sports-betting accounts post on X and grades them against final scores. Once a day it updates a leaderboard website showing who's actually making money: each account's record, win %, units won and ROI.

```
X API (posts + bet-slip images) → Claude (read picks) → Aurora Postgres (store) → ESPN scores (grade) → leaderboard site
```

The daily job runs on GitHub's servers and signs in to your AWS account without stored keys. Its X and Anthropic keys live in AWS Secrets Manager, and the data lives in an Aurora Serverless Postgres database, which pauses itself when idle, so you only pay for the few minutes a day it's awake. Visitors to the site never touch the database: the daily job queries it once and bakes the results into the page.

## Setup

You'll need about 30 minutes. Steps 1–5 happen in the AWS console and step 6 in GitHub. Pick one AWS region and use it throughout. The steps below assume **us-east-1 (N. Virginia)**.

### 1. Create the database

In the AWS console, open **RDS → Databases → Create database**, then:

1. **Creation method:** Standard create.
2. **Engine:** Aurora (PostgreSQL Compatible). For **version**, pick the newest **16.x**. It needs to be 16.3 or later to pause at zero.
3. **Templates:** Dev/Test.
4. **DB cluster identifier:** `sharps-leaderboard`.
5. **Credentials:** keep the master username `postgres` and choose **Self managed**. Set a long random password and save it somewhere for step 2.
6. **Cluster storage configuration:** Aurora Standard.
7. **Instance configuration:** choose **Serverless v2**. Set the capacity range to **minimum 0 ACUs** and **maximum 1 ACU**, and set it to pause after **5 minutes** of inactivity.
8. **Availability & durability:** Don't create an Aurora Replica.
9. **Connectivity:** keep the default VPC and set **Public access** to **No**. Tick **Enable the RDS Data API**. The daily job reaches the database through this AWS service, so the database is never open to the internet.
10. **Additional configuration:** set **Initial database name** to `sharps`.
11. Click **Create database**. It takes about 10 minutes.

When it's ready, open the cluster (`sharps-leaderboard`, not the instance under it). On the **Configuration** tab, copy the **ARN**. This is your `DB_CLUSTER_ARN`.

### 2. Store the database password in Secrets Manager

The Data API reads the database password from Secrets Manager.

1. Open **Secrets Manager → Store a new secret**.
2. Choose **Credentials for Amazon RDS database**. Enter `postgres` and the password from step 1, then select the `sharps-leaderboard` database.
3. Name it `sharps-leaderboard/db` and store it.
4. Open the secret and copy its **Secret ARN**. This is your `DB_SECRET_ARN`.

### 3. Get the X and Anthropic keys

- **X:** sign in at developer.x.com, create a project and an app, and add credits. Copy the app's **Bearer Token**.
- **Anthropic:** create an API key at console.anthropic.com. It's used to read picks out of post text and bet-slip images.

### 4. Store the app's keys in Secrets Manager

Every key the service needs lives in one secret. The daily job reads it when it starts, so no key is ever stored in GitHub or in this repo.

1. Open **Secrets Manager → Store a new secret** and choose **Other type of secret**.
2. Under **Key/value pairs**, add these four rows:

   | Key | Value |
   |---|---|
   | `X_BEARER_TOKEN` | X bearer token |
   | `ANTHROPIC_API_KEY` | Anthropic API key |
   | `DB_CLUSTER_ARN` | from step 1 |
   | `DB_SECRET_ARN` | from step 2 |

3. Name it `sharps-leaderboard/app` and store it. Leave automatic rotation off.
4. Open the secret and copy its **Secret ARN** for the next step.

**Changing a key later:** open this secret, choose **Retrieve secret value → Edit**, and save. The next run uses the new value, and nothing in GitHub needs to change.

### 5. Let GitHub sign in to AWS

Instead of storing AWS access keys in GitHub, you tell AWS to trust this repository. Each run, GitHub proves which repo and branch it's running from, and AWS hands back credentials that expire when the run ends.

1. **Trust GitHub as a sign-in provider.** Open **IAM → Identity providers → Add provider** and choose **OpenID Connect**. Set *Provider URL* to `https://token.actions.githubusercontent.com` and *Audience* to `sts.amazonaws.com`, then add it. You only ever do this once per AWS account.
2. **Create a role for the daily job.** Open **IAM → Roles → Create role** and choose **Web identity**. Pick the `token.actions.githubusercontent.com` provider and the `sts.amazonaws.com` audience. Set *GitHub organization* to `RyanMcCord`, *GitHub repository* to `sharps_leaderboard`, and *GitHub branch* to `main`. Skip adding permissions, name the role `sharps-leaderboard-github`, and create it.

   Only runs from this repo's `main` branch can use the role. Forks and other repos can't. To check, open the role's **Trust relationships** tab. The condition should read:
   ```json
   "token.actions.githubusercontent.com:sub": "repo:RyanMcCord/sharps_leaderboard:ref:refs/heads/main"
   ```
3. **Give the role only what it needs.** On the role, choose **Add permissions → Create inline policy**, switch to **JSON**, and paste this, replacing the three ARNs with yours:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["rds-data:ExecuteStatement", "rds-data:BatchExecuteStatement"],
         "Resource": "YOUR_DB_CLUSTER_ARN"
       },
       {
         "Effect": "Allow",
         "Action": "secretsmanager:GetSecretValue",
         "Resource": ["YOUR_APP_SECRET_ARN", "YOUR_DB_SECRET_ARN"]
       }
     ]
   }
   ```
   Name the policy `sharps-leaderboard-access`. The role can run SQL on this one database and read these two secrets, nothing else.
4. Copy the role's **ARN** from the top of its page.

### 6. Point GitHub at the role and turn on the site

1. In this repo, open **Settings → Secrets and variables → Actions**, switch to the **Variables** tab, and add two repository variables:

   | Variable | Value |
   |---|---|
   | `AWS_ROLE_ARN` | the role ARN from step 5 |
   | `AWS_REGION` | `us-east-1` |

   These aren't secret: a role ARN is useless outside this repo. If you added any repository *secrets* in an earlier version of these steps, delete them, because they're no longer used.
2. Open **Settings → Pages**, set *Source* to **Deploy from a branch**, then choose **main** and **/docs**.
3. On the **Actions** tab, open **Daily update** and click **Run workflow**. The first run creates the tables and adds the accounts from `accounts.txt`. After that it runs by itself every day at about 5 PM Eastern.

The leaderboard is at `https://ryanmccord.github.io/sharps_leaderboard/`.

**If the run fails at "Sign in to AWS",** check the two variables and the role's trust relationship from step 5. **If it fails reading the secret,** check the secret name is exactly `sharps-leaderboard/app` and that its ARN is in the role's policy.

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

**Looking at the data by hand:** in the RDS console, open **Query editor**, choose the cluster, and pick **Connect with a Secrets Manager ARN** using your secret ARN and database `sharps`. Then run, for example:

```sql
SELECT * FROM leaderboard;
SELECT * FROM picks ORDER BY created_at DESC LIMIT 50;
```

The editor wakes the database. It pauses again 5 minutes after you stop querying.

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

Each day at about 5 PM Eastern, the run:

1. Makes sure the tables exist and adds any new handles from `accounts.txt`.
2. Fetches each active account's posts since the last run, which wakes the database (about 15–30 seconds).
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

- **Aurora:** the database is awake about 10 minutes a day (the run plus 5 idle minutes before it pauses) at the smallest size, which comes to roughly $1 a month. Storage for this much data is pennies. When paused there's no compute charge. Leaving the query editor connected keeps it awake, so close it when you're done.
- **Secrets Manager:** about $0.40 a month per secret, so about $0.80 for the two. Reading them once a day is a fraction of a cent.
- **X API:** about $0.005 per post fetched. Only posts since the last run come back, so the cost depends on how much these accounts post, not how often the tracker runs. At 5 to 15 posts per account per day, that's about $7 to $20 a month. The very first run is capped at 20 posts per account.
- **Deleted-post checks:** one more X read per pick post, once. At 5 pick posts per account per day, that's roughly $7 a month for nine accounts. Set `CHECK_DELETIONS=false` to turn it off.
- **Anthropic API:** posts that obviously aren't picks are skipped without an API call. Expect a few dollars a month at most.
- **GitHub:** free for a public repo.
- **Scores:** free. ESPN's public scoreboard feed is unofficial and undocumented, so it could change. If it breaks, swap `tracker/results.py` for a paid feed.

To spend less, track fewer accounts.

## Running it yourself (optional)

```
pip install -r requirements.txt
cp .env.example .env
python run.py all             # the full daily run
```

To use the same keys as the daily job, sign in to AWS on your computer (`aws configure` or `aws sso login`) with an account allowed to read the `sharps-leaderboard/app` secret. Then set `APP_SECRET_ID` and `AWS_REGION` in `.env`. Anything you set directly in `.env` overrides the value in the secret, which is handy for trying a different key without touching the shared one.

The local run uses the same Aurora database, so anything you do shows up in the next daily run. To work against a local Postgres instead, set `DATABASE_URL` in `.env` and `pip install "psycopg[binary]"`.

## Files

```
run.py                               command line
accounts.txt                         starter list of handles
tracker/schema.py                    tables, enums and the leaderboard view
tracker/db.py                        database connection (RDS Data API, or direct Postgres)
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

# Sharps Leaderboard

Tracks the free picks that sports-betting accounts post on X and grades them against final scores. Once a day it updates a leaderboard website showing who's actually making money: each account's record, win %, units won and ROI.

```
X API (posts + bet-slip images) → Claude (read picks) → Aurora Postgres (store) → ESPN scores (grade) → leaderboard site
```

The daily job runs on GitHub's servers. The data lives in an Aurora Serverless Postgres database in your AWS account, which pauses itself when idle, so you only pay for the few minutes a day it's awake. Visitors to the site never touch the database: the daily job queries it once and bakes the results into the page.

## Setup

You'll need about 30 minutes. Steps 1–4 happen in the AWS console and step 5 in GitHub. Pick one AWS region and use it throughout. The steps below assume **us-east-1 (N. Virginia)**.

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

The Data API reads the password from Secrets Manager, so it's never in GitHub.

1. Open **Secrets Manager → Store a new secret**.
2. Choose **Credentials for Amazon RDS database**. Enter `postgres` and the password from step 1, then select the `sharps-leaderboard` database.
3. Name it `sharps-leaderboard/db` and store it.
4. Open the secret and copy its **Secret ARN**. This is your `DB_SECRET_ARN`.

### 3. Create a limited AWS user for the daily job

This user can run SQL on this one database and read this one password, nothing else.

1. Open **IAM → Users → Create user** and name it `sharps-leaderboard-bot`. Don't give it console access, and skip permissions for now.
2. Open the new user, then choose **Add permissions → Create inline policy**. Switch to **JSON** and paste this, replacing the two ARNs with yours:
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
         "Resource": "YOUR_DB_SECRET_ARN"
       }
     ]
   }
   ```
   Name the policy `sharps-leaderboard-db`.
3. On the user's **Security credentials** tab, choose **Create access key → Application running outside AWS**. Copy the **access key ID** and the **secret access key**. The secret key is shown only once.

### 4. Get the X and Anthropic keys

- **X:** sign in at developer.x.com, create a project and an app, and add credits. Copy the app's **Bearer Token**.
- **Anthropic:** create an API key at console.anthropic.com. It's used to read picks out of post text and bet-slip images.

### 5. Add the secrets to GitHub and turn on the site

1. In this repo, open **Settings → Secrets and variables → Actions** and add these repository secrets:

   | Secret | Value |
   |---|---|
   | `X_BEARER_TOKEN` | X bearer token |
   | `ANTHROPIC_API_KEY` | Anthropic API key |
   | `AWS_ACCESS_KEY_ID` | from step 3 |
   | `AWS_SECRET_ACCESS_KEY` | from step 3 |
   | `AWS_REGION` | `us-east-1` |
   | `DB_CLUSTER_ARN` | from step 1 |
   | `DB_SECRET_ARN` | from step 2 |

   Secrets stay hidden even though the repo is public. Never put keys in a file in the repo.
2. Open **Settings → Pages**, set *Source* to **Deploy from a branch**, then choose **main** and **/docs**.
3. On the **Actions** tab, open **Daily update** and click **Run workflow**. The first run creates the tables and adds the accounts from `accounts.txt`. After that it runs by itself every day at about 5 PM Eastern.

The leaderboard is at `https://ryanmccord.github.io/sharps_leaderboard/`.

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
| `team`, `opponent`, `side`, `point` | | what grading needs: the side taken, over/under, and the spread or total number (-4.5, 215.5) |
| `units`, `legs` | | units stated, and the legs of a parlay or teaser |
| `result`, `units_net`, `graded_at`, `note` | | `pending`, `win`, `loss`, `push`, `void` or `manual`, plus units won or lost |
| `post_id` | text | the X post it came from |

There's also a `posts` table, which keeps a copy of every post so a pick still counts if it's deleted later, and a `leaderboard` view with each active account's standings.

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
4. Grades every pick whose game has finished. Last night's games are final by then.
5. Queries the results and rebuilds `docs/index.html`, which GitHub Pages publishes.

If one step fails, for example because X credits ran out, the rest still run and the site still updates. The run shows as failed on the Actions tab with the reason.

**Trade-off of running once a day:** a pick that's posted and then deleted before the next run is never seen. To catch more of those, add a second time in `.github/workflows/daily-update.yml`, for example `- cron: "7 15 * * *"` for about 11 AM Eastern.

## The leaderboard

Accounts are ranked by units won. Each account's bar extends right from the zero line if they're up and left if they're down. You can filter to the last 30 or 7 days or to one league, and click an account to see its results by league and its latest picks, with links to the original posts.

## What gets graded automatically

| Bet type | Graded? |
|---|---|
| Moneyline, spread, over/under | Yes, from final scores |
| Parlays of those | Yes. One losing leg makes it a loss, and pushed legs drop out |
| Props, futures, teasers, other | Marked `manual` |
| Postponed or canceled games | `void` (not counted) |
| Sport `OTHER` | `manual` |

Picks are also marked `manual` when the team can't be matched to a game, or when more than one game fits.

## How the numbers work

- **Units:** treated as the amount risked. "2u" on a -110 winner is +1.82u. A pick with no stated units counts as 1u.
- **Odds:** the `line` from the post. When it's missing, the tracker assumes `DEFAULT_ODDS` (-110), which is generous for underdog-heavy tipsters.
- **ROI:** units won divided by units risked, with pushes excluded.
- **Small samples:** accounts with fewer than 30 graded picks are faded out on the leaderboard. 55% over 20 picks means very little.
- **Game matching:** a pick is matched to the first game for that team on or after the post, looking ahead 2 days (7 for football, since those picks often go out midweek). A pick posted after the game started is still graded, with the note "live bet?".

## Costs

Rough monthly figures at one run a day for nine accounts:

- **Aurora:** the database is awake about 10 minutes a day (the run plus 5 idle minutes before it pauses) at the smallest size, which comes to roughly $1 a month. Storage for this much data is pennies. When paused there's no compute charge. Leaving the query editor connected keeps it awake, so close it when you're done.
- **Secrets Manager:** about $0.40 a month for the one secret.
- **X API:** about $0.005 per post fetched. Only posts since the last run come back, so the cost depends on how much these accounts post, not how often the tracker runs. At 5 to 15 posts per account per day, that's about $7 to $20 a month. The very first run is capped at 20 posts per account.
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

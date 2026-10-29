# X Bots

A home for several bots that work with X (Twitter) data, sharing one codebase, one database and one setup. Each bot runs on its own schedule on GitHub Actions.

The repo started as the Sharps Leaderboard, which is still its first bot (and the reason for the repo's name).

## Bots

| Bot | What it does | Runs | Docs |
|---|---|---|---|
| `sharps-leaderboard` | Grades the free picks betting tipsters post on X and publishes a [leaderboard](https://ryanmccord.github.io/sharps_leaderboard/) | Daily, 21:07 UTC | [bots/sharps_leaderboard](bots/sharps_leaderboard/README.md) |

## How it's built

The code follows the [hexagonal architecture](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/hexagonal-architecture.html) (ports and adapters) pattern. Each bot's business logic knows nothing about X, AWS or any other service. It talks to the outside world only through **ports** (interfaces), and **adapters** plug real technology into those ports.

```
              primary adapters                                   secondary adapters
        (things that drive a bot)                          (things a bot drives)

   command line ─┐                                       ┌─ X API            (PostSource)
  python -m xbots│     ┌───────────────────────────┐     ├─ Aurora/Postgres  (SqlDatabase, repositories)
                 ├────▶│ application (use cases)   │────▶├─ Claude           (e.g. PickReader)
 GitHub Actions ─┘     │   ↓ uses                  │     ├─ ESPN             (e.g. ScoresProvider)
   (schedules)         │ domain (business rules)   │     └─ static site      (e.g. SitePublisher)
                       └───────────────────────────┘
                          input port        output ports
```

The rules that keep it that way:

- **Domain** (`bots/<bot>/domain/`) is plain logic: no network, database, files or settings. It's tested on its own.
- **Application** (`bots/<bot>/application.py`) holds the use cases, and is the bot's input port. It depends only on ports, never on an adapter.
- **Ports** are abstract classes. Shared ones live in `xbots/ports/`: reading posts, publishing posts, and a SQL database. A bot's own ports live in `bots/<bot>/ports.py`.
- **Adapters** implement ports. Shared ones live in `xbots/adapters/` (X API, the three database connections, Claude). A bot's own adapters live in `bots/<bot>/adapters/`.
- **Composition root** (`bots/<bot>/app.py`) is the only place that picks which adapter goes behind each port. Tests swap in in-memory adapters instead.
- **Each bot owns its data.** Its tables live in its own Postgres schema (the leaderboard uses `sharps`), so bots share the database without clashing.

```
xbots/                     shared by every bot
  core/                    shared domain objects: Post, XUser
  ports/                   PostSource, PostPublisher, SqlDatabase
  adapters/                x_api.py, database.py, claude.py
  config.py                shared settings (keys, database)
  registry.py              the list of bots
  cli.py                   python -m xbots <bot> <command>
bots/
  sharps_leaderboard/      the first bot (see its README)
    domain/  ports.py  application.py  adapters/  app.py  cli.py  settings.py
tests/
  shared/                  the shared adapters
  sharps_leaderboard/      domain, use cases (in-memory), adapters, Postgres
.github/workflows/
  run-bot.yml              shared runner every bot's workflow calls
  sharps-leaderboard.yml   the leaderboard's schedule
  tests.yml                all tests (with a real Postgres) on every push
docs/                      what bots publish with GitHub Pages
```

## Setup

You'll need about 20 minutes. Steps 1–3 happen in the AWS console and step 4 in GitHub. Every bot shares this setup. Create everything in one AWS region. This project's database is in **us-east-2 (Ohio)**, so check the region selector in the top-right of the AWS console says Ohio before each step.

### 1. Create the database

1. In the AWS console, open **RDS → Create database** and choose **Aurora (PostgreSQL Compatible)**.
2. Choose **Express configuration**. On the AWS Free plan it's the only option. Full configuration is greyed out until you upgrade.
3. Name it `sharps-leaderboard`. If the page offers capacity settings, set the **minimum to 0 ACUs** and **pause after 5 minutes**, so it sleeps between runs. Leave everything else as it is.
4. Create it and wait until its status says **Available**.
5. Open the cluster and go to the **Connectivity & security** tab. Under **Endpoints**, copy the endpoint whose type is **Writer**. It looks like `sharps-leaderboard.cluster-xxxxxxxx.us-east-2.rds.amazonaws.com`. This is your `DB_HOST`.

Express clusters have no password and can't be given one. Sign-in is by IAM token only. They also have no initial database, so the bots use the built-in `postgres` database, each in its own schema. You don't need to enable the RDS Data API, and you don't need a Secrets Manager secret.

### 2. Get the X and Anthropic keys

- **X:** sign in at developer.x.com, create a project and an app inside it, set up pay-per-use billing and add credits. Copy the app's **Bearer Token**.
- **Anthropic:** sign in at platform.claude.com, add credits under **Billing**, then create a key under **API Keys**.

### 3. Create a limited AWS user for the bots

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

1. In this repo, open **Settings → Secrets and variables → Actions** and add these repository secrets. Every bot's workflow shares them.

   | Secret | Value |
   |---|---|
   | `X_BEARER_TOKEN` | X bearer token |
   | `ANTHROPIC_API_KEY` | Anthropic API key |
   | `AWS_ACCESS_KEY_ID` | from step 3 |
   | `AWS_SECRET_ACCESS_KEY` | from step 3 |
   | `AWS_REGION` | the region your database is in: `us-east-2` |
   | `DB_HOST` | the writer endpoint from step 1 |
   | `DB_NAME` | optional. Leave it out, or set it to `postgres` |

   Secrets stay hidden even though the repo is public. Never put keys in a file in the repo.
2. Open **Settings → Pages**, set *Source* to **Deploy from a branch**, then choose **main** and **/docs**.
3. On the **Actions** tab, open a bot's workflow (e.g. **Sharps leaderboard**) and click **Run workflow**. After that it runs on its own schedule.

**Changing a key later:** update the matching repo secret. The next run uses it.

**If a run fails,** open it on the Actions tab and expand the step called **Run** followed by the bot's name. Errors print a line starting with `->` that says what to check. The usual causes are:

- **"PAM authentication failed".** AWS didn't accept the database sign-in token. Check the user's policy from step 3, the access key secrets, and that `AWS_REGION` matches the cluster.
- **"could not translate host name".** `DB_HOST` isn't the writer endpoint.
- **"database ... does not exist".** `DB_NAME` should be `postgres`, or left out.
- **"client-not-enrolled" from X.** The X app isn't inside a Project, or doesn't have pay-per-use billing set up. Fix both in the X developer console, then regenerate the Bearer Token.

The first database step can take an extra 15–30 seconds while the database wakes up. That's normal. Each run also runs the tests first, so a broken change stops before it touches any data.

### Using a full-configuration cluster instead

On a paid AWS plan you can create a regular (full-configuration) cluster with a password instead. In that case:

- Enable the RDS Data API on the cluster.
- Store the password in a Secrets Manager secret of type *Credentials for Amazon RDS database*.
- Set `DB_CLUSTER_ARN` and `DB_SECRET_ARN` as GitHub secrets instead of `DB_HOST`.
- Give the AWS user `rds-data:ExecuteStatement` on the cluster and `secretsmanager:GetSecretValue` on the secret.

The bots use whichever setup the secrets point to.

## Running bots from your computer

```
pip install -r requirements.txt
cp .env.example .env               # then fill in the same values as the GitHub secrets
python -m xbots list               # the bots
python -m xbots sharps-leaderboard # a bot's commands
python -m xbots sharps-leaderboard standings
```

`python run.py <bot> <command>` is a shortcut for the same thing. The older `python run.py <command>`, with no bot name, still runs the sharps leaderboard. A local run uses the same Aurora database as the daily runs. To work against a local Postgres instead, set `DATABASE_URL` in `.env` and leave `DB_HOST` blank.

## Adding a bot

1. **Create `bots/<your_bot>/`** with the same layers as the leaderboard:
   - `__init__.py`, with `NAME = "your-bot"` and a one-line `DESCRIPTION`.
   - `domain/`, with the business rules as plain functions and data, and no I/O.
   - `ports.py`, with abstract classes for anything the bot needs that the shared ports don't cover. Use the shared `PostSource` for reading X.
   - `application.py`, with the use cases. They take ports in their constructor and never import an adapter.
   - `adapters/`, with the bot's own adapters. If it stores data, use a schema named after the bot, and create and upgrade its tables in a `setup` step that's safe to re-run.
   - `app.py`, the composition root. It builds the use cases with real adapters (`xbots.adapters.database.connect()`, `XApiPostSource()` and your own).
   - `cli.py`, with `main(args) -> int`, the bot's commands.
2. **Register it** in `xbots/registry.py`.
3. **Test it** in `tests/<your_bot>/`. Test the domain on its own and the use cases with in-memory adapters, the way `tests/sharps_leaderboard/` does.
4. **Schedule it** with a workflow that calls the shared runner. Copy `.github/workflows/sharps-leaderboard.yml`, and change the name, schedule, `bot`, and `publish` (the files it writes for GitHub Pages, e.g. `docs/your-bot/`; leave it empty if it publishes nothing).
5. **Add settings it needs:** shared keys go in `xbots/config.py` and new secrets in `run-bot.yml`. Bot-only settings go in the bot's own `settings.py`.

## Posting to X

No bot posts yet. The shared `PostPublisher` port is ready for it, and `XApiPostPublisher` is a placeholder that says so. Posting needs credentials for the bot's own X account, not the read-only bearer token: OAuth 1.0a keys and an access token, or OAuth 2.0 with the `tweet.write` scope. X also charges per post created. When a bot needs it, the work is to implement `XApiPostPublisher.publish`, add those credentials as secrets, and pass the publisher to the bot in its `app.py`.

## Tests

```
python -m unittest                 # everything that doesn't need a database
PG_TEST_URL=postgresql://user:pass@localhost/test python -m unittest   # plus the database tests
```

The **Tests** workflow runs all of them on every push, against a throwaway Postgres.

## Notes

- Only the official X API is used. No site scraping.
- The database adapter accepts `:name` placeholders and explicit `CAST(...)`s, so the same SQL works through IAM sign-in, the RDS Data API and plain Postgres.

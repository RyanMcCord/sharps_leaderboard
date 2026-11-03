# AI art

Posts your Midjourney images to X, one every 4 hours, each with a caption Claude writes after looking at it.

You make the images in Midjourney as usual and drop the ones you want posted into a Google Drive folder. The bot takes them oldest first, posts each one once, and stops when the folder runs dry. Add more images and it carries on.

**Midjourney itself isn't automated.** Midjourney has no API, and its Terms of Service forbid using "automated tools to access, interact with, or generate Assets". Tools that claim to automate it work by driving someone's account, and accounts get banned for it. So the bot never touches Midjourney: you generate, it posts.

## How a run works

Every 4 hours (`.github/workflows/ai-art.yml`), the bot:

1. Lists the Drive folder and picks the oldest image it hasn't posted.
2. Makes the image fit X: PNG, JPEG and WebP up to 5 MB go up as they are. Anything bigger, which Midjourney PNGs often are, or in another format, is re-encoded as a high-quality JPEG that fits.
3. Writes the caption. Claude looks at the image and writes a caption in your chosen style, plus plain alt text for screen readers. If a text file with the same name sits beside the image (`fox.png` and `fox.txt`), its contents are the caption instead. Claude still writes the alt text.
4. Posts the image with its caption and alt text, and records it so it never posts again.

Captions never contain links: X charges $0.20 for a post with a link and $0.015 for one without, so any link is removed. Captions are also trimmed to fit X's 280-character limit.

**No double posts.** The bot records an image as mid-post before it calls X.
- If X refuses the post (rate limit, an expired token), the record is undone and the image is tried again next run.
- If the run dies without an answer from X, the image stays marked, because the post may have gone out. The bot moves on, and `status` lists the image as stuck. Check the account, and if it didn't post, run `requeue <name>`.

Files the bot can't post, such as videos or unreadable images, are recorded as skipped so the queue moves past them.

## Setup

You need an X account for the bot, a Google account and the repo's existing setup (database, Anthropic key; see the [main README](../../README.md#setup)). It takes about 20 minutes.

### 1. The bot's X account

1. Create the X account the images will post from.
2. Label it as automated, as X's rules require. Signed in as the bot, open **Settings and privacy → Your account → Account information → Automation**, and set your own account as the managing account. The profile then shows "Automated by @you".
3. Saying in the bio that the images are AI-generated is a good idea, too.

### 2. Let your X app post

The bot posts through the X developer app you already use for the leaderboard. Posting needs extra permission, plus a token for the bot account.

1. In the [X developer console](https://developer.x.com), open your app and go to **User authentication settings → Set up** (or **Edit**):
   - **App permissions:** **Read and write**.
   - **Type of app:** **Web App, Automated App or Bot**.
   - **Callback URI** and **Website URL:** any valid URL, such as this repo's GitHub page. The sign-in below doesn't use them.
2. Under **Keys and tokens**, copy the **API Key and Secret** (regenerate them if you don't have them). These are `X_API_KEY` and `X_API_SECRET`.
3. Get the bot account's own token, on your computer:
   1. Put the two keys in your `.env` file.
   2. In your browser, sign in to X **as the bot account**.
   3. Run:

      ```bash
      python -m xbots ai-art authorize
      ```

   4. It prints a link. Open it, click **Authorize app**, and type the PIN X shows.
   5. It prints `AI_ART_X_ACCESS_TOKEN` and `AI_ART_X_ACCESS_SECRET`. Keep them private: anyone with them can post as the account.

If you change the app's permissions later, run `authorize` again. Tokens made before the change keep the old permissions.

### 3. A Google service account for Drive

The bot reads the folder as a Google "service account", a robot Google user that only sees what you share with it.

1. In the [Google Cloud console](https://console.cloud.google.com), create a project, e.g. `x-bots`.
2. Open **APIs & Services → Library**, search for **Google Drive API**, and click **Enable**.
3. Open **IAM & Admin → Service Accounts → Create service account**. Name it `ai-art-bot`, skip the optional steps, and click **Done**.
4. Open the new service account and go to **Keys → Add key → Create new key → JSON**. A file downloads. Its whole contents are `GOOGLE_SERVICE_ACCOUNT_JSON`.
5. Copy the service account's email. It looks like `ai-art-bot@x-bots.iam.gserviceaccount.com`.

### 4. The Drive folder

1. In Google Drive, create a folder for the queue, e.g. `AI art queue`.
2. **Share** it with the service account's email, as **Viewer**. The bot never changes or deletes anything.
3. Open the folder and copy its id: the last part of the URL, after `drive.google.com/drive/folders/`. That's `AI_ART_DRIVE_FOLDER_ID`.

### 5. Add the secrets to GitHub

In this repo, open **Settings → Secrets and variables → Actions → New repository secret**, and add:

| Secret | Value |
|---|---|
| `X_API_KEY` | the app's API Key (step 2) |
| `X_API_SECRET` | the app's API Key Secret (step 2) |
| `AI_ART_X_ACCESS_TOKEN` | from `authorize` (step 2) |
| `AI_ART_X_ACCESS_SECRET` | from `authorize` (step 2) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the whole JSON key file (step 3) |
| `AI_ART_DRIVE_FOLDER_ID` | the folder id (step 4) |

**Optional caption style:** to change how captions sound, open the **Variables** tab on the same page and add `AI_ART_CAPTION_STYLE`, e.g. `Two or three words, all lowercase, like a gallery title.` The default is one short, evocative line, with no hashtags, emoji or links, under 120 characters.

### 6. Try it, then turn on the schedule

1. Put a few images in the Drive folder.
2. On the **Actions** tab, open **AI art → Run workflow**, type `preview` as the command, and run it. The log shows which image would post next and the caption and alt text it would get, without posting anything.
3. Run it again with `post` to post one now.
4. Turn on the schedule: on the **Variables** tab (Settings → Secrets and variables → Actions), add `AI_ART_ENABLED` with the value `true`. From then on it posts every 4 hours. Until it's set, the schedule stays off, so nothing fails while you're setting up. To pause the bot, set it to `false`.

## Commands

Run these from your computer (`python -m xbots ai-art <command>`, with your `.env` filled in), or from **Run workflow** for `post`, `preview` and `status`.

| Command | What it does |
|---|---|
| `post` | Post the next image (what the schedule runs) |
| `preview` | Show the next image and its caption, without posting |
| `status` | How many images are waiting and how many days that lasts, what's next, and anything stuck |
| `requeue <name>` | Make an image postable again: a stuck one, or one you want to post again |
| `setup` | Create the bot's tables (every command does this too) |
| `authorize` | Get the bot account's X token (step 2) |

## Costs

- **X:** $0.015 per post on pay-per-use. Every 4 hours is 6 posts a day, about $2.70 a month.
- **Claude:** about a cent per caption.
- **Google Drive** and **GitHub Actions:** free at this size.

At 6 posts a day, the queue empties fast: 42 images last a week. `status` tells you how many days you have left.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `AI_ART_CAPTION_STYLE` | one short, evocative line | How captions should sound, in plain words |
| `AI_ART_HOURS_BETWEEN_POSTS` | `4` | Only used by `status` to estimate how long the queue lasts. The actual schedule is the cron line in `.github/workflows/ai-art.yml` |

## Data

Everything lives in the `ai_art` Postgres schema, in one table:

**`posts`**: one row per image the bot has dealt with.

| Column | Meaning |
|---|---|
| `image_id` | The file's Drive id |
| `file_name` | The file's name |
| `status` | `posted`, `skipped`, or `posting` (mid-post, or stuck) |
| `reason` | Why it was skipped |
| `caption` | What was posted |
| `alt_text` | The alt text sent with it |
| `x_post_id` | The post on X |
| `started_at` | When posting began |
| `posted_at` | When it posted |

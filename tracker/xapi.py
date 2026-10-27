"""Fetch recent posts for the tracked accounts from the official X API (v2).

Billing note: X charges per post returned (and per user returned on lookups),
so we only ask for posts newer than the last one we saw.
"""
import time

import requests

from . import config, store

API = "https://api.x.com/2"


class XAPIError(RuntimeError):
    pass


def _get(path, params):
    if not config.X_BEARER_TOKEN:
        raise XAPIError("X_BEARER_TOKEN is not set. Add it to your .env file.")
    headers = {"Authorization": f"Bearer {config.X_BEARER_TOKEN}"}
    for attempt in range(3):
        r = requests.get(f"{API}{path}", headers=headers, params=params, timeout=30)
        if r.status_code == 429:  # rate limited: wait until the window resets
            reset = int(r.headers.get("x-rate-limit-reset", time.time() + 60))
            wait = max(5, reset - int(time.time()))
            print(f"  rate limited, waiting {wait}s")
            time.sleep(min(wait, 900))
            continue
        if r.status_code == 402:
            raise XAPIError("X API says payment is required - add credits in the developer console.")
        if not r.ok:
            raise XAPIError(f"{r.status_code} from X API: {r.text[:300]}")
        return r.json()
    raise XAPIError("Gave up after repeated rate limiting.")


def resolve_accounts(db, accounts):
    """Look up X user IDs for accounts we haven't resolved yet (one paid user read each, once)."""
    missing = [a["x_handle"] for a in accounts if not a["x_user_id"]]
    for i in range(0, len(missing), 100):
        data = _get("/users/by", {"usernames": ",".join(missing[i:i + 100]), "user.fields": "public_metrics"})
        for u in data.get("data", []):
            store.save_x_user(db, u["username"], u["id"], u.get("public_metrics", {}).get("followers_count"))
        for err in data.get("errors", []):
            print(f"  could not find account: {err.get('value') or err.get('detail')}")


def fetch_new_posts(db):
    """Pull posts newer than the last one stored for each active account. Returns the count of new posts."""
    resolve_accounts(db, store.list_accounts(db))
    total = 0
    for acct in store.list_accounts(db):
        if not acct["x_user_id"]:
            continue
        # First run: only a small backfill. After that, room for a full day of posts
        # (you're only billed for posts actually returned, and since_id keeps it to new ones).
        params = {
            "max_results": config.MAX_POSTS_PER_ACCOUNT if acct["last_seen_post_id"] else config.FIRST_RUN_POSTS,
            "exclude": "retweets",
            "tweet.fields": "created_at,attachments,referenced_tweets,note_tweet",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url,type",
        }
        if acct["last_seen_post_id"]:
            params["since_id"] = acct["last_seen_post_id"]
        data = _get(f"/users/{acct['x_user_id']}/tweets", params)
        media = {m["media_key"]: m for m in data.get("includes", {}).get("media", [])}
        posts = data.get("data", [])
        for p in posts:
            urls = []
            for key in p.get("attachments", {}).get("media_keys", []):
                m = media.get(key, {})
                url = m.get("url") or m.get("preview_image_url")
                if url:
                    urls.append(url)
            text = (p.get("note_tweet") or {}).get("text") or p.get("text", "")
            store.insert_post(db, p["id"], acct["id"], p["created_at"], text, urls, p)
        if posts:
            newest = data.get("meta", {}).get("newest_id") or max(posts, key=lambda p: int(p["id"]))["id"]
            store.set_last_seen(db, acct["id"], newest)
        print(f"  @{acct['x_handle']}: {len(posts)} new posts")
        total += len(posts)
    return total


def check_deletions(db):
    """Re-read each pick post once, after its picks are graded, to see whether it was deleted.

    Posts that still exist cost one read each; deleted ones come back as "Not Found"
    errors. Posts from accounts that went private or were suspended aren't counted as
    deleted. Returns the number of deleted posts found.
    """
    if not config.CHECK_DELETIONS:
        print("  skipped (CHECK_DELETIONS is off)")
        return 0
    found = 0
    while True:
        ids = store.posts_to_check_for_deletion(db)
        if not ids:
            break
        data = _get("/tweets", {"ids": ",".join(ids), "tweet.fields": "id"})
        deleted = set()
        for err in data.get("errors", []):
            pid = err.get("value") or err.get("resource_id")
            # "Not Found" means deleted. Other errors (account protected or suspended)
            # mean we can't tell, so the post is marked checked but not deleted.
            if pid in ids and "Not Found" in (err.get("title") or ""):
                deleted.add(pid)
        store.record_deletion_check(db, ids, deleted)
        found += len(deleted)
        if len(ids) < 100:
            break
    print(f"  {found} deleted pick posts found")
    return found

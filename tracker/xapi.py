"""Fetch recent posts for the tracked accounts from the official X API (v2).

Billing note: X charges per post returned (and per user returned on lookups),
so we only ask for posts newer than the last one we saw.
"""
import time

import requests

from . import config
from .db import dumps

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


def resolve_accounts(conn, handles):
    """Look up user IDs for handles we haven't seen yet (one paid user read each, once)."""
    known = {row["handle"].lower() for row in conn.execute("SELECT handle FROM accounts WHERE user_id IS NOT NULL")}
    missing = [h for h in handles if h.lower() not in known]
    for i in range(0, len(missing), 100):
        batch = missing[i:i + 100]
        data = _get("/users/by", {"usernames": ",".join(batch), "user.fields": "public_metrics"})
        for u in data.get("data", []):
            conn.execute(
                "INSERT INTO accounts(handle, user_id, followers) VALUES (?,?,?) "
                "ON CONFLICT(handle) DO UPDATE SET user_id=excluded.user_id, followers=excluded.followers",
                (u["username"], u["id"], u.get("public_metrics", {}).get("followers_count")),
            )
        for err in data.get("errors", []):
            print(f"  could not find account: {err.get('value') or err.get('detail')}")
    conn.commit()


def fetch_new_posts(conn, handles):
    """Pull posts newer than the last one stored for each account. Returns the count of new posts."""
    resolve_accounts(conn, handles)
    wanted = {h.lower() for h in handles}
    total = 0
    for acct in conn.execute("SELECT * FROM accounts WHERE user_id IS NOT NULL").fetchall():
        if acct["handle"].lower() not in wanted:
            continue
        params = {
            "max_results": config.MAX_POSTS_PER_ACCOUNT,
            "exclude": "retweets",
            "tweet.fields": "created_at,attachments,referenced_tweets,note_tweet",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url,type",
        }
        if acct["last_seen_id"]:
            params["since_id"] = acct["last_seen_id"]
        data = _get(f"/users/{acct['user_id']}/tweets", params)
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
            conn.execute(
                "INSERT OR IGNORE INTO posts(id, handle, created_at, text, media_urls, raw) VALUES (?,?,?,?,?,?)",
                (p["id"], acct["handle"], p["created_at"], text, dumps(urls), dumps(p)),
            )
        if posts:
            newest = data.get("meta", {}).get("newest_id") or max(posts, key=lambda p: int(p["id"]))["id"]
            conn.execute("UPDATE accounts SET last_seen_id=? WHERE handle=?", (newest, acct["handle"]))
        conn.commit()
        print(f"  @{acct['handle']}: {len(posts)} new posts")
        total += len(posts)
    return total

"""X API (v2) adapters for the PostSource and PostPublisher ports.

Billing note: X charges per post returned (and per user returned on lookups), so callers
ask only for posts newer than the last one they saw.
"""
import time

import requests

from .. import config
from ..core import Post, XUser
from ..ports import PostPublisher, PostSource

API = "https://api.x.com/2"


class XAPIError(RuntimeError):
    pass


def _get(path, params, token=None):
    token = token or config.X_BEARER_TOKEN
    if not token:
        raise XAPIError("X_BEARER_TOKEN is not set. Add it as a GitHub repo secret (or to .env when running locally).")
    headers = {"Authorization": f"Bearer {token}"}
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
        if r.status_code == 403 and "client-not-enrolled" in r.text:
            raise XAPIError("X API says this app isn't enrolled (403 client-not-enrolled).\n"
                            "    -> In the X developer console: put the app inside a Project, turn on pay-per-use "
                            "billing with some credits, then regenerate the Bearer Token and update X_BEARER_TOKEN.")
        if r.status_code == 401:
            raise XAPIError("X API rejected the token (401). Check X_BEARER_TOKEN is the app's Bearer Token.")
        if not r.ok:
            raise XAPIError(f"{r.status_code} from X API: {r.text[:300]}")
        return r.json()
    raise XAPIError("Gave up after repeated rate limiting.")



def _to_post(p, media):
    urls = []
    for key in p.get("attachments", {}).get("media_keys", []):
        m = media.get(key, {})
        url = m.get("url") or m.get("preview_image_url")
        if url:
            urls.append(url)
    text = (p.get("note_tweet") or {}).get("text") or p.get("text", "")
    return Post(id=p["id"], created_at=p["created_at"], text=text, media_urls=tuple(urls), raw=p)


class XApiPostSource(PostSource):
    """Reads posts with an app's bearer token (read-only access)."""

    def __init__(self, bearer_token=None, get=_get):
        self.token = bearer_token
        self._get = get

    def _call(self, path, params):
        return self._get(path, params, self.token) if self.token else self._get(path, params)

    def lookup_users(self, handles):
        found, missing = [], []
        for i in range(0, len(handles), 100):
            data = self._call("/users/by", {"usernames": ",".join(handles[i:i + 100]), "user.fields": "public_metrics"})
            for u in data.get("data", []):
                found.append(XUser(id=u["id"], handle=u["username"],
                                   followers=u.get("public_metrics", {}).get("followers_count")))
            for err in data.get("errors", []):
                missing.append(err.get("value") or err.get("detail"))
        return found, missing

    def fetch_timeline(self, user_id, since_id, max_results, max_pages):
        params = {
            "max_results": max_results,
            "exclude": "retweets",
            "tweet.fields": "created_at,attachments,referenced_tweets,note_tweet",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url,type",
        }
        if since_id:
            params["since_id"] = since_id
        raw, media = [], {}
        for _ in range(max_pages if since_id else 1):
            data = self._call(f"/users/{user_id}/tweets", params)
            raw += data.get("data", [])
            media.update({m["media_key"]: m for m in data.get("includes", {}).get("media", [])})
            token = data.get("meta", {}).get("next_token")
            if not token:  # no more posts since since_id
                break
            params["pagination_token"] = token
        return [_to_post(p, media) for p in raw]

    def find_deleted(self, post_ids):
        if not post_ids:
            return set()
        data = self._call("/tweets", {"ids": ",".join(post_ids[:100]), "tweet.fields": "id"})
        deleted = set()
        for err in data.get("errors", []):
            pid = err.get("value") or err.get("resource_id")
            # "Not Found" means deleted. Other errors (account protected or suspended) mean we can't tell.
            if pid in post_ids and "Not Found" in (err.get("title") or ""):
                deleted.add(pid)
        return deleted


class XApiPostPublisher(PostPublisher):
    """Not built yet. Posting needs user-context credentials for the bot's own X account
    (OAuth 1.0a keys and access token, or OAuth 2.0 with the tweet.write scope), not the
    read-only bearer token. See README "Posting to X"."""

    def publish(self, text, reply_to=None):
        raise NotImplementedError("Posting to X isn't set up yet. See README, 'Posting to X'.")

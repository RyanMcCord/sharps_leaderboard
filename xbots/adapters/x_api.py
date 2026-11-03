"""X API (v2) adapters for the PostSource and PostPublisher ports.

Billing note: X charges per post returned (and per user returned on lookups), so callers
ask only for posts newer than the last one they saw. Creating a post is charged per post,
and a post containing a link costs much more than one without.
"""
import time

import requests

from .. import config
from ..core import Post, XUser
from ..ports import PostPublisher, PostRejected, PostSource

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


def _oauth1_header(method, url, api_key, api_secret, token, token_secret, **extra):
    """An OAuth 1.0a Authorization header for one request. The body isn't signed: X's JSON and
    multipart endpoints don't include it in the signature."""
    from oauthlib.oauth1 import Client
    client = Client(api_key, client_secret=api_secret, resource_owner_key=token,
                    resource_owner_secret=token_secret, **extra)
    _, headers, _ = client.sign(url, http_method=method)
    return headers["Authorization"]


def _explain(r):
    """A plain-language reason for a failed posting request."""
    hints = {
        401: "the tokens were rejected. Check X_API_KEY/X_API_SECRET and the bot account's access token and secret.",
        402: "payment is required. Add credits in the X developer console.",
        403: "X refused. The app needs 'Read and write' permission, and the access token must be generated after "
             "that was set. Also check the account isn't locked or posting a duplicate.",
        429: "rate limited. The next scheduled run will try again.",
    }
    return f"{r.status_code} from X: {hints.get(r.status_code, r.text[:300])}"


class XApiPostPublisher(PostPublisher):
    """Posts as one bot account, signed with OAuth 1.0a: the app's keys plus that account's
    access token and secret (see `python -m xbots <bot> authorize` for getting them).

    Images go up first through the v2 media endpoint, with alt text when given, then the
    post is created with them attached.
    """

    MAX_IMAGES = 4

    def __init__(self, access_token, access_secret, api_key=None, api_secret=None, http=None, log=print):
        self.api_key = api_key or config.X_API_KEY
        self.api_secret = api_secret or config.X_API_SECRET
        self.token, self.token_secret = access_token, access_secret
        self.http = http or requests
        self.log = log

    def _auth(self, method, url):
        missing = [n for n, v in (("X_API_KEY", self.api_key), ("X_API_SECRET", self.api_secret),
                                  ("access token", self.token), ("access token secret", self.token_secret)) if not v]
        if missing:
            raise PostRejected(f"Can't post: missing {', '.join(missing)}. See the bot's README for setting them up.")
        return {"Authorization": _oauth1_header(method, url, self.api_key, self.api_secret,
                                                self.token, self.token_secret)}

    def _upload(self, media):
        """Upload one image; returns its media id. Any failure here means nothing was posted."""
        url = f"{API}/media/upload"
        try:
            r = self.http.post(url, headers=self._auth("POST", url), timeout=60,
                               data={"media_category": "tweet_image", "media_type": media.mime_type},
                               files={"media": ("image", media.data, media.mime_type)})
        except requests.RequestException as e:
            raise PostRejected(f"Image upload failed: {e}") from e
        if not r.ok:
            raise PostRejected(f"Image upload failed: {_explain(r)}")
        media_id = str(((r.json() or {}).get("data") or {}).get("id") or "")
        if not media_id:
            raise PostRejected(f"Image upload returned no media id: {r.text[:300]}")
        if media.alt_text:
            self._set_alt_text(media_id, media.alt_text)
        return media_id

    def _set_alt_text(self, media_id, alt_text):
        """Best effort: a post without alt text is better than no post."""
        url = f"{API}/media/metadata"
        try:
            r = self.http.post(url, headers=self._auth("POST", url), timeout=30,
                               json={"id": media_id, "metadata": {"alt_text": {"text": alt_text[:1000]}}})
            if not r.ok:
                self.log(f"  alt text not set ({_explain(r)}); posting without it")
        except requests.RequestException as e:
            self.log(f"  alt text not set ({e}); posting without it")

    def publish(self, text, reply_to=None, media=()):
        if len(media) > self.MAX_IMAGES:
            raise PostRejected(f"X allows at most {self.MAX_IMAGES} images per post, got {len(media)}.")
        media_ids = [self._upload(m) for m in media]
        body = {"text": text}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        url = f"{API}/tweets"
        try:
            r = self.http.post(url, headers=self._auth("POST", url), json=body, timeout=30)
        except requests.RequestException as e:
            # No answer from X: the post may or may not have gone out. Not PostRejected on purpose.
            raise XAPIError(f"No answer from X while posting ({e}). Check the account before posting again.") from e
        if not r.ok:
            raise PostRejected(f"Post rejected: {_explain(r)}")
        return str(r.json()["data"]["id"])


# --- getting a bot account's access token -------------------------------------------

REQUEST_TOKEN_URL = "https://api.x.com/oauth/request_token"
AUTHORIZE_URL = "https://api.x.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://api.x.com/oauth/access_token"


def authorize_with_pin(ask=input, log=print, http=None, api_key=None, api_secret=None):
    """Get an access token and secret for any X account, using the app's keys and a PIN.

    Sign in to X as the bot account in your browser first, open the link this prints,
    approve the app, and type the PIN X shows. Returns (access_token, access_secret, handle).
    """
    from urllib.parse import parse_qs
    http = http or requests
    key, secret = api_key or config.X_API_KEY, api_secret or config.X_API_SECRET
    if not key or not secret:
        raise XAPIError("Set X_API_KEY and X_API_SECRET (the developer app's API Key and Secret) first.")

    header = _oauth1_header("POST", REQUEST_TOKEN_URL, key, secret, None, None, callback_uri="oob")
    r = http.post(REQUEST_TOKEN_URL, headers={"Authorization": header}, timeout=30)
    if not r.ok:
        raise XAPIError(f"Couldn't start sign-in: {_explain(r)}")
    started = {k: v[0] for k, v in parse_qs(r.text).items()}

    log("1. In your browser, sign in to X as the BOT account (not your own).")
    log(f"2. Open: {AUTHORIZE_URL}?oauth_token={started['oauth_token']}")
    log("3. Click 'Authorize app', then type the PIN X shows you.")
    pin = ask("PIN: ").strip()

    header = _oauth1_header("POST", ACCESS_TOKEN_URL, key, secret, started["oauth_token"],
                            started["oauth_token_secret"], verifier=pin)
    r = http.post(ACCESS_TOKEN_URL, headers={"Authorization": header}, timeout=30)
    if not r.ok:
        raise XAPIError(f"Sign-in failed: {_explain(r)}")
    done = {k: v[0] for k, v in parse_qs(r.text).items()}
    return done["oauth_token"], done["oauth_token_secret"], done.get("screen_name", "")

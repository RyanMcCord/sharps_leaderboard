"""The X API adapters (PostSource and PostPublisher) and the PIN sign-in, with a fake HTTP layer."""
import unittest

import requests

from xbots.adapters.x_api import XAPIError, XApiPostPublisher, XApiPostSource, authorize_with_pin
from xbots.core import Media, Post
from xbots.ports import PostRejected


class FakeX:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, path, params, token=None):
        self.calls.append((path, dict(params)))
        r = self.responses[path]
        return r.pop(0) if isinstance(r, list) else r


class PostSourceTests(unittest.TestCase):
    def test_lookup_users(self):
        x = FakeX({"/users/by": {"data": [{"id": "1", "username": "SharpPickss", "public_metrics": {"followers_count": 9}}],
                                 "errors": [{"value": "gone_account"}]}})
        found, missing = XApiPostSource(get=x).lookup_users(["sharppickss", "gone_account"])
        self.assertEqual(found[0].handle, "SharpPickss")
        self.assertEqual(found[0].followers, 9)
        self.assertEqual(missing, ["gone_account"])

    def test_first_fetch_is_one_page(self):
        x = FakeX({"/users/1/tweets": {"data": [{"id": "5", "created_at": "2026-10-25T16:00:00.000Z", "text": "hi"}],
                                       "meta": {"next_token": "more"}}})
        posts = XApiPostSource(get=x).fetch_timeline("1", None, 20, 5)
        self.assertEqual([p.id for p in posts], ["5"])
        self.assertEqual(len(x.calls), 1)
        self.assertNotIn("since_id", x.calls[0][1])
        self.assertEqual(x.calls[0][1]["max_results"], 20)

    def test_later_fetches_follow_pages_and_keep_media(self):
        x = FakeX({"/users/1/tweets": [
            {"data": [{"id": "9", "created_at": "2026-10-25T18:00:00.000Z", "text": "short",
                       "note_tweet": {"text": "the full long post"}, "attachments": {"media_keys": ["m1"]}}],
             "includes": {"media": [{"media_key": "m1", "type": "photo", "url": "https://pbs.twimg.com/a.jpg"}]},
             "meta": {"next_token": "p2"}},
            {"data": [{"id": "8", "created_at": "2026-10-25T17:00:00.000Z", "text": "older"}], "meta": {}},
        ]})
        posts = XApiPostSource(get=x).fetch_timeline("1", "7", 100, 5)
        self.assertEqual([p.id for p in posts], ["9", "8"])
        self.assertEqual(posts[0].text, "the full long post")
        self.assertEqual(posts[0].media_urls, ("https://pbs.twimg.com/a.jpg",))
        self.assertIsInstance(posts[0], Post)
        self.assertEqual(x.calls[1][1]["pagination_token"], "p2")
        self.assertEqual(x.calls[1][1]["since_id"], "7")

    def test_find_deleted(self):
        x = FakeX({"/tweets": {"data": [{"id": "1"}],
                               "errors": [{"value": "2", "title": "Not Found Error"},
                                          {"value": "3", "title": "Authorization Error"}]}})
        self.assertEqual(XApiPostSource(get=x).find_deleted(["1", "2", "3"]), {"2"})
        self.assertEqual(XApiPostSource(get=x).find_deleted([]), set())



class FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text or str(body)

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body


class FakeHttp:
    """Answers POSTs by URL suffix, in order; an Exception in the list is raised instead."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def post(self, url, headers=None, **kwargs):
        self.calls.append((url, headers, kwargs))
        key = next(k for k in self.answers if url.endswith(k))
        answer = self.answers[key].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def publisher(http):
    return XApiPostPublisher("tok", "tok-secret", api_key="key", api_secret="secret", http=http, log=lambda m: None)


class PostPublisherTests(unittest.TestCase):
    def test_uploads_the_image_sets_alt_text_then_posts(self):
        http = FakeHttp({"/media/upload": [FakeResponse(body={"data": {"id": "m1"}})],
                         "/media/metadata": [FakeResponse(body={})],
                         "/tweets": [FakeResponse(201, {"data": {"id": "p9"}})]})
        post_id = publisher(http).publish("Neon rain", media=(Media(b"img", "image/png", "A rainy street"),))
        self.assertEqual(post_id, "p9")
        upload, alt, tweet = http.calls
        self.assertEqual(upload[2]["data"]["media_category"], "tweet_image")
        self.assertEqual(upload[2]["files"]["media"][1], b"img")
        self.assertEqual(alt[2]["json"], {"id": "m1", "metadata": {"alt_text": {"text": "A rainy street"}}})
        self.assertEqual(tweet[2]["json"], {"text": "Neon rain", "media": {"media_ids": ["m1"]}})
        self.assertTrue(tweet[1]["Authorization"].startswith("OAuth "))
        self.assertIn('oauth_token="tok"', tweet[1]["Authorization"])

    def test_alt_text_failure_still_posts(self):
        http = FakeHttp({"/media/upload": [FakeResponse(body={"data": {"id": "m1"}})],
                         "/media/metadata": [FakeResponse(500, text="oops")],
                         "/tweets": [FakeResponse(201, {"data": {"id": "p1"}})]})
        self.assertEqual(publisher(http).publish("x", media=(Media(b"i", "image/png", "alt"),)), "p1")

    def test_failures_before_or_refused_by_x_are_safe_to_retry(self):
        cases = {
            "upload refused": {"/media/upload": [FakeResponse(403, text="forbidden")]},
            "upload unreachable": {"/media/upload": [requests.ConnectionError("down")]},
            "post refused": {"/media/upload": [FakeResponse(body={"data": {"id": "m"}})],
                             "/tweets": [FakeResponse(403, text="duplicate content")]},
        }
        for name, answers in cases.items():
            with self.subTest(name), self.assertRaises(PostRejected):
                publisher(FakeHttp(answers)).publish("x", media=(Media(b"i", "image/png"),))

    def test_no_answer_while_posting_is_not_marked_safe(self):
        http = FakeHttp({"/tweets": [requests.Timeout("slow")]})
        with self.assertRaises(XAPIError) as ctx:
            publisher(http).publish("hello")
        self.assertNotIsInstance(ctx.exception, PostRejected)

    def test_missing_credentials(self):
        with self.assertRaises(PostRejected) as ctx:
            XApiPostPublisher("", "", api_key="k", api_secret="s", http=FakeHttp({})).publish("x")
        self.assertIn("access token", str(ctx.exception))

    def test_four_images_at_most(self):
        with self.assertRaises(PostRejected):
            publisher(FakeHttp({})).publish("x", media=tuple(Media(b"i", "image/png") for _ in range(5)))


class AuthorizeTests(unittest.TestCase):
    def test_pin_flow(self):
        http = FakeHttp({"/oauth/request_token": [FakeResponse(text="oauth_token=rt&oauth_token_secret=rs")],
                         "/oauth/access_token": [FakeResponse(text="oauth_token=at&oauth_token_secret=as"
                                                                   "&screen_name=artbot")]})
        lines = []
        token, secret, handle = authorize_with_pin(ask=lambda prompt: " 1234 ", log=lines.append, http=http,
                                                   api_key="k", api_secret="s")
        self.assertEqual((token, secret, handle), ("at", "as", "artbot"))
        self.assertTrue(any("oauth_token=rt" in line for line in lines))
        self.assertIn('oauth_callback="oob"', http.calls[0][1]["Authorization"])
        self.assertIn('oauth_verifier="1234"', http.calls[1][1]["Authorization"])


if __name__ == "__main__":
    unittest.main()

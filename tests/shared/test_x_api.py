"""The X API adapter (PostSource), with a fake HTTP layer."""
import unittest

from xbots.adapters.x_api import XApiPostPublisher, XApiPostSource
from xbots.core import Post


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

    def test_posting_is_not_set_up_yet(self):
        with self.assertRaises(NotImplementedError):
            XApiPostPublisher().publish("hello")


if __name__ == "__main__":
    unittest.main()

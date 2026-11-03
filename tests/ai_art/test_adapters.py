"""The ai-art adapters: Google Drive and Claude with fakes, and the post log against a real Postgres."""
import os
import unittest
from types import SimpleNamespace

from bots.ai_art.adapters.claude_captioner import ClaudeCaptioner
from bots.ai_art.adapters.drive_queue import DriveError, DriveImageQueue
from bots.ai_art.adapters.postgres_post_log import PostgresPostLog
from bots.ai_art.domain.queue import QueuedImage
from xbots.core import Media


class FakeResponse:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._body, self.content, self.text = status, body or {}, content, str(body)

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


class DriveTests(unittest.TestCase):
    def test_lists_every_page_of_the_folder(self):
        s = FakeSession([
            FakeResponse(body={"files": [{"id": "1", "name": "a.png", "mimeType": "image/png",
                                          "createdTime": "2026-11-01T00:00:00Z", "size": "2048"}],
                               "nextPageToken": "p2"}),
            FakeResponse(body={"files": [{"id": "2", "name": "a.txt", "mimeType": "text/plain",
                                          "createdTime": "2026-11-01T00:01:00Z"}]}),
        ])
        files = DriveImageQueue("folder_123", session=s).list_files()
        self.assertEqual([(f.id, f.size) for f in files], [("1", 2048), ("2", None)])
        self.assertIn("'folder_123' in parents and trashed = false", s.calls[0][1]["q"])
        self.assertEqual(s.calls[1][1]["pageToken"], "p2")

    def test_download(self):
        s = FakeSession([FakeResponse(content=b"pixels")])
        self.assertEqual(DriveImageQueue("f", session=s).download("abc"), b"pixels")
        self.assertTrue(s.calls[0][0].endswith("/files/abc"))
        self.assertEqual(s.calls[0][1]["alt"], "media")

    def test_unshared_folder_says_what_to_do(self):
        s = FakeSession([FakeResponse(404)])
        with self.assertRaises(DriveError) as ctx:
            DriveImageQueue("f", session=s, account_email="bot@proj.iam.gserviceaccount.com").list_files()
        self.assertIn("Share the folder with bot@proj.iam.gserviceaccount.com", str(ctx.exception))

    def test_folder_id_must_look_like_one(self):
        for bad in ("", "x' or '1'='1", "https://drive.google.com/drive/folders/abc"):
            with self.subTest(folder=bad), self.assertRaises(DriveError):
                DriveImageQueue(bad, session=FakeSession([]))


class FakeClaude:
    def __init__(self, content):
        self.content, self.calls = content, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.content)


class CaptionerTests(unittest.TestCase):
    def test_reads_the_tool_answer_and_sends_the_image(self):
        tool = SimpleNamespace(type="tool_use", name="write_caption",
                               input={"caption": "Lanterns over a quiet river", "alt_text": "Paper lanterns at dusk"})
        client = FakeClaude([tool])
        caption = ClaudeCaptioner(client, model="m").describe(Media(b"\xff\xd8jpeg", "image/jpeg"), "dreamy")
        self.assertEqual((caption.text, caption.alt_text), ("Lanterns over a quiet river", "Paper lanterns at dusk"))
        sent = client.calls[0]
        self.assertEqual(sent["messages"][0]["content"][0]["source"]["media_type"], "image/jpeg")
        self.assertIn("dreamy", sent["messages"][0]["content"][1]["text"])
        self.assertEqual(sent["tool_choice"], {"type": "auto"})

    def test_plain_text_answer_is_still_used(self):
        client = FakeClaude([SimpleNamespace(type="text", text="A misty forest")])
        caption = ClaudeCaptioner(client, model="m").describe(Media(b"x", "image/jpeg"), "s")
        self.assertEqual(caption.text, "A misty forest")


PSQL, URL = os.getenv("PG_TEST_PSQL"), os.getenv("PG_TEST_URL")


@unittest.skipUnless(PSQL or URL, "set PG_TEST_URL or PG_TEST_PSQL to run Postgres tests")
class PostgresPostLogTests(unittest.TestCase):
    def setUp(self):
        from tests.sharps_leaderboard.test_postgres_repository import make_db
        self.db = make_db()
        self.db.execute("DROP SCHEMA IF EXISTS ai_art_test CASCADE")
        self.store = PostgresPostLog(self.db, "ai_art_test")
        self.store.setup()
        self.store.setup()   # safe to run again

    def tearDown(self):
        self.db.execute("DROP SCHEMA IF EXISTS ai_art_test CASCADE")

    def test_lifecycle(self):
        img = QueuedImage("f1", "Fox.png", "image/png", "2026-11-01T00:00:00Z")
        self.store.start(img, "A fox", "A red fox in snow")
        self.assertEqual(self.store.handled_ids(), {"f1"})
        self.assertEqual(self.store.counts()["posting"], 1)
        with self.assertRaises(Exception):
            self.store.start(img, "again", "again")       # a second run can't start the same image
        self.store.mark_posted("f1", "999")
        row = self.store.entries()[0]
        self.assertEqual((row["status"], row["x_post_id"], row["caption"]), ("posted", "999", "A fox"))
        self.assertTrue(row["posted_at"].endswith("Z"))

    def test_release_only_undoes_a_mid_post(self):
        img = QueuedImage("f2", "b.png", "image/png", "")
        self.store.start(img, "c", "a")
        self.store.release("f2")
        self.assertEqual(self.store.handled_ids(), set())
        self.store.start(img, "c", "a")
        self.store.mark_posted("f2", "1")
        self.store.release("f2")
        self.assertEqual(self.store.handled_ids(), {"f2"})  # a posted image stays posted

    def test_skips_and_requeue(self):
        self.store.mark_skipped("v1", "clip.mp4", "not an image")
        self.store.mark_skipped("v1", "clip.mp4", "again")   # recorded once
        self.assertEqual(self.store.entries("skipped")[0]["reason"], "not an image")
        self.assertTrue(self.store.requeue("CLIP.MP4"))     # by name, ignoring case
        self.assertFalse(self.store.requeue("clip.mp4"))
        self.assertEqual(self.store.counts(), {"posted": 0, "skipped": 0, "posting": 0})


if __name__ == "__main__":
    unittest.main()

"""The ai-art use cases against in-memory adapters."""
import unittest

from bots.ai_art.application import AiArtPoster, Settings

from .memory import FakeCaptioner, FakePublisher, InMemoryPostLog, InMemoryQueue, PassThroughImages, PostRejected


class Rig:
    def __init__(self):
        self.queue, self.store = InMemoryQueue(), InMemoryPostLog()
        self.captioner, self.x, self.lines = FakeCaptioner(), FakePublisher(), []
        self.poster = AiArtPoster(self.queue, PassThroughImages(), self.captioner, self.store, self.x,
                                  Settings(caption_style="dreamy"), log=self.lines.append)


class PostingTests(unittest.TestCase):
    def setUp(self):
        self.r = Rig()

    def test_posts_oldest_first_once_each_then_waits(self):
        self.r.queue.add("first.png", added_at="2026-11-01T00:00:00Z")
        self.r.queue.add("second.png", added_at="2026-11-02T00:00:00Z")
        self.assertEqual(self.r.poster.post_next().image, "first.png")
        self.assertEqual(self.r.poster.post_next().image, "second.png")
        result = self.r.poster.post_next()
        self.assertEqual(result.status, "empty")
        self.assertEqual(len(self.r.x.posts), 2)
        self.assertIn("queue is empty", self.r.lines[-1])

    def test_caption_and_alt_text_are_attached(self):
        self.r.queue.add("city.png", data=b"pixels")
        result = self.r.poster.post_next()
        text, media = self.r.x.posts[0]
        self.assertEqual(text, "A quiet neon city")
        self.assertEqual(media[0].data, b"pixels")
        self.assertEqual(media[0].alt_text, "A city street at night lit by neon signs")
        self.assertEqual(result.post_id, "post-1")
        self.assertEqual(self.r.captioner.calls[0][1], "dreamy")
        self.assertEqual(self.r.store.rows["id-1"]["status"], "posted")

    def test_a_text_file_beside_the_image_is_its_caption(self):
        self.r.queue.add("fox.png", added_at="2026-11-01T00:00:00Z")
        self.r.queue.add("fox.txt", data="Fox in the snow https://link.com".encode(), mime="text/plain")
        self.r.poster.post_next()
        self.assertEqual(self.r.x.posts[0][0], "Fox in the snow")        # yours, with the link taken out
        self.assertEqual(self.r.x.posts[0][1][0].alt_text, self.r.captioner.alt)   # alt text still described

    def test_links_from_claude_never_reach_the_post(self):
        self.r.captioner.text = "See more at https://example.com"
        self.r.queue.add("a.png")
        self.r.poster.post_next()
        self.assertEqual(self.r.x.posts[0][0], "See more at")

    def test_unusable_images_are_skipped_and_the_next_one_posts(self):
        self.r.queue.add("broken.png", data=b"bad", added_at="2026-11-01T00:00:00Z")
        self.r.queue.add("good.png", added_at="2026-11-02T00:00:00Z")
        self.r.queue.add("clip.mp4", mime="video/mp4")
        self.assertEqual(self.r.poster.post_next().image, "good.png")
        statuses = {r["file_name"]: r["status"] for r in self.r.store.rows.values()}
        self.assertEqual(statuses, {"broken.png": "skipped", "good.png": "posted", "clip.mp4": "skipped"})

    def test_a_refused_post_is_tried_again_next_run(self):
        self.r.queue.add("a.png")
        self.r.x.fail = PostRejected("429 rate limited")
        self.assertEqual(self.r.poster.post_next().status, "failed")
        self.assertEqual(self.r.store.rows, {})                         # released, not stuck
        self.assertEqual(self.r.poster.post_next().status, "posted")

    def test_no_answer_from_x_never_risks_a_double_post(self):
        self.r.queue.add("a.png")
        self.r.queue.add("b.png")
        self.r.x.fail = RuntimeError("connection reset while posting")
        with self.assertRaises(RuntimeError):
            self.r.poster.post_next()
        self.assertEqual(self.r.store.rows["id-1"]["status"], "posting")
        self.assertEqual(self.r.poster.post_next().image, "b.png")      # moves on; a.png isn't retried
        self.assertEqual([e["file_name"] for e in self.r.poster.status()["stuck"]], ["a.png"])
        self.assertTrue(self.r.poster.requeue("a.png"))
        self.assertEqual(self.r.poster.post_next().image, "a.png")

    def test_preview_posts_and_records_nothing(self):
        self.r.queue.add("a.png")
        self.r.queue.add("clip.mp4", mime="video/mp4")
        result = self.r.poster.post_next(dry_run=True)
        self.assertEqual((result.status, result.image, result.caption), ("preview", "a.png", "A quiet neon city"))
        self.assertEqual(self.r.x.posts, [])
        self.assertEqual(self.r.store.rows, {})


class StatusTests(unittest.TestCase):
    def test_status_counts_and_runway(self):
        r = Rig()
        for i in range(12):
            r.queue.add(f"img{i:02}.png", added_at=f"2026-11-01T00:{i:02}:00Z")
        r.poster.post_next()
        s = r.poster.status()
        self.assertEqual((s["waiting"], s["next"], s["days_left"]), (11, "img01.png", 1.8))   # 6 posts a day
        self.assertEqual(s["counts"]["posted"], 1)


if __name__ == "__main__":
    unittest.main()

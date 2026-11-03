"""The ai-art bot's pure rules: the queue's order, caption files, and caption limits."""
import unittest

from bots.ai_art.domain.captions import MAX_POST, clean_alt_text, clean_caption, weighted_length
from bots.ai_art.domain.queue import QueueFile, build_queue, next_image, waiting


def f(file_id, name, mime="image/png", added="2026-11-01T00:00:00Z"):
    return QueueFile(file_id, name, mime, added)


class QueueTests(unittest.TestCase):
    def test_oldest_first_and_caption_files_pair_by_name(self):
        files = [f("b", "b.png", added="2026-11-02T00:00:00Z"), f("a", "a.jpg", "image/jpeg"),
                 f("t", "B.txt", "text/plain"), f("n", "notes.txt", "text/plain")]
        images, unpostable = build_queue(files)
        self.assertEqual([i.id for i in images], ["a", "b"])
        self.assertEqual(images[1].caption_file_id, "t")     # B.txt belongs to b.png, ignoring case
        self.assertIsNone(images[0].caption_file_id)
        self.assertEqual(unpostable, [])                     # stray text files are just ignored

    def test_non_images_are_unpostable(self):
        images, unpostable = build_queue([f("v", "clip.mp4", "video/mp4"), f("p", "x.pdf", "application/pdf")])
        self.assertEqual(images, [])
        self.assertEqual([u[0].id for u in unpostable], ["v", "p"])

    def test_next_skips_everything_handled_and_ends_when_dry(self):
        images, _ = build_queue([f("1", "one.png", added="1"), f("2", "two.png", added="2")])
        self.assertEqual(next_image(images, set()).id, "1")
        self.assertEqual(next_image(images, {"1"}).id, "2")
        self.assertIsNone(next_image(images, {"1", "2"}))
        self.assertEqual([i.id for i in waiting(images, {"1"})], ["2"])


class CaptionTests(unittest.TestCase):
    def test_links_are_removed(self):
        self.assertEqual(clean_caption("Dreaming in color https://midjourney.com/x www.site.io"), "Dreaming in color")

    def test_wrapping_quotes_and_extra_space_go(self):
        self.assertEqual(clean_caption('  "Neon rain,\n  softly"  '), "Neon rain, softly")

    def test_long_captions_fit_x_and_end_cleanly(self):
        cut = clean_caption("word " * 100)
        self.assertLessEqual(weighted_length(cut), MAX_POST)
        self.assertTrue(cut.endswith("word…"))

    def test_emoji_and_cjk_count_double(self):
        self.assertEqual(weighted_length("ab"), 2)
        self.assertEqual(weighted_length("夢🌙"), 4)
        self.assertLessEqual(weighted_length(clean_caption("夢" * 200)), MAX_POST)

    def test_nothing_usable_is_empty(self):
        self.assertEqual(clean_caption("https://only-a-link.com"), "")

    def test_alt_text_limit(self):
        alt = clean_alt_text("a " * 800)
        self.assertLessEqual(len(alt), 1000)
        self.assertTrue(alt.endswith("…"))


if __name__ == "__main__":
    unittest.main()

"""The Pillow image processor: fitting images to X's limit, and small copies for Claude."""
import io
import os
import unittest

from PIL import Image

from xbots.adapters.images import PillowImageProcessor
from xbots.ports import UnusableImage


def png(w, h, noise=False):
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3)) if noise else Image.new("RGB", (w, h), "teal")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


class ImageTests(unittest.TestCase):
    def test_small_supported_images_are_posted_as_they_are(self):
        data = png(64, 64)
        m = PillowImageProcessor().for_post(data, "image/png")
        self.assertEqual((m.data, m.mime_type), (data, "image/png"))

    def test_too_big_images_are_re_encoded_to_fit(self):
        data = png(600, 600, noise=True)                 # about 1 MB of noise
        m = PillowImageProcessor(limit=200_000).for_post(data, "image/png")
        self.assertEqual(m.mime_type, "image/jpeg")
        self.assertLessEqual(len(m.data), 200_000)

    def test_other_formats_are_converted(self):
        out = io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(out, "BMP")
        self.assertEqual(PillowImageProcessor().for_post(out.getvalue(), "image/bmp").mime_type, "image/jpeg")

    def test_reading_copy_is_small(self):
        m = PillowImageProcessor().for_reading(png(4000, 2000), "image/png")
        self.assertLessEqual(max(Image.open(io.BytesIO(m.data)).size), 1568)

    def test_garbage_is_unusable(self):
        with self.assertRaises(UnusableImage):
            PillowImageProcessor().for_post(b"not an image", "image/png")


if __name__ == "__main__":
    unittest.main()

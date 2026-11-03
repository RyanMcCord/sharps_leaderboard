"""ImageProcessor adapter, using Pillow."""
import io

from ..core import Media
from ..ports.images import ImageProcessor, UnusableImage

X_IMAGE_LIMIT = 5 * 1000 * 1000        # X takes images up to 5 MB; stay just under it
X_FORMATS = {"image/jpeg", "image/png", "image/webp"}
READING_EDGE = 1568                    # Claude reads images up to about this size on the long edge


def _open(data):
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception as e:
        raise UnusableImage(f"not a readable image ({e.__class__.__name__})") from e


def _jpeg(img, quality):
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


class PillowImageProcessor(ImageProcessor):
    def __init__(self, limit=X_IMAGE_LIMIT):
        self.limit = limit

    def for_post(self, data, mime_type):
        if mime_type in X_FORMATS and len(data) <= self.limit:
            _open(data)                   # still make sure it's a real image
            return Media(data, mime_type)
        img = _open(data)
        if getattr(img, "is_animated", False):
            raise UnusableImage("animated images aren't supported; post a still image")
        # Too big (Midjourney PNGs often are) or another format: re-encode as a high-quality JPEG,
        # lowering quality first and then size until it fits.
        for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
            frame = img if scale == 1.0 else img.resize((max(1, int(img.width * scale)),
                                                         max(1, int(img.height * scale))))
            for quality in (95, 90, 85, 78, 70):
                out = _jpeg(frame, quality)
                if len(out) <= self.limit:
                    return Media(out, "image/jpeg")
        raise UnusableImage("couldn't fit the image under X's 5 MB limit")

    def for_reading(self, data, mime_type):
        img = _open(data)
        img.thumbnail((READING_EDGE, READING_EDGE))
        return Media(_jpeg(img, 85), "image/jpeg")

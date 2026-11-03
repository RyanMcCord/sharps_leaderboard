"""In-memory adapters for the ai-art bot's ports, for testing the use cases without Google
Drive, Claude, a database or X."""
from bots.ai_art.domain.queue import POSTED, POSTING, SKIPPED, QueueFile
from bots.ai_art.ports import Caption, Captioner, ImageQueue, PostLog
from xbots.core import Media
from xbots.ports import ImageProcessor, PostPublisher, PostRejected, UnusableImage


class InMemoryQueue(ImageQueue):
    def __init__(self):
        self.files, self.data = [], {}

    def add(self, name, data=b"img", mime="image/png", added_at=None):
        file_id = f"id-{len(self.files) + 1}"
        self.files.append(QueueFile(file_id, name, mime, added_at or f"2026-11-0{1 + len(self.files) % 9}T00:00:00Z"))
        self.data[file_id] = data
        return file_id

    def list_files(self):
        return list(self.files)

    def download(self, file_id):
        return self.data[file_id]


class PassThroughImages(ImageProcessor):
    """Treats b"bad" as unusable; everything else is returned as is."""

    def for_post(self, data, mime_type):
        if data == b"bad":
            raise UnusableImage("not a readable image")
        return Media(data, mime_type)

    def for_reading(self, data, mime_type):
        return Media(data, "image/jpeg")


class FakeCaptioner(Captioner):
    def __init__(self, text="A quiet neon city", alt="A city street at night lit by neon signs"):
        self.text, self.alt, self.calls = text, alt, []

    def describe(self, image, style):
        self.calls.append((image, style))
        return Caption(self.text, self.alt)


class InMemoryPostLog(PostLog):
    def __init__(self):
        self.rows = {}

    def setup(self):
        pass

    def handled_ids(self):
        return set(self.rows)

    def start(self, image, caption, alt_text):
        if image.id in self.rows:
            raise RuntimeError("duplicate key")
        self.rows[image.id] = {"image_id": image.id, "file_name": image.name, "status": POSTING, "reason": None,
                               "caption": caption, "x_post_id": None, "started_at": "now", "posted_at": None}

    def mark_posted(self, image_id, post_id):
        self.rows[image_id].update(status=POSTED, x_post_id=post_id, posted_at="now")

    def release(self, image_id):
        if self.rows.get(image_id, {}).get("status") == POSTING:
            del self.rows[image_id]

    def mark_skipped(self, image_id, name, reason):
        self.rows.setdefault(image_id, {"image_id": image_id, "file_name": name, "status": SKIPPED, "reason": reason,
                                        "caption": None, "x_post_id": None, "started_at": "now", "posted_at": None})

    def requeue(self, name_or_id):
        key = next((k for k, r in self.rows.items() if name_or_id in (k, r["file_name"])), None)
        if key is None:
            return False
        del self.rows[key]
        return True

    def entries(self, status=None, limit=20):
        rows = [dict(r) for r in self.rows.values() if status in (None, r["status"])]
        return list(reversed(rows))[:limit]

    def counts(self):
        out = {POSTED: 0, SKIPPED: 0, POSTING: 0}
        for r in self.rows.values():
            out[r["status"]] += 1
        return out


class FakePublisher(PostPublisher):
    """Records posts. Set .fail to an exception to raise it on the next publish."""

    def __init__(self):
        self.posts, self.fail = [], None

    def publish(self, text, reply_to=None, media=()):
        if self.fail:
            error, self.fail = self.fail, None
            raise error
        self.posts.append((text, media))
        return f"post-{len(self.posts)}"


__all__ = ["InMemoryQueue", "PassThroughImages", "FakeCaptioner", "InMemoryPostLog", "FakePublisher", "PostRejected"]

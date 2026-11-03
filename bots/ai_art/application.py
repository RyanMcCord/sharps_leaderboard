"""The ai-art bot's use cases (its input port): post the next image from the queue, preview
it, and report on the queue.

Everything here works through ports, so it runs the same against Google Drive, Claude,
Aurora and X (see app.py) as against in-memory test adapters.
"""
from dataclasses import dataclass

from xbots.core import Media
from xbots.ports import ImageProcessor, PostPublisher, PostRejected, UnusableImage

from .domain.captions import clean_alt_text, clean_caption
from .domain.queue import POSTING, build_queue, next_image, waiting
from .ports import Captioner, ImageQueue, PostLog

DEFAULT_STYLE = ("One short, evocative line that fits the mood of the image, like a gallery title or a single "
                 "thought it inspires. No hashtags, no emoji, no links, no @mentions. Under 120 characters.")


@dataclass
class Settings:
    caption_style: str = DEFAULT_STYLE
    hours_between_posts: int = 4       # only used to estimate how long the queue lasts
    max_skips_per_run: int = 5         # unusable images to step over in one run before giving up


@dataclass
class Result:
    status: str                        # posted, preview, empty, failed
    image: str | None = None
    caption: str | None = None
    alt_text: str | None = None
    post_id: str | None = None
    detail: str = ""


class AiArtPoster:
    def __init__(self, queue: ImageQueue, images: ImageProcessor, captioner: Captioner, log_store: PostLog,
                 publisher: PostPublisher | None, settings: Settings | None = None, log=print):
        self.queue, self.images, self.captioner = queue, images, captioner
        self.store, self.publisher = log_store, publisher
        self.settings = settings or Settings()
        self.log = log

    def setup(self):
        self.store.setup()

    # --- posting -------------------------------------------------------------------

    def post_next(self, dry_run=False):
        """Post the oldest image not posted yet, with a caption, then record it.

        dry_run prepares the post (image and caption) without posting or recording anything.
        The image is recorded as mid-post before X is called; if X refuses, that's undone so it's
        tried again next run, but if the run dies with no answer from X it stays recorded, so it
        can never post twice. `status` lists any stuck that way.
        """
        files = self.queue.list_files()
        queue, unpostable = build_queue(files)
        handled = self.store.handled_ids()
        if not dry_run:
            for f, reason in unpostable:
                if f.id not in handled:
                    self.store.mark_skipped(f.id, f.name, reason)
                    self.log(f"  skipped {f.name}: {reason}")

        for _ in range(self.settings.max_skips_per_run + 1):
            image = next_image(queue, handled)
            if image is None:
                self.log("  The queue is empty: every image has been posted. Add more to the folder.")
                return Result("empty")
            try:
                media = self.images.for_post(self.queue.download(image.id), image.mime_type)
            except UnusableImage as e:
                handled.add(image.id)
                if not dry_run:
                    self.store.mark_skipped(image.id, image.name, str(e))
                self.log(f"  skipped {image.name}: {e}")
                continue
            break
        else:
            return Result("failed", detail=f"Gave up after {self.settings.max_skips_per_run} unusable images.")

        caption, alt_text = self._caption(image, media)
        if dry_run:
            return Result("preview", image.name, caption, alt_text)

        self.store.start(image, caption, alt_text)
        try:
            post_id = self.publisher.publish(caption, media=(Media(media.data, media.mime_type, alt_text),))
        except PostRejected as e:
            self.store.release(image.id)       # nothing went out: try again next run
            self.log(f"  {image.name} not posted: {e}")
            return Result("failed", image.name, caption, alt_text, detail=str(e))
        self.store.mark_posted(image.id, post_id)
        self.log(f"  posted {image.name}: {caption}")
        return Result("posted", image.name, caption, alt_text, post_id)

    def _caption(self, image, media):
        """The caption from the image's text file if it has one, otherwise Claude's. Alt text always comes
        from looking at the image."""
        described = self.captioner.describe(self.images.for_reading(media.data, media.mime_type),
                                            self.settings.caption_style)
        text = described.text
        if image.caption_file_id:
            text = self.queue.download(image.caption_file_id).decode("utf-8", errors="replace")
        caption = clean_caption(text) or clean_caption(described.text)
        if not caption:
            raise ValueError(f"No usable caption for {image.name}.")
        return caption, clean_alt_text(described.alt_text)

    # --- reporting -----------------------------------------------------------------

    def status(self):
        """The queue at a glance: what's waiting, what's next, how long it lasts, and anything stuck."""
        queue, _ = build_queue(self.queue.list_files())
        handled = self.store.handled_ids()
        left = waiting(queue, handled)
        per_day = 24 / self.settings.hours_between_posts
        return {
            "waiting": len(left),
            "next": left[0].name if left else None,
            "days_left": round(len(left) / per_day, 1),
            "counts": self.store.counts(),
            "stuck": self.store.entries(POSTING),
            "recent": self.store.entries(limit=5),
        }

    def requeue(self, name_or_id):
        return self.store.requeue(name_or_id)

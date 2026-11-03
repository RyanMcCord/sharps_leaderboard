"""The posting queue: which files are images, which text files are their captions, and what
posts next. Pure logic, no dependencies.

Rotation: each image is posted once, oldest first (by when it was added to the folder).
When every image has been posted, nothing posts until new ones are added.
"""
from dataclasses import dataclass
from pathlib import PurePosixPath

POSTABLE = ("image/jpeg", "image/png", "image/webp")
CAPTION_TYPES = ("text/plain",)

# What the post log records for each image
POSTING = "posting"   # we started posting it; if this stays, the run died mid-post (see stuck())
POSTED = "posted"
SKIPPED = "skipped"   # can't be posted (e.g. a format X doesn't take); never tried again


@dataclass(frozen=True)
class QueueFile:
    """A file in the queue folder, as the storage reports it."""
    id: str
    name: str
    mime_type: str
    added_at: str          # ISO 8601; orders the queue
    size: int | None = None


@dataclass(frozen=True)
class QueuedImage:
    """An image waiting to be posted, and its caption file if one sits beside it."""
    id: str
    name: str
    mime_type: str
    added_at: str
    caption_file_id: str | None = None


def _stem(name):
    return PurePosixPath(name).stem.lower()


def build_queue(files):
    """Split the folder's files into (images we can post, oldest first; files we can't post).

    A text file with the same name as an image (sunset.png + sunset.txt) is that image's
    caption. Other text files are ignored; other images and files are returned as
    unpostable with a reason.
    """
    captions = {_stem(f.name): f.id for f in files if f.mime_type in CAPTION_TYPES}
    images, unpostable = [], []
    for f in files:
        if f.mime_type in CAPTION_TYPES:
            continue
        if f.mime_type in POSTABLE or f.mime_type.startswith("image/"):
            # Other image types (HEIC, TIFF, ...) are converted when prepared; if that fails, it's skipped then.
            images.append(QueuedImage(f.id, f.name, f.mime_type, f.added_at, captions.get(_stem(f.name))))
        else:
            unpostable.append((f, f"not an image ({f.mime_type})"))
    images.sort(key=lambda i: (i.added_at, i.name.lower()))
    return images, unpostable


def next_image(queue, handled_ids):
    """The oldest image not posted, skipped or mid-post yet; None when the queue has run dry."""
    return next((img for img in queue if img.id not in handled_ids), None)


def waiting(queue, handled_ids):
    """Images still to post, in posting order."""
    return [img for img in queue if img.id not in handled_ids]

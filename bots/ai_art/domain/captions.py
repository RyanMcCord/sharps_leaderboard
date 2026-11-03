"""What a post's caption and alt text may contain. Pure logic, no dependencies."""
import re

MAX_POST = 280          # X's limit, in X's weighted characters
MAX_ALT_TEXT = 1000     # X's alt text limit

_URL = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
_SPACE = re.compile(r"\s+")


def weighted_length(text):
    """Length as X counts it: most Latin-script characters count 1, everything else
    (CJK, most emoji) counts 2. Links would count 23, but captions never contain them."""
    def weight(c):
        o = ord(c)
        if o <= 0x10FF or 0x2000 <= o <= 0x200D or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037:
            return 1
        return 2
    return sum(weight(c) for c in text)


def _shorten(text, limit, measure):
    if measure(text) <= limit:
        return text
    cut = text
    while cut and measure(cut + "…") > limit:
        cut = cut[:-1]
    # end on a whole word if one is close by
    space = cut.rfind(" ")
    if space > len(cut) * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def clean_caption(text):
    """A caption that's safe to post: no links (X charges far more for posts with one), one
    line of tidy spacing, no wrapping quotes, and within X's length limit. Empty if nothing
    usable is left."""
    text = _URL.sub("", text or "")
    text = _SPACE.sub(" ", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    text = text.strip("“”")
    return _shorten(text, MAX_POST, weighted_length)


def clean_alt_text(text):
    """Alt text within X's limit, on one line."""
    text = _SPACE.sub(" ", text or "").strip()
    return _shorten(text, MAX_ALT_TEXT, len)

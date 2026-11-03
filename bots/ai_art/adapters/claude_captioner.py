"""Captioner adapter: Claude looks at the image and writes the caption and alt text."""
import base64

from xbots.adapters import claude

from ..ports import Caption, Captioner

SYSTEM = """You write captions for an X account that posts AI-generated artwork made with Midjourney.

You'll see one image. Write:
- caption: the text of the post, following the style the user gives. Never include links or @mentions,
  and never describe the image as a real photo or a real event.
- alt_text: a plain, literal description of what's in the image for people using screen readers,
  in one or two sentences (under 400 characters). Describe, don't interpret.

Always answer by calling the write_caption tool exactly once. Don't reply in plain text."""

TOOL = {
    "name": "write_caption",
    "description": "Record the caption and alt text for the image.",
    "input_schema": {
        "type": "object",
        "properties": {
            "caption": {"type": "string", "description": "The post's text"},
            "alt_text": {"type": "string", "description": "A literal description for screen readers"},
        },
        "required": ["caption", "alt_text"],
    },
}


class ClaudeCaptioner(Captioner):
    def __init__(self, client=None, model=None):
        self._client, self.model = client, model or claude.model()

    @property
    def client(self):
        if self._client is None:
            self._client = claude.client()
        return self._client

    def describe(self, image, style):
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=600,
            system=SYSTEM,
            tools=[TOOL],
            tool_choice={"type": "auto"},   # "tool"/"any" isn't supported by every model; the prompt asks for the tool
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": image.mime_type,
                                             "data": base64.b64encode(image.data).decode()}},
                {"type": "text", "text": f"Caption style: {style}"},
            ]}],
        )
        for block in msg.content:
            if getattr(block, "type", "") == "tool_use" and block.name == TOOL["name"]:
                return Caption(str(block.input.get("caption", "")), str(block.input.get("alt_text", "")))
        text = " ".join(getattr(b, "text", "") for b in msg.content).strip()
        if not text:
            raise RuntimeError("Claude returned no caption.")
        return Caption(text, text)   # it answered in plain text instead of the tool: use that for both

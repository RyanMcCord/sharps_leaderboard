"""Shared Claude (Anthropic API) client. Bots build their own reading/writing adapters on top of it."""
from .. import config


def client():
    import anthropic
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it as a GitHub repo secret (or to .env when running locally).")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def model():
    return config.CLAUDE_MODEL

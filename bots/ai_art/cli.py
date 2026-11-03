"""Command line for the ai-art bot (a primary adapter).

    python -m xbots ai-art <command>

  post              post the next image in the queue (what the scheduled workflow runs)
  preview           show the next image and the caption it would get, without posting
  status            what's waiting, what's next, how long the queue lasts, anything stuck
  requeue <name>    make an image postable again (by file name or id)
  setup             create or upgrade the bot's tables
  authorize         get the bot account's X access token (run once, on your own computer)
"""
from . import app

COMMANDS = ("post", "preview", "status", "requeue", "setup", "authorize")


def main(args, build=app.build):
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}\n{__doc__}")
        return 1
    if cmd == "authorize":
        return _authorize()

    poster = build()
    poster.setup()
    if cmd == "setup":
        print("Tables ready.")
        return 0

    if cmd in ("post", "preview"):
        result = poster.post_next(dry_run=cmd == "preview")
        if result.status == "empty":
            return 0
        if result.status == "failed":
            print(f"Not posted: {result.detail}")
            return 1
        print(f"{'Would post' if cmd == 'preview' else 'Posted'}: {result.image}")
        print(f"  caption:  {result.caption}")
        print(f"  alt text: {result.alt_text}")
        if result.post_id:
            print(f"  https://x.com/i/status/{result.post_id}")
        return 0

    if cmd == "status":
        s = poster.status()
        c = s["counts"]
        print(f"Waiting: {s['waiting']} images (about {s['days_left']} days at the current pace)")
        print(f"Next:    {s['next'] or 'nothing. Add images to the Drive folder.'}")
        print(f"Posted {c['posted']}, skipped {c['skipped']}, stuck {c['posting']}")
        for e in s["stuck"]:
            print(f"  STUCK  {e['file_name']}: the run stopped while posting it (started {e['started_at']}).\n"
                  f"         Check the account. If it didn't post, run: requeue {e['file_name']}")
        for e in s["recent"]:
            extra = e["reason"] or e["caption"] or ""
            print(f"  {e['status']:<8} {e['file_name']}  {extra}")
        return 0

    if cmd == "requeue":
        if not rest:
            print("usage: requeue <file name or id>")
            return 1
        if poster.requeue(rest[0]):
            print(f"{rest[0]} will post again in its turn.")
            return 0
        print(f"No recorded image called {rest[0]}.")
        return 1
    return 1


def _authorize():
    from xbots.adapters.x_api import XAPIError, authorize_with_pin
    try:
        token, secret, handle = authorize_with_pin()
    except XAPIError as e:
        print(e)
        return 1
    print(f"\nSigned in as @{handle}. Add these two GitHub repo secrets (and to .env to post locally):")
    print(f"  AI_ART_X_ACCESS_TOKEN  = {token}")
    print(f"  AI_ART_X_ACCESS_SECRET = {secret}")
    print("Keep them private: anyone with them can post as this account.")
    return 0

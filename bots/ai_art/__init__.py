"""AI art: posts the Midjourney images you drop in a Google Drive folder to X, one at a time
on a schedule, each with a caption Claude writes after looking at it.

    domain/        the queue's rotation, and caption rules (pure logic)
    ports.py       what the use cases need: ImageQueue, Captioner, PostLog
    application.py the use cases (post the next image, preview, status)
    adapters/      Google Drive, Claude, Postgres
    app.py         composition root (real adapters)
    cli.py         command line

Midjourney itself isn't automated: its terms forbid automated tools. You make the images;
the bot posts them.
"""
NAME = "ai-art"
DESCRIPTION = "Posts the Midjourney images in a Google Drive folder to X on a schedule, with captions by Claude"

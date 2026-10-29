#!/usr/bin/env python3
"""Shortcut for the bots' command line.

    python run.py <bot> <command> ...     same as: python -m xbots <bot> <command> ...
    python run.py list                    the bots in this repo

Older commands without a bot name, like `python run.py all`, still run the sharps leaderboard.
"""
import sys

from xbots import cli, registry

DEFAULT_BOT = "sharps-leaderboard"

if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] not in registry.names() and args[0] not in ("list", "help", "-h", "--help"):
        args = [DEFAULT_BOT] + args
    sys.exit(cli.main(args))

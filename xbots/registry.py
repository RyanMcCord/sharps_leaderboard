"""The bots that live in this repo. To add one, create bots/<name>/ and list it here.

Each bot package exposes:
    NAME          the name used on the command line and in workflows
    DESCRIPTION   one line about what it does
    cli.main(args) -> int   its command line (a primary adapter)
"""
import importlib

BOTS = {
    "sharps-leaderboard": "bots.sharps_leaderboard",
}


def names():
    return list(BOTS)


def load(name):
    if name not in BOTS:
        raise KeyError(name)
    return importlib.import_module(BOTS[name])

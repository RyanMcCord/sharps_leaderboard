"""Command line for every bot (a primary adapter).

    python -m xbots list                      the bots in this repo
    python -m xbots <bot> [command] [args]    run a bot command; with no command, its help
"""
import importlib
import sys

from . import config, registry

USAGE = __doc__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        _print_bots()
        return 0
    if argv[0] == "list":
        _print_bots()
        return 0
    name, args = argv[0], argv[1:]
    try:
        bot = registry.load(name)
    except KeyError:
        print(f"Unknown bot: {name}")
        _print_bots()
        return 1
    loaded = config.load_secrets()
    if loaded:
        print(f"Loaded {len(loaded)} keys from AWS Secrets Manager ({config.APP_SECRET_ID}).")
    return importlib.import_module(f"{bot.__name__}.cli").main(args)


def _print_bots():
    print("Bots:")
    for name in registry.names():
        print(f"  {name:<22} {registry.load(name).DESCRIPTION}")


if __name__ == "__main__":
    sys.exit(main())

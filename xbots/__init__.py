"""xbots: a home for several X (Twitter) bots, built with hexagonal architecture.

    xbots/core       shared domain objects (a Post, an X user), with no outside dependencies
    xbots/ports      interfaces the bots depend on: reading posts, publishing posts, a SQL database
    xbots/adapters   the real technology behind those ports: X API, Aurora/Postgres, Claude
    xbots/cli.py     the command line every bot runs through: python -m xbots <bot> <command>
    xbots/registry.py  the list of bots

Each bot lives in bots/<name>/ with its own domain, ports, application (use cases),
adapters and composition root. See README "Adding a bot".
"""

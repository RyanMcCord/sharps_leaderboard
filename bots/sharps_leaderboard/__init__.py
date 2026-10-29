"""Sharps leaderboard: tracks the free picks betting tipsters post on X and grades them.

    domain/        picks and grading (pure logic)
    ports.py       what the use cases need: PickRepository, ScoresProvider, PickReader, SitePublisher
    application.py the use cases (daily pipeline, accounts, hand grading)
    adapters/      Postgres, ESPN, Claude, static site
    app.py         composition root (real adapters)
    cli.py         command line
"""
NAME = "sharps-leaderboard"
DESCRIPTION = "Grades the free picks betting tipsters post on X and publishes a leaderboard"

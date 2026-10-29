"""Adapters that plug the leaderboard's ports into real technology:

    postgres_repository.py   PickRepository  -> Postgres / Aurora (schema `sharps`)
    espn_scores.py           ScoresProvider  -> ESPN scoreboard
    claude_pick_reader.py    PickReader      -> Claude
    static_site.py           SitePublisher   -> HTML page for GitHub Pages

Reading posts uses the shared X adapter in xbots/adapters/x_api.py.
"""

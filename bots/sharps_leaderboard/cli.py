"""Command line for the sharps leaderboard (a primary adapter).

    python -m xbots sharps-leaderboard <command>

Daily pipeline
  setup      create or upgrade the tables, add any new handles from accounts.txt
  fetch      pull new posts from active accounts (X API)
  parse      read picks out of new posts (Claude)
  grade      grade picks whose games are final (ESPN scores)
  deletions  check graded pick posts once to see if the tipster deleted them (X API)
  report     rebuild the leaderboard site (docs/index.html)
  all        all of the above, in order (what the daily workflow runs)

Accounts
  accounts                    list accounts
  accounts add <handle>       start tracking an account
  accounts pause <handle>     stop tracking (history is kept)
  accounts resume <handle>    track again

Other
  standings                   print the leaderboard
  review                      list picks that need a hand grade
  set <id> <result> [units]   grade one by hand: win | loss | push | void
                              (<id> is the short id from review; units won is worked
                               out from the odds if left out)
  export [file.csv]           export every pick to CSV
"""
from . import app, settings

PIPELINE = ("setup", "fetch", "parse", "grade", "deletions", "report")


def main(args, build=app.build):
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = args[0], args[1:]
    known = PIPELINE + ("all", "accounts", "standings", "review", "set", "export")
    if cmd not in known:
        print(f"Unknown command: {cmd}\n{__doc__}")
        return 1

    leaderboard = build()

    if cmd in PIPELINE or cmd == "all":
        steps = PIPELINE if cmd == "all" else ("setup", cmd) if cmd != "setup" else ("setup",)
        failed = leaderboard.run(steps, settings.load_handles(), stop_on_error=cmd != "all")
        if failed:
            print(f"Finished with errors in: {', '.join(failed)}")
            return 1
        return 0

    if cmd == "accounts":
        return _accounts(leaderboard, rest)

    if cmd == "standings":
        for r in leaderboard.standings():
            roi = "—" if r["roi_pct"] is None else f"{r['roi_pct']:+.1f}%"
            print(f"  @{r['x_handle']:<20} {r['wins']}-{r['losses']}-{r['pushes']:<4} "
                  f"{r['units_won']:+8.2f}u  ROI {roi:>7}  pending {r['pending']}  hand grade {r['needs_hand_grade']}"
                  f"  late {r['late_picks']}  deleted {r['deleted_picks']}")
        return 0

    if cmd == "review":
        rows = leaderboard.picks_needing_hand_grade()
        for p in rows:
            period = "" if p["period"] == "full_game" else f" ({p['period'].replace('_', ' ')})"
            print(f"[{p['id'][:8]}] {p['created_at'][:10]} @{p['x_handle']} {p['sport']} {p['type']}{period} | "
                  f"{p['bet_description']} | odds {p['line']} units {p['units']} | {p['note'] or ''}\n"
                  f"           https://x.com/{p['x_handle']}/status/{p['post_id']}")
        print(f"{len(rows)} picks need a hand grade.")
        return 0

    if cmd == "set":
        if len(rest) < 2:
            print("usage: set <id> win|loss|push|void [units]")
            return 1
        try:
            pick, net = leaderboard.grade_by_hand(rest[0], rest[1], float(rest[2]) if len(rest) > 2 else None)
        except ValueError as e:
            print(e)
            return 1
        print(f"{pick['bet_description']} (@{pick['x_handle']}) set to {rest[1]}"
              f"{'' if net is None else f', {net:+.2f}u'}. It shows on the site after the next run.")
        return 0

    if cmd == "export":
        from .adapters.static_site import write_csv
        print(f"Wrote {write_csv(leaderboard.all_picks(), rest[0] if rest else 'picks.csv')}")
        return 0
    return 1


def _accounts(leaderboard, rest):
    if not rest:
        for a in leaderboard.accounts():
            status = "active" if a["active"] else "paused"
            followers = "" if a["followers"] is None else f"{a['followers']:,} followers"
            print(f"  @{a['x_handle']:<20} {status:<7} {followers}")
        return 0
    action, handle = rest[0], (rest[1] if len(rest) > 1 else "").lstrip("@")
    if not handle or action not in ("add", "pause", "resume"):
        print("usage: accounts [add|pause|resume] <handle>")
        return 1
    if action == "add":
        leaderboard.add_account(handle)
        print(f"Tracking @{handle}.")
    elif leaderboard.set_account_active(handle, action == "resume"):
        print(f"@{handle} {'resumed' if action == 'resume' else 'paused'}.")
    else:
        print(f"No account @{handle}.")
        return 1
    return 0

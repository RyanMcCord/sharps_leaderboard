#!/usr/bin/env python3
"""Sharps leaderboard.

Daily pipeline
  python run.py setup      create the tables (safe to re-run) and add any new handles from accounts.txt
  python run.py fetch      pull new posts from active accounts (X API)
  python run.py parse      read picks out of new posts (Claude)
  python run.py grade      grade picks whose games are final (ESPN scores)
  python run.py deletions  check graded pick posts once to see if the tipster deleted them (X API)
  python run.py report     rebuild the leaderboard site (docs/index.html)
  python run.py all        all of the above, in order (what the daily run does)

Accounts
  python run.py accounts                    list accounts
  python run.py accounts add <handle>       start tracking an account
  python run.py accounts pause <handle>     stop tracking (history is kept)
  python run.py accounts resume <handle>    track again

Other
  python run.py standings                   print the leaderboard view
  python run.py review                      list picks that need a hand grade
  python run.py set <id> <result> [units]   grade one by hand: win | loss | push | void
                                            (<id> is the short id from review; units won is
                                             worked out from the odds if left out)
  python run.py export [file.csv]           export every pick to CSV
"""
import sys

from tracker import config, db, store
from tracker.grader import units_net


def run_pipeline(conn, cmd):
    failed = []

    def step(name, fn):
        # In "all", one step failing (say, X credits ran out) shouldn't stop
        # grading and the page from updating with what we already have.
        print(f"{name}...")
        try:
            fn()
        except Exception as e:
            print(f"  {name} failed: {e}")
            failed.append(name)
            if cmd != "all":
                raise

    if cmd in ("setup", "all"):
        def setup():
            db.ensure_schema(conn)
            store.seed_accounts(conn, config.load_handles())
        step("Checking tables", setup)
        if failed:  # nothing else can work without the database
            return 1
    if cmd in ("fetch", "all"):
        from tracker.xapi import fetch_new_posts
        step("Fetching posts", lambda: fetch_new_posts(conn))
    if cmd in ("parse", "all"):
        from tracker.parser import parse_pending
        step("Reading picks", lambda: parse_pending(conn))
    if cmd in ("grade", "all"):
        from tracker.grader import grade_pending
        step("Grading", lambda: grade_pending(conn))
    if cmd in ("deletions", "all"):
        from tracker.xapi import check_deletions
        step("Checking for deleted posts", lambda: check_deletions(conn))
    if cmd in ("report", "all"):
        from tracker.report import write_site
        step("Building leaderboard",
             lambda: print(f"  wrote {write_site(conn, config.ROOT / 'docs' / 'index.html')}"))
    if failed:
        print(f"Finished with errors in: {', '.join(failed)}")
        return 1
    return 0


def accounts_cmd(conn, args):
    if not args:
        for a in store.list_accounts(conn, active_only=False):
            status = "active" if a["active"] else "paused"
            followers = "" if a["followers"] is None else f"{a['followers']:,} followers"
            print(f"  @{a['x_handle']:<20} {status:<7} {followers}")
        return 0
    action, handle = args[0], (args[1] if len(args) > 1 else "").lstrip("@")
    if not handle or action not in ("add", "pause", "resume"):
        print("usage: python run.py accounts [add|pause|resume] <handle>")
        return 1
    if action == "add":
        store.add_account(conn, handle)
        print(f"Tracking @{handle}.")
    elif store.set_active(conn, handle, action == "resume"):
        print(f"@{handle} {'resumed' if action == 'resume' else 'paused'}.")
    else:
        print(f"No account @{handle}.")
        return 1
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = argv[0], argv[1:]
    conn = db.connect()

    if cmd in ("setup", "fetch", "parse", "grade", "deletions", "report", "all"):
        return run_pipeline(conn, cmd)
    if cmd == "accounts":
        return accounts_cmd(conn, args)
    if cmd == "standings":
        for r in store.leaderboard(conn):
            roi = "—" if r["roi_pct"] is None else f"{r['roi_pct']:+.1f}%"
            print(f"  @{r['x_handle']:<20} {r['wins']}-{r['losses']}-{r['pushes']:<4} "
                  f"{r['units_won']:+8.2f}u  ROI {roi:>7}  pending {r['pending']}  hand grade {r['needs_hand_grade']}"
                  f"  late {r['late_picks']}  deleted {r['deleted_picks']}")
        return 0
    if cmd == "review":
        rows = store.manual_picks(conn)
        for p in rows:
            period = "" if p["period"] == "full_game" else f" ({p['period'].replace('_', ' ')})"
            print(f"[{p['id'][:8]}] {p['created_at'][:10]} @{p['x_handle']} {p['sport']} {p['type']}{period} | "
                  f"{p['bet_description']} | odds {p['line']} units {p['units']} | {p['note'] or ''}\n"
                  f"           https://x.com/{p['x_handle']}/status/{p['post_id']}")
        print(f"{len(rows)} picks need a hand grade.")
        return 0
    if cmd == "set":
        if len(args) < 2 or args[1] not in ("win", "loss", "push", "void"):
            print("usage: python run.py set <id> win|loss|push|void [units]")
            return 1
        matches = store.find_pick(conn, args[0])
        if len(matches) != 1:
            print("No pick with that id." if not matches else "More than one pick starts with that id; use more characters.")
            return 1
        p, result = matches[0], args[1]
        net = float(args[2]) if len(args) > 2 else (
            None if result == "void" else units_net(result, p["line"] or config.DEFAULT_ODDS, p["units"]))
        store.set_result(conn, p["id"], result, net, by_hand=True)
        print(f"{p['bet_description']} (@{p['x_handle']}) set to {result}"
              f"{'' if net is None else f', {net:+.2f}u'}. It shows on the site after the next run.")
        return 0
    if cmd == "export":
        from tracker.report import write_csv
        print(f"Wrote {write_csv(conn, args[0] if args else 'picks.csv')}")
        return 0
    print(f"Unknown command: {cmd}\n{__doc__}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

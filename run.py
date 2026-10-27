#!/usr/bin/env python3
"""Tipster tracker.

  python run.py fetch      pull new posts from the tracked accounts (X API)
  python run.py parse      extract picks from new posts (Claude)
  python run.py grade      grade picks whose games are final (ESPN scores)
  python run.py report     write leaderboard.html and picks.csv
  python run.py all        fetch + parse + grade + report (what the schedule runs)

  python run.py review                 list picks that need a manual grade
  python run.py set <pick_id> <result> [units_net]
                                       grade one by hand: win | loss | push | void
                                       (units_net is calculated from the odds if left out)
"""
import sys

from tracker import config, db
from tracker.grader import units_net


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    conn = db.connect()

    if cmd in ("fetch", "all"):
        from tracker.xapi import fetch_new_posts
        print("Fetching posts...")
        fetch_new_posts(conn, config.load_handles())
    if cmd in ("parse", "all"):
        from tracker.parser import parse_pending
        print("Parsing picks...")
        parse_pending(conn)
    if cmd in ("grade", "all"):
        from tracker.grader import grade_pending
        print("Grading...")
        grade_pending(conn)
    if cmd in ("report", "all"):
        from tracker.report import write_csv, write_html
        html_path = write_html(conn, config.ROOT / "leaderboard.html")
        write_csv(conn, config.ROOT / "picks.csv")
        print(f"Wrote {html_path}")

    elif cmd == "review":
        rows = conn.execute("SELECT * FROM picks WHERE result='manual' ORDER BY posted_at").fetchall()
        for p in rows:
            print(f"[{p['id']}] {p['posted_at'][:10]} @{p['handle']} {p['league']} | {p['description']} "
                  f"| odds {p['odds']} units {p['units']} | {p['note'] or ''}\n"
                  f"      https://x.com/{p['handle']}/status/{p['post_id']}")
        print(f"{len(rows)} picks need a manual grade.")
    elif cmd == "set":
        if len(argv) < 3 or argv[2] not in ("win", "loss", "push", "void"):
            print("usage: python run.py set <pick_id> win|loss|push|void [units_net]")
            return 1
        p = conn.execute("SELECT * FROM picks WHERE id=?", (argv[1],)).fetchone()
        if not p:
            print(f"No pick with id {argv[1]}")
            return 1
        result = argv[2]
        net = float(argv[3]) if len(argv) > 3 else (
            None if result == "void" else units_net(result, p["odds"] or config.DEFAULT_ODDS, p["units"]))
        conn.execute("UPDATE picks SET result=?, units_net=?, note=COALESCE(note,'') || ' [graded by hand]' WHERE id=?",
                     (result, net, p["id"]))
        conn.commit()
        print(f"Pick {p['id']} set to {result} ({net if net is not None else 'no units'})")
    elif cmd not in ("fetch", "parse", "grade", "report", "all"):
        print(f"Unknown command: {cmd}\n{__doc__}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

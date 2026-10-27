"""Builds the leaderboard website (docs/index.html) and a CSV export.

The page is one self-contained HTML file with the pick data embedded as JSON;
the browser works out the standings for whichever time range and league is picked.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config

TEMPLATE = Path(__file__).with_name("site_template.html")
MIN_SAMPLE = 30


def site_data(conn):
    followers = {r["handle"]: r["followers"] for r in conn.execute("SELECT handle, followers FROM accounts")}
    handles = config.load_handles()
    picks = []
    for p in conn.execute("SELECT * FROM picks ORDER BY posted_at DESC"):
        picks.append({
            "h": p["handle"], "t": p["posted_at"], "lg": p["league"], "d": p["description"],
            "o": p["odds"], "u": p["units"], "r": p["result"] or "pending", "n": p["units_net"], "id": p["post_id"],
        })
    seen = {p["h"].lower() for p in picks}
    accounts = [{"h": h, "f": followers.get(h)} for h in handles]
    # keep accounts that have picks even if they were removed from accounts.txt
    accounts += [{"h": h, "f": followers.get(h)} for h in {p["h"] for p in picks}
                 if h.lower() not in {a["h"].lower() for a in accounts}]
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "defaultOdds": config.DEFAULT_ODDS,
        "minSample": MIN_SAMPLE,
        "accounts": accounts,
        "picks": picks,
        "hasPicks": bool(seen),
    }


def write_site(conn, path):
    data = json.dumps(site_data(conn), ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")  # keep post text from closing the script tag
    page = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def write_csv(conn, path):
    rows = conn.execute("SELECT * FROM picks ORDER BY posted_at").fetchall()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(picks)")]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows([tuple(r) for r in rows])
    return path

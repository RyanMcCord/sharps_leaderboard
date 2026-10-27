"""Builds the leaderboard website (docs/index.html) and a CSV export.

Once a day the run queries the database and bakes the results into one
self-contained HTML page. Visitors never touch the database: the browser works
out the standings from the embedded data for whichever time range and league is picked.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, store

TEMPLATE = Path(__file__).with_name("site_template.html")
MIN_SAMPLE = 30


def site_data(db):
    accounts = store.list_accounts(db)
    picks = [{
        "h": p["x_handle"], "t": p["created_at"], "lg": p["sport"], "d": p["bet_description"],
        "o": p["line"], "u": p["units"], "r": p["result"], "n": p["units_net"], "id": p["post_id"],
        "late": p["posted_before_start"] is False, "del": bool(p["deleted"]),
    } for p in store.site_picks(db)]
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "defaultOdds": config.DEFAULT_ODDS,
        "minSample": MIN_SAMPLE,
        "accounts": [{"h": a["x_handle"], "f": a["followers"]} for a in accounts],
        "picks": picks,
    }


def write_site(db, path):
    data = json.dumps(site_data(db), ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")  # keep post text from closing the script tag
    page = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def write_csv(db, path):
    rows = store.site_picks(db)
    cols = ["id", "x_handle", "sport", "type", "period", "line", "bet_description", "created_at", "team", "opponent",
            "side", "point", "units", "result", "units_net", "game_id", "game_start", "home_team", "away_team",
            "home_score", "away_score", "posted_before_start", "deleted", "post_id", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in reversed(rows):
            w.writerow([r.get(c) for c in cols])
    return path

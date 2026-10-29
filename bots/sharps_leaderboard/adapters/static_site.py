"""SitePublisher adapter: the leaderboard as one self-contained HTML page (served by GitHub Pages).

The pick data is embedded as JSON; the browser works out the standings for whichever
time range and league the visitor picks. Visitors never touch the database.
Also: a CSV export of every pick.
"""
import csv
import json
from pathlib import Path

from ..ports import SitePublisher

TEMPLATE = Path(__file__).with_name("site_template.html")

CSV_COLUMNS = ["id", "x_handle", "sport", "type", "period", "line", "bet_description", "created_at", "team",
               "opponent", "side", "point", "units", "result", "units_net", "game_id", "game_start", "home_team",
               "away_team", "home_score", "away_score", "posted_before_start", "deleted", "post_id", "note"]


class StaticSitePublisher(SitePublisher):
    def __init__(self, path, template=TEMPLATE):
        self.path, self.template = Path(path), Path(template)

    def publish(self, data):
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        payload = payload.replace("</", "<\\/")  # keep post text from closing the script tag
        page = self.template.read_text(encoding="utf-8").replace("__DATA__", payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(page, encoding="utf-8")
        return self.path


def write_csv(picks, path):
    """picks: newest first, as PickRepository.all_picks returns them. Written oldest first."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in reversed(picks):
            w.writerow([r.get(c) for c in CSV_COLUMNS])
    return path

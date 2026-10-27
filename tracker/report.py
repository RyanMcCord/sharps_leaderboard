"""Leaderboard stats, an HTML report and a CSV export."""
import csv
import html
from datetime import datetime, timedelta, timezone

from . import config

GRADED = ("win", "loss", "push")


def leaderboard(conn, since=None):
    q = "SELECT * FROM picks"
    args = []
    if since:
        q += " WHERE posted_at >= ?"
        args.append(since)
    stats = {}
    for p in conn.execute(q, args):
        s = stats.setdefault(p["handle"], {"handle": p["handle"], "win": 0, "loss": 0, "push": 0, "void": 0,
                                           "manual": 0, "pending": 0, "units": 0.0, "risked": 0.0, "leagues": {}})
        r = p["result"] or "pending"
        s[r] = s.get(r, 0) + 1
        if r in GRADED:
            s["units"] += p["units_net"] or 0
            if r != "push":
                s["risked"] += p["units"] or 1.0
            lg = s["leagues"].setdefault(p["league"], {"win": 0, "loss": 0, "units": 0.0})
            if r in ("win", "loss"):
                lg[r] += 1
            lg["units"] += p["units_net"] or 0
    followers = {r["handle"]: r["followers"] for r in conn.execute("SELECT handle, followers FROM accounts")}
    rows = []
    for s in stats.values():
        decided = s["win"] + s["loss"]
        s["win_pct"] = s["win"] / decided if decided else None
        s["roi"] = s["units"] / s["risked"] if s["risked"] else None
        s["graded"] = s["win"] + s["loss"] + s["push"]
        s["followers"] = followers.get(s["handle"])
        rows.append(s)
    rows.sort(key=lambda s: (s["units"], s["graded"]), reverse=True)
    return rows


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _units(x):
    return f"{x:+.2f}u"


def _board_html(rows, min_sample):
    if not rows:
        return "<p class='empty'>No picks yet.</p>"
    out = ["<table><thead><tr><th>#</th><th>Account</th><th>Record</th><th>Win %</th><th>Units</th>"
           "<th>ROI</th><th>Graded</th><th>Pending / manual</th><th>Best league</th></tr></thead><tbody>"]
    for i, s in enumerate(rows, 1):
        best = max(s["leagues"].items(), key=lambda kv: kv[1]["units"], default=None)
        best_txt = f"{best[0]} {_units(best[1]['units'])}" if best else "—"
        small = " class='small'" if s["graded"] < min_sample else ""
        cls = "pos" if s["units"] > 0 else "neg" if s["units"] < 0 else ""
        handle = html.escape(s["handle"])
        fol = "" if s["followers"] is None else f"<span class='sub'>{s['followers']:,} followers</span>"
        out.append(
            f"<tr{small}><td>{i}</td><td><a href='https://x.com/{handle}'>@{handle}</a>{fol}</td>"
            f"<td>{s['win']}-{s['loss']}-{s['push']}</td><td>{_pct(s['win_pct'])}</td>"
            f"<td class='{cls}'>{_units(s['units'])}</td><td class='{cls}'>{_pct(s['roi'])}</td>"
            f"<td>{s['graded']}</td><td>{s['pending']} / {s['manual']}</td><td>{best_txt}</td></tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def write_html(conn, path, min_sample=30):
    all_time = leaderboard(conn)
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    last30 = leaderboard(conn, since)
    recent = conn.execute("SELECT * FROM picks ORDER BY posted_at DESC LIMIT 150").fetchall()
    log = ["<table><thead><tr><th>Posted</th><th>Account</th><th>League</th><th>Pick</th><th>Odds</th>"
           "<th>Units</th><th>Result</th><th>Net</th></tr></thead><tbody>"]
    for p in recent:
        res = p["result"] or "pending"
        net = "" if p["units_net"] is None else _units(p["units_net"])
        title = f" title='{html.escape(p['note'])}'" if p["note"] else ""
        log.append(
            f"<tr><td>{p['posted_at'][:10]}</td><td>@{html.escape(p['handle'])}</td><td>{html.escape(p['league'] or '')}</td>"
            f"<td><a href='https://x.com/{html.escape(p['handle'])}/status/{p['post_id']}'>{html.escape(p['description'] or '')}</a></td>"
            f"<td>{p['odds'] or ''}</td><td>{p['units'] or ''}</td><td class='r-{res}'{title}>{res}</td><td>{net}</td></tr>"
        )
    log.append("</tbody></table>")
    generated = datetime.now(timezone.utc).astimezone().strftime("%b %d, %Y %I:%M %p")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Tipster Leaderboard</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1a1a1a; --mute:#6b6b6b; --line:#e4e4e0; --pos:#137a3a; --neg:#b42318; --accent:#2f5bd3; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141414; --card:#1d1d1d; --ink:#eee; --mute:#9a9a9a; --line:#2e2e2e; --pos:#4cc27a; --neg:#f07167; --accent:#7ea2ff; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.45 -apple-system, system-ui, sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ margin:0 0 4px; font-size:26px; }} h2 {{ margin:32px 0 10px; font-size:18px; }}
.meta {{ color:var(--mute); margin:0 0 8px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; min-width:640px; }}
th, td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--mute); font-weight:600; }}
tr:last-child td {{ border-bottom:0; }}
a {{ color:var(--accent); text-decoration:none; }}
.sub {{ display:block; color:var(--mute); font-size:12px; }}
.pos {{ color:var(--pos); font-weight:600; }} .neg {{ color:var(--neg); font-weight:600; }}
tr.small td {{ opacity:.55; }}
.r-win {{ color:var(--pos); font-weight:600; }} .r-loss {{ color:var(--neg); font-weight:600; }}
.r-pending, .r-manual, .r-void {{ color:var(--mute); }}
.empty {{ padding:16px; color:var(--mute); }}
</style></head><body><main>
<h1>Tipster Leaderboard</h1>
<p class="meta">Free picks only · units treated as amount risked · missing odds assumed {config.DEFAULT_ODDS} · faded rows have under {min_sample} graded picks · updated {generated}</p>
<h2>All time</h2><div class="card">{_board_html(all_time, min_sample)}</div>
<h2>Last 30 days</h2><div class="card">{_board_html(last30, min_sample)}</div>
<h2>Recent picks</h2><div class="card">{''.join(log)}</div>
</main></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def write_csv(conn, path):
    rows = conn.execute("SELECT * FROM picks ORDER BY posted_at").fetchall()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if rows:
            w.writerow(rows[0].keys())
            w.writerows([tuple(r) for r in rows])
    return path

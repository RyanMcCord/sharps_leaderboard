"""All reads and writes, in one place."""
import json

# Timestamps come back as ISO 8601 UTC strings ending in Z.
def _iso(col, alias=None):
    return f"(to_json({col} AT TIME ZONE 'UTC') #>> '{{}}') || 'Z' AS {alias or col.split('.')[-1]}"


# --- accounts ---------------------------------------------------------------

def seed_accounts(db, handles):
    """Add handles that aren't in the table yet. Existing ones (and their active flag) are left alone."""
    for h in handles:
        db.execute("INSERT INTO accounts (x_handle) VALUES (:h) ON CONFLICT ((lower(x_handle))) DO NOTHING", {"h": h})


def list_accounts(db, active_only=True):
    where = "WHERE active" if active_only else ""
    return db.query(
        f"SELECT CAST(id AS text) AS id, x_handle, active, x_user_id, followers, last_seen_post_id, {_iso('created_at')} "
        f"FROM accounts {where} ORDER BY lower(x_handle)")


def add_account(db, handle):
    db.execute("INSERT INTO accounts (x_handle) VALUES (:h) "
               "ON CONFLICT ((lower(x_handle))) DO UPDATE SET active = true", {"h": handle})


def set_active(db, handle, active):
    rows = db.query("UPDATE accounts SET active = :a WHERE lower(x_handle) = lower(:h) RETURNING x_handle",
                    {"a": active, "h": handle})
    return bool(rows)


def save_x_user(db, handle, x_user_id, followers):
    db.execute("UPDATE accounts SET x_user_id = :uid, followers = :f, x_handle = :h WHERE lower(x_handle) = lower(:h)",
               {"uid": x_user_id, "f": followers, "h": handle})


def set_last_seen(db, account_id, post_id):
    db.execute("UPDATE accounts SET last_seen_post_id = :p WHERE id = CAST(:id AS uuid)", {"p": post_id, "id": account_id})


# --- posts ------------------------------------------------------------------

def insert_post(db, post_id, account_id, posted_at, text, media_urls, raw):
    db.execute(
        "INSERT INTO posts (id, sharp_account, posted_at, text, media_urls, raw) "
        "VALUES (:id, CAST(:acct AS uuid), CAST(:t AS timestamptz), :text, CAST(:media AS jsonb), CAST(:raw AS jsonb)) "
        "ON CONFLICT (id) DO NOTHING",
        {"id": post_id, "acct": account_id, "t": posted_at, "text": text, "media": media_urls, "raw": raw})


def unparsed_posts(db, limit=500):
    rows = db.query(
        f"SELECT p.id, CAST(p.sharp_account AS text) AS sharp_account, a.x_handle, {_iso('p.posted_at')}, "
        "p.text, CAST(p.media_urls AS text) AS media_urls "
        "FROM posts p JOIN accounts a ON a.id = p.sharp_account "
        "WHERE NOT p.parsed ORDER BY p.posted_at LIMIT :n", {"n": limit})
    for r in rows:
        r["media_urls"] = json.loads(r["media_urls"] or "[]")
    return rows


def mark_parsed(db, post_id):
    db.execute("UPDATE posts SET parsed = true WHERE id = :id", {"id": post_id})


# --- picks ------------------------------------------------------------------

def insert_pick(db, account_id, post_id, created_at, pick):
    db.execute(
        "INSERT INTO picks (sharp_account, sport, type, line, bet_description, created_at, "
        "post_id, team, opponent, side, point, units, legs) VALUES ("
        "CAST(:acct AS uuid), CAST(:sport AS sport), CAST(:type AS bet_type), :line, :desc, CAST(:t AS timestamptz), "
        ":post, :team, :opp, :side, :point, :units, CAST(:legs AS jsonb))",
        {"acct": account_id, "sport": pick["sport"], "type": pick["type"], "line": pick.get("line"),
         "desc": pick["bet_description"], "t": created_at, "post": post_id, "team": pick.get("team"),
         "opp": pick.get("opponent"), "side": pick.get("side"), "point": pick.get("point"),
         "units": pick.get("units"), "legs": pick.get("legs") or []})


_PICK_COLS = (
    "CAST(p.id AS text) AS id, a.x_handle, CAST(p.sport AS text) AS sport, CAST(p.type AS text) AS type, p.line, "
    "p.bet_description, {created}, p.post_id, p.team, p.opponent, p.side, CAST(p.point AS float8) AS point, "
    "CAST(p.units AS float8) AS units, CAST(p.legs AS text) AS legs, CAST(p.result AS text) AS result, "
    "CAST(p.units_net AS float8) AS units_net, p.note"
).format(created=_iso("p.created_at"))


def _picks(db, where, params=None, tail=""):
    rows = db.query(f"SELECT {_PICK_COLS} FROM picks p JOIN accounts a ON a.id = p.sharp_account WHERE {where} {tail}",
                    params)
    for r in rows:
        r["legs"] = json.loads(r["legs"] or "[]")
    return rows


def pending_picks(db):
    return _picks(db, "p.result = 'pending'", tail="ORDER BY p.created_at")


def manual_picks(db):
    return _picks(db, "p.result = 'manual'", tail="ORDER BY p.created_at")


def find_pick(db, id_prefix):
    """Look a pick up by the first few characters of its id (as shown by `run.py review`)."""
    return _picks(db, "CAST(p.id AS text) LIKE :p || '%'", {"p": id_prefix.lower()}, "LIMIT 2")


def set_result(db, pick_id, result, units_net, note=None, by_hand=False):
    db.execute(
        "UPDATE picks SET result = CAST(:r AS pick_result), units_net = :n, graded_at = now(), "
        "note = CASE WHEN :hand THEN concat_ws(' ', note, '[graded by hand]') ELSE :note END "
        "WHERE id = CAST(:id AS uuid)",
        {"r": result, "n": units_net, "note": note, "hand": by_hand, "id": pick_id})


def site_picks(db, page_size=1000):
    """Every pick for the website, newest first. Paged, because the Data API caps a response at 1 MB."""
    out, cursor = [], None
    while True:
        where = "a.active"
        params = {"n": page_size}
        if cursor:
            where += " AND (p.created_at, p.id) < (CAST(:ct AS timestamptz), CAST(:cid AS uuid))"
            params.update(ct=cursor[0], cid=cursor[1])
        rows = _picks(db, where, params, "ORDER BY p.created_at DESC, p.id DESC LIMIT :n")
        out += rows
        if len(rows) < page_size:
            return out
        cursor = (rows[-1]["created_at"], rows[-1]["id"])


def leaderboard(db):
    return db.query("SELECT x_handle, wins, losses, pushes, CAST(units_won AS float8) AS units_won, "
                    "CAST(roi_pct AS float8) AS roi_pct, pending, needs_hand_grade FROM leaderboard")

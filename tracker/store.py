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
    # Two statements, because the Data API only returns rows for SELECT (not UPDATE ... RETURNING)
    db.execute("UPDATE accounts SET active = :a WHERE lower(x_handle) = lower(:h)", {"a": active, "h": handle})
    return bool(db.query("SELECT 1 AS found FROM accounts WHERE lower(x_handle) = lower(:h)", {"h": handle}))


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
        "post_id, team, opponent, side, point, units, legs, period) VALUES ("
        "CAST(:acct AS uuid), CAST(:sport AS sport), CAST(:type AS bet_type), :line, :desc, CAST(:t AS timestamptz), "
        ":post, :team, :opp, :side, :point, :units, CAST(:legs AS jsonb), CAST(:period AS bet_period))",
        {"acct": account_id, "sport": pick["sport"], "type": pick["type"], "line": pick.get("line"),
         "desc": pick["bet_description"], "t": created_at, "post": post_id, "team": pick.get("team"),
         "opp": pick.get("opponent"), "side": pick.get("side"), "point": pick.get("point"),
         "units": pick.get("units"), "legs": pick.get("legs") or [], "period": pick.get("period") or "full_game"})


_PICK_COLS = (
    "CAST(p.id AS text) AS id, a.x_handle, CAST(p.sport AS text) AS sport, CAST(p.type AS text) AS type, p.line, "
    "p.bet_description, {created}, p.post_id, p.team, p.opponent, p.side, CAST(p.point AS float8) AS point, "
    "CAST(p.units AS float8) AS units, CAST(p.legs AS text) AS legs, CAST(p.result AS text) AS result, "
    "CAST(p.units_net AS float8) AS units_net, p.note, CAST(p.period AS text) AS period, p.game_id, {start}, "
    "p.home_team, p.away_team, CAST(p.home_score AS float8) AS home_score, CAST(p.away_score AS float8) AS away_score, "
    "p.posted_before_start, (po.deleted_at IS NOT NULL) AS deleted"
).format(created=_iso("p.created_at"), start=_iso("p.game_start"))


def _picks(db, where, params=None, tail=""):
    rows = db.query(f"SELECT {_PICK_COLS} FROM picks p JOIN accounts a ON a.id = p.sharp_account "
                    f"LEFT JOIN posts po ON po.id = p.post_id WHERE {where} {tail}", params)
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


def set_game(db, pick_id, game):
    """Record the game a pick was matched to and whether the pick went out before it started."""
    db.execute(
        "UPDATE picks SET game_id = :gid, game_start = CAST(:start AS timestamptz), home_team = :home, "
        "away_team = :away, home_score = :hs, away_score = :as_, posted_before_start = :before "
        "WHERE id = CAST(:id AS uuid)",
        {"gid": game.get("id"), "start": game.get("start"), "home": game.get("home_team"),
         "away": game.get("away_team"), "hs": game.get("home_score"), "as_": game.get("away_score"),
         "before": game.get("before_start"), "id": pick_id})


# --- deleted posts ------------------------------------------------------------

def posts_to_check_for_deletion(db, settle_hours=12, limit=100):
    """Pick posts not checked yet, whose picks have all been graded for at least settle_hours.

    Tipsters tend to delete losers soon after the result, so one check per post, a little
    after grading, catches most deletions without paying X to re-read every post every day.
    """
    rows = db.query(
        "SELECT po.id FROM posts po JOIN picks p ON p.post_id = po.id "
        "WHERE po.deletion_checked_at IS NULL "
        "GROUP BY po.id "
        "HAVING bool_and(p.result <> 'pending') "
        "AND max(p.graded_at) < now() - make_interval(hours => CAST(:h AS int)) "
        "ORDER BY min(po.posted_at) LIMIT :n",
        {"h": settle_hours, "n": limit})
    return [r["id"] for r in rows]


def record_deletion_check(db, post_ids, deleted_ids):
    if not post_ids:
        return
    ids = json.dumps(list(post_ids))
    gone = json.dumps(list(deleted_ids))
    db.execute(
        "UPDATE posts SET deletion_checked_at = now(), "
        "deleted_at = CASE WHEN id IN (SELECT jsonb_array_elements_text(CAST(:gone AS jsonb))) THEN now() ELSE deleted_at END "
        "WHERE id IN (SELECT jsonb_array_elements_text(CAST(:ids AS jsonb)))",
        {"ids": ids, "gone": gone})


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
                    "CAST(roi_pct AS float8) AS roi_pct, pending, needs_hand_grade, late_picks, deleted_picks "
                    "FROM leaderboard")

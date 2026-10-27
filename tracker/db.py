import json
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    handle      TEXT PRIMARY KEY,
    user_id     TEXT,
    followers   INTEGER,
    last_seen_id TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id          TEXT PRIMARY KEY,
    handle      TEXT NOT NULL,
    created_at  TEXT NOT NULL,          -- ISO 8601 UTC
    text        TEXT,
    media_urls  TEXT,                   -- JSON list of image URLs
    raw         TEXT,                   -- full JSON from the API, kept even if the post is later deleted
    parsed      INTEGER DEFAULT 0       -- 0 = not yet parsed, 1 = parsed
);

CREATE TABLE IF NOT EXISTS picks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     TEXT NOT NULL,
    handle      TEXT NOT NULL,
    posted_at   TEXT NOT NULL,
    league      TEXT,
    bet_type    TEXT,                   -- moneyline | spread | total | player_prop | parlay | other
    team        TEXT,                   -- team picked (moneyline/spread), or home/any team of the game (total)
    opponent    TEXT,
    side        TEXT,                   -- over | under (totals and props)
    line        REAL,
    odds        INTEGER,                -- American odds as posted (NULL if not stated)
    units       REAL,
    legs        TEXT,                   -- JSON list of legs for parlays
    description TEXT,                   -- the pick in plain words
    result      TEXT,                   -- NULL (pending) | win | loss | push | void | manual
    units_net   REAL,
    graded_at   TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS scoreboards (
    league      TEXT NOT NULL,
    date        TEXT NOT NULL,          -- YYYYMMDD
    data        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (league, date)
);

CREATE INDEX IF NOT EXISTS idx_picks_result ON picks(result);
CREATE INDEX IF NOT EXISTS idx_posts_parsed ON posts(parsed);
"""


def connect(path=None):
    conn = sqlite3.connect(path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)

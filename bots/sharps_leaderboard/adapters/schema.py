"""The leaderboard's tables, in their own Postgres schema ({S}, normally `sharps`).

Each entry runs as its own statement, and all of them are safe to re-run. accounts and
picks follow the agreed spec; the extra columns are what fetching and grading need on top
of it; posts keeps a copy of every post.
"""

# Before this repo hosted several bots, the leaderboard's tables lived in the default
# `public` schema. This moves them (data, indexes, enums) into {S} once, the first time
# it runs against such a database. Nothing is copied or deleted; it's a rename.
MIGRATE_FROM_PUBLIC = """DO $$ BEGIN
    IF to_regclass('public.picks') IS NOT NULL AND to_regclass('{S}.picks') IS NULL THEN
        DROP VIEW IF EXISTS public.leaderboard;
        ALTER TABLE public.accounts SET SCHEMA {S};
        ALTER TABLE public.posts SET SCHEMA {S};
        ALTER TABLE public.picks SET SCHEMA {S};
        ALTER TYPE public.sport SET SCHEMA {S};
        ALTER TYPE public.bet_type SET SCHEMA {S};
        ALTER TYPE public.pick_result SET SCHEMA {S};
        IF to_regtype('public.bet_period') IS NOT NULL THEN
            ALTER TYPE public.bet_period SET SCHEMA {S};
        END IF;
    END IF;
END $$"""

STATEMENTS = [
    "CREATE SCHEMA IF NOT EXISTS {S}",
    MIGRATE_FROM_PUBLIC,

    # --- enums -------------------------------------------------------------
    """DO $$ BEGIN
        CREATE TYPE {S}.sport AS ENUM ('NFL', 'NCAAF', 'NBA', 'WNBA', 'NCAAB', 'MLB', 'NHL', 'OTHER');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
    """DO $$ BEGIN
        CREATE TYPE {S}.bet_type AS ENUM ('moneyline', 'spread', 'over_under', 'prop', 'parlay', 'teaser', 'future', 'other');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
    """DO $$ BEGIN
        CREATE TYPE {S}.pick_result AS ENUM ('pending', 'win', 'loss', 'push', 'void', 'manual');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""",
    """DO $$ BEGIN
        CREATE TYPE {S}.bet_period AS ENUM ('full_game', 'first_half', 'second_half',
            'first_quarter', 'second_quarter', 'third_quarter', 'fourth_quarter',
            'first_period', 'second_period', 'third_period', 'first_five_innings', 'other');
    EXCEPTION WHEN duplicate_object THEN NULL; END $$""",

    # --- accounts ----------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS {S}.accounts (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        x_handle    text NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now(),
        active      boolean NOT NULL DEFAULT true,
        -- filled in from the X API
        x_user_id         text UNIQUE,
        followers         integer,
        last_seen_post_id text
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS accounts_handle_key ON {S}.accounts (lower(x_handle))",

    # --- posts (raw copy, so deleted posts still count) --------------------
    """CREATE TABLE IF NOT EXISTS {S}.posts (
        id            text PRIMARY KEY,               -- X post id
        sharp_account uuid NOT NULL REFERENCES {S}.accounts(id),
        posted_at     timestamptz NOT NULL,
        text          text,
        media_urls    jsonb NOT NULL DEFAULT '[]',
        raw           jsonb,
        parsed        boolean NOT NULL DEFAULT false
    )""",
    "CREATE INDEX IF NOT EXISTS posts_unparsed ON {S}.posts (posted_at) WHERE NOT parsed",

    # --- picks -------------------------------------------------------------
    """CREATE TABLE IF NOT EXISTS {S}.picks (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        sharp_account   uuid NOT NULL REFERENCES {S}.accounts(id),
        sport           {S}.sport NOT NULL,
        type            {S}.bet_type NOT NULL,
        line            integer,          -- odds as posted: +150 -> 150, -105 -> -105 (NULL if not stated)
        bet_description text NOT NULL,    -- e.g. 'Lakers moneyline', 'Tatum over 27.5 points'
        created_at      timestamptz NOT NULL,  -- when the pick was posted
        -- what grading needs
        post_id    text REFERENCES {S}.posts(id),
        team       text,                  -- side taken (moneyline/spread), or a team in the game (over/under)
        opponent   text,
        side       text CHECK (side IN ('over', 'under')),
        point      numeric,               -- spread or total number: -4.5, 215.5
        units      numeric,
        legs       jsonb NOT NULL DEFAULT '[]',   -- parlay/teaser legs
        result     {S}.pick_result NOT NULL DEFAULT 'pending',
        units_net  numeric,
        graded_at  timestamptz,
        note       text
    )""",
    "CREATE INDEX IF NOT EXISTS picks_account_time ON {S}.picks (sharp_account, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS picks_pending ON {S}.picks (created_at) WHERE result = 'pending'",

    # --- added later: ALTERs so an existing database picks them up -------------
    # Which part of the game the bet covers. Only full-game bets are graded automatically.
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS period {S}.bet_period NOT NULL DEFAULT 'full_game'",
    # The game the pick was matched to, and whether the pick went out before it started
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS game_id text",
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS game_start timestamptz",
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS home_team text",
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS away_team text",
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS home_score numeric",
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS away_score numeric",
    "ALTER TABLE {S}.picks ADD COLUMN IF NOT EXISTS posted_before_start boolean",  # NULL = no game matched yet
    # Deleted posts: checked once, after the post's picks are graded
    "ALTER TABLE {S}.posts ADD COLUMN IF NOT EXISTS deleted_at timestamptz",       # when we noticed it was gone
    "ALTER TABLE {S}.posts ADD COLUMN IF NOT EXISTS deletion_checked_at timestamptz",

    # --- leaderboard view, for querying the standings by hand ---------------
    # Picks posted after the game started don't count toward the record.
    # (New columns go at the end so CREATE OR REPLACE works on an existing view.)
    """CREATE OR REPLACE VIEW {S}.leaderboard AS
    SELECT a.x_handle,
           count(*) FILTER (WHERE p.result = 'win'  AND p.posted_before_start IS NOT FALSE) AS wins,
           count(*) FILTER (WHERE p.result = 'loss' AND p.posted_before_start IS NOT FALSE) AS losses,
           count(*) FILTER (WHERE p.result = 'push' AND p.posted_before_start IS NOT FALSE) AS pushes,
           round(coalesce(sum(p.units_net) FILTER (WHERE p.result IN ('win', 'loss', 'push')
                 AND p.posted_before_start IS NOT FALSE), 0), 2) AS units_won,
           round(coalesce(sum(p.units_net) FILTER (WHERE p.result IN ('win', 'loss', 'push')
                 AND p.posted_before_start IS NOT FALSE), 0)
                 / nullif(sum(coalesce(p.units, 1)) FILTER (WHERE p.result IN ('win', 'loss')
                 AND p.posted_before_start IS NOT FALSE), 0) * 100, 1) AS roi_pct,
           count(*) FILTER (WHERE p.result = 'pending') AS pending,
           count(*) FILTER (WHERE p.result = 'manual')  AS needs_hand_grade,
           count(*) FILTER (WHERE p.posted_before_start IS FALSE) AS late_picks,
           count(*) FILTER (WHERE po.deleted_at IS NOT NULL) AS deleted_picks
    FROM {S}.accounts a
    LEFT JOIN {S}.picks p ON p.sharp_account = a.id
    LEFT JOIN {S}.posts po ON po.id = p.post_id
    WHERE a.active
    GROUP BY a.x_handle
    ORDER BY units_won DESC""",
]

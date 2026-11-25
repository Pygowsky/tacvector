"""Query indexes and derived tables for the TacVector API.

    python db_setup.py        # (re)build indexes + team_rounds, then ANALYZE

Run automatically at the end of ingest_grid.py and checked at API startup.

team_rounds has one row per (game, round, team) with side, result, the team's
buy-phase economy and its buy tier:
    Pistol    round 1 or 13 (first round of each regulation half)
    Full Buy  team loadout >= 20,000
    Eco       team loadout <  5,000
    Bonus     won the previous round, loadout below a full buy (playing on saved gear)
    Force     everything else
Loadout is GRID's loadoutValue summed over the team at the end of the buy phase.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

DB_PATH = Path(os.environ.get("TACVECTOR_DB", Path(__file__).resolve().parent / "tacvector.db"))

FULL_BUY_MIN = 20_000
ECO_MAX = 5_000
PISTOL_ROUNDS = (1, 13)

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_games_valid ON games(session_valid, map_slug);
CREATE INDEX IF NOT EXISTS idx_games_team1 ON games(team1_id);
CREATE INDEX IF NOT EXISTS idx_games_team2 ON games(team2_id);
CREATE INDEX IF NOT EXISTS idx_rounds_attacker ON rounds(attacker_team_id);
CREATE INDEX IF NOT EXISTS idx_rounds_defender ON rounds(defender_team_id);
CREATE INDEX IF NOT EXISTS idx_game_players_team ON game_players(team_id, agent);

CREATE INDEX IF NOT EXISTS idx_casts_team_map ON utility_casts(team_id, map_slug, side);
CREATE INDEX IF NOT EXISTS idx_casts_map_side ON utility_casts(map_slug, side);
CREATE INDEX IF NOT EXISTS idx_casts_player ON utility_casts(player_id, map_slug);
CREATE INDEX IF NOT EXISTS idx_casts_agent ON utility_casts(agent, map_slug);
CREATE INDEX IF NOT EXISTS idx_casts_round_team ON utility_casts(game_id, round_number, team_id);

CREATE INDEX IF NOT EXISTS idx_kills_killer_team ON kills(killer_team_id, map_slug);
CREATE INDEX IF NOT EXISTS idx_kills_victim_team ON kills(victim_team_id, map_slug);
CREATE INDEX IF NOT EXISTS idx_kills_killer ON kills(killer_id);
CREATE INDEX IF NOT EXISTS idx_kills_victim ON kills(victim_id);
CREATE INDEX IF NOT EXISTS idx_kills_weapon ON kills(weapon, map_slug);
CREATE INDEX IF NOT EXISTS idx_kills_first ON kills(game_id, round_number) WHERE first_kill = 1;

CREATE INDEX IF NOT EXISTS idx_purchases_team ON purchases(team_id, item_id);
CREATE INDEX IF NOT EXISTS idx_purchases_item ON purchases(item_id, game_id, round_number);
CREATE INDEX IF NOT EXISTS idx_purchases_buyer ON purchases(player_id);
CREATE INDEX IF NOT EXISTS idx_purchases_recipient ON purchases(recipient_id);

CREATE INDEX IF NOT EXISTS idx_player_rounds_player ON player_rounds(player_id);
CREATE INDEX IF NOT EXISTS idx_player_rounds_team ON player_rounds(team_id, game_id, round_number);
CREATE INDEX IF NOT EXISTS idx_player_rounds_primary ON player_rounds(primary_weapon, team_id);
"""

TEAM_ROUNDS = f"""
DROP TABLE IF EXISTS team_rounds;
CREATE TABLE team_rounds AS
WITH sides AS (
    SELECT r.game_id, r.round_number, r.series_id, g.map_slug, g.session_valid, r.map_uncertain, r.finished,
           r.attacker_team_id AS team_id, r.defender_team_id AS opponent_team_id, 'attacker' AS side,
           r.attacker_loadout AS loadout, r.attacker_spent AS spent, r.winner_team_id, r.win_type
    FROM rounds r JOIN games g ON g.game_id = r.game_id
    WHERE r.attacker_team_id IS NOT NULL
    UNION ALL
    SELECT r.game_id, r.round_number, r.series_id, g.map_slug, g.session_valid, r.map_uncertain, r.finished,
           r.defender_team_id, r.attacker_team_id, 'defender',
           r.defender_loadout, r.defender_spent, r.winner_team_id, r.win_type
    FROM rounds r JOIN games g ON g.game_id = r.game_id
    WHERE r.defender_team_id IS NOT NULL
), seq AS (
    SELECT s.*,
           LAG(winner_team_id) OVER w AS prev_winner,
           LAG(round_number) OVER w AS prev_round
    FROM sides s
    WINDOW w AS (PARTITION BY game_id, team_id ORDER BY round_number)
)
SELECT game_id, round_number, series_id, map_slug, team_id, opponent_team_id, side,
       CAST(winner_team_id = team_id AS INTEGER) AS won, win_type, loadout, spent,
       CASE
           WHEN round_number IN {PISTOL_ROUNDS} THEN 'Pistol'
           WHEN loadout IS NULL THEN NULL
           WHEN loadout >= {FULL_BUY_MIN} THEN 'Full Buy'
           WHEN loadout < {ECO_MAX} THEN 'Eco'
           WHEN prev_winner = team_id AND prev_round = round_number - 1 THEN 'Bonus'
           ELSE 'Force'
       END AS buy_tier,
       session_valid, map_uncertain, finished
FROM seq;
CREATE UNIQUE INDEX idx_team_rounds_pk ON team_rounds(game_id, round_number, team_id);
CREATE INDEX idx_team_rounds_team ON team_rounds(team_id, map_slug, side);
CREATE INDEX idx_team_rounds_map ON team_rounds(map_slug, side);
"""


def _rounds_signature(conn: sqlite3.Connection) -> str:
    n = conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]
    last = conn.execute("SELECT MAX(ingested_at) FROM ingest_log").fetchone()[0]
    return f"{n}|{last}"


def ensure(conn: sqlite3.Connection, force: bool = False) -> bool:
    """Create indexes; rebuild team_rounds when missing or stale. Returns True if rebuilt."""
    conn.executescript(INDEXES)
    conn.execute("CREATE TABLE IF NOT EXISTS db_meta (key TEXT PRIMARY KEY, value TEXT)")
    sig = _rounds_signature(conn)
    row = conn.execute("SELECT value FROM db_meta WHERE key = 'team_rounds_signature'").fetchone()
    has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'team_rounds'").fetchone()
    if not force and has_table and row and row[0] == sig:
        return False
    with conn:
        conn.executescript(TEAM_ROUNDS)
        conn.execute("INSERT OR REPLACE INTO db_meta VALUES ('team_rounds_signature', ?)", (sig,))
    conn.execute("ANALYZE")
    return True


def main() -> None:
    t0 = time.perf_counter()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        ensure(conn, force=True)
        n = conn.execute("SELECT COUNT(*) FROM team_rounds").fetchone()[0]
        tiers = conn.execute("SELECT buy_tier, COUNT(*) FROM team_rounds GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print(f"Indexes ready, team_rounds rebuilt ({n} rows) in {time.perf_counter() - t0:.1f}s: {tiers}")


if __name__ == "__main__":
    main()

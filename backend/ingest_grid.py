"""Ingest the GRID VALORANT feeds + ability supplements into backend/tacvector.db.

Reads (streaming, never decompressed to disk):
    $STL_DATA_DIR/val/series.csv
    $STL_DATA_DIR/val/events_{seriesId}_grid.jsonl.zst
    $STL_DATA_DIR/val-ability-supplements/{seriesId}.jsonl.zst
STL_DATA_DIR defaults to ../stl-hackathon-2026-grid-starter (next to this repo).

Writes these tables (the asset tables from seed_assets.py are only read):
    matches, games, game_players, rounds, utility_casts, purchases, kills, ingest_log

Usage:
    python ingest_grid.py --limit 5          # small verification batch
    python ingest_grid.py                    # everything not yet ingested
    python ingest_grid.py --series 2748743   # specific series
    python ingest_grid.py --force            # re-ingest even if already done
    python ingest_grid.py --reset            # drop + recreate the GRID tables first

The resulting database holds data derived from the licensed GRID dataset:
it must never be committed or shared (see AGENTS.md). *.db is gitignored.

Notes on how things are derived (see AGENTS.md / val-ability-supplements/README.md):
  * Cast positions are the caster's position at the moment of the cast, taken
    from the supplement (interpolated, or the nearer frame when quality is
    gap_exceeded) with GRID's own 2D position as fallback. The data has no
    ability landing points; view_{x,y,z} is kept so a landing model can be added.
  * VALORANT feeds have no purchase events. Purchases are reconstructed per buy
    phase from the full-state inventories at round-started-freezetime and
    round-ended-freezetime, corrected for pickups/drops during the buy phase:
        gained = inv_end - inv_start - picked_up + dropped
    GRID often omits pickup/drop events for weapons handed over in the buy
    phase, so gains are reconciled per team against each player's credits spent
    (see reconcile_buys): hand-overs are discarded, and weapons bought for a
    teammate are attributed to the payer (player_id) with recipient_id = holder.
    Older feeds (VCT Americas 2025) omit shields from inventory; those are
    inferred from a currentArmor increase (25 = light, 50 = heavy).
  * GRID leaves weaponKills empty for ability kills; the weapon then comes
    from the killer's last damage source on the victim.
  * Games rolled back with series-!started-game, or absent from the final full
    state of their feed session, are dropped. When GRID starts the same round
    number again (a technical replay, often with no ! rollback event), only the
    last attempt's casts, kills and purchases are kept.
  * Where GRID merged the next map into a game (e.g. 2890334, 2890361), the
    merged rounds are detected from missing supplement coverage, flagged
    (games.merged_from_round, rounds.map_uncertain) and left unprojected.
  * Filter on games.session_valid: GRID marks some sessions invalid, e.g. the
    first session of 2890336, which re-sends rounds 1-13 as rounds 14-26.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from compression import zstd  # Python 3.14+

try:
    import orjson

    loads = orjson.loads
except ImportError:  # pragma: no cover
    import json

    loads = json.loads

BACKEND = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TACVECTOR_DB", BACKEND / "tacvector.db"))
DATA_DIR = Path(os.environ.get("STL_DATA_DIR", BACKEND.parent.parent / "stl-hackathon-2026-grid-starter")).resolve()

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    series_id        TEXT PRIMARY KEY,
    event            TEXT,
    stage            TEXT,
    scheduled_start  TEXT,
    started_at       TEXT,
    format           TEXT,
    team1_id         TEXT, team1_name TEXT, team1_score INTEGER,
    team2_id         TEXT, team2_name TEXT, team2_score INTEGER,
    winner_team_id   TEXT,
    valid            INTEGER,  -- GRID valid flag at the end of the last session
    sessions         INTEGER,
    has_supplement   INTEGER
);

CREATE TABLE IF NOT EXISTS games (
    game_id          TEXT PRIMARY KEY,
    series_id        TEXT NOT NULL,
    session          INTEGER NOT NULL,
    session_valid    INTEGER,
    game_number      INTEGER,
    map_slug         TEXT,     -- joins maps.slug
    started_at       TEXT,
    finished         INTEGER,
    rounds_played    INTEGER,
    team1_id TEXT, team1_name TEXT, team1_final_side TEXT, team1_score INTEGER,
    team2_id TEXT, team2_name TEXT, team2_final_side TEXT, team2_score INTEGER,
    winner_team_id   TEXT,
    merged_from_round INTEGER  -- set when GRID merged the next map into this game from this round on
);
CREATE INDEX IF NOT EXISTS idx_games_series ON games(series_id);
CREATE INDEX IF NOT EXISTS idx_games_map ON games(map_slug);

CREATE TABLE IF NOT EXISTS game_players (
    game_id     TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    series_id   TEXT NOT NULL,
    team_id     TEXT,
    player_name TEXT,
    agent       TEXT,           -- joins agents.slug
    kills INTEGER, deaths INTEGER, assists INTEGER,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS idx_game_players_series ON game_players(series_id);
CREATE INDEX IF NOT EXISTS idx_game_players_player ON game_players(player_id);

CREATE TABLE IF NOT EXISTS rounds (
    game_id            TEXT NOT NULL,
    round_number       INTEGER NOT NULL,
    series_id          TEXT NOT NULL,
    started_at         TEXT,
    buy_end_at         TEXT,     -- round-ended-freezetime
    duration_ms        INTEGER,
    attacker_team_id   TEXT,
    defender_team_id   TEXT,
    winner_team_id     TEXT,
    winner_side        TEXT,
    win_type           TEXT,     -- opponentEliminated, bombExploded, bombDefused, ...
    finished           INTEGER,  -- 0 for segments GRID started but never finished
    map_uncertain      INTEGER,  -- 1 = round belongs to a map GRID merged into this game (see games.merged_from_round)
    attacker_loadout   INTEGER,  -- sum of loadoutValue at buy end
    defender_loadout   INTEGER,
    attacker_spent     INTEGER,  -- credits spent during the buy phase (incl. abilities)
    defender_spent     INTEGER,
    PRIMARY KEY (game_id, round_number)
);
CREATE INDEX IF NOT EXISTS idx_rounds_series ON rounds(series_id);

CREATE TABLE IF NOT EXISTS utility_casts (
    event_id          TEXT PRIMARY KEY,  -- GRID events[].id == supplement grid_event_id
    series_id         TEXT NOT NULL,
    game_id           TEXT NOT NULL,
    round_number      INTEGER,
    occurred_at       TEXT NOT NULL,
    round_time_ms     INTEGER,  -- ms since buy phase ended (negative = during buy phase)
    map_slug          TEXT,
    player_id         TEXT NOT NULL,
    team_id           TEXT,
    side              TEXT,
    agent             TEXT,
    ability_id        TEXT NOT NULL,  -- GRID ability id, e.g. "toxic-screen"
    supplement_slot   TEXT,           -- ABILITY_1, ABILITY_2, GRENADE, ULTIMATE (from supplement)
    api_slot          TEXT,           -- Ability1, Ability2, Grenade, Ultimate, Passive (joins agent_abilities.slot)
    charges_consumed  INTEGER,
    x REAL, y REAL, z REAL,           -- caster world position (cm)
    view_x REAL, view_y REAL, view_z REAL,
    position_source   TEXT,           -- interpolated | frame_before | frame_after | grid
    supplement_quality TEXT,          -- ok, gap_exceeded, missing_after, ... (NULL = no supplement)
    frame_gap_ms      INTEGER,        -- ~200 normal, ~1000 in low-rate series
    minimap_u REAL, minimap_v REAL,
    callout           TEXT,           -- nearest callouts.region_name
    callout_super     TEXT,           -- its callouts.super_region_name
    callout_dist      REAL            -- world units (cm) to that callout
);
CREATE INDEX IF NOT EXISTS idx_casts_series ON utility_casts(series_id);
CREATE INDEX IF NOT EXISTS idx_casts_game_round ON utility_casts(game_id, round_number);
CREATE INDEX IF NOT EXISTS idx_casts_map_ability ON utility_casts(map_slug, agent, ability_id);

CREATE TABLE IF NOT EXISTS purchases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id     TEXT NOT NULL,
    game_id       TEXT NOT NULL,
    round_number  INTEGER,
    occurred_at   TEXT,     -- buy phase end
    player_id     TEXT NOT NULL,  -- who paid
    recipient_id  TEXT NOT NULL,  -- who held it at buy end (differs for drops bought for a teammate)
    team_id       TEXT,
    side          TEXT,
    agent         TEXT,           -- buyer's agent
    item_id       TEXT NOT NULL,  -- GRID inventory id, e.g. "vandal", "heavyshield"
    item_kind     TEXT,           -- weapon | gear | unknown
    item_uuid     TEXT,           -- weapons.uuid or gear.uuid
    quantity      INTEGER NOT NULL,
    unit_cost     INTEGER,         -- current valorant-api price (historical prices may differ)
    inferred      INTEGER NOT NULL DEFAULT 0  -- 1 = shield inferred from currentArmor (older feeds omit shields from inventory)
);
CREATE INDEX IF NOT EXISTS idx_purchases_series ON purchases(series_id);
CREATE INDEX IF NOT EXISTS idx_purchases_game_round ON purchases(game_id, round_number);

CREATE TABLE IF NOT EXISTS kills (
    event_id        TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL,
    game_id         TEXT NOT NULL,
    round_number    INTEGER,
    occurred_at     TEXT NOT NULL,
    round_time_ms   INTEGER,
    map_slug        TEXT,
    kill_type       TEXT,   -- enemy | team | self
    killer_id TEXT, killer_team_id TEXT, killer_side TEXT, killer_agent TEXT,
    victim_id TEXT, victim_team_id TEXT, victim_side TEXT, victim_agent TEXT,
    weapon          TEXT,   -- weapon id, or ability id for ability kills
    weapon_source   TEXT,   -- grid (weaponKills) | damage (killer's last damage source on victim)
    first_kill      INTEGER,
    assister_ids    TEXT,   -- comma separated
    killer_x REAL, killer_y REAL, killer_u REAL, killer_v REAL, killer_callout TEXT, killer_callout_super TEXT,
    victim_x REAL, victim_y REAL, victim_u REAL, victim_v REAL, victim_callout TEXT, victim_callout_super TEXT
);
CREATE INDEX IF NOT EXISTS idx_kills_series ON kills(series_id);
CREATE INDEX IF NOT EXISTS idx_kills_game_round ON kills(game_id, round_number);

CREATE TABLE IF NOT EXISTS ingest_log (
    series_id    TEXT PRIMARY KEY,
    status       TEXT NOT NULL,   -- ok | error
    ingested_at  TEXT NOT NULL,
    seconds      REAL,
    message      TEXT
);

CREATE TABLE IF NOT EXISTS player_rounds (
    game_id        TEXT NOT NULL,
    round_number   INTEGER NOT NULL,
    player_id      TEXT NOT NULL,
    series_id      TEXT NOT NULL,
    team_id        TEXT,
    side           TEXT,
    agent          TEXT,
    money_start    INTEGER,  -- credits at buy phase start
    money_end      INTEGER,  -- credits at buy phase end
    spent          INTEGER,
    loadout_value  INTEGER,  -- GRID loadoutValue at buy end
    armor          INTEGER,  -- currentArmor at buy end (25 light, 50 heavy)
    weapons        TEXT,     -- comma-separated weapon ids held at buy end, e.g. "classic,operator"
    primary_weapon TEXT,     -- most expensive non-sidearm held at buy end (NULL = sidearm only)
    has_spike      INTEGER,
    PRIMARY KEY (game_id, round_number, player_id)
);
CREATE INDEX IF NOT EXISTS idx_player_rounds_series ON player_rounds(series_id);

-- Casts with the ability's name and icon from the asset tables.
CREATE VIEW IF NOT EXISTS utility_casts_v AS
SELECT c.*, ab.name AS ability_name, ab.display_icon AS ability_icon
FROM utility_casts c
LEFT JOIN agents a ON a.slug = c.agent
LEFT JOIN agent_abilities ab ON ab.agent_uuid = a.uuid AND ab.slot = c.api_slot;
"""

GRID_TABLES = ("matches", "games", "game_players", "rounds", "utility_casts", "purchases", "kills", "player_rounds")

SUPPLEMENT_SLOT_TO_API = {
    "ABILITY_1": "Ability1", "ABILITY_2": "Ability2", "GRENADE": "Grenade",
    "ULTIMATE": "Ultimate", "PASSIVE": "Passive",
}
# GRID inventory ids for gear that valorant-api names differently.
GEAR_ALIASES = {"heavyshield": "heavy armor", "lightshield": "light armor", "regenshield": "regen shield"}
NOT_PURCHASABLE = {"spike", "melee"}
SIDEARM_MAX_COST = 800  # Sheriff; anything pricier is a primary weapon
# A team's reconstructed buys may exceed its credits spent by this much before the buy phase is
# rejected as an inconsistent snapshot. Real buys run up to ~15% over (current prices vs 2025
# prices, unrecorded floor pickups); broken snapshots are off by 10x.
ECON_TOLERANCE = (1.25, 1500)
KILL_TYPES = {"player-killed-player": "enemy", "player-teamkilled-player": "team", "player-selfkilled-player": "self"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9']+", "-", name.lower()).strip("-")


def parse_ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def ms_between(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return round((parse_ts(b) - parse_ts(a)).total_seconds() * 1000)


_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?")


def duration_ms(s: str | None) -> int | None:
    if not s:
        return None
    m = _DUR.fullmatch(s)
    if not m:
        return None
    d, h, mi, sec = (float(x) if x else 0.0 for x in m.groups())
    return round(((d * 24 + h) * 60 + mi) * 60_000 + sec * 1000)


def round_from_id(segment_id: str | None) -> int | None:
    if segment_id and segment_id.startswith("round-"):
        try:
            return int(segment_id.split("-", 1)[1])
        except ValueError:
            return None
    return None


def iter_jsonl_zst(path: Path):
    with zstd.open(path, "rb") as f:
        for line in f:
            if line.strip():
                yield loads(line)


def g(obj, *keys):
    """Nested dict get that tolerates None along the way."""
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


# --------------------------------------------------------------------------- #
# Static assets (loaded once per worker)
# --------------------------------------------------------------------------- #

class Assets:
    def __init__(self, db_path: Path):
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            missing = {"maps", "callouts", "agents", "agent_abilities", "weapons", "gear"} - {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if missing:
                raise SystemExit(f"Asset tables missing ({', '.join(sorted(missing))}); run seed_assets.py first.")
            self.maps = {
                slug: (xm, ym, xa, ya)
                for slug, xm, ym, xa, ya in conn.execute(
                    "SELECT slug, x_multiplier, y_multiplier, x_scalar_to_add, y_scalar_to_add FROM maps")
            }
            self.callouts: dict[str, list[tuple[str, str, float, float]]] = {}
            for slug, region, sup, x, y in conn.execute(
                    "SELECT m.slug, c.region_name, c.super_region_name, c.x, c.y FROM callouts c JOIN maps m ON m.uuid = c.map_uuid"):
                self.callouts.setdefault(slug, []).append((region, sup, x, y))
            self.ability_slots: dict[str, dict[str, str]] = {}
            for agent, slot, name in conn.execute(
                    "SELECT a.slug, ab.slot, ab.name FROM agent_abilities ab JOIN agents a ON a.uuid = ab.agent_uuid"):
                self.ability_slots.setdefault(agent, {})[slugify(name)] = slot
            self.items: dict[str, tuple[str, str, int | None]] = {}
            for uuid, slug, cost in conn.execute("SELECT uuid, slug, cost FROM weapons"):
                self.items[re.sub(r"[^a-z0-9]", "", slug)] = ("weapon", uuid, cost)
            gear = {slug: (uuid, cost) for uuid, slug, cost in conn.execute("SELECT uuid, slug, cost FROM gear")}
            for grid_id, slug in GEAR_ALIASES.items():
                if slug in gear:
                    self.items[grid_id] = ("gear", *gear[slug])

    def project(self, map_slug: str | None, x: float | None, y: float | None):
        """World (x, y) -> (u, v, callout, callout_super, dist). Axis-swapped valorant-api formula."""
        if map_slug not in self.maps or x is None or y is None:
            return None, None, None, None, None
        xm, ym, xa, ya = self.maps[map_slug]
        u = y * xm + xa
        v = x * ym + ya
        best = None
        for region, sup, cx, cy in self.callouts.get(map_slug, ()):
            d2 = (cx - x) ** 2 + (cy - y) ** 2
            if best is None or d2 < best[0]:
                best = (d2, region, sup)
        if best is None:
            return u, v, None, None, None
        return u, v, best[1], best[2], best[0] ** 0.5

    def api_slot(self, agent: str | None, ability_id: str, supplement_slot: str | None) -> str | None:
        by_name = self.ability_slots.get(agent or "", {}).get(ability_id)
        return by_name or SUPPLEMENT_SLOT_TO_API.get(supplement_slot or "")

    def item(self, item_id: str):
        return self.items.get(re.sub(r"[^a-z0-9]", "", item_id), ("unknown", None, None))


_ASSETS: Assets | None = None


def _init_worker(db_path: str) -> None:
    global _ASSETS
    _ASSETS = Assets(Path(db_path))


# --------------------------------------------------------------------------- #
# Per-series parsing (runs in worker processes)
# --------------------------------------------------------------------------- #

def cast_position(sup: dict | None, grid_pos: dict | None):
    """Pick the best caster position/view per the supplement's quality rules."""
    if sup:
        q = sup.get("quality")
        fb, fa = sup.get("frame_before"), sup.get("frame_after")
        gap = (fa["offset_ms"] - fb["offset_ms"]) if fb and fa else None
        if q == "gap_exceeded" and fb and fa:
            src, frame = ("frame_before", fb) if abs(fb["offset_ms"]) <= abs(fa["offset_ms"]) else ("frame_after", fa)
            return frame["position"], frame.get("view"), src, q, gap
        if sup.get("interpolated"):
            ip = sup["interpolated"]
            return ip["position"], ip.get("view"), "interpolated", q, gap
        if grid_pos:
            return grid_pos, None, "grid", q, gap
        return None, None, None, q, gap
    return (grid_pos, None, "grid", None, None) if grid_pos else (None, None, None, None, None)


def reconcile_buys(players: dict[str, dict], cost_of) -> list[tuple[str, str, str, int]]:
    """Turn one team's buy-phase inventory changes into (buyer, holder, item, inferred) units.

    GRID often doesn't emit pickup/drop events for weapons handed over during the
    buy phase, so an inventory gain may be (a) the player's own buy, (b) a weapon a
    teammate was carrying, or (c) a weapon a teammate bought for them. Each
    player's credits spent bound what they can have bought themselves:
      1. Gains the holder couldn't afford that match an item a teammate lost are
         hand-overs, not purchases.
      2. Remaining unaffordable gains are attributed to the teammate with the
         tightest-fitting unexplained spend (a "drop").
    """
    cost = lambda it: cost_of(it.rstrip("*")) or 0
    units = {pid: [(it, 0) for it in p["gained"]] + [(it.rstrip("*"), int(it.endswith("*"))) for it in p["shields"]]
             for pid, p in players.items()}
    lost_by: dict[str, list[str]] = {}
    for pid, p in players.items():
        for it in p["lost"]:
            lost_by.setdefault(it, []).append(pid)

    for pid, p in players.items():
        need = sum(cost(it) for it, _ in units[pid])
        for unit in sorted(units[pid], key=lambda u: -cost(u[0])):
            if need <= p["spent"]:
                break
            it, inferred = unit
            if "shield" in it:
                continue
            giver = next((o for o in lost_by.get(it, ()) if o != pid), None)
            if giver is not None:
                lost_by[it].remove(giver)
                units[pid].remove(unit)
                need -= cost(it)

    spare = {pid: p["spent"] - sum(cost(it) for it, _ in units[pid]) for pid, p in players.items()}
    out = []
    for pid in players:
        for it, inferred in sorted(units[pid], key=lambda u: -cost(u[0])):
            buyer = pid
            if spare[pid] < 0 and "shield" not in it:
                fits = [q for q in players if q != pid and spare[q] >= cost(it)]
                if fits:
                    buyer = min(fits, key=lambda q: spare[q])
                    spare[buyer] -= cost(it)
                    spare[pid] += cost(it)
            out.append((buyer, pid, it, inferred))
    return out


def inventory_counts(player: dict) -> Counter:
    c = Counter()
    for it in g(player, "inventory", "items") or ():
        c[it["id"]] += it.get("quantity") or 1
    return c


def find_game(state: dict | None, game_id: str | None) -> dict | None:
    for game in (state or {}).get("games") or ():
        if game.get("id") == game_id:
            return game
    return None


def event_game_id(e: dict, current: str | None) -> str | None:
    games = g(e, "seriesStateDelta", "games")
    if games:
        return games[-1].get("id") or current
    return current


def parse_series(row: dict) -> dict:
    t0 = time.perf_counter()
    A = _ASSETS
    sid = row["series_id"]
    feed = DATA_DIR / row["file"]
    sup_path = DATA_DIR / row["ability_supplement"] if row.get("ability_supplement") else None

    supplement: dict[str, dict] = {}
    if sup_path and sup_path.exists():
        for c in iter_jsonl_zst(sup_path):
            if c.get("in_grid") and c.get("grid_event_id"):
                supplement[c["grid_event_id"]] = c

    session = 0
    cur_game: str | None = None
    round_no: dict[str, int] = {}
    rolled_back: set[str] = set()
    last_full: dict[int, dict] = {}
    game_map: dict[str, str] = {}
    agent_of: dict[tuple[str, str], str] = {}
    round_started: dict[tuple[str, int], str] = {}
    buy_end: dict[tuple[str, int], str] = {}
    econ: dict[tuple[str, int], dict] = {}
    freeze: dict[str, dict] = {}  # game_id -> open buy-phase tracker
    attempts: Counter = Counter()  # (game_id, round) -> times started; GRID replays rounds after tech issues

    casts, kills, purchases = [], [], []
    econ_skipped = 0
    player_rounds: dict[tuple[str, int, str], dict] = {}  # keyed so a replayed round's last attempt wins
    last_damage: dict[tuple[str, str], str] = {}  # (attacker, victim) -> damage source

    for env in iter_jsonl_zst(feed):
        at = env["occurredAt"]
        for e in env["events"]:
            t = e["type"]
            if t == "grid-started-feed":
                session += 1
                cur_game = None
                freeze.clear()

            state = e.get("seriesState") if e.get("includesFullState") else None
            if state:
                last_full[session] = state
                for game in state.get("games") or ():
                    gid_ = game["id"]
                    if g(game, "map", "name"):
                        game_map[gid_] = game["map"]["name"]
                for game in (state.get("games") or ())[-1:]:
                    for team in game.get("teams") or ():
                        for p in team.get("players") or ():
                            if g(p, "character", "id"):
                                agent_of[(game["id"], p["id"])] = p["character"]["id"]

            actor, target = e.get("actor") or {}, e.get("target") or {}

            if t == "series-started-game":
                cur_game = target.get("id")
                continue
            if t == "series-!started-game":
                rolled_back.add(target.get("id"))
                continue

            gid = event_game_id(e, cur_game)

            if t in ("game-started-round", "game-!started-round"):
                gid = actor.get("id") or gid
                n = g(target, "state", "sequenceNumber") or round_from_id(target.get("id"))
                if n is not None:
                    if t == "game-started-round":
                        round_no[gid] = n
                        round_started[(gid, n)] = at
                        attempts[(gid, n)] += 1
                    else:
                        round_no[gid] = n - 1
                continue

            rn = round_no.get(gid)

            if t == "round-started-freezetime" and state:
                game = find_game(state, gid)
                if game is not None:
                    snap = {}
                    for team in game.get("teams") or ():
                        for p in team.get("players") or ():
                            snap[p["id"]] = (inventory_counts(p), p.get("money") or 0, p.get("currentArmor") or 0)
                    freeze[gid] = {"round": rn, "start": snap, "picked": Counter(), "dropped": Counter()}
                continue

            if t in ("player-pickedUp-item", "player-!pickedUp-item", "player-dropped-item", "player-!dropped-item"):
                fz = freeze.get(gid)
                if fz is not None:
                    key = "picked" if "pickedUp" in t else "dropped"
                    fz[key][(actor.get("id"), target.get("id"))] += -1 if "!" in t else 1
                continue

            if t == "round-ended-freezetime" and state:
                buy_end[(gid, rn)] = at
                fz = freeze.pop(gid, None)
                game = find_game(state, gid)
                if fz is None or game is None or fz["round"] != rn:
                    continue
                team_econ = {}
                for team in game.get("teams") or ():
                    loadout = spent = 0
                    buyers = {}
                    for p in team.get("players") or ():
                        pid = p["id"]
                        end_inv, end_money = inventory_counts(p), p.get("money") or 0
                        start_inv, start_money, start_armor = fz["start"].get(pid, (Counter(), end_money, 0))
                        loadout += p.get("loadoutValue") or 0
                        spent += max(0, start_money - end_money)
                        gained, lost, shields = [], [], []
                        for item in set(end_inv) | set(start_inv) | {i for (pp, i) in fz["dropped"] if pp == pid}:
                            if item in NOT_PURCHASABLE:
                                continue
                            qty = (end_inv[item] - start_inv[item]
                                   - fz["picked"][(pid, item)] + fz["dropped"][(pid, item)])
                            if qty > 0:
                                # Shields can't be handed over, so they are always the holder's own buy.
                                (shields if "shield" in item else gained).extend([item] * qty)
                            elif qty < 0:
                                lost.extend([item] * -qty)
                        end_armor = p.get("currentArmor") or 0
                        if not any("shield" in i for i in end_inv) and end_armor > start_armor:
                            shields.append("heavyshield*" if end_armor >= 50 else "lightshield*")
                        buyers[pid] = dict(spent=max(0, start_money - end_money), gained=gained, lost=lost, shields=shields)
                        held = sorted(i for i in end_inv if i not in NOT_PURCHASABLE)
                        guns = [(A.item(i)[2] or 0, i) for i in held if A.item(i)[0] == "weapon"]
                        primary = max(guns)[1] if guns and max(guns)[0] > SIDEARM_MAX_COST else None
                        player_rounds[(gid, rn, pid)] = dict(
                            game_id=gid, round_number=rn, player_id=pid, series_id=sid, team_id=team["id"],
                            side=team.get("side"), agent=agent_of.get((gid, pid)), money_start=start_money,
                            money_end=end_money, spent=max(0, start_money - end_money),
                            loadout_value=p.get("loadoutValue"), armor=end_armor,
                            weapons=",".join(i for i in held if A.item(i)[0] == "weapon") or None,
                            primary_weapon=primary, has_spike=int("spike" in end_inv),
                        )
                    team_buys = []
                    for buyer, holder, item, inferred in reconcile_buys(buyers, lambda i: A.item(i)[2]):
                        kind, uuid, cost = A.item(item)
                        if cost == 0:  # free default sidearm
                            continue
                        team_buys.append(dict(
                            series_id=sid, game_id=gid, round_number=rn, occurred_at=at, _att=attempts[(gid, rn)],
                            player_id=buyer, recipient_id=holder, team_id=team["id"], side=team.get("side"),
                            agent=agent_of.get((gid, buyer)), item_id=item, item_kind=kind,
                            item_uuid=uuid, quantity=1, unit_cost=cost, inferred=inferred,
                        ))
                    # Inconsistent snapshots (e.g. a match resumed from restored state) show far more
                    # gear than the team paid for; skip those rather than store phantom buys.
                    # The margin absorbs patch-to-patch price changes.
                    if sum(b["unit_cost"] or 0 for b in team_buys) > spent * ECON_TOLERANCE[0] + ECON_TOLERANCE[1]:
                        econ_skipped += 1
                    else:
                        purchases.extend(team_buys)
                    team_econ[team.get("side")] = (loadout, spent)
                econ[(gid, rn)] = team_econ
                continue

            if t in ("player-damaged-player", "player-teamdamaged-player", "player-selfdamaged-player"):
                ad = actor.get("stateDelta") or {}
                for scope in ("round", "game"):
                    src = g(ad, scope, "damageDealtSources")
                    if src and g(src[0], "source", "name"):
                        last_damage[(actor.get("id"), target.get("id"))] = src[0]["source"]["name"]
                        break
                continue

            if t == "player-used-ability":
                ast = actor.get("state") or {}
                sup = supplement.get(e["id"])
                pos, view, src, quality, gap = cast_position(sup, g(ast, "game", "position"))
                agent = agent_of.get((gid, actor.get("id")))
                ability = target.get("id")
                casts.append(dict(
                    event_id=e["id"], series_id=sid, game_id=gid, round_number=rn, occurred_at=at, _att=attempts[(gid, rn)],
                    round_time_ms=None,
                    player_id=actor.get("id"), team_id=ast.get("teamId"), side=ast.get("side"),
                    agent=agent, ability_id=ability,
                    supplement_slot=sup.get("slot") if sup else None,
                    api_slot=A.api_slot(agent, ability, sup.get("slot") if sup else None),
                    charges_consumed=sup.get("charges_consumed") if sup else None,
                    x=g(pos, "x"), y=g(pos, "y"), z=g(pos, "z"),
                    view_x=g(view, "x"), view_y=g(view, "y"), view_z=g(view, "z"),
                    position_source=src, supplement_quality=quality, frame_gap_ms=gap,
                ))
                continue

            if t in KILL_TYPES:
                ast, tst = actor.get("state") or {}, target.get("state") or {}
                ad = actor.get("stateDelta") or {}
                weapon = None
                for scope in ("round", "game", "series"):
                    for k in ("weaponKills", "weaponTeamkills"):
                        wk = g(ad, scope, k)
                        if wk:
                            weapon = next(iter(wk))
                            break
                    if weapon:
                        break
                weapon_source = "grid" if weapon else None
                if not weapon and last_damage.get((actor.get("id"), target.get("id"))):
                    weapon, weapon_source = last_damage[(actor.get("id"), target.get("id"))], "damage"
                assisters = [
                    p["id"]
                    for team in g(e, "seriesStateDelta", "teams") or ()
                    for p in team.get("players") or ()
                    if p.get("killAssistsGiven") and p["id"] != actor.get("id")
                ]
                kills.append(dict(
                    event_id=e["id"], series_id=sid, game_id=gid, round_number=rn, occurred_at=at, _att=attempts[(gid, rn)],
                    round_time_ms=None, kill_type=KILL_TYPES[t],
                    killer_id=actor.get("id"), killer_team_id=ast.get("teamId"), killer_side=ast.get("side"),
                    killer_agent=agent_of.get((gid, actor.get("id"))),
                    victim_id=target.get("id"), victim_team_id=tst.get("teamId"), victim_side=tst.get("side"),
                    victim_agent=agent_of.get((gid, target.get("id"))),
                    weapon=weapon, weapon_source=weapon_source,
                    first_kill=int(bool(g(ad, "round", "firstKill") or g(ad, "game", "firstKill"))),
                    assister_ids=",".join(assisters) or None,
                    killer_x=g(ast, "game", "position", "x"), killer_y=g(ast, "game", "position", "y"),
                    victim_x=g(tst, "game", "position", "x"), victim_y=g(tst, "game", "position", "y"),
                ))
                continue

    # ---- final states -> matches / games / rounds / rosters ---------------- #
    kept_games: set[str] = set()
    games, rounds, roster = [], [], []
    for sess, st in sorted(last_full.items()):
        for game in st.get("games") or ():
            gid = game["id"]
            if gid in rolled_back:
                continue
            kept_games.add(gid)
            teams = game.get("teams") or []
            t1 = teams[0] if teams else {}
            t2 = teams[1] if len(teams) > 1 else {}
            winner = next((tm["id"] for tm in teams if tm.get("won")), None)
            segs = [s for s in game.get("segments") or () if s.get("type") == "round"]
            games.append(dict(
                game_id=gid, series_id=sid, session=sess, session_valid=int(bool(st.get("valid"))),
                game_number=game.get("sequenceNumber"), map_slug=g(game, "map", "name"),
                started_at=game.get("startedAt"), finished=int(bool(game.get("finished"))),
                rounds_played=sum(1 for s in segs if s.get("finished")),
                team1_id=t1.get("id"), team1_name=t1.get("name"), team1_final_side=t1.get("side"), team1_score=t1.get("score"),
                team2_id=t2.get("id"), team2_name=t2.get("name"), team2_final_side=t2.get("side"), team2_score=t2.get("score"),
                winner_team_id=winner, merged_from_round=None,
            ))
            for tm in teams:
                for p in tm.get("players") or ():
                    roster.append(dict(
                        game_id=gid, player_id=p["id"], series_id=sid, team_id=tm["id"], player_name=p.get("name"),
                        agent=g(p, "character", "id"), kills=p.get("kills"), deaths=p.get("deaths"),
                        assists=p.get("killAssistsGiven"),
                    ))
            for s in segs:
                n = s.get("sequenceNumber") or round_from_id(s.get("id"))
                by_side = {tm.get("side"): tm for tm in s.get("teams") or ()}
                win = next((tm for tm in s.get("teams") or () if tm.get("won")), None)
                ec = econ.get((gid, n), {})
                rounds.append(dict(
                    game_id=gid, round_number=n, series_id=sid,
                    started_at=s.get("startedAt") or round_started.get((gid, n)),
                    buy_end_at=buy_end.get((gid, n)), duration_ms=duration_ms(s.get("duration")),
                    attacker_team_id=g(by_side, "attacker", "id"), defender_team_id=g(by_side, "defender", "id"),
                    winner_team_id=win.get("id") if win else None, winner_side=win.get("side") if win else None,
                    win_type=win.get("winType") if win else None, finished=int(bool(s.get("finished"))), map_uncertain=0,
                    attacker_loadout=ec.get("attacker", (None, None))[0], defender_loadout=ec.get("defender", (None, None))[0],
                    attacker_spent=ec.get("attacker", (None, None))[1], defender_spent=ec.get("defender", (None, None))[1],
                ))

    # Series-level summary from the last session that actually has games.
    sessions_with_games = [st for _, st in sorted(last_full.items()) if st.get("games")]
    st = sessions_with_games[-1] if sessions_with_games else (last_full[max(last_full)] if last_full else {})
    steams = st.get("teams") or []
    s1 = steams[0] if steams else {}
    s2 = steams[1] if len(steams) > 1 else {}
    fmt = st.get("format")
    if isinstance(fmt, dict):
        fmt = fmt.get("nameShortened") or fmt.get("name") or fmt.get("id")
    first_state = last_full.get(min(last_full)) if last_full else {}
    match = dict(
        series_id=sid, event=row.get("event"), stage=row.get("stage"), scheduled_start=row.get("scheduled_start"),
        started_at=first_state.get("startedAt"), format=fmt,
        team1_id=s1.get("id"), team1_name=s1.get("name"), team1_score=s1.get("score"),
        team2_id=s2.get("id"), team2_name=s2.get("name"), team2_score=s2.get("score"),
        winner_team_id=next((tm["id"] for tm in steams if tm.get("won")), None),
        valid=int(bool((last_full[max(last_full)] if last_full else {}).get("valid"))),
        sessions=session, has_supplement=int(bool(supplement)),
    )

    # ---- drop rolled-back / unplayed games, then project positions ------- #
    dropped = Counter()

    def keep(rows, name):
        # Drop rolled-back games and superseded attempts of replayed rounds.
        out = [r for r in rows
               if r["game_id"] in kept_games and r.pop("_att") == attempts[(r["game_id"], r["round_number"])]]
        dropped[name] = len(rows) - len(out)
        return out

    casts, kills, purchases = keep(casts, "casts"), keep(kills, "kills"), keep(purchases, "purchases")
    player_rounds_rows = [r for r in player_rounds.values()
                          if r["game_id"] in kept_games and r["round_number"] is not None]
    for r in (*casts, *kills):
        r["round_time_ms"] = ms_between(buy_end.get((r["game_id"], r["round_number"])), r["occurred_at"])
    # GRID sometimes merges the next map into the previous game (README: "maps that GRID
    # merged into the previous game"). The supplement only covers the real map, so a tail of
    # rounds with casts but no supplement match marks where the merged map begins. Those
    # rounds keep raw x/y but get no map, minimap position or callout.
    merged_from: dict[str, int] = {}
    if supplement:
        per_round: dict[str, dict[int, list[int]]] = {}
        for c in casts:
            if c["round_number"] is not None:
                cnt = per_round.setdefault(c["game_id"], {}).setdefault(c["round_number"], [0, 0])
                cnt[0] += 1
                cnt[1] += c["supplement_quality"] is not None
        for gid, by_round in per_round.items():
            start = None
            for n in sorted(by_round, reverse=True):
                total, with_sup = by_round[n]
                if with_sup:
                    break
                if total >= 5:
                    start = n
            covered_before = start is not None and any(v[1] for r, v in by_round.items() if r < start)
            if covered_before and sum(1 for r in by_round if r >= start) >= 3:
                merged_from[gid] = start
    for gm in games:
        gm["merged_from_round"] = merged_from.get(gm["game_id"])
    for rd in rounds:
        rd["map_uncertain"] = int(rd["game_id"] in merged_from and rd["round_number"] >= merged_from[rd["game_id"]])

    def map_for(row):
        start = merged_from.get(row["game_id"])
        if start is not None and row["round_number"] is not None and row["round_number"] >= start:
            return None
        return game_map.get(row["game_id"])

    for c in casts:
        c["map_slug"] = map_for(c)
        u, v, co, cs, d = A.project(c["map_slug"], c["x"], c["y"])
        c.update(minimap_u=u, minimap_v=v, callout=co, callout_super=cs, callout_dist=d)
    for k in kills:
        k["map_slug"] = m = map_for(k)
        for who in ("killer", "victim"):
            u, v, co, cs, _ = A.project(m, k[f"{who}_x"], k[f"{who}_y"])
            k.update({f"{who}_u": u, f"{who}_v": v, f"{who}_callout": co, f"{who}_callout_super": cs})

    return dict(
        series_id=sid, match=match, games=games, game_players=roster, rounds=rounds,
        utility_casts=casts, purchases=purchases, kills=kills, player_rounds=player_rounds_rows,
        stats=dict(
            seconds=time.perf_counter() - t0, sessions=session, games=len(games), rounds=len(rounds),
            casts=len(casts), casts_with_supplement=sum(c["supplement_quality"] is not None for c in casts),
            supplement_records=len(supplement), kills=len(kills), purchases=len(purchases),
            rolled_back=len(rolled_back), dropped=dict(dropped), econ_skipped_team_rounds=econ_skipped,
            replayed_rounds=sum(1 for (gid, _), v in attempts.items() if v > 1 and gid in kept_games),
            unmapped_casts=sum(c["minimap_u"] is None for c in casts),
            merged_games=len(merged_from),
            unknown_items=sorted({p["item_id"] for p in purchases if p["item_kind"] == "unknown"}),
        ),
    )


def safe_parse(row: dict) -> dict:
    try:
        return parse_series(row)
    except Exception:
        return {"series_id": row["series_id"], "error": traceback.format_exc()}


# --------------------------------------------------------------------------- #
# Writing (main process)
# --------------------------------------------------------------------------- #

def insert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    conn.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [tuple(r[c] for c in cols) for r in rows],
    )


def write_result(conn: sqlite3.Connection, res: dict) -> None:
    sid = res["series_id"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        for table in GRID_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE series_id = ?", (sid,))
        if "error" in res:
            conn.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?)", (sid, "error", now, None, res["error"]))
            return
        insert(conn, "matches", [res["match"]])
        for table in GRID_TABLES[1:]:
            insert(conn, table, res[table])
        conn.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?)",
                     (sid, "ok", now, res["stats"]["seconds"], repr(res["stats"])))


def backfill_slots(conn: sqlite3.Connection) -> int:
    """Casts without a supplement or name match get the slot most often seen for that agent+ability."""
    before = conn.total_changes
    with conn:
        conn.execute("""
            WITH best AS (
                SELECT agent, ability_id, api_slot,
                       ROW_NUMBER() OVER (PARTITION BY agent, ability_id ORDER BY COUNT(*) DESC) AS rk
                FROM utility_casts WHERE api_slot IS NOT NULL GROUP BY agent, ability_id, api_slot
            )
            UPDATE utility_casts SET api_slot = (
                SELECT b.api_slot FROM best b
                WHERE b.agent = utility_casts.agent AND b.ability_id = utility_casts.ability_id AND b.rk = 1)
            WHERE api_slot IS NULL""")
    return conn.total_changes - before


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="only ingest the first N pending series")
    ap.add_argument("--series", nargs="+", help="specific series IDs")
    ap.add_argument("--force", action="store_true", help="re-ingest series already marked ok")
    ap.add_argument("--reset", action="store_true", help="drop and recreate the GRID tables (asset tables are untouched)")
    ap.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 4))
    args = ap.parse_args()

    if not (DATA_DIR / "val" / "series.csv").exists():
        sys.exit(f"GRID data not found at {DATA_DIR} (set STL_DATA_DIR)")
    Assets(DB_PATH)  # fail fast if seed_assets.py hasn't run

    with open(DATA_DIR / "val" / "series.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if args.reset:
        conn.executescript("DROP VIEW IF EXISTS utility_casts_v;"
                           + "".join(f"DROP TABLE IF EXISTS {t};" for t in (*GRID_TABLES, "ingest_log")))
    conn.executescript(SCHEMA)
    with closing(sqlite3.connect(":memory:")) as ref:
        ref.executescript(SCHEMA.split("-- Casts with the ability")[0])
        for table in (*GRID_TABLES, "ingest_log"):
            want = [r[1] for r in ref.execute(f"PRAGMA table_info({table})")]
            have = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if want != have:
                sys.exit(f"Table '{table}' has an older schema; re-run with --reset to rebuild the GRID tables.")

    if args.series:
        wanted = set(args.series)
        rows = [r for r in rows if r["series_id"] in wanted]
    if not args.force:
        done = {r[0] for r in conn.execute("SELECT series_id FROM ingest_log WHERE status = 'ok'")}
        rows = [r for r in rows if r["series_id"] not in done]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("Nothing to ingest.")
        return

    print(f"Ingesting {len(rows)} series from {DATA_DIR} with {args.workers} workers -> {DB_PATH}")
    t0 = time.perf_counter()
    totals, errors = Counter(), []
    unknown_items, unmapped = set(), 0
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker, initargs=(str(DB_PATH),)) as pool:
        futures = [pool.submit(safe_parse, r) for r in rows]
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            write_result(conn, res)
            if "error" in res:
                errors.append(res["series_id"])
                print(f"[{i}/{len(rows)}] {res['series_id']} ERROR\n{res['error']}", file=sys.stderr)
                continue
            s = res["stats"]
            for k in ("games", "rounds", "casts", "casts_with_supplement", "kills", "purchases", "rolled_back", "replayed_rounds", "econ_skipped_team_rounds", "merged_games"):
                totals[k] += s[k]
            unknown_items.update(s["unknown_items"])
            unmapped += s["unmapped_casts"]
            if args.limit or len(rows) <= 20 or i % 50 == 0 or i == len(rows):
                print(f"[{i}/{len(rows)}] {res['series_id']}: {s['games']} games, {s['rounds']} rounds, "
                      f"{s['casts']} casts ({s['casts_with_supplement']} w/ supplement), {s['kills']} kills, "
                      f"{s['purchases']} purchases, {s['seconds']:.1f}s"
                      + (f", rolled back {s['rolled_back']} game(s)" if s["rolled_back"] else "")
                      + (f", {s['replayed_rounds']} replayed round(s)" if s["replayed_rounds"] else "")
                      + (f", dropped {s['dropped']}" if any(s["dropped"].values()) else "")
                      + (f", skipped buys for {s['econ_skipped_team_rounds']} inconsistent team-round(s)" if s["econ_skipped_team_rounds"] else ""))

    filled = backfill_slots(conn)
    import db_setup  # query indexes + team_rounds (buy tiers) for the API

    db_setup.ensure(conn, force=True)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    print(f"\nDone in {time.perf_counter() - t0:.0f}s: " + ", ".join(f"{v} {k}" for k, v in totals.items()))
    print(f"Backfilled api_slot on {filled} casts; {unmapped} casts without a minimap position.")
    if unknown_items:
        print(f"Items with no asset match (stored with NULL cost): {sorted(unknown_items)}")
    if errors:
        print(f"{len(errors)} series failed (see ingest_log): {errors}")
        sys.exit(1)


if __name__ == "__main__":
    main()

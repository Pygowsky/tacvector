"""Seed static VALORANT assets from https://valorant-api.com into SQLite.

Stores, for every competitive map: minimap image URL, the world->minimap
coordinate multipliers, and every callout with its world (x, y, z) position
(plus the pre-computed normalised minimap position). Also stores agents with
their ability icons and weapons with their credit costs.

Usage:
    python seed_assets.py            # fetch + (re)build backend/tacvector.db
    python seed_assets.py --serve    # seed, then serve the read API on :8000
    uvicorn seed_assets:app --reload # serve an already-seeded database

The `slug` columns are lowercase display names, which is how the GRID feeds
and meta/games.csv name maps (e.g. "haven", "lotus"), so they join directly.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

API_BASE = "https://valorant-api.com/v1"
DB_PATH = Path(os.environ.get("TACVECTOR_DB", Path(__file__).resolve().parent / "tacvector.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS callouts;
DROP TABLE IF EXISTS maps;
DROP TABLE IF EXISTS agent_abilities;
DROP TABLE IF EXISTS agents;
DROP TABLE IF EXISTS weapons;

CREATE TABLE maps (
    uuid            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,  -- matches GRID map names
    map_url         TEXT,                  -- internal path, e.g. /Game/Maps/Ascent/Ascent
    minimap_url     TEXT NOT NULL,         -- valorant-api "displayIcon"
    splash_url      TEXT,
    x_multiplier    REAL NOT NULL,
    y_multiplier    REAL NOT NULL,
    x_scalar_to_add REAL NOT NULL,
    y_scalar_to_add REAL NOT NULL
);

CREATE TABLE callouts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    map_uuid          TEXT NOT NULL REFERENCES maps(uuid) ON DELETE CASCADE,
    region_name       TEXT NOT NULL,  -- e.g. "Tree"
    super_region_name TEXT,           -- e.g. "A", "Mid", "Attacker Side"
    x                 REAL NOT NULL,  -- world units (cm), same space as GRID positions
    y                 REAL NOT NULL,
    z                 REAL,
    minimap_u         REAL NOT NULL,  -- 0..1 across the minimap image
    minimap_v         REAL NOT NULL   -- 0..1 down the minimap image
);
CREATE INDEX idx_callouts_map ON callouts(map_uuid);

CREATE TABLE agents (
    uuid         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    slug         TEXT NOT NULL UNIQUE,
    role         TEXT,
    display_icon TEXT,
    minimap_icon TEXT,
    killfeed_icon TEXT
);

CREATE TABLE agent_abilities (
    agent_uuid   TEXT NOT NULL REFERENCES agents(uuid) ON DELETE CASCADE,
    slot         TEXT NOT NULL,  -- Ability1, Ability2, Grenade, Ultimate, Passive
    name         TEXT NOT NULL,
    description  TEXT,
    display_icon TEXT,
    PRIMARY KEY (agent_uuid, slot)
);

CREATE TABLE weapons (
    uuid          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    category      TEXT,     -- Rifle, Sidearm, SMG, ...
    shop_category TEXT,     -- e.g. "Heavy Weapons"
    cost          INTEGER,  -- credits; NULL for melee
    display_icon  TEXT,
    kill_icon     TEXT
);
"""


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #

def fetch(client: httpx.Client, path: str, **params) -> list[dict]:
    resp = client.get(f"{API_BASE}{path}", params=params or None)
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") != 200:
        raise RuntimeError(f"{path}: unexpected status {body.get('status')}")
    return body["data"]


def world_to_minimap(m: dict, x: float, y: float) -> tuple[float, float]:
    """valorant-api convention: axes are swapped when projecting to the minimap."""
    u = y * m["xMultiplier"] + m["xScalarToAdd"]
    v = x * m["yMultiplier"] + m["yScalarToAdd"]
    return u, v


def is_competitive(m: dict) -> bool:
    # Standard/competitive maps have a site description ("A/B Sites"), callouts
    # and a real coordinate transform. Range, TDM, Skirmish etc. don't.
    return bool(m.get("tacticalDescription") and m.get("callouts") and m.get("xMultiplier") and m.get("displayIcon"))


def short_category(raw: str | None) -> str | None:
    return raw.split("::")[-1] if raw else None


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

def seed(db_path: Path = DB_PATH) -> dict[str, int]:
    with httpx.Client(timeout=30, headers={"User-Agent": "tacvector-seed/1.0"}) as client:
        maps = fetch(client, "/maps")
        agents = fetch(client, "/agents", isPlayableCharacter="true")
        weapons = fetch(client, "/weapons")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executescript(SCHEMA)

        n_maps = n_callouts = 0
        for m in filter(is_competitive, maps):
            conn.execute(
                "INSERT INTO maps VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    m["uuid"], m["displayName"], m["displayName"].lower(), m.get("mapUrl"),
                    m["displayIcon"], m.get("splash"),
                    m["xMultiplier"], m["yMultiplier"], m["xScalarToAdd"], m["yScalarToAdd"],
                ),
            )
            n_maps += 1
            for c in m["callouts"]:
                loc = c["location"]
                u, v = world_to_minimap(m, loc["x"], loc["y"])
                conn.execute(
                    "INSERT INTO callouts (map_uuid, region_name, super_region_name, x, y, z, minimap_u, minimap_v)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (m["uuid"], c["regionName"], c.get("superRegionName"), loc["x"], loc["y"], loc.get("z"), u, v),
                )
                n_callouts += 1

        n_abilities = 0
        for a in agents:
            conn.execute(
                "INSERT INTO agents VALUES (?,?,?,?,?,?,?)",
                (
                    a["uuid"], a["displayName"], a["displayName"].lower(),
                    (a.get("role") or {}).get("displayName"),
                    a.get("displayIcon"), a.get("minimapPortrait"), a.get("killfeedPortrait"),
                ),
            )
            for ab in a.get("abilities") or []:
                conn.execute(
                    "INSERT OR REPLACE INTO agent_abilities VALUES (?,?,?,?,?)",
                    (a["uuid"], ab["slot"], ab["displayName"], ab.get("description"), ab.get("displayIcon")),
                )
                n_abilities += 1

        for w in weapons:
            shop = w.get("shopData") or {}
            conn.execute(
                "INSERT INTO weapons VALUES (?,?,?,?,?,?,?,?)",
                (
                    w["uuid"], w["displayName"], w["displayName"].lower(), short_category(w.get("category")),
                    shop.get("categoryText"), shop.get("cost"), w.get("displayIcon"), w.get("killStreamIcon"),
                ),
            )

    return {
        "maps": n_maps,
        "callouts": n_callouts,
        "agents": len(agents),
        "abilities": n_abilities,
        "weapons": len(weapons),
    }


# --------------------------------------------------------------------------- #
# Read API
# --------------------------------------------------------------------------- #

app = FastAPI(title="TacVector assets")


def query(sql: str, params: tuple = ()) -> list[dict]:
    if not DB_PATH.exists():
        raise HTTPException(503, "Database not seeded; run `python seed_assets.py` first.")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params)]


@app.get("/maps")
def list_maps() -> list[dict]:
    return query("SELECT * FROM maps ORDER BY name")


@app.get("/maps/{slug}")
def get_map(slug: str) -> dict:
    rows = query("SELECT * FROM maps WHERE slug = ?", (slug.lower(),))
    if not rows:
        raise HTTPException(404, f"Unknown map '{slug}'")
    m = rows[0]
    m["callouts"] = query(
        "SELECT region_name, super_region_name, x, y, z, minimap_u, minimap_v"
        " FROM callouts WHERE map_uuid = ? ORDER BY super_region_name, region_name",
        (m["uuid"],),
    )
    return m


@app.get("/agents")
def list_agents() -> list[dict]:
    agents = query("SELECT * FROM agents ORDER BY name")
    abilities = query("SELECT * FROM agent_abilities ORDER BY slot")
    by_agent: dict[str, list[dict]] = {}
    for ab in abilities:
        by_agent.setdefault(ab.pop("agent_uuid"), []).append(ab)
    for a in agents:
        a["abilities"] = by_agent.get(a["uuid"], [])
    return agents


@app.get("/weapons")
def list_weapons() -> list[dict]:
    return query("SELECT * FROM weapons ORDER BY cost IS NULL, cost, name")


# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", action="store_true", help="serve the read API after seeding")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    counts = seed()
    print(f"Seeded {DB_PATH}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))

    if args.serve:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

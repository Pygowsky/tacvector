"""TacVector scouting API.

    uvicorn main:app --reload            (from backend/)
    DEMO_PASSWORD=secret uvicorn main:app

Endpoints (all under /api, password-protected when DEMO_PASSWORD is set):
    GET /api/filters    teams (Cloud9 self-scout presets), maps + callouts, players, agents,
                        abilities by category, high-impact weapons, buy tiers
    GET /api/utility    utility cast map, callout density, round phases, combos, insights, anti-strats
    GET /api/operator   Operator/Odin buy + possession rates, holders, setups, angles, opening duels
    GET /api/health     unauthenticated liveness check

Every query excludes corrupted data: games.session_valid = 1, rounds.map_uncertain = 0
(and rounds.finished = 1). Sample sizes are always reported as n/N team-rounds, where a
team-round is one team's half of one round.

Auth: if DEMO_PASSWORD is set, send either `X-Demo-Password: <password>` or HTTP Basic
credentials with that password (any username, unless DEMO_USER is also set). If it is
unset, access is open (local development). 401s carry `WWW-Authenticate: Basic` only for
plain browser navigation; requests sending X-Demo-Password or `Accept: application/json`
(the React app) get a bare 401 so the app's LoginModal handles it, not a native popup.

Environment:
    DB_PATH         SQLite database (default backend/tacvector.db)
    DEMO_PASSWORD   enable password protection; DEMO_USER optionally pins the Basic username
    CORS_ORIGINS    comma-separated allowed origins (default *)
    FRONTEND_DIST   built React app (default frontend/dist). When it exists, it is served at /
                    and every non-/api path falls back to its index.html (client-side routing).
"""

from __future__ import annotations

import base64
import math
import os
import secrets
import sqlite3
import statistics
from collections import Counter, defaultdict
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import db_setup
from ability_categories import ABILITY_CATEGORY, CATEGORY_ORDER, NAME_OVERRIDES, OTHER, category_of

DB_PATH = db_setup.DB_PATH  # DB_PATH env var, default backend/tacvector.db
FRONTEND_DIST = Path(
    os.environ.get("FRONTEND_DIST") or Path(__file__).resolve().parent.parent / "frontend" / "dist"
).resolve()

BUY_TIERS = {
    "Pistol": "Round 1 or 13 (first round of each half)",
    "Eco": f"Team loadout under {db_setup.ECO_MAX:,} credits",
    "Force": f"Team loadout {db_setup.ECO_MAX:,}-{db_setup.FULL_BUY_MIN - 1:,} after a lost round",
    "Bonus": f"Won the previous round and playing on saved gear (loadout under {db_setup.FULL_BUY_MIN:,})",
    "Full Buy": f"Team loadout of {db_setup.FULL_BUY_MIN:,}+ credits",
}
PHASES = ["buy_phase", "0-20s", "20-40s", "40s+"]
ROUND_PHASES = PHASES[1:]
HIGH_IMPACT_WEAPONS = ("operator", "odin")
SIDE_WORD = {"attacker": "attack", "defender": "defense"}
MAX_SCOPE_CASTS = 400_000   # beyond this, ask the client to narrow the filters
COMBO_GAP_MS = 3_000        # casts this close together form one coordinated burst


def phase_of(ms: int | None) -> str | None:
    if ms is None:
        return None
    if ms < 0:
        return "buy_phase"
    if ms < 20_000:
        return "0-20s"
    if ms < 40_000:
        return "20-40s"
    return "40s+"


def frac(n: int, d: int) -> dict:
    return {"n": n, "of": d, "fraction": f"{n}/{d}", "pct": round(100 * n / d, 1) if d else None}


def pct_text(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%, {n}/{d}" if d else f"{n}/0"


def pct_of(n: int, d: int, what: str) -> str:
    """'84% of defense rounds (242/289)'"""
    return f"{round(100 * n / d) if d else 0}% of {what} ({n}/{d})"


def possessive(name: str) -> str:
    return name + ("'" if name.endswith("s") else "'s")


def timing_phrase(entry: dict) -> str | None:
    """How a callout/category hotspot is timed, honest about buy-phase setups."""
    if (entry.get("buy_phase_share") or 0) >= 0.5:
        return "mostly set up during the buy phase"
    t = entry.get("median_time_s")
    if t is None:
        return None
    return "right as barriers drop" if t < 2 else f"usually ~{t:.0f}s in"


def confidence(n: int, d: int) -> str:
    share = n / d if d else 0
    if d >= 20 and share >= 0.5:
        return "high"
    if d >= 10 and share >= 0.3:
        return "medium"
    return "low"


def median_s(values: list[int]) -> float | None:
    vals = [v for v in values if v is not None and v >= 0]
    return round(statistics.median(vals) / 1000, 1) if vals else None


# --------------------------------------------------------------------------- #
# Static lookups, loaded once at startup (the database is read-only at runtime)
# --------------------------------------------------------------------------- #

class Catalog:
    def __init__(self, conn: sqlite3.Connection):
        conn.row_factory = sqlite3.Row
        q = lambda sql, *a: [dict(r) for r in conn.execute(sql, a)]

        self.maps: dict[str, dict] = {}
        for m in q("SELECT * FROM maps ORDER BY name"):
            m["callouts"] = []
            self.maps[m["slug"]] = m
        for c in q("SELECT m.slug, c.region_name, c.super_region_name, c.x, c.y FROM callouts c JOIN maps m ON m.uuid = c.map_uuid"):
            m = self.maps[c["slug"]]
            u, v = self.to_uv(c["slug"], c["x"], c["y"])
            m["callouts"].append(dict(label=callout_label(c["super_region_name"], c["region_name"]),
                                      callout=c["region_name"], callout_super=c["super_region_name"],
                                      u=round(u, 4), v=round(v, 4)))
        self.callout_uv = {(slug, c["callout_super"], c["callout"]): (c["u"], c["v"])
                           for slug, m in self.maps.items() for c in m["callouts"]}

        valid_games = "FROM games WHERE session_valid = 1"
        map_games = Counter({r["map_slug"]: r["n"] for r in q(f"SELECT map_slug, COUNT(*) n {valid_games} GROUP BY 1")})
        for slug, m in self.maps.items():
            m["games"] = map_games.get(slug, 0)

        self.teams: dict[str, dict] = {}
        for r in q(f"""SELECT team_id, name, COUNT(*) games, MAX(started_at) last_played FROM (
                         SELECT team1_id team_id, team1_name name, started_at {valid_games}
                         UNION ALL SELECT team2_id, team2_name, started_at {valid_games})
                       GROUP BY team_id ORDER BY games DESC"""):
            latest = conn.execute(f"""SELECT name FROM (SELECT team1_id id, team1_name name, started_at {valid_games}
                                       UNION ALL SELECT team2_id, team2_name, started_at {valid_games})
                                       WHERE id = ? ORDER BY started_at DESC LIMIT 1""", (r["team_id"],)).fetchone()[0]
            self.teams[r["team_id"]] = dict(team_id=r["team_id"], name=latest, games=r["games"],
                                            last_played=r["last_played"], is_cloud9="cloud9" in latest.lower().replace(" ", ""))

        self.players: dict[str, dict] = {}
        for r in q("""SELECT gp.player_id, gp.player_name, gp.team_id, gp.agent, g.started_at
                      FROM game_players gp JOIN games g ON g.game_id = gp.game_id AND g.session_valid = 1
                      ORDER BY g.started_at"""):
            p = self.players.setdefault(r["player_id"], dict(player_id=r["player_id"], games=0, agents=Counter()))
            p.update(name=r["player_name"], team_id=r["team_id"], last_played=r["started_at"])
            p["games"] += 1
            if r["agent"]:
                p["agents"][r["agent"]] += 1
        for p in self.players.values():
            p["team_name"] = self.teams.get(p["team_id"], {}).get("name")
            p["top_agents"] = [a for a, _ in p.pop("agents").most_common(3)]

        self.agents = {a["slug"]: a for a in q("SELECT slug, name, role, display_icon, killfeed_icon FROM agents ORDER BY name")}
        self.abilities: dict[str, dict] = {}
        for r in q("""SELECT agent, ability_id, MAX(ability_name) name, MAX(ability_icon) icon, MAX(api_slot) slot,
                             COUNT(*) casts FROM utility_casts_v GROUP BY agent, ability_id"""):
            if r["ability_id"] in self.abilities and self.abilities[r["ability_id"]]["casts"] > r["casts"]:
                continue
            self.abilities[r["ability_id"]] = dict(
                ability_id=r["ability_id"], name=NAME_OVERRIDES.get(r["ability_id"], r["name"] or r["ability_id"]),
                agent=r["agent"], agent_name=self.agents.get(r["agent"], {}).get("name", r["agent"]),
                slot=r["slot"], icon=r["icon"], casts=r["casts"], category=category_of(r["ability_id"]))
        self.weapons = {w["slug"]: w for w in q("SELECT slug, name, category, cost, display_icon, kill_icon FROM weapons")}

    def to_uv(self, slug: str, x: float, y: float) -> tuple[float, float]:
        m = self.maps[slug]
        return y * m["x_multiplier"] + m["x_scalar_to_add"], x * m["y_multiplier"] + m["y_scalar_to_add"]

    def aim_uv(self, slug: str, vx: float | None, vy: float | None) -> list[float] | None:
        """World aim vector -> unit direction on the minimap (same axis swap as positions)."""
        if vx is None or vy is None or slug not in self.maps:
            return None
        m = self.maps[slug]
        du, dv = vy * m["x_multiplier"], vx * m["y_multiplier"]
        norm = math.hypot(du, dv)
        return [round(du / norm, 3), round(dv / norm, 3)] if norm else None

    def ability_label(self, ability_id: str) -> str:
        a = self.abilities.get(ability_id)
        return f"{a['name']} ({a['agent_name']})" if a else ability_id


def callout_label(super_region: str | None, region: str | None) -> str | None:
    if not region:
        return None
    if super_region and not region.lower().startswith(super_region.lower()):
        return f"{super_region} {region}"
    return region


CATALOG: Catalog | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global CATALOG
    if not DB_PATH.exists():
        raise RuntimeError(f"{DB_PATH} not found: run seed_assets.py and ingest_grid.py first")
    with closing(sqlite3.connect(DB_PATH)) as conn:
        try:
            db_setup.ensure(conn)  # indexes + team_rounds; no-op when current
        except sqlite3.OperationalError as exc:
            # A read-only database (e.g. a :ro Docker volume) is fine if it was prepared already.
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'team_rounds'").fetchone():
                raise RuntimeError(f"{DB_PATH} has no team_rounds table and can't be updated ({exc}); "
                                   "run db_setup.py on a writable copy first") from exc
        CATALOG = Catalog(conn)
    yield


# --------------------------------------------------------------------------- #
# App, CORS, auth
# --------------------------------------------------------------------------- #

app = FastAPI(title="TacVector API", version="1.0.0", lifespan=lifespan)

_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_origins != ["*"],  # browsers reject credentials with a wildcard origin
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def require_demo_auth(request: Request) -> None:
    password = os.environ.get("DEMO_PASSWORD")
    if not password:
        return  # open access when no password is configured (local dev)
    expected = password.encode()
    header = request.headers.get("x-demo-password")
    if header is not None and secrets.compare_digest(header.encode(), expected):
        return
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            user = pw = ""
        demo_user = os.environ.get("DEMO_USER")
        user_ok = demo_user is None or secrets.compare_digest(user.encode(), demo_user.encode())
        if secrets.compare_digest(pw.encode(), expected) and user_ok:
            return
    # Only challenge plain browser navigation: a `WWW-Authenticate: Basic` header makes browsers
    # show their native credentials dialog, which would cover the React LoginModal.
    wants_json = "application/json" in request.headers.get("accept", "").lower()
    headers = None if header is not None or wants_json else {"WWW-Authenticate": 'Basic realm="TacVector demo"'}
    raise HTTPException(401, "Demo password required", headers=headers)


def get_db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


api = APIRouter(prefix="/api", dependencies=[Depends(require_demo_auth)])


@app.get("/api/health", include_in_schema=False)
def health() -> dict:
    return {"ok": CATALOG is not None, "auth_required": bool(os.environ.get("DEMO_PASSWORD"))}


# --------------------------------------------------------------------------- #
# Filter parsing
# --------------------------------------------------------------------------- #

class Scope:
    """Validated filters shared by /api/utility and /api/operator."""

    def __init__(self, team, map_, side, player, agent, buy_tier):
        C = CATALOG
        self.team = self._resolve(team, C.teams, "team", "name")
        self.player = self._resolve(player, C.players, "player", "name")
        self.map = self._choice(map_, C.maps, "map")
        self.agent = self._choice(agent, C.agents, "agent")
        self.side = self._choice(side, {"attacker": 1, "defender": 1, "attack": "attacker", "defense": "defender"}, "side")
        self.buy_tier = None
        if buy_tier:
            norm = {t.lower().replace(" ", "").replace("_", ""): t for t in BUY_TIERS}
            self.buy_tier = norm.get(buy_tier.lower().replace(" ", "").replace("_", "").replace("-", ""))
            if not self.buy_tier:
                raise HTTPException(422, f"Unknown buy_tier '{buy_tier}'. Use one of {list(BUY_TIERS)}")

    @staticmethod
    def _resolve(value, table, kind, name_key):
        if not value:
            return None
        if value in table:
            return value
        matches = [k for k, v in table.items() if (v.get(name_key) or "").lower() == value.lower()]
        if not matches:
            raise HTTPException(404, f"Unknown {kind} '{value}' (use an id from /api/filters)")
        return max(matches, key=lambda k: table[k].get("last_played") or "")

    @staticmethod
    def _choice(value, table, kind):
        if not value:
            return None
        key = value.lower()
        if key not in table:
            raise HTTPException(422, f"Unknown {kind} '{value}'. Options: {sorted(k for k, v in table.items() if v == 1 or isinstance(v, dict))}")
        return table[key] if isinstance(table[key], str) else key

    def round_sql(self) -> tuple[str, list]:
        """Team-rounds in scope, excluding corrupted games/rounds."""
        where, params = [], []
        for col, val in (("tr.team_id", self.team), ("tr.map_slug", self.map), ("tr.side", self.side),
                         ("tr.buy_tier", self.buy_tier)):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        for col, val in (("pr.player_id", self.player), ("pr.agent", self.agent)):
            if val:
                where.append(f"EXISTS (SELECT 1 FROM player_rounds pr WHERE pr.game_id = tr.game_id AND "
                             f"pr.round_number = tr.round_number AND pr.team_id = tr.team_id AND {col} = ?)")
                params.append(val)
        sql = f"""SELECT tr.game_id, tr.round_number, tr.team_id, tr.map_slug, tr.side, tr.buy_tier, tr.won
                  FROM team_rounds tr
                  JOIN games g ON g.game_id = tr.game_id AND g.session_valid = 1
                  JOIN rounds r ON r.game_id = tr.game_id AND r.round_number = tr.round_number
                                AND r.map_uncertain = 0 AND r.finished = 1
                  {'WHERE ' + ' AND '.join(where) if where else ''}"""
        return sql, params

    def subject(self) -> str:
        C = CATALOG
        agent = C.agents[self.agent]["name"] if self.agent else None
        if self.player:
            return C.players[self.player]["name"] + (f" on {agent}" if agent else "")
        if self.team:
            return C.teams[self.team]["name"] + (f"'s {agent}" if agent else "")
        return f"{agent} players" if agent else "Teams"

    def plural(self) -> bool:
        return not (self.player or self.team)

    def verb(self, singular: str, plural: str) -> str:
        return plural if self.plural() else singular

    def rounds_word(self) -> str:
        parts = [self.buy_tier.lower() if self.buy_tier else None, SIDE_WORD.get(self.side), "rounds"]
        return " ".join(p for p in parts if p)

    def describe(self) -> dict:
        C = CATALOG
        return dict(
            team=dict(id=self.team, name=C.teams[self.team]["name"]) if self.team else None,
            player=dict(id=self.player, name=C.players[self.player]["name"]) if self.player else None,
            map=dict(slug=self.map, name=C.maps[self.map]["name"]) if self.map else None,
            agent=self.agent, side=self.side, buy_tier=self.buy_tier,
            excludes="games.session_valid = 0, rounds.map_uncertain = 1, unfinished rounds",
        )


def map_name(slug: str) -> str:
    return CATALOG.maps.get(slug, {}).get("name", slug)


# --------------------------------------------------------------------------- #
# /api/filters
# --------------------------------------------------------------------------- #

@api.get("/filters")
def filters() -> dict:
    C = CATALOG
    teams = sorted(C.teams.values(), key=lambda t: (not t["is_cloud9"], -t["games"]))
    by_cat: dict[str, list] = {c: [] for c in [*CATEGORY_ORDER, OTHER]}
    for a in sorted(C.abilities.values(), key=lambda a: -a["casts"]):
        by_cat[a["category"]].append(a)
    return dict(
        teams=teams,
        self_scout_presets=[dict(label=f"Self-Scout: {t['name']}", team=t["team_id"], params={"team": t["team_id"]})
                            for t in teams if t["is_cloud9"]],
        maps=[dict(slug=m["slug"], name=m["name"], minimap_url=m["minimap_url"], splash_url=m["splash_url"],
                   games=m["games"], callouts=m["callouts"],
                   transform=dict(x_multiplier=m["x_multiplier"], y_multiplier=m["y_multiplier"],
                                  x_scalar_to_add=m["x_scalar_to_add"], y_scalar_to_add=m["y_scalar_to_add"],
                                  formula="u = y * x_multiplier + x_scalar_to_add; v = x * y_multiplier + y_scalar_to_add"))
              for m in C.maps.values() if m["games"]],
        players=sorted(C.players.values(), key=lambda p: (p["team_name"] or "", p["name"] or "")),
        agents=list(C.agents.values()),
        ability_categories=[dict(category=c, priority=i + 1 if c in CATEGORY_ORDER else None, abilities=by_cat[c])
                            for i, c in enumerate([*CATEGORY_ORDER, OTHER])],
        high_impact_weapons=[C.weapons[w] for w in HIGH_IMPACT_WEAPONS if w in C.weapons],
        buy_tiers=[dict(id=k, definition=v) for k, v in BUY_TIERS.items()],
        sides=["attacker", "defender"],
        round_phases=[dict(id="buy_phase", definition="Before barriers drop"),
                      dict(id="0-20s", definition="0-20 seconds after the buy phase"),
                      dict(id="20-40s", definition="20-40 seconds"), dict(id="40s+", definition="40 seconds or later")],
    )


# --------------------------------------------------------------------------- #
# /api/utility
# --------------------------------------------------------------------------- #

@api.get("/utility")
def utility(
    team: str | None = None, map: str | None = None, side: str | None = None, player: str | None = None,
    agent: str | None = None, ability_category: str | None = None, ability: str | None = None,
    buy_tier: str | None = None,
    limit: int = Query(3000, ge=0, le=20000, description="max cast points returned (evenly sampled)"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    C = CATALOG
    s = Scope(team, map, side, player, agent, buy_tier)
    category = None
    if ability_category:
        category = next((c for c in [*CATEGORY_ORDER, OTHER] if c.lower() == ability_category.lower()), None)
        if not category:
            raise HTTPException(422, f"Unknown ability_category. Use one of {[*CATEGORY_ORDER, OTHER]}")
    if ability and ability not in C.abilities:
        raise HTTPException(404, f"Unknown ability '{ability}' (see /api/filters ability_categories)")

    def ability_ok(ab: str) -> bool:
        return (not ability or ab == ability) and (not category or category_of(ab) == category)

    # Denominators: team-rounds in scope, per map.
    round_sql, round_params = s.round_sql()
    rounds_by_map = Counter(m for (m,) in db.execute(f"SELECT map_slug FROM ({round_sql})", round_params))
    total_rounds = sum(rounds_by_map.values())

    where, params = ["c.minimap_u IS NOT NULL"], []
    for col, val in (("c.team_id", s.team), ("c.map_slug", s.map), ("c.side", s.side), ("c.player_id", s.player),
                     ("c.agent", s.agent), ("tr.buy_tier", s.buy_tier)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    from_sql = f"""FROM utility_casts c
        JOIN team_rounds tr ON tr.game_id = c.game_id AND tr.round_number = c.round_number AND tr.team_id = c.team_id
        JOIN games g ON g.game_id = c.game_id AND g.session_valid = 1
        JOIN rounds r ON r.game_id = c.game_id AND r.round_number = c.round_number AND r.map_uncertain = 0 AND r.finished = 1
        WHERE {' AND '.join(where)}"""
    n_scope = db.execute(f"SELECT COUNT(*) {from_sql}", params).fetchone()[0]
    if n_scope > MAX_SCOPE_CASTS:
        raise HTTPException(422, f"Scope too broad ({n_scope:,} casts). Add a team, map, player or agent filter.")

    rows = db.execute(f"""SELECT c.game_id, c.round_number, c.team_id, c.map_slug, c.side, c.player_id, c.agent,
                                 c.ability_id, c.round_time_ms, c.minimap_u, c.minimap_v, c.view_x, c.view_y, c.view_z,
                                 c.callout, c.callout_super, tr.buy_tier
                          {from_sql} ORDER BY c.game_id, c.round_number, c.team_id, c.round_time_ms""", params).fetchall()
    casts = [r for r in rows if ability_ok(r[7])]

    # ---- aggregation --------------------------------------------------- #
    rk = lambda r: (r[0], r[1], r[2])
    co_key = lambda r: (r[3], r[15], r[14])
    callout_rounds, callout_casts = defaultdict(set), Counter()
    cc_rounds, cc_times, cc_abilities = defaultdict(set), defaultdict(list), defaultdict(Counter)
    phase_rounds, phase_casts = defaultdict(set), Counter()
    phase_cat = defaultdict(Counter)
    phase_callout = defaultdict(lambda: defaultdict(set))
    cat_rounds, cat_casts = defaultdict(set), Counter()
    rounds_with_any = set()
    for r in casts:
        key, ck, cat, ph = rk(r), co_key(r), category_of(r[7]), phase_of(r[8])
        rounds_with_any.add(key)
        callout_rounds[ck].add(key)
        callout_casts[ck] += 1
        cc_rounds[(*ck, cat)].add(key)
        cc_times[(*ck, cat)].append(r[8])
        cc_abilities[(*ck, cat)][r[7]] += 1
        cat_rounds[cat].add(key)
        cat_casts[cat] += 1
        if ph:
            phase_rounds[ph].add(key)
            phase_casts[ph] += 1
            phase_cat[ph][cat] += 1
            phase_callout[ph][ck].add(key)

    def callout_entry(ck, rounds_set, extra=None):
        m, sup, co = ck
        u, v = C.callout_uv.get(ck, (None, None))
        d = dict(map=m, callout=co, callout_super=sup, label=callout_label(sup, co), u=u, v=v,
                 rounds=frac(len(rounds_set), rounds_by_map[m]))
        if extra:
            d.update(extra)
        return d

    density = []
    for ck, rset in sorted(callout_rounds.items(), key=lambda kv: -len(kv[1])):
        cats = {cat: len(cc_rounds[(*ck, cat)]) for cat in [*CATEGORY_ORDER, OTHER] if cc_rounds.get((*ck, cat))}
        density.append(callout_entry(ck, rset, dict(casts=callout_casts[ck], rounds_by_category=cats)))

    callout_categories = []
    for (m, sup, co, cat), rset in sorted(cc_rounds.items(), key=lambda kv: -len(kv[1])):
        callout_categories.append(callout_entry((m, sup, co), rset, dict(
            category=cat, casts=len(cc_times[(m, sup, co, cat)]),
            median_time_s=median_s(cc_times[(m, sup, co, cat)]),
            buy_phase_share=round(sum(1 for t in cc_times[(m, sup, co, cat)] if t is not None and t < 0)
                                  / len(cc_times[(m, sup, co, cat)]), 2),
            top_abilities=[dict(ability_id=a, label=C.ability_label(a), casts=n)
                           for a, n in cc_abilities[(m, sup, co, cat)].most_common(3)])))

    phases = []
    for ph in PHASES:
        top = sorted(phase_callout[ph].items(), key=lambda kv: -len(kv[1]))[:5]
        phases.append(dict(
            phase=ph, casts=phase_casts[ph], share_of_casts=frac(phase_casts[ph], len(casts)),
            rounds=frac(len(phase_rounds[ph]), total_rounds),
            by_category={c: phase_cat[ph][c] for c in [*CATEGORY_ORDER, OTHER] if phase_cat[ph][c]},
            top_callouts=[callout_entry(ck, rset) for ck, rset in top]))

    categories = [dict(category=c, casts=cat_casts[c], rounds=frac(len(cat_rounds[c]), total_rounds))
                  for c in [*CATEGORY_ORDER, OTHER] if cat_casts[c]]

    combos = find_combos(rows, rounds_by_map, require_multi_player=not s.player, ability_ok=ability_ok,
                         filtered=bool(ability or category))

    # ---- points (evenly sampled) -------------------------------------- #
    step = max(1, math.ceil(len(casts) / limit)) if limit else None
    points = []
    if step:
        for r in casts[::step]:
            points.append(dict(
                u=round(r[9], 4), v=round(r[10], 4), aim_uv=C.aim_uv(r[3], r[11], r[12]),
                view=[r[11], r[12], r[13]] if r[11] is not None else None,
                ability_id=r[7], category=category_of(r[7]), agent=r[6], player_id=r[5],
                callout=callout_label(r[15], r[14]), time_s=round(r[8] / 1000, 1) if r[8] is not None else None,
                phase=phase_of(r[8]), side=r[4], buy_tier=r[16], map=r[3], game_id=r[0], round_number=r[1]))

    insights = utility_insights(s, callout_categories, phases, combos, casts, total_rounds)
    anti = anti_strats(s, callout_categories, phases, combos, total_rounds)
    return dict(
        scope=dict(**s.describe(), ability_category=category, ability=ability),
        sample=dict(team_rounds=total_rounds, team_rounds_by_map=dict(rounds_by_map),
                    rounds_with_matching_utility=frac(len(rounds_with_any), total_rounds),
                    casts=len(casts), points_returned=len(points), point_sample_step=step),
        categories=categories, callout_density=density, callout_categories=callout_categories[:60],
        round_phases=phases, combos=combos, insights=insights, anti_strats=anti, points=points,
    )


def find_combos(rows, rounds_by_map, require_multi_player: bool, ability_ok, filtered: bool, top: int = 10) -> list[dict]:
    """Recurring utility bursts: >=2 different abilities (from >=2 players) within COMBO_GAP_MS of each other."""
    C = CATALOG
    rounds_for = defaultdict(set)
    regions = defaultdict(Counter)
    starts = defaultdict(list)

    def flush(burst):
        if len(burst) < 2:
            return
        abilities = frozenset((r[6], r[7]) for r in burst)
        if len(abilities) < 2 or (require_multi_player and len({r[5] for r in burst}) < 2):
            return
        if filtered and not any(ability_ok(a) for _, a in abilities):
            return
        key = (burst[0][3], abilities)
        rounds_for[key].add((burst[0][0], burst[0][1], burst[0][2]))
        regions[key].update(r[15] for r in burst if r[15])
        starts[key].append(burst[0][8])

    burst, last_key, last_t = [], None, None
    for r in rows:
        if r[8] is None or r[8] < 0 or category_of(r[7]) == OTHER:
            continue
        key = (r[0], r[1], r[2])
        if key != last_key or r[8] - last_t > COMBO_GAP_MS:
            flush(burst)
            burst = []
        burst.append(r)
        last_key, last_t = key, r[8]
    flush(burst)

    out = []
    for (m, abilities), rset in sorted(rounds_for.items(), key=lambda kv: -len(kv[1])):
        if len(rset) < 3:
            break
        ordered = sorted(abilities, key=lambda a: (CATEGORY_ORDER.index(category_of(a[1])) if category_of(a[1]) in CATEGORY_ORDER else 9, a[1]))
        region = regions[(m, abilities)].most_common(1)
        out.append(dict(
            map=m, region=region[0][0] if region else None,
            abilities=[dict(ability_id=a, agent=ag, label=C.ability_label(a), category=category_of(a)) for ag, a in ordered],
            label=" + ".join(C.ability_label(a) for _, a in ordered),
            rounds=frac(len(rset), rounds_by_map[m]), median_start_s=median_s(starts[(m, abilities)])))
        if len(out) >= top:
            break
    return out


def utility_insights(s: Scope, callout_categories, phases, combos, casts, total_rounds) -> list[str]:
    subject, rw = s.subject(), s.rounds_word()
    out = []
    if total_rounds < 5 or not casts:
        return [f"Not enough data for {subject} with these filters ({total_rounds} {rw})."]
    small = " (small sample)" if total_rounds < 15 else ""
    seen = set()
    for e in callout_categories:  # sorted by rounds desc
        if e["category"] in seen or e["category"] == OTHER or e["rounds"]["n"] < 2:
            continue
        seen.add(e["category"])
        r = e["rounds"]
        when = timing_phrase(e)
        out.append(f"{subject} most often ({pct_text(r['n'], r['of'])} {rw}) {s.verb('deploys', 'deploy')} "
                   f"{e['category']} utility at {e['label']} on {map_name(e['map'])}{', ' + when if when else ''}{small}.")
    in_round = [p for p in phases if p["phase"] in ROUND_PHASES]
    live_casts = sum(p["casts"] for p in in_round)
    if live_casts:
        top = max(in_round, key=lambda p: p["casts"])
        late = in_round[2]["rounds"]["n"]
        out.append(f"{round(100 * top['casts'] / live_casts)}% of {possessive(subject)} in-round utility comes "
                   f"{top['phase']} after barriers drop; utility is still being used 40s+ in {pct_of(late, total_rounds, rw)}.")
    buy = next(p for p in phases if p["phase"] == "buy_phase")
    if buy["casts"] and buy["top_callouts"]:
        t = buy["top_callouts"][0]
        out.append(f"Pre-round setup: {subject} {s.verb('places', 'place')} utility at {t['label']} on "
                   f"{map_name(t['map'])} during the buy phase in {pct_of(t['rounds']['n'], t['rounds']['of'], rw)}.")
    if combos:
        c = combos[0]
        where = f" around {c['region']}" if c["region"] else ""
        start_t = c["median_start_s"]
        when = "right as barriers drop" if start_t is not None and start_t < 2 else f"~{start_t}s in"
        out.append(f"Most recurring combo: {c['label']}{where} on {map_name(c['map'])} in "
                   f"{pct_of(c['rounds']['n'], c['rounds']['of'], rw)}, {when}.")
    return out


COUNTERS = {
    "Placeable": "Expect {abilities} at {where} ({stat}{when}). Clear it with recon or damage utility (or shoot it) before taking that space, and never dry-peek it.",
    "Recon": "{where} gets scanned by {abilities} ({stat}{when}). Hold outside the reveal, swing just after it expires, or shoot the drone/bolt.",
    "Smoke": "{where} is smoked by {abilities} ({stat}{when}). Pre-set off-angles on the smoke exits, spam through on sound, or use it to reposition.",
    "Stall": "{abilities} lands on {where} ({stat}{when}). Don't route through then: delay the hit, take an alternate path, or save a counter-clear.",
    "Flash": "Flashes from {abilities} come at {where} ({stat}{when}). Turn away or hold an off-angle, and answer with a jiggle or counter-flash instead of holding the cross.",
}


def anti_strats(s: Scope, callout_categories, phases, combos, total_rounds) -> list[dict]:
    if total_rounds < 5:
        return []
    out, per_cat = [], Counter()
    for e in callout_categories:
        cat, r = e["category"], e["rounds"]
        if cat == OTHER or per_cat[cat] >= 2 or r["n"] < 3 or (r["pct"] or 0) < 25:
            continue
        per_cat[cat] += 1
        abilities = " / ".join(a["label"] for a in e["top_abilities"][:2])
        phrase = timing_phrase(e)
        when = f", {phrase}" if phrase else ""
        out.append(dict(
            rule=f"{cat.lower()}_hotspot", category=cat, map=e["map"], callout=e["label"],
            recommendation=COUNTERS[cat].format(abilities=abilities, where=f"{e['label']} on {map_name(e['map'])}",
                                                stat=f"{pct_text(r['n'], r['of'])} {s.rounds_word()}", when=when),
            evidence=r, confidence=confidence(r["n"], r["of"])))
    for c in combos[:2]:
        if (c["rounds"]["pct"] or 0) < 15:
            continue
        first, rest = c["abilities"][0]["label"], ", ".join(a["label"] for a in c["abilities"][1:])
        out.append(dict(
            rule="combo_tell", category="Combo", map=c["map"], callout=c["region"],
            recommendation=f"{first} is the tell: {rest} follow within ~3s"
                           f"{' around ' + c['region'] if c['region'] else ''} ({pct_text(c['rounds']['n'], c['rounds']['of'])} rounds). "
                           "Fall back and re-take, or trade the entry instead of holding the first contact.",
            evidence=c["rounds"], confidence=confidence(c["rounds"]["n"], c["rounds"]["of"])))
    live = {p["phase"]: p["casts"] for p in phases if p["phase"] in ROUND_PHASES}
    total = sum(live.values())
    if total >= 20:
        early, late = live["0-20s"] / total, live["40s+"] / total
        if early >= 0.6:
            out.append(dict(rule="front_loaded", category="Timing", recommendation=(
                f"{round(early * 100)}% of their in-round utility is spent in the first 20s. Absorb the opening, then punish the "
                "utility lull with a mid-round push or retake."), evidence=frac(live["0-20s"], total), confidence="medium"))
        elif late >= 0.4:
            out.append(dict(rule="late_utility", category="Timing", recommendation=(
                f"{round(late * 100)}% of their in-round utility comes after 40s: expect late executes or retakes. Don't over-rotate "
                "on early info, and keep utility for the post-plant."), evidence=frac(live["40s+"], total), confidence="medium"))
    return out


# --------------------------------------------------------------------------- #
# /api/operator
# --------------------------------------------------------------------------- #

@api.get("/operator")
def operator(
    team: str | None = None, map: str | None = None, side: str | None = None, player: str | None = None,
    agent: str | None = None, weapon: str = "operator", buy_tier: str | None = None,
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    C = CATALOG
    weapon = weapon.lower()
    if weapon not in HIGH_IMPACT_WEAPONS:
        raise HTTPException(422, f"weapon must be one of {list(HIGH_IMPACT_WEAPONS)}")
    s = Scope(team, map, side, player, agent, buy_tier)
    wname = C.weapons.get(weapon, {}).get("name", weapon.title())
    round_sql, round_params = s.round_sql()

    # Every player-round in scope with "holds the weapon at buy end".
    pr_filter, pr_params = "", []
    if s.player:
        pr_filter += " AND pr.player_id = ?"
        pr_params.append(s.player)
    if s.agent:
        pr_filter += " AND pr.agent = ?"
        pr_params.append(s.agent)
    pr_rows = db.execute(f"""
        WITH scope AS ({round_sql})
        SELECT pr.game_id, pr.round_number, pr.team_id, pr.player_id, pr.agent, sc.side, sc.map_slug, sc.won,
               sc.buy_tier, (',' || COALESCE(pr.weapons, '') || ',') LIKE ? AS holds
        FROM player_rounds pr
        JOIN scope sc ON sc.game_id = pr.game_id AND sc.round_number = pr.round_number AND sc.team_id = pr.team_id
        WHERE 1 = 1 {pr_filter}""", [*round_params, f"%,{weapon},%", *pr_params]).fetchall()
    if len({(r[0], r[1], r[2]) for r in pr_rows}) > 120_000:
        raise HTTPException(422, "Scope too broad; add a team, map, player or agent filter.")

    team_rounds: dict[tuple, dict] = {}
    holders: dict[tuple, set] = defaultdict(set)  # (game, round, team) -> holder ids
    for g, rn, t, p, ag, side_, m, won, tier, holds in pr_rows:
        tr = team_rounds.setdefault((g, rn, t), dict(side=side_, map=m, won=won or 0, tier=tier))
        if holds:
            holders[(g, rn, t)].add(p)

    keys = list(team_rounds)
    N = len(keys)
    # Purchases of the weapon in scope (buyer/recipient).
    buys = db.execute(f"""
        WITH scope AS ({round_sql})
        SELECT p.game_id, p.round_number, p.team_id, p.player_id, p.recipient_id
        FROM purchases p JOIN scope sc ON sc.game_id = p.game_id AND sc.round_number = p.round_number AND sc.team_id = p.team_id
        WHERE p.item_id = ?""", [*round_params, weapon]).fetchall()
    bought_rounds = set()
    buys_by_round = defaultdict(list)
    for g, rn, t, buyer, rec in buys:
        if s.player and s.player not in (buyer, rec):
            continue
        bought_rounds.add((g, rn, t))
        buys_by_round[(g, rn, t)].append((buyer, rec))

    held_keys = [k for k in keys if holders.get(k)]

    def rate_block(subset):
        n = len(subset)
        held = sum(1 for k in subset if holders.get(k))
        bought = sum(1 for k in subset if k in bought_rounds)
        won_with = sum(team_rounds[k]["won"] for k in subset if holders.get(k))
        won_without = sum(team_rounds[k]["won"] for k in subset if not holders.get(k))
        return dict(team_rounds=n, possession=frac(held, n), bought=frac(bought, n),
                    round_win_with=frac(won_with, held), round_win_without=frac(won_without, n - held))

    by_side = {sd: rate_block([k for k in keys if team_rounds[k]["side"] == sd]) for sd in ("attacker", "defender")}
    by_tier = {t: rate_block([k for k in keys if team_rounds[k]["tier"] == t]) for t in BUY_TIERS}
    by_map = {m: rate_block([k for k in keys if team_rounds[k]["map"] == m])
              for m in sorted({team_rounds[k]["map"] for k in keys})}

    # ---- players / agents / shifts ------------------------------------ #
    pstat = defaultdict(lambda: Counter())
    pside = defaultdict(lambda: defaultdict(Counter))
    agent_stat = defaultdict(Counter)
    for g, rn, t, p, ag, side_, m, won, tier, holds in pr_rows:
        k = (g, rn, t)
        pstat[p]["rounds"] += 1
        agent_stat[ag]["rounds"] += 1
        pside[p][side_]["rounds"] += 1
        if holds:
            pstat[p]["held"] += 1
            agent_stat[ag]["held"] += 1
            pside[p][side_]["held"] += 1
            pairs = buys_by_round.get(k, [])
            if any(b == p and r == p for b, r in pairs):
                pstat[p]["bought_self"] += 1
            elif any(r == p and b != p for b, r in pairs):
                pstat[p]["received_drop"] += 1
            else:
                pstat[p]["carried_over"] += 1
        for b, r in buys_by_round.get(k, []):
            if b == p and r != p:
                pstat[p]["bought_for_teammate"] += 1
    drops = Counter((b, r) for pairs in buys_by_round.values() for b, r in pairs if b != r)

    # Holder hand-offs between consecutive rounds of the same game.
    transitions = Counter()
    for (g, rn, t) in sorted(held_keys):
        prev = holders.get((g, rn - 1, t))
        cur = holders[(g, rn, t)]
        if prev and len(prev) == 1 and len(cur) == 1 and prev != cur:
            transitions[(next(iter(prev)), next(iter(cur)))] += 1

    pname = lambda pid: C.players.get(pid, {}).get("name", pid)
    players = []
    for p, st in sorted(pstat.items(), key=lambda kv: -kv[1]["held"]):
        if not st["held"] and not st["bought_for_teammate"]:
            continue
        players.append(dict(
            player_id=p, name=pname(p), rounds_played=st["rounds"], possession=frac(st["held"], st["rounds"]),
            acquired=dict(bought_self=st["bought_self"], received_drop=st["received_drop"], carried_over=st["carried_over"]),
            bought_for_teammates=st["bought_for_teammate"],
            by_side={sd: frac(v["held"], v["rounds"]) for sd, v in pside[p].items()}))
    agents = [dict(agent=a, name=C.agents.get(a, {}).get("name", a), possession=frac(st["held"], st["rounds"]))
              for a, st in sorted(agent_stat.items(), key=lambda kv: -kv[1]["held"]) if st["held"]]

    # ---- holder positions, angles, opening duels ---------------------- #
    kills = db.execute(f"""
        WITH scope AS ({round_sql})
        SELECT DISTINCT k.game_id, k.round_number, k.killer_id, k.victim_id, k.killer_team_id, k.victim_team_id, k.weapon,
               k.round_time_ms, k.first_kill, k.map_slug, k.killer_u, k.killer_v, k.killer_callout, k.killer_callout_super,
               k.victim_u, k.victim_v, k.victim_callout, k.victim_callout_super
        FROM kills k JOIN scope sc ON sc.game_id = k.game_id AND sc.round_number = k.round_number
             AND (sc.team_id = k.killer_team_id OR sc.team_id = k.victim_team_id)
        WHERE k.kill_type = 'enemy'""", round_params).fetchall()
    casts = db.execute(f"""
        WITH scope AS ({round_sql})
        SELECT c.game_id, c.round_number, c.team_id, c.player_id, c.round_time_ms, c.map_slug, c.minimap_u,
               c.minimap_v, c.callout, c.callout_super, c.view_x, c.view_y
        FROM utility_casts c JOIN scope sc ON sc.game_id = c.game_id AND sc.round_number = c.round_number AND sc.team_id = c.team_id
        WHERE c.minimap_u IS NOT NULL""", round_params).fetchall()

    def is_holder(g, rn, team_id, pid):
        return pid in holders.get((g, rn, team_id), ())

    positions, angle_pairs = [], defaultdict(list)
    duel = defaultdict(Counter)
    duel_total = Counter()
    setup_rounds = defaultdict(lambda: defaultdict(set))  # side -> phase -> holder rounds with a sighting
    setup_callouts = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))

    def add_position(g, rn, team_id, pid, kind, t_ms, m, u, v, co, sup, aim=None):
        if u is None:
            return
        sd, ph = team_rounds[(g, rn, team_id)]["side"], phase_of(t_ms)
        label = callout_label(sup, co)
        positions.append(dict(kind=kind, player_id=pid, u=round(u, 4), v=round(v, 4), aim_uv=aim, callout=label,
                              time_s=round(t_ms / 1000, 1) if t_ms is not None else None, phase=ph, side=sd, map=m,
                              game_id=g, round_number=rn))
        if ph and label:
            setup_callouts[sd][ph][(m, label)].add((g, rn, team_id))
            setup_rounds[sd][ph].add((g, rn, team_id))

    for (g, rn, killer, victim, kt, vt, w, t_ms, first, m, ku, kv, kc, ks, vu, vv, vc, vs) in kills:
        k_holder, v_holder = is_holder(g, rn, kt, killer), is_holder(g, rn, vt, victim)
        if k_holder:
            add_position(g, rn, kt, killer, f"kill_{'with_' + weapon if w == weapon else 'other_weapon'}", t_ms, m, ku, kv, kc, ks)
            if w == weapon and ku is not None and vu is not None:
                angle_pairs[(m, callout_label(ks, kc), callout_label(vs, vc))].append(
                    dict(t=t_ms, from_uv=[round(ku, 4), round(kv, 4)], to_uv=[round(vu, 4), round(vv, 4)]))
        if v_holder:
            add_position(g, rn, vt, victim, "death", t_ms, m, vu, vv, vc, vs)
        if first:  # opening duel: record from each holder's point of view
            if k_holder:
                duel[(m, callout_label(ks, kc), team_rounds[(g, rn, kt)]["side"])]["won"] += 1
                duel_total["won"] += 1
            if v_holder:
                duel[(m, callout_label(vs, vc), team_rounds[(g, rn, vt)]["side"])]["lost"] += 1
                duel_total["lost"] += 1
    for (g, rn, t, pid, t_ms, m, u, v, co, sup, vx, vy) in casts:
        if is_holder(g, rn, t, pid):
            add_position(g, rn, t, pid, "utility_cast", t_ms, m, u, v, co, sup, aim=C.aim_uv(m, vx, vy))

    held_by_side = Counter(team_rounds[k]["side"] for k in held_keys)
    setups = {}
    for sd in ("defender", "attacker"):
        if not held_by_side[sd]:
            continue
        setups[sd] = dict(holder_rounds=held_by_side[sd], phases=[
            dict(phase=ph, rounds_with_position=frac(len(setup_rounds[sd][ph]), held_by_side[sd]),
                 top_callouts=[dict(map=m, callout=label, rounds=frac(len(rs), held_by_side[sd]))
                               for (m, label), rs in sorted(setup_callouts[sd][ph].items(), key=lambda kv: -len(kv[1]))[:6]])
            for ph in PHASES])

    angles = []
    for (m, frm, to), items in sorted(angle_pairs.items(), key=lambda kv: -len(kv[1])):
        if len(angles) >= 15:
            break
        by_phase = Counter(phase_of(i["t"]) for i in items)
        angles.append(dict(map=m, from_callout=frm, to_callout=to, kills=len(items),
                           median_time_s=median_s([i["t"] for i in items]),
                           by_phase={ph: by_phase[ph] for ph in ROUND_PHASES},
                           example=dict(from_uv=items[0]["from_uv"], to_uv=items[0]["to_uv"])))
    weapon_kills_by_phase = Counter(phase_of(i["t"]) for items in angle_pairs.values() for i in items)

    duels = sorted((dict(map=m, callout=label, side=sd, won=c["won"], lost=c["lost"],
                         win_rate=frac(c["won"], c["won"] + c["lost"]))
                    for (m, label, sd), c in duel.items()), key=lambda d: -(d["won"] + d["lost"]))

    result = dict(
        scope=dict(**s.describe(), weapon=weapon),
        weapon=C.weapons.get(weapon),
        sample=dict(team_rounds=N, holder_team_rounds=len(held_keys)),
        rates=dict(overall=rate_block(keys), by_side=by_side,
                   by_buy_tier={t: v for t, v in by_tier.items() if v["team_rounds"]}, by_map=by_map),
        players=players, agents=agents,
        possession_shifts=dict(
            round_to_round=[dict(from_player=pname(a), to_player=pname(b), from_id=a, to_id=b, times=n)
                            for (a, b), n in transitions.most_common(10)],
            drops=[dict(buyer=pname(b), recipient=pname(r), buyer_id=b, recipient_id=r, times=n)
                   for (b, r), n in drops.most_common(10)]),
        setups=setups,
        angles=angles,
        weapon_kills_by_phase={ph: weapon_kills_by_phase[ph] for ph in ROUND_PHASES},
        opening_duels=dict(total=dict(won=duel_total["won"], lost=duel_total["lost"],
                                      win_rate=frac(duel_total["won"], duel_total["won"] + duel_total["lost"])),
                           by_callout=duels[:20]),
        positions=positions[:5000],
    )
    result["summaries"] = sniper_summaries(s, wname, result)
    return result


def pp(f: dict) -> str:
    """'56% (57/102)' from a frac() block."""
    return f"{f['pct'] if f['pct'] is not None else 0:.0f}% ({f['n']}/{f['of']})"


def sniper_summaries(s: Scope, wname: str, res: dict) -> list[str]:
    subject, rw = s.subject(), s.rounds_word()
    o = res["rates"]["overall"]
    if not o["team_rounds"]:
        return [f"No rounds for {subject} with these filters."]
    out = [f"{subject} {s.verb('has', 'have')} the {wname} at buy end in {pct_of(o['possession']['n'], o['team_rounds'], rw)} "
           f"and {s.verb('buys', 'buy')} one in {pct_of(o['bought']['n'], o['team_rounds'], rw)}."]
    bs = res["rates"]["by_side"]
    if bs.get("defender", {}).get("team_rounds") and bs.get("attacker", {}).get("team_rounds") and not s.side:
        out.append(f"{wname} rate by side: defense {pp(bs['defender']['possession'])}, attack {pp(bs['attacker']['possession'])}.")
    if o["possession"]["n"] >= 5 and o["round_win_with"]["of"] and o["round_win_without"]["of"]:
        out.append(f"Round win rate with the {wname}: {pp(o['round_win_with'])}; without it: {pp(o['round_win_without'])}.")
    if res["players"] and not s.player:
        p = res["players"][0]
        acq, pos = p["acquired"], p["possession"]
        out.append(f"Main {wname} holder: {p['name']}, with it in {pos['n']} of their {pos['of']} rounds ({pos['pct']:.0f}%): "
                   f"bought {acq['bought_self']}, received as a teammate drop {acq['received_drop']}, "
                   f"carried over {acq['carried_over']}.")
    elif res["players"]:
        acq = res["players"][0]["acquired"]
        out.append(f"How {subject} gets it: bought {acq['bought_self']}, received as a teammate drop "
                   f"{acq['received_drop']}, carried over from the previous round {acq['carried_over']}.")
    drops = [d for d in res["possession_shifts"]["drops"] if d["times"] >= 2]
    if drops:
        d = drops[0]
        out.append(f"Recurring {wname} drop: {d['buyer']} buys it for {d['recipient']} ({d['times']} rounds).")
    angle = next((a for a in res["angles"] if a["kills"] >= 2), None)
    if angle:
        out.append(f"Favourite {wname} angle: from {angle['from_callout']} into {angle['to_callout']} on "
                   f"{map_name(angle['map'])} ({angle['kills']} kills, median {angle['median_time_s']:.0f}s into the round).")
    kp = res["weapon_kills_by_phase"]
    total = sum(kp.values())
    if total >= 5:
        ph = max(kp, key=kp.get)
        out.append(f"{round(100 * kp[ph] / total)}% of {wname} kills ({kp[ph]}/{total}) land {ph} after barriers drop.")
    od = res["opening_duels"]
    if od["total"]["win_rate"]["of"] >= 3:
        line = f"{wname} holders win {pp(od['total']['win_rate'])} of the opening duels they take"
        best = next((d for d in od["by_callout"] if d["won"] + d["lost"] >= 3), None)
        if best:
            line += (f"; most contested spot: {best['callout']} on {map_name(best['map'])} "
                     f"({best['won']}W-{best['lost']}L on {SIDE_WORD.get(best['side'], best['side'])})")
        out.append(line + ".")
    defn = res["setups"].get("defender")
    if defn:
        early = next(p for p in defn["phases"] if p["phase"] == "0-20s")
        if early["top_callouts"]:
            c = early["top_callouts"][0]
            out.append(f"Early defense setup: in the first 20s the {wname} is most often seen at {c['callout']} on "
                       f"{map_name(c['map'])}, in {pct_of(c['rounds']['n'], c['rounds']['of'], 'holder rounds')}.")
    return out


app.include_router(api)


# --------------------------------------------------------------------------- #
# Built React frontend (production): registered last so /api routes win
# --------------------------------------------------------------------------- #

if (FRONTEND_DIST / "index.html").is_file():
    _INDEX = FRONTEND_DIST / "index.html"

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not Found")  # unknown API routes stay JSON 404s
        if path:
            file = (FRONTEND_DIST / path).resolve()
            if file.is_file() and file.is_relative_to(FRONTEND_DIST):
                # Vite fingerprints everything in assets/, so those can be cached forever.
                cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
                return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(_INDEX, headers={"Cache-Control": "no-cache"})  # SPA fallback

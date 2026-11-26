# TacVector

**VALORANT tactical scouting: utility placement maps and an Operator/Odin profiler, built on GRID professional match telemetry.**

Built for the **Cloud9 x JetBrains: Sky's The Limit Hackathon 2026**.

TacVector turns GRID's event-by-event VALORANT feeds (plus the hackathon's ability-cast supplement) into a scouting tool that works for any of the 160 teams in the dataset. Pick a team, map, side and buy tier, and TacVector shows where and when the team uses its utility, how it gets and uses its Operator or Odin, and rule-based counters a coach can take straight into prep. Scout an opponent, or flip the same view around to see what an opponent would learn about you.

---

## Contents

- [Live demo](#live-demo)
- [Core capabilities](#core-capabilities)
- [Architecture](#architecture)
- [Data pipeline methodology](#data-pipeline-methodology)
- [API](#api)
- [Local setup](#local-setup)
- [Running with Docker](#running-with-docker)
- [Configuration](#configuration)
- [Repository layout](#repository-layout)
- [Data licensing](#data-licensing)

---

## Live demo

> **Live demo:** `https://tacvector.fly.dev`

The hosted demo is password-protected because it serves data derived from the licensed GRID dataset. **The demo login password is provided in the Devpost submission notes.** Enter it in the login screen; it is kept only in that browser tab's `sessionStorage` and sent with each API request as an `X-Demo-Password` header.

---

## Core capabilities

### Priority utility placement taxonomy
Every GRID ability id is assigned to one tactical category in [`backend/ability_categories.py`](backend/ability_categories.py). If an ability does two jobs, the higher-priority category wins:

| Priority | Category | What it covers |
|---:|---|---|
| 1 | **Placeable** | Unattended deployables that hold space or gather info: traps, cams, turrets, sensors |
| 2 | **Recon** | Reveals enemy positions: bolts, drones, dogs, prowlers |
| 3 | **Smoke** | Blocks vision: smokes and vision-blocking walls |
| 4 | **Stall** | Delays a push or retake: mollies, slows, concussions, nets, damage zones |
| 5 | **Flash** | Blinds or nearsights |
| — | **Other** | Movement, heals, self-buffs, decoys and duel ultimates |

So Spycam counts as Placeable rather than Recon, and Arc Rose as Placeable rather than Flash. Each category is reported as a share of **team-rounds** (one team's half of one round), so you can compare "how often" across teams with different sample sizes.

### Cast origins and aim direction vectors
Each cast is plotted at the **caster's position at the moment of the cast**, with a short tick showing the caster's **aim direction**. Both are projected onto the map's minimap image and snapped to the nearest named callout. Casts are coloured by round timing: **buy phase**, **0–20s**, **20–40s**, **40s+** after barriers drop. Timing filter pills isolate any window, and the callout-frequency overlay then shows that window's own percentages and n/N round counts. The data has no ability landing points, so TacVector shows origin + aim, and keeps the 3D view vector for a future landing model.

### Operator/Odin economy: bought, dropped and carried over
For each team-round, TacVector works out whether a player held the Operator (or Odin) when the buy phase ended, and how they got it:
- **Bought**: they paid for it themselves.
- **Teammate drop**: a teammate paid for it (`purchases.player_id ≠ recipient_id`).
- **Carried over**: they held it with no purchase that round, so it survived from the previous round.

On top of that the profiler shows possession and buy rates by side and buy tier, round win rate with vs. without the weapon, the main holders, recurring drop pairs, hand-offs between rounds, setup positions by timing window, kill sightlines (killer → victim vectors), and opening-duel win/loss records at each callout.

### Dynamic filtering
Searchable dropdowns cover **Team** (all 160, with one-click Self-Scout presets for Cloud9 and Cloud9 GC), **Map** (defaults to the selected team's richest map), **Side**, **Buy tier** (Pistol, Eco, Force, Bonus, Full Buy), plus optional **Player**, **Agent**, **Ability category** and **Weapon** (Operator/Odin). Every view is encoded in the URL, so a scouting view can be shared or bookmarked.

Buy tiers are derived per team-round from GRID's `loadoutValue`, summed over the team when the buy phase ends:

| Tier | Rule |
|---|---|
| Pistol | Round 1 or 13 |
| Full Buy | Team loadout ≥ 20,000 |
| Eco | Team loadout < 5,000 |
| Bonus | Won the previous round, loadout below a full buy (playing on saved gear) |
| Force | Everything else |

### Opponent Scout / Self-Scout toggle
A single toggle works for any selected team. **Opponent Scout** frames the output as "how they play and how to beat them". **Self-Scout** frames the same statistics as "what your habits give away": the counters become *what a prepared opponent will run against you*, so you know which patterns to vary.

### Rule-based Anti-Strat counters
The counters are deterministic rules computed on the aggregates, not generated text, and each one shows its evidence (n/N rounds) and a confidence level:

| Rule | Fires when |
|---|---|
| Category hotspot | One utility category lands at a callout in ≥ 25% of rounds (≥ 3 rounds); at most two per category |
| Combo tell | A recurring combo (≥ 2 different abilities from ≥ 2 players within 3s, seen in ≥ 3 rounds) appears in ≥ 15% of rounds |
| Front-loaded utility | ≥ 60% of in-round utility (min. 20 casts) is spent in the first 20s |
| Late utility | ≥ 40% of in-round utility (min. 20 casts) comes after 40s |

Confidence is **high** for N ≥ 20 and a share ≥ 50%, **medium** for N ≥ 10 and a share ≥ 30%, and **low** otherwise. The insight cards ("Cloud9 most often (46%, 134/289 rounds) deploys Smoke utility at A Lobby on Lotus, right as barriers drop") follow the same rule: every claim comes with its sample size.

---

## Architecture

```
 ┌───────────────────────────┐      ┌─────────────────────────────┐
 │ valorant-api.com          │      │ GRID starter repo (local)   │
 │ maps, callouts, agents,   │      │ val/series.csv              │
 │ abilities, weapons, gear  │      │ val/events_*.jsonl.zst      │
 └─────────────┬─────────────┘      │ val-ability-supplements/    │
               │                    └──────────────┬──────────────┘
   seed_assets.py (Phase 1)            ingest_grid.py (Phase 2)
               │                     streaming zstd, process pool
               ▼                                   ▼
        ┌──────────────────────────────────────────────────┐
        │ SQLite: backend/tacvector.db  (never committed)   │
        │ assets · matches · games · rounds · player_rounds │
        │ utility_casts · purchases · kills · team_rounds   │
        └────────────────────────┬─────────────────────────┘
                db_setup.py: indexes + team_rounds / buy tiers
                                 ▼
        ┌──────────────────────────────────────────────────┐
        │ FastAPI (main.py, Phase 3)                        │
        │ /api/filters · /api/utility · /api/operator       │
        │ demo auth · serves frontend/dist in production    │
        └────────────────────────┬─────────────────────────┘
                                 ▼
        ┌──────────────────────────────────────────────────┐
        │ React 19 + TypeScript + Vite (Phase 4)            │
        │ canvas + SVG minimap, dual lenses, side panels    │
        └──────────────────────────────────────────────────┘
                  Docker: one container, both tiers (Phase 5)
```

- **Backend:** Python 3.14 (its standard-library `compression.zstd` streams the feeds), FastAPI and SQLite. The API opens the database read-only for every request. Static lookups (maps, callouts, teams, players, abilities) are loaded once at startup.
- **Frontend:** React 19, TypeScript and Vite. The minimap draws thousands of cast markers and aim ticks on a `<canvas>`, with an SVG layer on top for labelled callout bubbles and sightline overlays. Hover hit-testing uses a spatial grid, and the view zooms to fit the map's callout bounds. API responses are cached by URL, so switching lenses or going back to an earlier filter combination is instant.
- **Data hygiene:** every query excludes invalid feed sessions (`games.session_valid = 0`), rounds that may belong to a merged map (`rounds.map_uncertain = 1`) and unfinished rounds. Results always report their sample size as n/N team-rounds.

---

## Data pipeline methodology

### 1. Minimap transforms: valorant-api's swapped axes
`seed_assets.py` stores, for each competitive map, valorant-api's minimap image and its world→minimap multipliers, plus every callout's world position. The projection **swaps the axes**: world `y` drives the horizontal image coordinate and world `x` drives the vertical one.

```
u = y · xMultiplier + xScalarToAdd        # 0..1 across the minimap image
v = x · yMultiplier + yScalarToAdd        # 0..1 down the minimap image
```

GRID positions are in centimetres, the same space as valorant-api callouts, so casts and kills project with the same formula. Aim vectors use the same axis swap (normalised to a unit direction on the minimap). Each projected point is labelled with its nearest callout by world-space distance. Map, agent and weapon slugs are lower-case display names, which is how GRID names them, so they join directly.

### 2. Buy-phase inventory reconstruction
VALORANT GRID feeds have **no purchase events**. TacVector rebuilds each buy from the full-state inventory snapshots at `round-started-freezetime` and `round-ended-freezetime`, corrected for pickups and drops during the buy phase:

```
gained = inventory_end − inventory_start − picked_up + dropped
```

GRID often leaves out pickup/drop events for weapons handed over during the buy phase, so `reconcile_buys` checks each team's gains against the credits each player actually spent:

1. A gain the holder couldn't afford that matches an item a teammate lost is a **hand-over**, not a purchase.
2. Any remaining gain the holder couldn't afford is attributed to the teammate whose unexplained spend fits it most tightly. That is a **drop**: `player_id` is the payer and `recipient_id` the holder.
3. A team-round whose reconstructed buys exceed its spend by more than `1.25 × spent + 1,500` is treated as an inconsistent snapshot (e.g. a match resumed from restored state) and skipped, rather than storing phantom buys.

The result is the `purchases` table (624,819 rows; 25,339 attributed to a teammate). Every player's inventory when the buy phase ends is also kept in `player_rounds`, and that is what the "carried over" classification and the Operator/Odin possession rates are built from.

**Inferred shields.** Older feeds (VCT Americas 2025) leave shields out of the inventory. When a player's `currentArmor` rises during the buy phase and no shield appears in their inventory, a shield purchase is inferred (≥ 50 armour → heavy, otherwise light) and flagged `inferred = 1`. There are 45,666 of these.

**Ability kills.** GRID leaves `weaponKills` empty for ability kills, so the weapon is taken from the killer's last damage source on the victim (13,909 kills).

### 3. Multi-session, rolled-back and replayed GRID series
| Edge case | Handling |
|---|---|
| **Rolled-back maps** (`series-!started-game`) | The game id is dropped together with every cast, kill and purchase recorded under it (87 rolled-back games across the corpus). |
| **Games missing from the final state** | Only games present in the last full `seriesState` of their feed session are kept. |
| **Replayed rounds** (GRID restarts the same round number after a technical pause, often *without* a `!` rollback) | Each round's start events are counted; only the **last attempt's** casts, kills and purchases are kept (42 replayed rounds). |
| **Two feed sessions in one file** (`sequenceNumber` restarts at 1) | Sessions are tracked separately from each `grid-started-feed`; each game records its `session` and GRID's `valid` flag as `session_valid`, and the API only queries valid sessions. |
| **Invalid series** (GRID `valid: false`) | Stored but filtered out at query time (98 games in invalid sessions). |
| **Next map merged into the previous game** | Spotted where a run of trailing rounds has casts but no supplement coverage. Those games get `merged_from_round` and their rounds `map_uncertain = 1`, and their positions are left off the map (2 games, 44 rounds). |
| **Supplement quality** | Interpolated positions are used when the supplement's quality flag allows it. If the frame gap was exceeded, the nearer real frame is used instead. If there is no supplement match, GRID's own 2D position is the fallback. The low-rate (≈1 Hz) movement sampling in VCT Americas 2025 is recorded per cast in `frame_gap_ms`. |

After ingestion, about 99.6% of the 1.40M utility casts have a supplement position, and all but ~1,600 land on a minimap.

---

## API

All endpoints are under `/api`. When `DEMO_PASSWORD` is set, they need an `X-Demo-Password` header (or HTTP Basic credentials with that password).

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Liveness and whether auth is required (no auth needed) |
| `GET /api/filters` | Teams (with Self-Scout presets), maps with callouts and transforms, players, agents, abilities by category, Operator/Odin metadata, buy tiers, round phases |
| `GET /api/utility` | Cast points with aim vectors, callout density, category × callout hotspots, round-phase breakdown, recurring combos, insights, anti-strat counters |
| `GET /api/operator` | Possession / buy / win rates (overall, by side, buy tier, map), holders with bought/dropped/carried split, hand-offs, setups by timing window, sightlines, opening duels, summaries |

Shared query parameters: `team`, `map`, `side` (`attacker`/`defender`), `buy_tier`, `player`, `agent`. `/api/utility` also accepts `ability_category`, `ability` and `limit`; `/api/operator` accepts `weapon` (`operator`/`odin`). FastAPI's interactive docs are at `/docs`.

---

## Local setup

### Prerequisites
- **Python 3.14+** (the ingestion uses the standard-library `compression.zstd`)
- **Node.js 24** (as used in the Dockerfile) and npm
- The hackathon data repo cloned **next to** this one, not inside it:

```
hackathon/
├── stl-hackathon-2026-grid-starter/   ← GRID data (never commit)
└── tacvector/                         ← this repo
```

### 1. Backend: build the database
```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

cd backend
python seed_assets.py                  # valorant-api.com assets → tacvector.db (rebuilds the asset tables)
python ingest_grid.py --limit 5        # quick check on 5 series
python ingest_grid.py                  # everything not yet ingested (parallel; --workers N)
```

`ingest_grid.py` streams the `.zst` files and never decompresses them to disk. It reads from `STL_DATA_DIR` (default `../stl-hackathon-2026-grid-starter`), can be resumed (`--series`, `--force` and `--reset` are available), and rebuilds the query indexes and `team_rounds` when it finishes. The full corpus produces a database of about 1.4 GB.

### 2. Backend: run the API
```bash
cd backend
uvicorn main:app --reload                          # open access, http://127.0.0.1:8000
DEMO_PASSWORD=choose-one uvicorn main:app          # password-protected (PowerShell: $env:DEMO_PASSWORD="choose-one")
```

### 3. Frontend: development server
```bash
cd frontend
npm ci
npm run dev                                        # http://localhost:5173
```

Vite proxies `/api` to `http://127.0.0.1:8000` (override with `TACVECTOR_API`), so there are no CORS or port issues during development.

### 4. Production build (single process)
```bash
cd frontend && npm run build                       # → frontend/dist
cd ../backend && uvicorn main:app --port 8080      # serves the API and the built app at /
```

When `frontend/dist/index.html` exists, FastAPI serves the app at `/` and sends every non-`/api` path back to `index.html`. Fingerprinted assets are cached as immutable.

---

## Running with Docker

The multi-stage [`Dockerfile`](Dockerfile) builds the React app with Node 24 and then runs FastAPI on Python 3.14 as an unprivileged user, serving both on port 8080. **The database is never copied into the image** ([`.dockerignore`](.dockerignore) excludes all `*.db`, `*.zst`, `*.jsonl` and similar files). You mount it at runtime, and `DB_PATH` points at it (default `/data/tacvector.db`).

```bash
docker build -t tacvector .

# Mount the folder that holds tacvector.db at /data
docker run --rm -p 8080:8080 \
  -e DEMO_PASSWORD=choose-one \
  -v "$PWD/backend:/data" \
  tacvector

# Custom file name or location inside the mount
docker run --rm -p 8080:8080 \
  -e DB_PATH=/data/scouting.db \
  -v "/path/to/dbs:/data" \
  tacvector
```

PowerShell: replace `"$PWD/backend:/data"` with `"${PWD}/backend:/data"` and use a backtick (`` ` ``) for line continuation.

Mount the database **writable** on the first run, so startup can create its indexes and the `team_rounds` table. Once those exist, a read-only database also works: the API checks for `team_rounds` and carries on. The container has a `HEALTHCHECK` against `/api/health`.

---

## Configuration

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DB_PATH` | all backend scripts | `backend/tacvector.db` (`/data/tacvector.db` in Docker) | SQLite database location (`TACVECTOR_DB` is accepted as a legacy alias) |
| `STL_DATA_DIR` | `ingest_grid.py` | `../stl-hackathon-2026-grid-starter` | Local GRID data repo |
| `DEMO_PASSWORD` | API | unset (open access) | Turns on password protection |
| `DEMO_USER` | API | unset (any username) | Pins the HTTP Basic username |
| `CORS_ORIGINS` | API | `*` | Comma-separated allowed origins |
| `FRONTEND_DIST` | API | `frontend/dist` (`/app/frontend/dist` in Docker) | Built React app to serve at `/` |
| `TACVECTOR_API` | Vite dev/preview proxy | `http://127.0.0.1:8000` | Backend URL for `/api` during development |

---

## Repository layout

```
tacvector/
├── backend/
│   ├── seed_assets.py          # Phase 1: valorant-api.com assets + minimap transforms
│   ├── ingest_grid.py          # Phase 2: GRID feed + supplement ingestion and cleaning
│   ├── db_setup.py             # indexes, team_rounds, buy tiers
│   ├── ability_categories.py   # priority utility taxonomy
│   ├── main.py                 # Phase 3: FastAPI scouting API, demo auth, static frontend
│   └── requirements.txt
├── frontend/                   # Phase 4: React + TypeScript + Vite
│   ├── src/components/         # Minimap (canvas + SVG), Combobox, ControlBar, LoginModal, …
│   ├── src/views/              # UtilityView, OperatorView
│   └── vite.config.ts          # /api proxy
├── Dockerfile, .dockerignore   # Phase 5: single-container deployment
├── AGENTS.md                   # data rules + GRID format reference for AI coding agents
├── AI_SHOWCASE.md              # how JetBrains IDEs + Claude were used
└── LICENSE                     # MIT (code only)
```

---

## Data licensing

The code in this repository is MIT-licensed (see [LICENSE](LICENSE)). The **GRID match data is not included and must never be committed**: it is licensed only for building hackathon projects during the hackathon, with no redistribution and no commercial use. `.gitignore` and `.dockerignore` block the raw feeds and every derived database. To run TacVector, clone the [official data repository](https://github.com/cloud9esports/stl-hackathon-2026-grid-starter) next to this one and build the database locally as described above.

GRID event feeds © GRID eSports GmbH and Riot Games, Inc. Ability supplements and metadata © 2026 Cloud9 Esports, Inc. VALORANT is a trademark of Riot Games, Inc. This project is not endorsed by Riot Games.

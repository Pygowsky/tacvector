# AI Showcase: Building TacVector with JetBrains IDEs and Claude

*Tooling explanation and Bonus Prize entry for the Cloud9 x JetBrains: Sky's The Limit Hackathon 2026.*

TacVector went from an empty repository to a containerised, full-stack scouting tool in five commits, one per phase. Each phase was done in a JetBrains IDE with **Claude running inside JetBrains Air** as the coding agent. The human set the scope, made the domain calls and approved each commit. Claude read the data contracts, wrote the code and checked its own work against the real data. This document covers how that split worked and what it found.

## Toolchain

| Tool | Role in the project |
|---|---|
| **PyCharm** | The Python backend (Phases 1–3, 5). The project interpreter is bound to the repo's `.venv` on **Python 3.14**, which the ingestion needs for the standard-library `compression.zstd`. Used for the seeding script, the GRID ingestion pipeline and the FastAPI service. |
| **WebStorm 2026.3** | The React + TypeScript frontend (Phase 4) on the TypeScript 7 native (Go) compiler preview, with oxlint for linting. WebStorm's bundled **Chrome DevTools CLI** let the agent open the running app in a headless browser, click through it, read the console and network traffic, and take screenshots. |
| **JetBrains Air + Claude** | The agent workspace for every phase. Claude worked in the repo through the terminal, file and search tools, ran the scripts and builds, queried the live API, and reported back with evidence before anything was committed through the IDE's VCS integration. |

## 1. Context engineering with AGENTS.md

The highest-leverage thing in the project is a single file. The root [`AGENTS.md`](AGENTS.md) (484 lines) is the hackathon's data README and its agent rules, concatenated, with one project-specific line on top:

> The GRID dataset is located locally at ../stl-hackathon-2026-grid-starter. Never copy raw data files or .sqlite databases into this repository.

Air loads it into every Claude session, so every phase started with the same data contract already in context. Six of its rules became concrete design decisions without being re-explained:

| AGENTS.md rule | Where it shows up in the code |
|---|---|
| Never commit the data, including derived extracts and databases | `.gitignore` blocks `*.zst`, `*.jsonl`, `*.db*`, `*.sqlite`; Phase 5's `.dockerignore` keeps them out of the image too. The database is mounted at runtime, never built in. |
| Keep the data repo outside the project and reference it by path | `STL_DATA_DIR`, defaulting to `../stl-hackathon-2026-grid-starter` |
| Stream; don't decompress the corpus (≈160 GB uncompressed) | `ingest_grid.py` reads every feed line by line with `compression.zstd`, in a process pool |
| `seriesState` is partial unless `includesFullState` is true; removals are explicit `!` events | Inventories, rosters and results are taken only from full states; `series-!started-game` and `game-!started-round` are handled as rollbacks |
| Check `valid` / `session_valid` before counting results; three files contain two sessions | Sessions are tracked from each `grid-started-feed`; every API query filters on `session_valid = 1` |
| Supplement `grid_event_id` = GRID `events[].id` | The join key for cast positions and aim vectors |

The same rules shaped how the agent explored the data. It looked at single files and aggregate counts, not whole envelopes. When it needed real API responses to write TypeScript types, it saved them to a temp folder outside the repo and deleted them afterwards. Before finishing, it ran `git status` to confirm that nothing data-derived was untracked or staged.

## 2. Phase-by-phase

| Phase | Commit | Human direction | What Claude delivered |
|---|---|---|---|
| 1 | Seed valorant-api.com assets | Use valorant-api as the asset and coordinate source | `seed_assets.py`: maps, callouts, agents, abilities, weapons and gear in SQLite, with valorant-api's **axis-swapped** world→minimap transform (`u` from world `y`, `v` from world `x`) and callouts pre-projected |
| 2 | Ingest and clean GRID telemetry | Turn the raw feeds into a clean, queryable database | `ingest_grid.py`: a parallel streaming ingester that turns 857 series into games, rounds, 1.40M utility casts, 300k kills and 625k reconstructed purchases, plus per-series diagnostics in `ingest_log` |
| 3 | FastAPI endpoints + carry-over tracking + demo auth | The utility taxonomy's priorities, buy-tier thresholds, what counts as a counter | `ability_categories.py`, `db_setup.py` (team-rounds and buy tiers) and `main.py`: `/api/filters`, `/api/utility`, `/api/operator`, insight and anti-strat rules, bought/dropped/carried-over classification, `DEMO_PASSWORD` auth |
| 4 | React + TypeScript dual-lens UI | The feature spec: dual lenses, filters, Self-Scout toggle, login modal | A Vite app with a canvas + SVG minimap, searchable comboboxes, timing pills, side panels, URL-shareable state and a validated colour system |
| 5 | Production bundle + Docker | Ship it as a single deployable | FastAPI serves `frontend/dist` with an SPA fallback, configurable `DB_PATH`, read-only database tolerance, and a multi-stage non-root Dockerfile with a health check |

## 3. Telemetry edge cases uncovered during GRID ingestion

Phase 2 was where the agent did the most work. GRID's VALORANT feeds are accurate, but they are a live broadcast record, not a clean dataset. The agent ran the ingester, read its per-series diagnostics, traced each anomaly back to specific series, and wrote a handling rule for each. The rules are documented in the module docstring. Counts below are from the final database:

| Finding | Handling | Scale |
|---|---|---|
| **There are no purchase events at all** | Buys are rebuilt from freeze-time inventory snapshots: `gained = end − start − picked_up + dropped` | 624,819 purchases |
| **Weapons handed over in the buy phase often have no pickup/drop event** | `reconcile_buys` matches each gain against the credits each player spent: unaffordable gains matching a teammate's loss are hand-overs; the rest are drops paid for by the best-fitting teammate | 25,339 teammate-bought weapons |
| **Older feeds (VCT Americas 2025) leave shields out of the inventory** | Shields are inferred from a `currentArmor` increase (≥ 50 → heavy, otherwise light) and flagged `inferred` | 45,666 inferred shields |
| **Some snapshots are inconsistent** (matches resumed from restored state) | A team-round whose buys exceed its spend by more than `1.25 × spent + 1,500` is skipped, not stored as phantom buys | 38 team-rounds |
| **Ability kills have no weapon** | The weapon falls back to the killer's last damage source on the victim | 13,909 kills |
| **Rolled-back maps** | The rolled-back game id is dropped along with everything recorded under it | 87 games; 5,630 casts, 1,133 kills, 2,826 purchases dropped |
| **Rounds replayed with no `!` rollback** | Round starts are counted per round number, and only the last attempt is kept | 42 rounds |
| **The next map merged into the previous game** (e.g. series 2890334, 2890361) | Spotted from a run of trailing rounds with no supplement coverage; flagged `map_uncertain`, kept off the minimap | 2 games, 44 rounds |
| **Invalid and duplicate sessions** (e.g. 2890336 re-sends rounds 1–13 as 14–26) | Every game stores `session` and `session_valid`; the API only reads valid sessions | 98 games excluded |
| **The supplement's interpolation gap was exceeded; 1 Hz movement sampling in VCT Americas 2025** | Use the nearer real frame when the gap was exceeded; record `frame_gap_ms` on every cast | 3,523 nearer-frame casts; 175,909 low-rate casts |
| **valorant-api's current kits no longer contain some abilities GRID logged** (Cascade, Astra's Stars) | `NAME_OVERRIDES`, and a most-common-slot backfill for casts with no slot match | — |

Each fix exists because the agent compared its first-pass output with what had to be true (a team can't buy more than it spent; a game can't have two round 7s), found where they disagreed, and chased the cause down to the raw events.

## 4. Full-stack integration

Phases 4 and 5 were integration work, and the agent's loop was **contract → build → verify in a real browser**.

- **Contract first.** Claude read `main.py`, started the backend and pulled real responses from every endpoint before writing any UI code. The TypeScript types in `src/types.ts` mirror those responses. The first `npm run build` (`tsc -b` + Vite) passed with zero type errors.
- **Colours checked, not eyeballed.** A data-visualisation skill's colour-blind validator ruled out a 4-colour timing scheme for the scatter map. The final scheme uses three validated colours for the in-round windows, and a neutral hollow diamond for buy-phase casts, so shape tells them apart, not colour alone.
- **Verified in a real browser.** Using WebStorm's Chrome DevTools CLI, the agent drove the running app and found four issues that the type checker couldn't:
  1. The minimap art has a lot of padding, so the view now zooms to fit the map's callout bounds.
  2. "Every 2th" sample text.
  3. Python rounds half-to-even, so the backend said **62%** for 10/16 while the UI said **63%**; the UI now rounds the same way.
  4. The backend's `WWW-Authenticate: Basic` header made browsers pop a native login box over the app's own login screen.

  Fix 4 started as a header strip in the Vite proxy and was then **moved into the backend in Phase 5**, which now only challenges plain browser navigation.
- **End-to-end auth test.** The backend was restarted with `DEMO_PASSWORD`, and the agent confirmed the sequence: 401, the login screen appears, a wrong password is rejected, the right one unlocks the app, and the `X-Demo-Password` header is present on every following request (checked in the captured network traffic).
- **Deployable without leaking data.** Phase 5's Dockerfile builds the frontend in a Node stage and runs FastAPI as an unprivileged user. The licensed database stays outside the build context and is mounted at runtime through `DB_PATH`.

## 5. What we took away

- **Write the context down once.** Putting the data contract and licence rules in `AGENTS.md` meant they never had to be repeated, and never got forgotten in a later session.
- **Make the agent prove it.** The most valuable outputs came from checks: ingestion diagnostics, number-by-number comparisons between the API's text and the UI, screenshots and network logs. Every figure in the README was measured from the database, not estimated.
- **People own the judgement calls.** The taxonomy's priority order, the buy-tier thresholds, the counter rules' significance cut-offs and the Self-Scout framing were product decisions. Claude made them executable and testable, and each one comes with its evidence (n/N rounds) in the UI.

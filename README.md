# Fleet Command — Strait of Hormuz

A real-time maritime crisis-ops system: 15 simulated cargo ships, live
tracking, Command/Captain roles, AI-parsed distress calls, weather-aware
A* routing, and predictive collision/zone alerts — built for the Code Rush
Web Dev Track brief.

## Quick start

```bash
cp .env.example .env        # optional — see "AI features" below
docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000 (docs at http://localhost:8000/docs)
- Open the frontend in **two browser tabs** to see two clients stay in sync
  (or more — the brief asks for 5+ concurrent viewers, which this supports).
- One tab: leave role as **Command**. Another tab: switch to **Captain**
  and pick a ship — that's how you play both sides of a directive.

No API keys are required for the system to run and satisfy every core
requirement. See "AI features" below for what a Groq key adds.

## Architecture

```
┌─────────────┐   WebSocket (1Hz broadcast)   ┌──────────────┐
│   React +   │ ◄──────────────────────────── │   FastAPI    │
│   Leaflet   │ ──────────────────────────►   │  (Python)    │
│  (frontend) │   REST (directives, zones,     │  simulator   │
└─────────────┘   distress, playback…)         └──────┬───────┘
                                                        │
                                        ┌───────────────┼────────────────┐
                                        │               │                │
                                  Open-Meteo        Groq API         SQLite
                                  (weather,          (AI/NLP,      (alerts +
                                  no key needed)     optional key)  30s history)
```

### Why this stack

- **Backend: FastAPI.** WebSocket support is native (Starlette) — no
  Socket.IO/Flask-SocketIO middleware layer to install or wire up. REST and
  the `/ws` route live in the same app, same process. Async by default,
  which is what the 1Hz simulator tick and weather/AI HTTP calls need.
  Python also has the strongest ecosystem for the AI/NLP piece (20% of the
  grade).
- **Frontend: React + Vite + Leaflet.** `react-leaflet` + `leaflet-draw`
  for the map and the Command-only zone-drawing tool. Basemap tiles are
  CartoDB dark tiles over OpenStreetMap data (free, no key) — the brief
  explicitly allows hard-coded basemap tiles.
- **Database: SQLite (Python standard-library sqlite3), not Redis/Postgres.** This is a single
  backend process — there's no second instance needing pub/sub to stay in
  sync, so Redis buys nothing here. SQLite is zero-config and file-based,
  which keeps `docker compose up` simple and fully reproducible on a
  laptop with no extra services. Live ship state itself lives in memory
  (it's regenerated every tick anyway); SQLite persists the alert log and
  the 30-second-resolution playback history.
- **Weather: Open-Meteo.** Free, no API key, has a marine (wave height)
  endpoint that fits a shipping scenario better than a generic weather API.
- **AI/NLP: Groq.** Free tier, no credit card, fast inference (Llama
  3.3 70B) — good fit for a live ops tool where distress parsing needs to
  feel instant.

## AI features (optional GROQ_API_KEY)

Every AI-backed feature has a **deterministic rule-based fallback**, so
`docker compose up` with zero configuration still runs the entire system
end-to-end — distress messages still get triaged (keyword-based severity),
the fleet advisor still gives rule-based suggestions (lowest fuel, open
critical alerts), incident reports still get logged (templated text). This
was a deliberate choice: the brief requires the system to run with "no
reliance on infra you can't reproduce during judging," and an AI feature
that hard-fails without a key would violate that.

To get real LLM-backed parsing/advice:
1. Go to https://console.groq.com → API Keys → Create Key (free, no card).
2. `cp .env.example .env` and paste it in as `GROQ_API_KEY=...`.
3. `docker compose up --build` (or just restart the `backend` service).

## Feature checklist (mapped to the grading rubric)

**Core functionality (60%)**
- [x] 15 ships, simulator ticks at 1Hz, state broadcast to every connected
      client over WebSocket; fleet state is not polled
- [x] Client-side interpolation between ticks (ships move smoothly, no
      teleporting, capped by their actual speed)
- [x] Command draws, graphically edits, and deletes restricted zones at runtime; Captains see them read-only
- [x] Geofence breach alert on entry, visual + logged until
      acknowledged/resolved
- [x] Click a ship → cargo, fuel, speed, destination, status
- [x] A* routing over a grid, respecting the navigable-water polygon and
      all active restricted zones
- [x] Two reroute triggers implemented exactly as specified: a new zone
      intersecting the path (automatic, no approval needed), and an
      accepted Command directive
- [x] Edge cases: no valid path → `stranded` + alert; zone drawn on top of
      a ship → immediate breach + reroute-out; insufficient fuel → flagged
      but the ship keeps going until it actually hits 0%
- [x] Roles enforced server-side: only `X-Role: command` can create/delete
      zones or issue directives
- [x] Captain responds ACCEPT / ESCALATE_DISTRESS; the response reaches
      every connected client immediately
- [x] 5+ concurrent viewers stay in sync (broadcast-to-all architecture,
      not per-client state)

**AI/NLP (20%)**
- [x] Free-form distress messages → structured severity / issue /
      injuries / damage estimate
- [x] That structured data drives alert severity/priority end-to-end
- [x] *Bonus:* AI fleet advisor — proactive, prioritized suggestions
      on demand
- [x] *Extra, beyond the brief:* auto-generated incident reports attached
      to distress alerts for the fleet log

**Geospatial accuracy / UI/UX (15%)**
- [x] Real weather (Open-Meteo) drives the 30% fuel penalty and biases
      routing away from the worst of it
- [x] Fuel-to-destination feasibility reported (`fuel_warning`)
- [x] Proximity warnings at 2km, run through the same alert pipeline as
      everything else
- [x] *Extra, beyond the brief:* CPA (Closest Point of Approach)
      collision prediction — projects both ships' current course/speed
      forward and warns several minutes before two ships that *aren't*
      close yet are about to be, not just a same-instant distance check
- [x] *Bonus:* predictive zone-entry warnings ("will enter zone in ~3
      min at current heading")
- [x] Playback timeline, last hour at 30s resolution

**Bonuses implemented**
- Multiple route candidates (fastest / weather-safe / fuel-efficient) —
  Command can preview and pick one
- Ship-to-ship assistance requests (fuel transfer, medical aid, escort,
  cargo offload) with a nearby-ship broadcast
- Predictive alerts (zone entry, fuel shortage, CPA collision)
- AI fleet advisor

## Documented assumptions

Per the brief's own ground rules ("documented assumptions will be honored"):

1. **Canonical fleet data:** `backend/fleet.json` is the supplied scenario JSON.
   It is kept with the exact 15 ship records, 10 ports, bounding box, units,
   and navigable-water vertices from the brief. The source uses `[lat, lng]`
   and fuel in tons; runtime normalizes coordinates to `(lng, lat)` internally.
2. **Fuel simulation assumption:** the supplied JSON gives fuel remaining in
   tons but does not specify vessel capacity or a burn rate. Runtime therefore
   preserves the exact starting fuel values and derives a simulation-only burn
   rate from speed: `0.08 + 0.0025 * max(speed_knots - 10, 0)` tons/km.
   Adverse weather applies the required 30% multiplier. This assumption is
   used only for fuel feasibility/depletion and does not alter the source data.
3. **Geometry repair assumption:** the supplied navigable-water ring has a
   self-intersection at the Strait crossing. `backend/app/geo.py` keeps the
   source vertices unchanged, polygonizes the ring, and adds a narrow runtime
   bridge between the two main pieces so all 15 supplied ships can route
   through the chokepoint. This is a runtime normalization, not a rewrite of
   `fleet.json`.
4. **No authentication system.** Role is asserted via an `X-Role: command`
   header on REST calls and a `role` query param on the WebSocket connection.
   A real deployment would put proper accounts/auth in front of this; this is
   a hackathon-scope simplification.
5. **"Rerouting" status** is event-driven (flips the moment a reroute is
   triggered, per the brief's own phrasing), not recomputed from scratch every
   tick. It persists until the ship arrives, is redirected again, or a
   higher-priority state (distressed/stranded/out of fuel) takes over.
6. **Ports sit on/near the coast**, so ships route to a navigable-water
   approach point for the port rather than requiring real harbor-channel data.
7. **Weather is sampled on a coarse grid and refreshed every 10 minutes**, not
   fetched live per ship per tick, to keep the free weather service practical.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | No | *(empty → rule-based fallback)* | Enables real AI for distress parsing / advisor / incident reports |
| `GROQ_MODEL` | No | `openai/gpt-oss-120b` | Groq model id |
| `DB_PATH` | No | `/data/fleet.db` | SQLite file location (backend container) |

## Key REST endpoints

`GET /api/fleet` · `GET /api/zones` · `POST /api/zones` (Command) ·
`PUT /api/zones/{id}` (Command) · `DELETE /api/zones/{id}` (Command) · `GET /api/alerts` ·
`POST /api/alerts/{id}/ack` · `POST /api/alerts/{id}/resolve` ·
`POST /api/directives` (Command) · `POST /api/captain-response` ·
`POST /api/distress` · `GET /api/ships/{id}/route-options` (Command) ·
`POST /api/ships/route-select` (Command) · `POST /api/assistance` ·
`POST /api/assistance/respond` · `POST /api/advisor` (Command) ·
`GET /api/history/timeline` · `GET /api/history/snapshot?ts=`

Full interactive docs: `http://localhost:8000/docs` (FastAPI's built-in
OpenAPI UI).

## Validation and test commands

The repository includes tests; run them in the target environment rather than treating this README as a claim that they were already executed there.

Backend integration test (after `docker compose up --build`):

```bash
docker compose exec backend python test_integration.py
```

Frontend tests/build (inside the frontend container or a local Node 20+ environment):

```bash
npm ci
npm test
npm run build
```

The integration suite covers WebSocket ticks/movement, zone-triggered reroute,
Command role enforcement, directive → captain accept, directive → distress,
route options, assistance, advisor, playback, the stranded edge case, and
recovery after a blocking zone is removed.

The frontend smoke suite (`frontend/src/App.test.jsx`) covers both roles, ship
selection, directive controls, and the alert feed.

## What I'd do next with more time

- Real auth (JWT per captain/command account) instead of header-based roles
- WebSocket-driven captain inbox push (currently relies on the directive
  broadcast already reaching all clients, which works, but a per-captain
  targeted channel would be cleaner)
- Click-to-place waypoint on the map for `divert_to_waypoint` (currently
  lat/lng text inputs)
- A real, licensed coastline dataset instead of the simplified polygon

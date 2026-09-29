# Fixed Build Notes

## Root cause of the Docker build failure

The backend previously used `shapely==2.0.6` on Python 3.12. Shapely 2.x declares a NumPy runtime dependency, and the user's Docker package index could not provide a compatible NumPy distribution, producing:

`ERROR: Could not find a version that satisfies the requirement numpy<3,>=1.14`

The backend now uses Python 3.11 with `shapely==1.8.5.post1`. Shapely 1.8.5.post1 provides CPython 3.11 Linux wheels and does not add the NumPy runtime dependency used by the previous Shapely 2.x build.

The backend storage layer also now uses Python's standard-library `sqlite3`, removing the unnecessary `aiosqlite` dependency.

## Fresh run

From the project root:

```powershell
docker compose down -v
docker compose build --no-cache
docker compose up -d
docker compose ps
```

Open:

- http://localhost:3000 — frontend
- http://localhost:8000/docs — API documentation
- http://localhost:8000/api/health — health endpoint

## API key

Create `.env` in the project root from `.env.example` and set:

```env
GROQ_API_KEY=YOUR_GROQ_KEY
GROQ_MODEL=openai/gpt-oss-120b
```

The app still works without a Groq key by using the deterministic fallback parser/advisor.

## Integration test

With the backend running:

```powershell
docker compose exec backend python test_integration.py
```

The test covers the 15-ship WebSocket flow, zone create/edit/reroute, role enforcement, directives, captain acceptance, distress escalation, route candidates, assistance, advisor fallback, playback, stranded/recovery behavior.

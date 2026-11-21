# Maya–Hermes Bridge

A small FastAPI translation shim between **Maya Core** and **Hermes Agent**.

Maya Core's `HermesHttpTransport` speaks a compact `/runs` contract. Hermes's
gateway `api_server` platform speaks OpenAI-style `/v1/runs` with a different
payload and event vocabulary. This bridge owns that difference so neither side
has to special-case the other.

```text
Maya Core (:8080)  ──►  Bridge (:8765)  ──►  Hermes gateway (:8642)
   /runs                     /api/runs             /v1/runs
   /runs/{id}/events         /api/runs/{id}/events      /v1/runs/{id}/events
   /runs/{id}/cancel         /api/runs/{id}/cancel      /v1/runs/{id}/stop
   /health                   /api/health               /v1/health
```

## Translations

| Direction | Mapping |
|---|---|
| Submit request | Maya `task` → Hermes `input`; `metadata.system_prompt` → `instructions` |
| Submit response | Hermes `{"run_id": …}` passed through unchanged |
| Text stream events | Hermes `output` → Maya `delta` |
| Terminal events | `run.completed` / `run.failed` / `run.cancelled` (and legacy names): `output` mirrored into `result`, and into `error` for failures |
| Cancel | Maya `/cancel` → Hermes `/stop` |
| SSE framing | Upstream frames are buffered and re-emitted once per blank line, so `event:` and `data:` stay in one frame |

## Requirements

- Python 3.11+
- A running Hermes gateway with its `api_server` platform enabled
- `API_SERVER_KEY` available in the environment (`~/.hermes/.env` by convention).
  The bridge reads it per request, so rotating the key needs no code change.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn httpx
export API_SERVER_KEY="$(grep -E '^API_SERVER_KEY=' ~/.hermes/.env | cut -d= -f2-)"
nohup .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8765 > bridge.log 2>&1 &
```

Point Maya Core at it:

```bash
export MAYA_HERMES_ENABLED=true
export MAYA_HERMES_ENDPOINT=http://127.0.0.1:8765/api
```

Health check:

```bash
curl -s http://127.0.0.1:8765/api/health
# {"status": "ok", "platform": "hermes-agent", "version": "..."}
```

## Notes

- The bridge listens on loopback only and holds no credentials of its own; it
  forwards the caller's requests to Hermes with `API_SERVER_KEY`.
- A non-200 upstream event stream is surfaced as an `event: error` frame rather
  than an empty 200, so Maya Core can report a real cause.
- Maya Core still blocks approvals on the `POST …/approve` request for the
  duration of the run, so long tasks should be watched through
  `MAYA_HERMES_SUBMISSION_TIMEOUT` / `MAYA_HERMES_STREAM_TIMEOUT` and the
  delegation event stream.

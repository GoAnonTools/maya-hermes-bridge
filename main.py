from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import httpx
import os
import json

app = FastAPI()
HERMES_URL = "http://127.0.0.1:8642/v1"


def _auth_headers() -> dict:
    # Read at call time so rotating API_SERVER_KEY only needs an env change
    # (and a bridge restart), not a code edit.
    return {"Authorization": f"Bearer {os.environ.get('API_SERVER_KEY', '')}"}


# Terminal Hermes events: Maya Core's HermesExecutor builds its final result
# from these fields, so mirror Hermes's "output" into the expected keys.
_TERMINAL_EVENTS = {
    "run.completed": ("result",),
    "completed": ("result",),
    "run.failed": ("result", "error"),
    "failed": ("result", "error"),
    "run.cancelled": ("result",),
    "cancelled": ("result",),
}


def _translate_frame(event_name: str | None, data: dict) -> dict:
    """Translate one Hermes SSE JSON payload into Maya Core's expected shape."""
    data = dict(data)
    event = event_name or data.get("event") or data.get("type") or ""
    # Maya Core streams text via "delta"; Hermes puts it in "output".
    if "delta" not in data and "output" in data:
        data["delta"] = data["output"]
    for key in _TERMINAL_EVENTS.get(event, ()):
        if key not in data and "output" in data:
            data[key] = data["output"]
    return data


@app.post("/api/runs")
async def create_run(request: Request):
    body = await request.json()
    metadata = body.get("metadata") or {}

    # Translate Maya Core payload -> Hermes payload
    hermes_body = {
        "input": body.get("task") or "",
        "instructions": metadata.get("system_prompt") or "You are Maya.",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{HERMES_URL}/runs", json=hermes_body, headers=_auth_headers())
        if resp.status_code not in (200, 201, 202):
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request):
    async def event_generator():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{HERMES_URL}/runs/{run_id}/events", headers=_auth_headers()
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    yield f"event: error\ndata: {json.dumps({'error': body, 'status': response.status_code})}\n\n"
                    return

                event_name = None
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if line == "":
                        # Frame boundary: emit the transformed frame exactly once.
                        if data_lines:
                            raw = "\n".join(data_lines)
                            try:
                                payload = _translate_frame(event_name, json.loads(raw))
                                raw = json.dumps(payload)
                            except (json.JSONDecodeError, TypeError, AttributeError):
                                pass  # [DONE] or non-JSON: forward verbatim
                            if event_name:
                                yield f"event: {event_name}\n"
                            yield f"data: {raw}\n"
                        yield "\n"
                        event_name = None
                        data_lines = []
                        continue
                    if line.startswith(":"):
                        yield f"{line}\n"
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    if line.startswith("id:"):
                        yield f"{line}\n"
                        continue
                if data_lines:
                    raw = "\n".join(data_lines)
                    try:
                        raw = json.dumps(_translate_frame(event_name, json.loads(raw)))
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass
                    if event_name:
                        yield f"event: {event_name}\n"
                    yield f"data: {raw}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    # Translate Maya Core /cancel -> Hermes /stop
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{HERMES_URL}/runs/{run_id}/stop", headers=_auth_headers())
        return resp.json() if resp.content else {}


@app.get("/api/health")
async def health():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{HERMES_URL}/health", headers=_auth_headers())
        return resp.json()

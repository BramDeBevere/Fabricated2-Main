"""
Fabricated 2.0 — HUB ("the tiny room")
──────────────────────────────────────
Runs ON Projector 2's machine. This is the local, auto-stopping room that
every other device joins by typing the ROOM CODE. It is NOT a separate or
cloud server: it lives on the hub device and dies when you close it.

What it does (the coordination the old central server used to do):
  * holds the shared session (each visitor's separate progress, keyed by id)
  * folds the live camera presence in — now PUBLISHED by Projector 1 from
    its in-browser person detector instead of a Raspberry Pi
  * broadcasts a snapshot to every connected device (tablets + collective)

Join flow (enforced): the first message a device sends MUST be
    { "type": "join", "code": "<ROOM CODE>", "id": "...", "kind": "..." }
A wrong/missing code is denied and the socket is dropped.

Run it:
    python3 hub.py                 # auto-generates a code like VENZ-XXXX
    python3 hub.py --code=VENZ-2026  # use a specific code
    ROOM_CODE=VENZ-2026 python3 hub.py
Then open  http://<this-device-ip>:8000/  on every screen / tablet.
"""
import os
import sys
import secrets
import string
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ── Room code (the key every device types to join) ───────────────────────────
def _gen_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(4))
    return "VENZ-" + body


def _resolve_code() -> str:
    arg_code = next(
        (a.split("=", 1)[1].strip() for a in sys.argv[1:] if a.startswith("--code=")), ""
    )
    code = arg_code or (os.environ.get("ROOM_CODE") or "").strip() or _gen_code()
    return code.upper()


ROOM_CODE = _resolve_code()


# ── Camera state (published by Projector 1 in-browser; shape == old Pi POST) ─
_state: dict = {
    "timestamp": None,
    "frame_width": 0,
    "frame_height": 0,
    "person_count": 0,
    "people": [],
}


class BBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Center(BaseModel):
    x: float
    y: float


class Person(BaseModel):
    id: int
    confidence: Optional[float]
    bbox: BBox
    center: Center
    center_normalized: Center


class DetectionPayload(BaseModel):
    timestamp: str
    frame_width: int
    frame_height: int
    person_count: int
    people: List[Person]


# ── Session room ─────────────────────────────────────────────────────────────
_session_visitors: dict = {}     # id -> {id, kind, side, answer, progress, joined_at}
_session_clients: list = []      # authorized /session sockets
_session_state: dict = {}        # free-form aggregate relayed by the hub board
_ws_owner: dict = {}             # socket -> visitor id

# Compatibility camera channel (read-only; keeps dashboard.html working)
_cam_clients: list = []

CAMERA_FRESH_S = 1.5   # a frame older than this reads as "no live feed"


def _set_state_from_payload(payload: dict) -> None:
    global _state
    _state = {
        "timestamp": payload.get("timestamp"),
        "frame_width": int(payload.get("frame_width") or 0),
        "frame_height": int(payload.get("frame_height") or 0),
        "person_count": int(payload.get("person_count") or 0),
        "people": payload.get("people") or [],
    }


def _camera_presence() -> dict:
    """Translate the latest camera frame into an honest presence block."""
    try:
        people = int(_state.get("person_count") or 0)
    except (TypeError, ValueError):
        people = 0
    online = False
    ts = _state.get("timestamp")
    if ts:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
            online = 0 <= age <= CAMERA_FRESH_S
        except Exception:
            online = bool(ts)
    return {"online": bool(online), "people": people,
            "active": bool(online and people > 0), "at": ts}


def _session_snapshot() -> dict:
    visitors = list(_session_visitors.values())
    tablet_count = sum(1 for v in visitors if v.get("kind") == "tablet")
    projector_count = sum(1 for v in visitors
                          if v.get("kind") in ("projector", "projector1", "projector2"))
    camera_people = int(_state.get("person_count") or 0)
    camera = _camera_presence()
    return {
        "visitors": visitors,
        "players": visitors,                      # alias for older clients
        "tablet_count": tablet_count,
        "projector_count": projector_count,
        "camera_people": camera_people,
        "camera": camera,
        "participants": tablet_count + camera_people,
        "state": dict(_session_state),
        "code": ROOM_CODE,                        # so the hub board can display it
    }


async def broadcast_session() -> None:
    if not _session_clients:
        return
    message = json.dumps(_session_snapshot())
    dead = []
    for ws in _session_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _session_clients.remove(ws)


async def broadcast_camera() -> None:
    if not _cam_clients:
        return
    message = json.dumps(_state)
    dead = []
    for ws in _cam_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _cam_clients.remove(ws)


def _upsert_visitor(pid: str, kind: str) -> dict:
    visitor = _session_visitors.get(pid)
    if visitor is None:
        visitor = {
            "id": pid,
            "kind": kind,
            "side": None,
            "answer": None,
            "progress": {},
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }
        _session_visitors[pid] = visitor
    return visitor


# ── Config (lets the hub board / index page read the code + origin) ──────────
@app.get("/config")
async def config(request: Request):
    host = request.headers.get("host", "")
    return {"code": ROOM_CODE, "host": host, "origin": "http://" + host, "version": 2}


# ── Camera ingestion (kept for any external Pi that may still POST) ──────────
@app.post("/detections")
async def post_detections(payload: DetectionPayload):
    global _state
    _state = payload.model_dump()
    await broadcast_camera()
    await broadcast_session()
    return {"ok": True, "person_count": payload.person_count}


@app.get("/detections")
async def get_detections():
    return _state


# ── Compatibility raw camera channel (dashboard.html) ────────────────────────
@app.websocket("/ws")
async def ws_camera(websocket: WebSocket):
    await websocket.accept()
    _cam_clients.append(websocket)
    await websocket.send_text(json.dumps(_state))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _cam_clients:
            _cam_clients.remove(websocket)


# ── The shared room (join with the room code) ────────────────────────────────
@app.websocket("/session")
async def session_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        # 1) Authorize: the first message must be a "join" carrying the code.
        raw = await websocket.receive_text()
        try:
            msg = json.loads(raw)
        except Exception:
            msg = {}
        if msg.get("type") != "join":
            await websocket.send_text(json.dumps({"type": "denied", "reason": "expected-join"}))
            await websocket.close()
            return
        code = str(msg.get("code") or "").strip().upper()
        if ROOM_CODE and code != ROOM_CODE.upper():
            await websocket.send_text(json.dumps({"type": "denied", "reason": "bad-code"}))
            await websocket.close()
            return
        pid = str(msg.get("id") or "").strip()
        kind = msg.get("kind", "tablet")
        if not pid:
            await websocket.send_text(json.dumps({"type": "denied", "reason": "no-id"}))
            await websocket.close()
            return

        _upsert_visitor(pid, kind)
        _ws_owner[websocket] = pid
        _session_clients.append(websocket)
        await websocket.send_text(json.dumps(_session_snapshot()))

        # 2) Relayed messages
        while True:
            raw = await websocket.receive_text()
            try:
                m = json.loads(raw)
            except Exception:
                continue
            mtype = m.get("type")
            mpid = m.get("id")

            if mtype == "camera":
                # Live camera presence published by Projector 1 (in-browser YOLO).
                p = m.get("payload") or {}
                if p.get("people") is not None:
                    _set_state_from_payload(p)
                    await broadcast_camera()
            elif mtype == "join" and mpid:
                _upsert_visitor(mpid, m.get("kind", "tablet"))
            elif mtype == "vote" and mpid:
                _upsert_visitor(mpid, m.get("kind", "tablet"))["side"] = m.get("side")
            elif mtype == "answer" and mpid:
                _upsert_visitor(mpid, m.get("kind", "tablet"))["answer"] = m.get("answer")
            elif mtype == "progress" and mpid:
                _upsert_visitor(mpid, m.get("kind", "tablet"))["progress"] = dict(m.get("progress") or {})
            elif mtype == "progressAll":
                for entry in m.get("progresses") or []:
                    vid = entry.get("id")
                    if vid:
                        _upsert_visitor(vid, entry.get("kind", "tablet"))["progress"] = dict(entry.get("progress") or {})
            elif mtype == "state":
                _session_state.clear()
                _session_state.update(m.get("payload") or {})

            await broadcast_session()
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _session_clients:
            _session_clients.remove(websocket)
        pid = _ws_owner.pop(websocket, None)
        if pid:
            _session_visitors.pop(pid, None)
        await broadcast_session()


# ── Static pages (open http://<host>:8000/<page>) ────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    ip = "0.0.0.0"
    print("=" * 62)
    print("  FABRICATED 2.0  ·  HUB (local room)")
    print("=" * 62)
    print("  ROOM CODE :  %s" % ROOM_CODE)
    print("  Open       :  http://<this-device-ip>:8000/")
    print("  (Every screen / tablet opens that URL, picks its role,")
    print("   and types the room code to connect.)")
    print("=" * 62)
    uvicorn.run("hub:app", host=ip, port=8000, reload=False)

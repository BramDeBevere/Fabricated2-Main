import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Current detection state — replaced entirely on each POST from the Pi
_state: dict = {
    "timestamp": None,
    "frame_width": 0,
    "frame_height": 0,
    "person_count": 0,
    "people": [],
}

_clients: list[WebSocket] = []


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


@app.post("/detections")
async def post_detections(payload: DetectionPayload):
    global _state
    _state = payload.model_dump()

    message = json.dumps(_state)
    dead = []
    for ws in _clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.remove(ws)

    # Keep the shared installation session in sync with live camera presence.
    # (Additive — the YOLO endpoints above are otherwise unchanged.)
    try:
        await broadcast_session()
    except Exception:
        pass

    return {"ok": True, "person_count": payload.person_count}


@app.get("/detections")
async def get_detections():
    return _state


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.append(websocket)
    # Send current state immediately so new clients aren't blank
    await websocket.send_text(json.dumps(_state))
    try:
        while True:
            await websocket.receive_text()  # keeps the connection alive
    except WebSocketDisconnect:
        if websocket in _clients:
            _clients.remove(websocket)


# ── Shared installation session ──────────────────────────────────────────────
# A second, independent channel that binds Projector 2, the tablets and (if it
# chooses to) Projector 1 into one shared room. The YOLO camera pipeline above
# keeps running; its live person_count is folded into every session snapshot so
# the room always knows how many people are in frame. This block is additive
# and does not modify the YOLO state or /ws.
#
# Design goals:
#   * every tablet is a distinct "visitor" with its own unique id
#   * an UNLIMITED number of visitors can connect (no hard-coded upper bound)
#   * each visitor's progress is stored SEPARATELY, keyed by id
#   * disconnects are safe: a visitor's presence + progress are removed on
#     socket close, and the remaining visitors are unaffected

_session_visitors: dict[str, dict] = {}   # id -> {id, kind, side, progress, joined_at}
_session_clients: list[WebSocket] = []
_session_state: dict = {}                 # free-form aggregate relayed by Projector 2
_ws_owner: dict = {}                      # websocket -> visitor id


# ── YOLO → session camera-presence adapter ──────────────────────────────────
# The single, isolated bridge between the raw YOLO feed (POST /detections →
# _state) and the shared /session room. Strictly READ-ONLY: it turns the latest
# frame into a small, honest presence block and never writes into
# _session_visitors — so by construction the camera can never create/modify a
# visitor, cast a vote, or advance a walk. Selections and navigation remain the
# exclusive domain of the tablets.
#
# Projector 1 keeps its own untouched camera channel (WS /ws); this adapter is
# purely additive and changes nothing about what /ws delivers.

CAMERA_FRESH_S = 1.5   # a YOLO frame older than this reads as "no live feed"


def _camera_presence() -> dict:
    """Translate the latest raw YOLO state into a normalized presence block.

    online  — the Pi delivered a frame within CAMERA_FRESH_S (the feed is live)
    people  — most recent person count (0 when nothing is detected)
    active  — the feed is live AND someone is currently in frame
    at      — the ISO timestamp of the most recent frame
    """
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
            online = bool(ts)   # unparseable clock → trust that a frame arrived
    return {"online": bool(online), "people": people,
            "active": bool(online and people > 0), "at": ts}


def _session_snapshot() -> dict:
    visitors = list(_session_visitors.values())
    tablet_count = sum(1 for v in visitors if v.get("kind") == "tablet")
    projector_count = sum(1 for v in visitors if v.get("kind") == "projector")
    camera_people = int(_state.get("person_count") or 0)
    camera = _camera_presence()
    return {
        "visitors": visitors,
        # "players" alias kept so older clients keep working.
        "players": visitors,
        "tablet_count": tablet_count,
        "projector_count": projector_count,
        "camera_people": camera_people,   # unchanged: raw last count, for existing screens
        "camera": camera,                 # NEW: honest first-class presence block
        "participants": tablet_count + camera_people,
        "state": dict(_session_state),
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


def _upsert_visitor(pid: str, kind: str) -> dict:
    """Create or return a visitor record. If a tablet rejoins with the same id
    its prior progress is preserved, so reconnecting resumes where it left off."""
    visitor = _session_visitors.get(pid)
    if visitor is None:
        visitor = {
            "id": pid,
            "kind": kind,
            "side": None,
            "answer": None,          # the visitor's answer to the opening question
            "progress": {},
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }
        _session_visitors[pid] = visitor
    return visitor


@app.websocket("/session")
async def session_endpoint(websocket: WebSocket):
    await websocket.accept()
    _session_clients.append(websocket)
    # New clients immediately see the current room state.
    await websocket.send_text(json.dumps(_session_snapshot()))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            pid = msg.get("id")

            if mtype == "join" and pid:
                visitor = _upsert_visitor(pid, msg.get("kind", "tablet"))
                visitor["kind"] = msg.get("kind", visitor.get("kind", "tablet"))
                _ws_owner[websocket] = pid
            elif mtype == "vote" and pid:
                # Set this single visitor's current side (left/right/null).
                visitor = _upsert_visitor(pid, msg.get("kind", "tablet"))
                visitor["side"] = msg.get("side")
            elif mtype == "answer" and pid:
                # Store this visitor's answer to the opening question. Kept as
                # its own field (not in "progress") so Projector 2's progressAll
                # relay never overwrites it.
                _upsert_visitor(pid, msg.get("kind", "tablet"))["answer"] = msg.get("answer")
            elif mtype == "progress" and pid:
                # One visitor's progress (relayed by Projector 2).
                _upsert_visitor(pid, msg.get("kind", "tablet"))["progress"] = dict(msg.get("progress") or {})
            elif mtype == "progressAll":
                # Bulk per-visitor progress — Projector 2 is the tree authority
                # and pushes every visitor's separate progress at once.
                for entry in msg.get("progresses") or []:
                    vid = entry.get("id")
                    if vid:
                        _upsert_visitor(vid, entry.get("kind", "tablet"))["progress"] = dict(entry.get("progress") or {})
            elif mtype == "state":
                # Lightweight aggregate context relayed by Projector 2.
                _session_state.clear()
                _session_state.update(msg.get("payload") or {})

            await broadcast_session()
    except WebSocketDisconnect:
        if websocket in _session_clients:
            _session_clients.remove(websocket)
        # Safe teardown: drop ONLY this socket's visitor (and its progress).
        pid = _ws_owner.pop(websocket, None)
        if pid:
            _session_visitors.pop(pid, None)
        await broadcast_session()


# ── Static pages ─────────────────────────────────────────────────────────────
# Serve the installation pages from this folder so every screen can just
# open http://<host>:8000/<page>.html (additive; all routes above keep
# priority over the mount).
import os
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/", StaticFiles(directory=BASE_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=False)

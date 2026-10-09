"""
End-to-end isolation test for the shared /session room.

Simulates:
  - 4 tablets (v-t1 .. v-t4), each pushing its own independent rabbit-hole
    progress, exactly the message shape tablet.html sends
  - 1 Projector 2 (kind=projector) that only watches snapshots
  - the YOLO /ws channel (Projector 1's input) to prove the camera pipeline
    keeps running and is not disturbed by tablet traffic

Assertions:
  1. all 4 tablets + projector show up; tablet_count == 4
  2. one tablet's progress NEVER leaks into / overwrites another tablet
  3. concurrent progress updates from several tablets all land separately
  4. finishing on one tablet leaves the others exactly as they were
  5. a disconnect removes ONLY that tablet; the rest keep their progress
  6. POST /detections still flows to /ws (Projector 1 stays driven by camera)
     and only folds camera_people into the session snapshot
"""
import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS_SESSION = "ws://127.0.0.1:8000/session"
WS_YOLO = "ws://127.0.0.1:8000/ws"

FAILURES = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        FAILURES.append(name)


async def recv_snapshot(ws, timeout=3.0):
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


async def wait_for(p2, pred, what, timeout=5.0):
    """Drain queued broadcasts until a snapshot satisfies pred.

    Broadcasts are event-triggered, so a client may have stale snapshots
    queued ahead of the one we care about; read until the room actually
    shows the expected state (or time out)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    snap = None
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            check(what, False, f"timeout; last snapshot: {json.dumps(snap)[:400]}")
            return snap
        try:
            snap = await recv_snapshot(p2, timeout=remaining)
        except asyncio.TimeoutError:
            continue
        if pred(snap):
            check(what, True)
            return snap


class Tablet:
    def __init__(self, tid):
        self.id = tid
        self.ws = None

    async def join(self):
        self.ws = await websockets.connect(WS_SESSION)
        await self.ws.send(json.dumps(
            {"type": "join", "id": self.id, "kind": "tablet"}))
        await recv_snapshot(self.ws)  # initial snapshot

    async def progress(self, p):
        await self.ws.send(json.dumps({"type": "progress", "id": self.id,
                                       "progress": p}))

    async def answer(self, text):
        await self.ws.send(json.dumps({"type": "answer", "id": self.id,
                                       "answer": text}))


async def main():
    t1, t2, t3, t4 = (Tablet(f"v-t{i}") for i in (1, 2, 3, 4))

    # Projector 2: display-only observer
    p2 = await websockets.connect(WS_SESSION)
    await p2.send(json.dumps({"type": "join", "id": "projector2", "kind": "projector"}))
    snap = await wait_for(p2, lambda s: s["tablet_count"] == 0 and
                          any(v["id"] == "projector2" for v in s["visitors"]),
                          "P2 connected, empty room ok")

    # Projector 1 stands in: its live input is the YOLO /ws channel
    p1 = await websockets.connect(WS_YOLO)
    await recv_snapshot(p1)  # initial YOLO state push

    async def join_all():
        await asyncio.gather(*[t.join() for t in (t1, t2, t3, t4)])

    await join_all()
    snap = await wait_for(p2, lambda s: s["tablet_count"] == 4,
                          "all 4 tablets visible to projector 2 (tablet_count == 4)")
    ids = {v["id"] for v in snap["visitors"]}
    check("all 4 tablet ids present", ids >= {"v-t1", "v-t2", "v-t3", "v-t4"}, str(ids))

    # ── 1 visitor acts; the other 3 must be untouched ─────────────────────
    p_t1 = {"started": True, "inTopicBracket": False,
            "options": ["Shark", "Dolphin"], "path": ["Nature", "Water"],
            "round": 2, "topic": "Nature", "finished": False, "choice": None}
    await t1.progress(p_t1)
    await t1.answer("caracas")
    snap = await wait_for(p2, lambda s: any(v["id"] == "v-t1"
                                            and v["progress"] == p_t1
                                            and v.get("answer") == "caracas"
                                            for v in s["visitors"]),
                          "tablet1 progress + answer visible on projector 2")
    by_id = {v["id"]: v for v in snap["visitors"]}
    check("tablet1 progress stored", by_id["v-t1"]["progress"] == p_t1,
          json.dumps(by_id["v-t1"]["progress"]))
    check("tablet1 answer stored", by_id["v-t1"].get("answer") == "caracas")
    for other in ("v-t2", "v-t3", "v-t4"):
        check(f"{other} progress NOT overwritten",
              by_id[other]["progress"] == {},
              json.dumps(by_id[other]["progress"]))

    # ── concurrent updates from 3 different tablets ───────────────────────
    p_t2 = {"started": True, "options": ["Cacao", "Arepa"], "path": ["Culture"],
            "round": 1, "topic": "Culture", "finished": False, "choice": None}
    p_t3 = {"started": True, "options": ["Tepuis", "Barranca"], "path": ["History", "Ancient"],
            "round": 1, "topic": "History", "finished": False, "choice": None}
    p_t4 = {"started": True, "options": ["Oil boom", "Shortages"], "path": ["Politics", "1970s"],
            "round": 1, "topic": "Politics", "finished": False, "choice": None}
    await asyncio.gather(t2.progress(p_t2), t3.progress(p_t3), t4.progress(p_t4))
    want = {"v-t1": ["Nature", "Water"], "v-t2": ["Culture"],
            "v-t3": ["History", "Ancient"], "v-t4": ["Politics", "1970s"]}
    snap = await wait_for(p2, lambda s: all(
        next((v for v in s["visitors"] if v["id"] == tid), None) is not None
        and next(v for v in s["visitors"] if v["id"] == tid)["progress"].get("path") == path
        for tid, path in want.items()),
                          "concurrent: all 4 tablets show their own distinct paths")
    by_id = {v["id"]: v for v in snap["visitors"]}
    check("concurrent: tablet2 has its own path",
          by_id["v-t2"]["progress"].get("path") == ["Culture"])
    check("concurrent: tablet3 has its own path",
          by_id["v-t3"]["progress"].get("path") == ["History", "Ancient"])
    check("concurrent: tablet4 has its own path",
          by_id["v-t4"]["progress"].get("path") == ["Politics", "1970s"])
    check("concurrent: tablet1 still on Nature/Water",
          by_id["v-t1"]["progress"].get("path") == ["Nature", "Water"])

    # ── one tablet finishes; others unchanged ─────────────────────────────
    p_t1_done = {"started": True, "options": ["Shark", "Dolphin"],
                 "path": ["Nature", "Water", "Shark"], "round": 3,
                 "topic": "Nature", "finished": True, "choice": "Shark"}
    await t1.progress(p_t1_done)
    snap = await wait_for(p2, lambda s: any(v["id"] == "v-t1" and
                                            v["progress"].get("finished") is True
                                            for v in s["visitors"]),
                          "tablet1 finished state visible on projector 2")
    by_id = {v["id"]: v for v in snap["visitors"]}
    check("tablet1 marked finished/Shark",
          by_id["v-t1"]["progress"].get("finished") is True
          and by_id["v-t1"]["progress"].get("choice") == "Shark")
    check("tablet2 NOT clobbered by tablet1 finishing",
          by_id["v-t2"]["progress"].get("finished") is False
          and by_id["v-t2"]["progress"].get("path") == ["Culture"])

    # ── camera pipeline still runs alongside (Projector 1 stays live) ─────
    det = {"timestamp": "2026-10-09T12:00:00Z", "frame_width": 1280,
           "frame_height": 720, "person_count": 3,
           "people": [{"id": i, "confidence": 0.9,
                       "bbox": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
                       "center": {"x": 5, "y": 5},
                       "center_normalized": {"x": 0.3 + 0.2 * i, "y": 0.5}}
                      for i in range(3)]}
    async with httpx.AsyncClient() as http:
        r = await http.post(f"{BASE}/detections", json=det)
        check("POST /detections accepted", r.status_code == 200)
    raw = await asyncio.wait_for(p1.recv(), timeout=3.0)
    check("Projector 1 (/ws) still receives camera state",
          json.loads(raw)["person_count"] == 3)
    snap = await wait_for(p2, lambda s: s["camera_people"] == 3,
                          "session snapshot carries camera_people=3")
    by_id = {v["id"]: v for v in snap["visitors"]}
    check("camera update did NOT touch tablet progress",
          by_id["v-t1"]["progress"].get("choice") == "Shark"
          and by_id["v-t3"]["progress"].get("path") == ["History", "Ancient"])

    # ── disconnect safety: drop tablet2 only ──────────────────────────────
    await t2.ws.close()
    snap = await wait_for(p2, lambda s: s["tablet_count"] == 3 and
                          all(v["id"] != "v-t2" for v in s["visitors"]),
                          "tablet2 removed after disconnect; 3 remain")
    ids = {v["id"] for v in snap["visitors"]}
    check("tablets 1/3/4 survive tablet2 disconnect",
          {"v-t1", "v-t3", "v-t4"} <= ids, str(ids))
    by_id = {v["id"]: v for v in snap["visitors"]}
    check("surviving progress intact after disconnect",
          by_id["v-t1"]["progress"].get("choice") == "Shark"
          and by_id["v-t3"]["progress"].get("path") == ["History", "Ancient"]
          and by_id["v-t4"]["progress"].get("path") == ["Politics", "1970s"])
    check("tablet_count back to 3", snap["tablet_count"] == 3,
          str(snap["tablet_count"]))

    # ── rejoin with same id resumes (persistent tablet id) ────────────────
    await t2.join()
    snap = await wait_for(p2, lambda s: s["tablet_count"] == 4,
                          "tablet2 rejoins; room back to 4 tablets")
    ids = {v["id"] for v in snap["visitors"]}
    check("tablet2 rejoins as v-t2", "v-t2" in ids, str(ids))

    for t in (t1, t3, t4):
        await t.ws.close()
    await p2.close()
    await p1.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):", *FAILURES, sep="\n  - ")
        sys.exit(1)
    print("ALL CHECKS PASSED")


asyncio.run(main())

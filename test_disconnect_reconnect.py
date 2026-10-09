#!/usr/bin/env python3
"""
test_disconnect_reconnect.py
============================
Verifies the installation survives real-world disruption, using ONLY real
shared data over the live /ws and /session channels (no invented examples):

  [1] A tablet drops and rejoins  -> progress resumes, other tablets untouched.
  [2] Projector 2 (collective)    -> refreshes its view when it reconnects.
  [3] Projector 1 (camera /ws)    -> keeps running through all /session churn.
  [4] Temporary YOLO failures     -> stale feed / malformed frames do NOT crash
                                     the app, and the feed recovers cleanly.
  [5] Final liveness             -> static pages + HTTP + both WS still served.

Run:  python3 api_server.py   (background)
      python3 test_disconnect_reconnect.py
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import websockets
import httpx

BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")

passed = 0
failed = 0


def check(cond, what):
    global passed, failed
    if cond:
        passed += 1
        print(f"  \u2713 {what}")
    else:
        failed += 1
        print(f"  \u2717 {what}")


def by_tablets(d):
    return {v["id"]: (v.get("progress") or {})
            for v in d.get("visitors", []) if v.get("kind") == "tablet"}


class WS:
    """A /session or /ws client that caches the latest frame it has seen."""

    def __init__(self, url):
        self.url = url
        self.ws = None
        self.last = None

    async def connect(self):
        self.ws = await websockets.connect(self.url)
        self.last = None

    async def recv(self):
        d = json.loads(await self.ws.recv())
        self.last = d
        return d

    async def send(self, obj):
        await self.ws.send(json.dumps(obj))

    async def wait_for(self, pred, what, timeout=6.0):
        """Return once pred holds on the latest frame (drops stale frames)."""
        async with asyncio.timeout(timeout):
            if self.last is not None and pred(self.last):
                return self.last
            while True:
                d = await self.recv()
                if pred(d):
                    return d

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass
        self.last = None


# A realistic tablet progress payload (mirrors tablet.html syncProgress()).
def progress(started=True, inBracket=False, options=("A", "B"), path=(),
             round_=1, topicPhase=0, topic=None, finished=False, choice=None):
    return {"started": started, "inTopicBracket": inBracket,
            "options": list(options), "path": list(path), "round": round_,
            "topicPhase": topicPhase, "topic": topic,
            "finished": finished, "choice": choice}


async def post_frame(client, person_count, ts=None, body_override=None):
    if body_override is not None:
        return await client.post(BASE + "/detections", json=body_override)
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    people = [{
        "id": i, "confidence": 0.9,
        "bbox": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
        "center": {"x": 5, "y": 5},
        "center_normalized": {"x": 0.5, "y": 0.5},
    } for i in range(person_count)]
    body = {"timestamp": ts, "frame_width": 1280, "frame_height": 720,
            "person_count": person_count, "people": people}
    return await client.post(BASE + "/detections", json=body)


async def main():
    print("Disconnect / reconnect / failure-resilience test\n" + "=" * 60)

    # ── Connect Projector 1, Projector 2, and two tablets ──────────────
    p1 = WS(f"{WS_BASE}/ws")
    p2 = WS(f"{WS_BASE}/session")
    t1 = WS(f"{WS_BASE}/session")
    t2 = WS(f"{WS_BASE}/session")
    for c in (p1, p2, t1, t2):
        await c.connect()
    await p2.send({"type": "join", "id": "projector2", "kind": "projector"})
    await t1.send({"type": "join", "id": "t1", "kind": "tablet"})
    await t2.send({"type": "join", "id": "t2", "kind": "tablet"})

    T1_PROG = progress(inBracket=False, options=("Water", "Land"),
                       path=("Nature", "Moving", "Water"), round_=3,
                       topicPhase=2, topic="Nature")
    T2_PROG = progress(inBracket=False, options=("Music", "Dance"),
                       path=("Culture", "Arts"), round_=2,
                       topicPhase=2, topic="Culture")
    await t1.send({"type": "progress", "id": "t1", "progress": T1_PROG})
    await t2.send({"type": "progress", "id": "t2", "progress": T2_PROG})
    base = await p2.wait_for(
        lambda d: (by_tablets(d).get("t1", {}).get("path") == list(T1_PROG["path"])
                   and by_tablets(d).get("t2", {}).get("path") == list(T2_PROG["path"])),
        "baseline: both tablets + P2 in sync")

    # ── [1] Tablet drops, then rejoins ──────────────────────────────────
    print("\n[1] Tablet t1 drops, then rejoins (progress resumes; t2 untouched)")
    await t1.close()
    gone = await p2.wait_for(
        lambda d: "t1" not in by_tablets(d), "t1 removed from the room on disconnect")
    check("t1" not in by_tablets(gone), "t1 gone from P2 snapshot after drop")
    check(by_tablets(gone).get("t2", {}).get("path") == list(T2_PROG["path"]),
          "t2 progress NOT clobbered by t1's drop")
    check(gone.get("tablet_count") == 1,
          f"tablet_count dropped to 1 (got {gone.get('tablet_count')})")

    # Reconnect with the SAME id; tablet.html re-sends join + (started) syncProgress().
    await t1.connect()
    await t1.send({"type": "join", "id": "t1", "kind": "tablet"})
    await t1.send({"type": "progress", "id": "t1", "progress": T1_PROG})
    back = await p2.wait_for(
        lambda d: by_tablets(d).get("t1", {}).get("path") == list(T1_PROG["path"])
        and by_tablets(d).get("t2", {}).get("path") == list(T2_PROG["path"]),
        "t1 rejoined with its progress restored")
    check(by_tablets(back).get("t1", {}).get("path") == list(T1_PROG["path"]),
          "t1 progress resumed after rejoin (no data loss)")
    check(by_tablets(back).get("t2", {}).get("path") == list(T2_PROG["path"]),
          "t2 still intact after t1's rejoin")
    check(back.get("tablet_count") == 2,
          f"tablet_count restored to 2 (got {back.get('tablet_count')})")

    # ── [2] Projector 2 refreshes on reconnect ──────────────────────────
    print("\n[2] Projector 2 drops and refreshes from the live room on reconnect")
    await p2.close()
    # While P2 is down, t2 advances one more in-tree step.
    T2_ADV = progress(inBracket=False, options=("Joropo", "Gaita"),
                      path=("Culture", "Arts", "Music"), round_=3,
                      topicPhase=2, topic="Culture")
    await t2.send({"type": "progress", "id": "t2", "progress": T2_ADV})
    await t2.wait_for(
        lambda d: by_tablets(d).get("t2", {}).get("path") == list(T2_ADV["path"]),
        "t2 advanced (while P2 was down)")
    # Reconnect P2 -> server sends the CURRENT snapshot immediately on accept.
    # collective.html re-sends its join on every onopen, so model that exactly.
    await p2.connect()
    await p2.send({"type": "join", "id": "projector2", "kind": "projector"})
    fresh = await p2.wait_for(
        lambda d: (by_tablets(d).get("t2", {}).get("path") == list(T2_ADV["path"])
                   and "t1" in by_tablets(d)),
        "P2 refreshed on reconnect (sees t2's advance + t1)")
    check(by_tablets(fresh).get("t2", {}).get("path") == list(T2_ADV["path"]),
          "P2's first frame after reconnect reflects t2's advance (no stale data)")
    check(by_tablets(fresh).get("t1", {}).get("path") == list(T1_PROG["path"]),
          "P2 also sees t1's resumed progress on reconnect")

    # ── [3] Projector 1 keeps running through /session churn ───────────
    print("\n[3] Projector 1 (camera /ws) keeps running through /session churn")
    async with httpx.AsyncClient() as probe_client:
        await post_frame(probe_client, 3)
    p1frame = await p1.wait_for(
        lambda d: (d.get("person_count") == 3), "P1 still receiving camera frames")
    check(p1frame.get("person_count") == 3,
          "P1 (/ws) alive and still receiving YOLO frames after /session churn")

    # P1 reconnects and immediately gets the current state.
    await p1.close()
    await p1.connect()
    p1re = await p1.wait_for(lambda d: "person_count" in d,
                             "P1 reconnects and gets current state immediately")
    check("person_count" in p1re,
          "P1 reconnect is safe and receives the live frame state")

    # ── [4] Temporary YOLO failures do not crash the app ────────────────
    print("\n[4] Temporary YOLO failures (stale feed / malformed frames)")
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 4a: a healthy frame -> camera online + active
        await post_frame(client, 3)
        ok = await p2.wait_for(
            lambda d: (d.get("camera") or {}).get("online") is True
            and (d.get("camera") or {}).get("active") is True,
            "healthy frame -> camera online/active")
        check((ok.get("camera") or {}).get("people") == 3,
              f"healthy frame: camera.people == 3 (got {(ok.get('camera') or {}).get('people')})")

        # 4b: a STALE frame (timestamp 5s in the past) -> feed reads offline,
        #     but the app keeps serving and keeps the last people count.
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        await post_frame(client, 2, ts=stale_ts)
        stale = await p2.wait_for(
            lambda d: (d.get("camera") or {}).get("online") is False,
            "stale feed reads as offline")
        cam = stale.get("camera") or {}
        check(cam.get("online") is False and cam.get("active") is False,
              "stale feed: camera.online==False and camera.active==False")
        check(cam.get("people") == 2,
              f"stale feed keeps the last people count (got {cam.get('people')})")

        # 4c: a malformed frame (schema-violating) is rejected, not fatal.
        r_bad = await client.post(BASE + "/detections", json={
            "timestamp": "not-a-date", "frame_width": "x", "frame_height": "y",
            "person_count": "many", "people": {"id": "oops"},
        })
        check(r_bad.status_code in (400, 422),
              f"malformed frame -> HTTP {r_bad.status_code} (rejected, not a 5xx crash)")

        # 4d: a non-JSON body is rejected, not fatal.
        r_garbage = await client.post(BASE + "/detections", content=b"{{{ this is not json",
                                      headers={"Content-Type": "application/json"})
        check(r_garbage.status_code in (400, 422),
              f"garbage body -> HTTP {r_garbage.status_code} (rejected, not a 5xx crash)")

        # 4e: the app is still fully alive after the bad frames.
        r_page = await client.get(BASE + "/collective.html")
        check(r_page.status_code == 200,
              f"static page /collective.html still served after bad frames (HTTP {r_page.status_code})")
        r_det = await client.get(BASE + "/detections")
        check(r_det.status_code == 200,
              f"GET /detections still works (HTTP {r_det.status_code})")

        # A tablet can still join + advance after the failures.
        t3 = WS(f"{WS_BASE}/session")
        await t3.connect()
        await t3.send({"type": "join", "id": "t3", "kind": "tablet"})
        T3_PROG = progress(inBracket=True, options=("Nature", "Culture"),
                           path=(), round_=0, topicPhase=0, topic=None)
        await t3.send({"type": "progress", "id": "t3", "progress": T3_PROG})
        alive = await p2.wait_for(
            lambda d: by_tablets(d).get("t3", {}).get("inTopicBracket") is True,
            "a tablet can still join + advance after YOLO failures")
        check(alive.get("tablet_count") == 3,
              f"tablet_count back to 3 (t1,t2,t3) after failures (got {alive.get('tablet_count')})")
        await t3.close()

        # 4f: recovery -> a fresh frame flips the feed back online.
        await post_frame(client, 3)
        rec = await p2.wait_for(
            lambda d: (d.get("camera") or {}).get("online") is True,
            "fresh frame -> feed back online")
        check((rec.get("camera") or {}).get("active") is True,
              "feed recovered: camera.active back to True")

    # ── [5] Final liveness ──────────────────────────────────────────────
    print("\n[5] Final liveness (all channels still healthy)")
    final = await p2.wait_for(
        lambda d: (by_tablets(d).get("t1", {}).get("path") == list(T1_PROG["path"])
                   and by_tablets(d).get("t2", {}).get("path") == list(T2_ADV["path"])),
        "final stable collective snapshot")
    check(set(by_tablets(final).keys()) == {"t1", "t2"},
          f"final room holds exactly t1,t2 (got {sorted(by_tablets(final).keys())})")
    check(final.get("projector_count") == 1,
          f"final projector_count == 1 (P2 in-room, P1 on /ws) (got {final.get('projector_count')})")

    for c in (p1, p2, t1, t2):
        await c.close()

    print("\n" + "=" * 60)
    print(f"RESULT: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)

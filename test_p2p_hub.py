"""
End-to-end test for the P2P hub (hub.py) — the local "room" that every device
joins by typing the ROOM CODE. This exercises the EXACT protocol the pages use:

  [1] wrong code  -> the hub denies and drops the socket
  [2] right code  -> the hub admits the device and sends the room snapshot
  [3] tablet progress relay (separate progress per id, keyed by id)
  [4] Projector 1 in-browser camera publish -> folded into camera presence
  [5] multiple devices -> tablet_count / projector_count are correct
  [6] safe disconnect -> only that device's visitor is removed

Run:  python3 test_p2p_hub.py
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import httpx

HOST = "127.0.0.1"
PORT = 8000
CODE = "VENZ-TEST"
BASE = f"http://{HOST}:{PORT}"
WS = f"ws://{HOST}:{PORT}/session"

try:
    import websockets
except Exception as e:
    print("websockets lib required:", e)
    sys.exit(2)


def port_free() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((HOST, PORT))
        s.close()
        return True
    except OSError:
        return False


async def wait_ready(timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(BASE + "/config", timeout=2)
                if r.status_code == 200:
                    return r.json()
        except Exception:
            pass
        await asyncio.sleep(0.25)
    raise RuntimeError("hub never became ready")


async def recv_one(ws, timeout=5):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def wait_snapshot(ws, pred, timeout=5):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        d = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        last = d
        if "type" in d:          # a control message, not a snapshot
            continue
        if pred(d):
            return d
    raise AssertionError("no snapshot satisfied predicate; last=" + json.dumps(last)[:200])


PASS = 0
def ok(label):
    global PASS
    PASS += 1
    print("  PASS  " + label)


def main():
    if not port_free():
        print("port 8000 busy — please free it and re-run")
        sys.exit(3)

    proc = subprocess.Popen([sys.executable, "hub.py", f"--code={CODE}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        asyncio.run(run())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    print("\n%d checks passed." % PASS)


async def run():
    cfg = await wait_ready()
    ok("[0] /config reachable, code=%s" % cfg.get("code"))
    assert cfg.get("code") == CODE, "code mismatch: %r" % cfg.get("code")

    # ── [1] wrong code -> denied + dropped ───────────────────────────────────
    async with websockets.connect(WS) as bad:
        await bad.send(json.dumps({"type": "join", "code": "VENZ-WRONG", "id": "t-bad", "kind": "tablet"}))
        m = await recv_one(bad)
        assert m.get("type") == "denied", m
        ok("[1] wrong code is denied (%s)" % m.get("reason"))
        try:
            await asyncio.wait_for(bad.recv(), 2)
            raise AssertionError("expected the bad-code socket to be closed")
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            ok("[1] wrong-code socket is dropped after denial")

    # ── [2] right code -> admitted, gets snapshot ────────────────────────────
    async with websockets.connect(WS) as t1:
        await t1.send(json.dumps({"type": "join", "code": CODE, "id": "tablet-1", "kind": "tablet"}))
        snap = await wait_snapshot(t1, lambda d: True)
        assert "visitors" in snap and "camera" in snap, "snapshot shape"
        assert any(v["id"] == "tablet-1" for v in snap["visitors"]), "tablet-1 in room"
        ok("[2] right code admitted + snapshot has the visitor")

        # ── [3] progress relay ───────────────────────────────────────────────
        await t1.send(json.dumps({"type": "progress", "id": "tablet-1",
                                  "progress": {"started": True, "path": ["Nature", "Moving"],
                                               "round": 2, "topicPhase": 2, "finished": False}}))
        await wait_snapshot(t1, lambda d:
            next((v for v in d["visitors"] if v["id"] == "tablet-1"), {}).get("progress", {}).get("path") == ["Nature", "Moving"])
        ok("[3] tablet progress is relayed back in the snapshot")

        # ── [4] Projector 1 in-browser camera publish ────────────────────────
        async with websockets.connect(WS) as p1:
            await p1.send(json.dumps({"type": "join", "code": CODE, "id": "projector1-stage", "kind": "projector1"}))
            await wait_snapshot(p1, lambda d: True)
            frame = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "frame_width": 1280, "frame_height": 720, "person_count": 5,
                "people": [
                    {"id": i, "confidence": 0.9,
                     "bbox": {"x1": i * 100, "y1": 0, "x2": i * 100 + 80, "y2": 400},
                     "center": {"x": i * 100 + 40, "y": 200},
                     "center_normalized": {"x": (i * 100 + 40) / 1280, "y": 200 / 720}}
                    for i in range(5)
                ],
            }
            await p1.send(json.dumps({"type": "camera", "payload": frame}))
            snap = await wait_snapshot(t1, lambda d: (d.get("camera") or {}).get("online") and d.get("camera_people") == 5)
            ok("[4] P1 camera publish -> camera.online & camera_people=5")

            # ── [5] counts (2 tablets expected after second join) ────────────
            async with websockets.connect(WS) as t2:
                await t2.send(json.dumps({"type": "join", "code": CODE, "id": "tablet-2", "kind": "tablet"}))
                snap = await wait_snapshot(t2, lambda d: d.get("tablet_count") == 2)
                ok("[5] tablet_count=2 with two tablets + P1 in the room")
                assert snap.get("projector_count") == 1, "projector_count=%r" % snap.get("projector_count")
                ok("[5] projector_count=1 (P1 is counted as a projector)")

            # ── [6] safe disconnect: drop t2, tablet_count back to 1 ─────────
            await t2.close()
            snap = await wait_snapshot(t1, lambda d: d.get("tablet_count") == 1)
            ids = [v["id"] for v in snap["visitors"]]
            assert "tablet-2" not in ids, "tablet-2 should be gone: %r" % ids
            ok("[6] disconnect removes only that tablet (tablet_count back to 1)")

    # ── [7] camera staleness: an old frame reads as offline ──────────────────
    async with websockets.connect(WS) as t3:
        await t3.send(json.dumps({"type": "join", "code": CODE, "id": "tablet-3", "kind": "tablet"}))
        await wait_snapshot(t3, lambda d: True)
        old = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(-10 * 60)),
               "frame_width": 1, "frame_height": 1, "person_count": 0, "people": []}
        await t3.send(json.dumps({"type": "camera", "payload": old}))
        snap = await wait_snapshot(t3, lambda d: (d.get("camera") or {}).get("online") is False)
        ok("[7] a stale frame reads as camera.online=False")


if __name__ == "__main__":
    main()

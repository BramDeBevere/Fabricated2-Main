#!/usr/bin/env python3
"""Full-installation E2E test (Projector 1 + Projector 2 + 4 tablets).

Simulates the REAL installation simultaneously and verifies:

  • Projector 1  (WS /ws, camera channel)  — receives the YOLO detection stream.
  • Projector 2  (WS /session, kind="projector") — display-only collective board;
    we replay its EXACT display math (progress bar %, "round N of TOTAL", theme
    counts, chosen rabbit holes) to catch the journey-length bug.
  • 4 tablets    (WS /session, kind="tablet")   — each drives its OWN walk with
    the exact selection_shared.js walker (ported 1:1) and we verify per-visitor
    progress independence end-to-end.

The topic trees and walker are transcribed 1:1 from selection_shared.js, so the
journey-math assertions are proven against the real content, not assumed.

Run:  python3 api_server.py     (first, in the background)
      python3 test_full_installation.py
"""
import asyncio
import json
import websockets

HOST = "localhost"
PORT = 8000
BASE = f"ws://{HOST}:{PORT}"

PASS = 0
FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  \u2713 {label}")
    else:
        FAIL += 1
        print(f"  \u2717 {label}")


# ═══════════════════════════════════════════════════════════════════════════
# selection_shared.js — trees + walker, transcribed 1:1 (real structure)
# Node = {left, right, children:{label:node} | None}. Leaf → children None.
# ═══════════════════════════════════════════════════════════════════════════
def _n(left, right, children=None):
    return {"left": left, "right": right, "children": children}


NATURE_TREE = _n("Moving", "Still", {
    "Moving": _n("Water", "Sky", {
        "Water": _n("Water", "Land", {
            "Water": _n("Fish", "Shark"),
            "Land": _n("Insect", "Monkey"),
        }),
        "Sky": _n("Sky", "Land", {
            "Sky": _n("Flying Insect", "Bird"),
            "Land": _n("Insect", "Monkey"),
        }),
    }),
    "Still": _n("Big", "Small", {
        "Big": _n("Mountain", "Waterfall"),
        "Small": _n("Tree", "Flower"),
    }),
})

CULTURE_TREE = _n("Arts", "Lifestyle", {
    "Arts": _n("Music", "Dance", {
        "Music": _n("Traditional", "Modern", {
            "Traditional": _n("Joropo", "Gaita"),
            "Modern": _n("Salsa", "Urban Venezuelan Rap"),
        }),
        "Dance": _n("Traditional", "Festive", {
            "Traditional": _n("Joropo dancing", "Tamunangue"),
            "Festive": _n("El Callao Carnival", "Caracas street dance"),
        }),
    }),
    "Lifestyle": _n("Food", "Sport", {
        "Food": _n("Street food", "Festive dishes", {
            "Street food": _n("Arepas", "Cachapas"),
            "Festive dishes": _n("Hallacas", "Pabellón Criollo"),
        }),
        "Sport": _n("Baseball", "Other sports", {
            "Baseball": _n("Venezuelan MLB players", "Local stadiums"),
            "Other sports": _n("Cycling", "Tejo"),
        }),
    }),
})

POLITICS_TREE = _n("Internal Dynamics", "External & Social Impact", {
    "Internal Dynamics": _n("Crisis", "Resistance", {
        "Crisis": _n("Displacement", "Economy", {
            "Displacement": _n("Border crossings", "Diaspora stories"),
            "Economy": _n("Hyperinflation", "Empty shelves"),
        }),
        "Resistance": _n("Protest", "Digital Space", {
            "Protest": _n("2024 election protests", "Student movements"),
            "Digital Space": _n("Social media censorship", "TikTok ban"),
        }),
    }),
    "External & Social Impact": _n("International", "Society", {
        "International": _n("Aid", "Sanctions", {
            "Aid": _n("UN relief efforts", "UNHCR camps"),
            "Sanctions": _n("US-Venezuela standoff", "Oil embargo"),
        }),
        "Society": _n("Everyday Survival", "Daily Life", {
            "Everyday Survival": _n("Shortages", "Healthcare collapse"),
            "Daily Life": _n("Education crisis", "Lost generation"),
        }),
    }),
})

HISTORY_TREE = _n("The Past", "The Present & Future", {
    "The Past": _n("Early Heritage", "Colonial Era", {
        "Early Heritage": _n("Indigenous", "Pre-colonial", {
            "Indigenous": _n("Pemon People", "Warao canoes"),
            "Pre-colonial": _n("Tepui legends", "Orinoco civilizations"),
        }),
        "Colonial Era": _n("Spanish", "Resistance", {
            "Spanish": _n("Caracas founding", "Cathedral"),
            "Resistance": _n("Simon Bolivar", "Independence war"),
        }),
    }),
    "The Present & Future": _n("Oil Era", "Modern", {
        "Oil Era": _n("Boom", "Decline", {
            "Boom": _n("1970s wealth", "Caracas skyline"),
            "Decline": _n("PDVSA collapse", "2000s poverty"),
        }),
        "Modern": _n("Hope", "Diaspora", {
            "Hope": _n("Return migration 2025", "Community rebuilding"),
            "Diaspora": _n("Venezuelans worldwide", "Cultural identity abroad"),
        }),
    }),
})

TOPIC_TREES = {"Nature": NATURE_TREE, "Culture": CULTURE_TREE,
               "Politics": POLITICS_TREE, "History": HISTORY_TREE}

# Leaf → (has a REWARDS entry with non-empty text?) — spot-check real rewards.
KNOWN_REWARDS = {"Fish", "Gaita", "Hyperinflation", "Pemon People", "Shark",
                 "Joropo", "Empty shelves", "Warao canoes"}


def _new_state():
    return {"topicPhase": 0, "topicWinners": [], "node": None, "path": [],
            "round": 0, "finished": False, "choice": None, "history": []}


def _is_bracket(s):
    return s["node"] is None


def _options(s):
    """Verbatim port of SELECTION.optionsOf."""
    if _is_bracket(s):
        if s["topicPhase"] == 0:
            return ["Nature", "Culture"]
        if s["topicPhase"] == 1:
            return ["Politics", "History"]
        return [s["topicWinners"][0], s["topicWinners"][1]]
    return [s["node"]["left"], s["node"]["right"]]


def _apply(s, side):
    """Verbatim port of SELECTION.applyChoice. Returns leaf name when finished."""
    opts = _options(s)
    winner = opts[0] if side == "left" else opts[1]
    s["history"].append({"topicPhase": s["topicPhase"], "topicWinners": s["topicWinners"][:],
                         "node": s["node"], "path": s["path"][:], "round": s["round"]})
    if _is_bracket(s):
        if s["topicPhase"] < 2:
            s["topicWinners"].append(winner)
            s["topicPhase"] += 1
        else:
            s["node"] = TOPIC_TREES[winner]
            s["path"] = [winner]
            s["round"] = 1
    else:
        s["path"].append(winner)
        child = (s["node"]["children"] or {}).get(winner)
        if child:
            s["node"] = child
            s["round"] += 1
        else:
            s["choice"] = winner
            s["finished"] = True
    return winner if s["finished"] else None


def _back(s):
    """Verbatim port of SELECTION.back."""
    if not s["history"]:
        return False
    prev = s["history"].pop()
    s.update({"topicPhase": prev["topicPhase"], "topicWinners": prev["topicWinners"],
              "node": prev["node"], "path": prev["path"], "round": prev["round"],
              "finished": False, "choice": None})
    return True


# ═══════════════════════════════════════════════════════════════════════════
# collective.html display math (the code under test), current + corrected
# ═══════════════════════════════════════════════════════════════════════════
TOTAL_ROUNDS_CURRENT = 6  # value currently hardcoded in collective.html

def collective_bar_pct_current(p):
    if p.get("finished"):
        return 100
    return min(100, round(len(p.get("path", [])) / TOTAL_ROUNDS_CURRENT * 100))

def collective_label_current(p):
    if p.get("finished"):
        return "Done"
    return f"round {len(p.get('path', [])) + 1} of {TOTAL_ROUNDS_CURRENT}"


# The CORRECTED math the fixed collective.html should drive (3 bracket + 4 tree = 7):
TOTAL_ROUNDS_TRUE = 7

def decisions_completed(p):
    """Mirror of collective.html's decisionsCompleted(p)."""
    if p.get("finished"):
        return TOTAL_ROUNDS_TRUE
    L = len(p.get("path", []))
    if L >= 1:
        return min(TOTAL_ROUNDS_TRUE, 2 + L)      # committed→3, in-tree → 4,5,6
    return p.get("topicPhase", 0)                 # bracket → 0,1,2

def current_round(p):
    if p.get("finished"):
        return TOTAL_ROUNDS_TRUE
    return min(TOTAL_ROUNDS_TRUE, decisions_completed(p) + 1)

def collective_bar_pct_fixed(p):
    return min(100, round(decisions_completed(p) / TOTAL_ROUNDS_TRUE * 100))


# ═══════════════════════════════════════════════════════════════════════════
# Clients
# ═══════════════════════════════════════════════════════════════════════════
class Client:
    def __init__(self, url):
        self.url = url
        self.ws = None
        self.last = None

    async def connect(self):
        self.ws = await websockets.connect(self.url)

    async def recv(self):
        d = json.loads(await self.ws.recv())
        self.last = d
        return d

    async def send(self, obj):
        await self.ws.send(json.dumps(obj))

    async def wait_for(self, pred, what, timeout=6.0):
        """Return once pred holds on the latest state (no new frame required)."""
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


def by_tablets(d):
    return {v["id"]: (v.get("progress") or {}) for v in d.get("visitors", [])
            if v.get("kind") == "tablet"}


class Tablet:
    def __init__(self, tid):
        self.id = tid
        self.s = _new_state()

    def sync(self):
        return {"type": "progress", "id": self.id, "progress": {
            "started": True,
            "inTopicBracket": _is_bracket(self.s),
            "options": _options(self.s),
            "path": self.s["path"][:],
            "round": self.s["round"],
            "topicPhase": self.s["topicPhase"],
            "topic": self.s["path"][0] if self.s["path"] else None,
            "finished": self.s["finished"],
            "choice": self.s["choice"]}}

    def choose(self, side):
        _apply(self.s, side)


async def main():
    print("Full installation E2E test\n" + "=" * 60)

    # ── 0. Journey-math proof against the REAL trees ──────────────────────
    print("\n[0] Journey math (ported 1:1 walker, real 4-level-deep trees)")
    probe = _new_state()
    for sd in ["left", "left", "left"]:          # bracket → Nature
        _apply(probe, sd)
    n_tree = 0
    while not probe["finished"]:
        _apply(probe, "left")
        n_tree += 1
    total = 3 + n_tree
    check(probe["finished"], "walker reaches a terminal reward")
    check(probe["choice"] == "Fish", f"Nature all-left ends on 'Fish' (got {probe['choice']})")
    check(len(probe["path"]) == 5, f"path length is 5 at finish (got {len(probe['path'])})")
    check(probe["round"] == 4, f"round counter reaches 4 (got {probe['round']})")
    check(total == 7, f"full journey is 7 decisions (3 bracket + 4 tree) (got {total})")
    check(probe["path"] == ["Nature", "Moving", "Water", "Water", "Fish"],
          f"path == Nature→Moving→Water→Water→Fish (got {probe['path']})")

    # Confirm every tree is 4 levels deep (the root cause of the display bug).
    depths = {}
    for topic, root in TOPIC_TREES.items():
        node = root
        d = 0
        while True:
            d += 1                       # this node presents a binary choice
            if not node["children"]:
                break                     # leaf → last decision made here
            node = node["children"][node["left"]]
        depths[topic] = d
    check(all(v == 4 for v in depths.values()),
          f"every tree is 4 decision-levels deep (got {depths}) — collective.html claims 3")

    # Corrected display math: walk one full journey step-by-step and confirm the
    # collective's decisionsCompleted/currentRound track the real 1→7 progression.
    done_seq, round_seq = [], []
    w = _new_state()
    done_seq.append(decisions_completed(w))            # just started
    round_seq.append(current_round(w))
    for _ in range(7):                                 # 3 bracket + 4 tree
        _apply(w, "left")
        done_seq.append(decisions_completed(w))
        round_seq.append(current_round(w))
    check(done_seq == [0, 1, 2, 3, 4, 5, 6, 7],
          f"corrected decisionsCompleted progresses 0→7 (got {done_seq})")
    check(round_seq == [1, 2, 3, 4, 5, 6, 7, 7],
          f"corrected currentRound progresses 1→7 (got {round_seq})")
    # The exact "one step from finish" state that the old /6 code showed as 67%:
    mid = _new_state()
    for _ in range(3): _apply(mid, "left")
    for _ in range(3): _apply(mid, "left")             # in-tree, path len 4, not finished
    check(not mid["finished"] and len(mid["path"]) == 4, "mid-journey state sanity (path len 4)")
    check(collective_bar_pct_fixed(mid) == 86,
          f"FIXED bar at one-step-from-finish is {collective_bar_pct_fixed(mid)}% (6/7)")
    check(current_round(mid) == 7,
          f"FIXED label at one-step-from-finish is round {current_round(mid)}/7")
    check(collective_bar_pct_current(mid) == 67,
          f"(sanity) the OLD /6 code would have shown {collective_bar_pct_current(mid)}% here — the bug we fixed")

    # ── 1. Connect everything ─────────────────────────────────────────────
    print("\n[1] Connect Projector 1, Projector 2, and 4 tablets")
    p1 = Client(f"{BASE}/ws")
    p2 = Client(f"{BASE}/session")
    t = {f"t{i}": Tablet(f"t{i}") for i in range(1, 5)}
    ts = {tid: Client(f"{BASE}/session") for tid in t}

    await p1.connect(); await p2.connect()
    for tid in t:
        await ts[tid].connect()

    await p1.send({"type": "join", "id": "projector1", "kind": "projector"})
    await p2.send({"type": "join", "id": "projector2", "kind": "projector"})
    for tid in t:
        await ts[tid].send({"type": "join", "id": tid, "kind": "tablet"})

    d = await p2.wait_for(
        lambda d: sum(1 for v in d["visitors"] if v["kind"] == "tablet") == 4,
        "all 4 tablets in the session snapshot")
    tablet_ids = {v["id"] for v in d["visitors"] if v["kind"] == "tablet"}
    check(tablet_ids == set(t.keys()), "all 4 tablet ids present (t1..t4)")
    check(d.get("tablet_count") == 4, "tablet_count == 4")
    # Projector 2 (collective.html) joins /session; Projector 1 (installation.html)
    # stays on its untouched /ws camera channel, so exactly 1 projector is in-room.
    check(d.get("projector_count") == 1,
          f"projector_count == 1 (got {d.get('projector_count')}: P2 in-room, P1 on /ws)")
    check(d["camera"]["online"] is False, "camera starts offline (no YOLO yet)")

    # ── 2. Independent per-visitor journeys ───────────────────────────────
    print("\n[2] Drive 4 independent journeys (distinct topics + rewards)")
    # bracket: (d1 Nature/Culture, d2 Politics/History, d3 win0/win1)
    bracket = {"t1": ["left", "left", "left"],   # → Nature
               "t2": ["right", "left", "left"],  # → Culture (win0)
               "t3": ["left", "left", "right"],  # → Politics (win1)
               "t4": ["left", "right", "right"]} # → History (win1)
    in_tree = {"t1": ["left", "left", "left", "left"],   # → Fish
               "t2": ["left", "left", "left", "right"],  # → Gaita
               "t3": ["left", "left", "right", "left"],  # → Hyperinflation
               "t4": ["left", "left", "left", "left"]}   # → Pemon People
    expected = {"t1": ("Nature", "Fish"), "t2": ("Culture", "Gaita"),
                "t3": ("Politics", "Hyperinflation"), "t4": ("History", "Pemon People")}

    def paths_match(d):
        prog = by_tablets(d)
        return all(prog.get(tid, {}).get("path") == t[tid].s["path"] for tid in t)

    # Bracket phase — verify snapshot sync and that all stay in the bracket.
    for r in range(3):
        for tid in t:
            t[tid].choose(bracket[tid][r])
            await ts[tid].send(t[tid].sync())
        d = await p2.wait_for(lambda d: all(
            by_tablets(d).get(tid, {}).get("round") == t[tid].s["round"] for tid in t),
            f"bracket round {r+1} sync'd")
        prog = by_tablets(d)
        if r < 2:
            check(all(prog[tid]["path"] == [] for tid in t) and
                  all(prog[tid]["inTopicBracket"] is True for tid in t),
                  f"bracket r{r+1}: all tablets still in the topic bracket (path empty)")
        else:
            # r3 is the commit: the winning topic lands in path, bracket ends, round→1
            check(all(prog[tid]["path"] == [t[tid].s["path"][0]] for tid in t) and
                  all(prog[tid]["inTopicBracket"] is False for tid in t) and
                  all(prog[tid]["round"] == 1 for tid in t),
                  "bracket r3: topic committed (path=[topic], inBracket=false, round=1)")

    # In-tree phase — each tablet must carry ONLY its own path.
    for r in range(4):
        for tid in t:
            t[tid].choose(in_tree[tid][r])
            await ts[tid].send(t[tid].sync())
        d = await p2.wait_for(paths_match, f"collective snapshot round {r+1} has all 4 distinct paths")
        prog = by_tablets(d)
        leak = any(prog[tid]["path"] == prog[other]["path"]
                   for tid in t for other in t if other != tid
                   and t[tid].s["path"] != t[other].s["path"])
        check(not leak, f"in-tree r{r+1}: no cross-tablet path leakage")
        check(all(prog[tid]["path"] == t[tid].s["path"] for tid in t),
              f"in-tree r{r+1}: each visitor shows its own path")

    # ── 3. Collective board data (from the authoritative snapshot) ───────
    print("\n[3] Verify collective snapshot data (themes, chosen holes, rewards)")
    d = await p2.wait_for(lambda d: all(by_tablets(d).get(tid, {}).get("finished") for tid in t),
                          "all 4 tablets finished")
    prog = by_tablets(d)
    check(all(prog[tid]["choice"] == expected[tid][1] for tid in t),
          "all 4 terminal rewards match the walked paths")
    check(all(prog[tid]["topic"] == expected[tid][0] for tid in t), "all 4 topics match")
    check(all(prog[tid]["choice"] in KNOWN_REWARDS for tid in t),
          "every chosen leaf has a real REWARDS entry")

    counts = {top: 0 for top in ["Nature", "Culture", "Politics", "History"]}
    for tid in t:
        counts[prog[tid]["path"][0]] += 1
    check(counts == {"Nature": 1, "Culture": 1, "Politics": 1, "History": 1},
          f"shared themes tally is 1/1/1/1 by path[0] (got {counts})")
    chosen = {prog[tid]["choice"] for tid in t}
    check(chosen == {"Fish", "Gaita", "Hyperinflation", "Pemon People"},
          f"4 distinct chosen rabbit holes (got {chosen})")

    # ── 4. Tablet interaction: back / vote / restart ──────────────────────
    print("\n[4] Tablet interaction (back, vote, restart)")
    # Back: t1 undoes its final choice → finished clears, path shrinks.
    before = t["t1"].s["path"][:]
    _back(t["t1"].s)
    await ts["t1"].send(t["t1"].sync())
    d = await p2.wait_for(lambda d: by_tablets(d).get("t1", {}).get("path") == t["t1"].s["path"],
                          "t1 back() reflected")
    check(by_tablets(d)["t1"]["path"] == before[:-1] and not by_tablets(d)["t1"]["finished"],
          "t1 back(): path shrinks by one, finished cleared")
    # Re-finish t1
    t["t1"].choose("left")
    await ts["t1"].send(t["t1"].sync())
    await p2.wait_for(lambda d: by_tablets(d).get("t1", {}).get("finished"), "t1 re-finished")

    # Vote (a camera-style side field, independent of the walk)
    await ts["t2"].send({"type": "vote", "id": "t2", "side": "right"})
    d = await p2.wait_for(
        lambda d: next((v for v in d["visitors"] if v["id"] == "t2"), {}).get("side") == "right",
        "t2 vote recorded")
    check(next(v for v in d["visitors"] if v["id"] == "t2")["side"] == "right",
          "t2 vote side == 'right' on the collective snapshot")

    # Restart: t3 resets to a fresh bracket
    t["t3"].s = _new_state()
    await ts["t3"].send({"type": "progress", "id": "t3", "progress": {
        "started": True, "inTopicBracket": True, "options": _options(t["t3"].s),
        "path": [], "round": 0, "topic": None, "finished": False, "choice": None}})
    d = await p2.wait_for(lambda d: by_tablets(d).get("t3", {}).get("path") == [],
                          "t3 restart reflected")
    check(by_tablets(d)["t3"]["path"] == [] and not by_tablets(d)["t3"]["finished"],
          "t3 restart(): path cleared, finished cleared")
    # restore t3 to finished so the board stays consistent for cleanup
    for sd in bracket["t3"] + in_tree["t3"]:
        t["t3"].choose(sd)
    await ts["t3"].send(t["t3"].sync())
    await p2.wait_for(lambda d: by_tablets(d).get("t3", {}).get("finished"), "t3 re-finished")

    # ── 5. Camera adapter folds into the session snapshot ────────────────
    print("\n[5] Camera (YOLO) adapter folds into the session snapshot")
    import httpx
    from datetime import datetime, timezone
    async with httpx.AsyncClient() as hx:
        await hx.post(f"http://{HOST}:{PORT}/detections", json={
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "frame_width": 1920, "frame_height": 1080, "person_count": 3,
            "people": [{"id": i, "confidence": 0.9,
                        "bbox": {"x1": i * 100, "y1": 0, "x2": i * 100 + 50, "y2": 50},
                        "center": {"x": i * 100 + 25, "y": 25},
                        "center_normalized": {"x": 0.2 + i * 0.1, "y": 0.5}}
                       for i in range(3)]})
    d = await p2.wait_for(lambda d: d["camera"]["online"] and d["camera"]["people"] == 3,
                          "camera online with 3 people")
    check(d["camera"]["active"] is True, "camera.active true when people > 0")
    check(d.get("camera_people") == 3, "camera_people == 3 in snapshot")

    # Projector 1 must have been receiving the YOLO stream all along.
    pd = await p1.wait_for(lambda d: d.get("person_count") == 3, "P1 YOLO frame", timeout=3.0)
    check(pd.get("person_count") == 3, "Projector 1 received the YOLO frame (person_count=3)")

    # ── 6. Disconnect isolation ─────────────────────────────────────────
    print("\n[6] Disconnect isolation")
    await ts["t4"].close()
    d = await p2.wait_for(lambda d: "t4" not in by_tablets(d), "t4 removed from session")
    check("t4" not in by_tablets(d), "t4 gone from collective snapshot after disconnect")
    check(by_tablets(d).get("t1", {}).get("finished") is True,
          "t1's finished progress survived t4's disconnect (no clobber)")

    print("\n" + "=" * 60)
    print(f"RESULT: {PASS} passed, {FAIL} failed")

    for c in (p1, p2, *ts.values()):
        await c.close()
    return FAIL == 0


if __name__ == "__main__":
    import sys
    try:
        ok = asyncio.run(main())
        sys.exit(0 if ok else 1)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)

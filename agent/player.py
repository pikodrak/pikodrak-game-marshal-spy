"""Heuristic Marshal & Spy agent — plays one game vs the built-in CPU AI.

Uses the bot API through the FastAPI TestClient (no HTTP server process needed).
Plays up to MS_TURNS turns per invocation (env var, default 50). Run repeatedly
until the game ends, or loop via run_game.py.

State is persisted to agent/state/ so invocations can pick up mid-game.

Strategic priorities (applied in order each turn):
  1. WIN  — if any move lands on enemy citadel, take it
  2. DEFEND — revealed enemy near MY citadel (artillery/drone insta-kill, then
     melee kill, then block)
  3. COUNTER-ARTY — destroy revealed enemy artillery immediately
  4. KNOWN KILL — attack any revealed enemy we outmatch
  4.5 BLOCK — move a strong blocker between an unkillable threat and my citadel
  5. TRAINER BOOST — boost rusher closest to enemy citadel
  6. RECON — reveal hidden enemies near MY citadel
  7. ADVANCE — push rushers toward enemy citadel (skip stalled ones)
  8. AIR — advance helicopters; fighters hunt revealed air
  9. FALLBACK — any forward move, any legal attack, pass
"""
import os
import sys
import json
import time
import argparse
import traceback

# Make /projects/game03 importable regardless of where this script is run from
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402

from agent.config import CONFIG  # noqa: E402

# ============================================================
# Paths
# ============================================================
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
os.makedirs(STATE_DIR, exist_ok=True)
GAME_FILE = os.path.join(STATE_DIR, "current_game.json")
AGENT_STATE_FILE = os.path.join(STATE_DIR, "agent_state.json")
LOG_FILE = os.path.join(STATE_DIR, "agent_log.txt")

cli = TestClient(server.app)


# ============================================================
# Game bootstrapping
# ============================================================

def load_game_cfg():
    if not os.path.exists(GAME_FILE):
        raise FileNotFoundError(
            f"{GAME_FILE} not found — run `python -m agent.run_game --new` first"
        )
    return json.load(open(GAME_FILE))


# ============================================================
# Agent state (persists across invocations)
# ============================================================

def fresh_agent_state():
    return {
        "action_counts": {"move": 0, "attack": 0, "special": 0, "pass": 0, "wasted": 0},
        "my_kills": 0,
        "enemy_kills": 0,
        "my_units_lost": [],
        "enemy_units_lost": [],
        "unit_stall_count": {},
        "stale_near_citadel_count": {},  # enemy_uid -> consecutive turns seen near our citadel
        "all_events": [],
        "turns_played": 0,
    }


def load_agent_state():
    if os.path.exists(AGENT_STATE_FILE):
        try:
            return json.load(open(AGENT_STATE_FILE))
        except json.JSONDecodeError:
            pass
    return fresh_agent_state()


def save_agent_state(state):
    state["all_events"] = state["all_events"][-CONFIG["events_keep_last"]:]
    json.dump(state, open(AGENT_STATE_FILE, "w"))


def reset_agent_state():
    s = fresh_agent_state()
    save_agent_state(s)
    open(LOG_FILE, "w").close()
    return s


# ============================================================
# Geometry
# ============================================================

def hex_dist(a, b):
    """Cube distance on odd-r offset hex grid."""
    def to_cube(c, r):
        x = c - (r - (r & 1)) // 2
        z = r
        return x, -x - z, z
    ax, ay, az = to_cube(*a)
    bx, by, bz = to_cube(*b)
    return (abs(ax - bx) + abs(ay - by) + abs(az - bz)) // 2


# ============================================================
# Decision logic
# ============================================================

def pick_action(st, me, agent_state):
    """Return (endpoint, payload, tag). `tag` is a short label for logging."""
    aa = st["available_actions"]
    moves = aa.get("moves", [])
    attacks = aa.get("attacks", [])
    specials = aa.get("specials", [])

    my_units = {u["id"]: u for u in st["my_units"]}
    enemy_units = {u["id"]: u for u in st["enemy_units"]}
    my_citadel = (8, 0) if me == 1 else (8, 16)
    enemy_citadel = (8, 16) if me == 1 else (8, 0)
    revealed = [u for u in st["enemy_units"] if u.get("revealed")]

    def dist_to_my_cit(u):
        return hex_dist((u["col"], u["row"]), my_citadel)

    # Flatten attacks: (attacker_id, target_id, tinfo)
    flat_attacks = []
    for atk in attacks:
        aid = atk.get("unit_id") or atk.get("attacker_id")
        for t in atk.get("targets", []):
            tid = t.get("unit_id") or t.get("target_id")
            flat_attacks.append((aid, tid, t))

    # ------------------------------------------------------------
    # P1 — WIN
    # ------------------------------------------------------------
    for mv in moves:
        uid = mv["unit_id"]
        u = my_units.get(uid)
        if not u or u.get("category") not in ("ground", "special"):
            continue
        for tgt in mv["targets"]:
            if (tgt["col"], tgt["row"]) == enemy_citadel:
                return ("move",
                        {"unit_id": uid, "col": enemy_citadel[0], "row": enemy_citadel[1],
                         "rationale": "WIN: capture enemy citadel"},
                        "WIN")

    # ------------------------------------------------------------
    # P1.25 — ENDGAME OVERRIDE (G14 fix): when we have a big kill lead late,
    # do not let P1.5 INTR keep us defensive. Force push.
    # ------------------------------------------------------------
    turns_played_so_far = agent_state.get("turns_played", 0)
    kill_lead = agent_state.get("my_kills", 0) - agent_state.get("enemy_kills", 0)
    if turns_played_so_far > 150 and kill_lead >= 8:
        push_types = ("terminator", "tank", "paratrooper", "hacker", "scout")
        best_push = None
        best_score = -1
        for mv in moves:
            uid = mv["unit_id"]
            u = my_units.get(uid)
            if not u or u.get("type") not in push_types:
                continue
            cur_d = hex_dist((u["col"], u["row"]), enemy_citadel)
            for tgt in mv["targets"]:
                new_d = hex_dist((tgt["col"], tgt["row"]), enemy_citadel)
                delta = cur_d - new_d
                if delta <= 0: continue
                score = u.get("attack", 0) + delta * 5 - new_d * 0.5
                if score > best_score:
                    best_score = score
                    best_push = (uid, tgt["col"], tgt["row"], u, new_d)
        if best_push:
            uid, c, r, u, nd = best_push
            return ("move",
                    {"unit_id": uid, "col": c, "row": r,
                     "rationale": f"OVERRIDE: lead {kill_lead}, pushing {u['type']} d={nd}"},
                    f"OVERRIDE {u['type']}")

    # ------------------------------------------------------------
    # P1.5 — CITADEL-INTRUSION INTERCEPT (G10 fix)
    # G11 fix: URGENT_R raised 4→6 (scout at row 9 slipped past R=4).
    # Also: scan DORMANT hidden enemies in battlefield — if a hidden unit hasn't
    # moved for 20+ turns, it's likely the G8/G11 silent infiltrator pre-sprint.
    # G14 fix: squatter exclusion (above) prevents hidden air from infinite loop.
    # ------------------------------------------------------------
    URGENT_R = 6  # was 4 (G11 scout at dist 9 with movement 3 reached citadel in 3 turns)
    # G14 fix: hidden air units trap us in infinite INTR loop because cat="unknown".
    # If a unit has been "stale near citadel" 30+ turns without moving or attacking,
    # it's clearly NOT a real infiltrator — real infiltrators sprint. Give up on it.
    # Also track per-unit position signature to detect "hasn't moved" — if same hex
    # for 25+ turns, treat as harmless squatter.
    def _can_capture_citadel(eu):
        cat = eu.get("category")
        return cat != "air"

    stale_ctr = agent_state.setdefault("stale_near_citadel_count", {})
    stale_pos = agent_state.setdefault("stale_near_citadel_pos", {})  # uid -> (col,row,idle_turns)
    current_near_ids = set()
    for eu in st["enemy_units"]:
        if not _can_capture_citadel(eu):
            continue
        if dist_to_my_cit(eu) <= URGENT_R:
            current_near_ids.add(eu["id"])
            stale_ctr[eu["id"]] = stale_ctr.get(eu["id"], 0) + 1
            prev = stale_pos.get(eu["id"])
            if prev and prev[0] == eu["col"] and prev[1] == eu["row"]:
                stale_pos[eu["id"]] = (eu["col"], eu["row"], prev[2] + 1)
            else:
                stale_pos[eu["id"]] = (eu["col"], eu["row"], 0)
    for eid in list(stale_ctr.keys()):
        if eid not in current_near_ids:
            stale_ctr.pop(eid, None)
            stale_pos.pop(eid, None)
    # Exclude squatters: hidden units that haven't moved for 25+ turns are not real threats
    for eid in list(stale_ctr.keys()):
        pos = stale_pos.get(eid)
        if pos and pos[2] >= 25:
            stale_ctr.pop(eid, None)  # give up tracking — not an infiltrator

    # G11 addition: track dormant hidden enemies in battlefield (rows 7-9).
    # Any hidden enemy sitting on same hex for 20+ turns is a pre-sprint infiltrator.
    dormant_pos = agent_state.setdefault("dormant_hidden_pos", {})  # uid -> (col, row, first_seen_turn)
    turns_played = agent_state.get("turns_played", 0)
    for eu in st["enemy_units"]:
        if eu.get("revealed"):
            dormant_pos.pop(eu["id"], None)
            continue
        r = eu["row"]
        if (me == 1 and r not in (7, 8, 9, 10)) or (me == 2 and r not in (6, 7, 8, 9)):
            dormant_pos.pop(eu["id"], None)
            continue
        prev = dormant_pos.get(eu["id"])
        if prev and prev[0] == eu["col"] and prev[1] == eu["row"]:
            pass  # still dormant on same hex
        else:
            dormant_pos[eu["id"]] = (eu["col"], eu["row"], turns_played)

    # Units dormant 20+ turns are urgent
    dormant_suspects = []
    for eu in st["enemy_units"]:
        pos = dormant_pos.get(eu["id"])
        if pos and (turns_played - pos[2]) >= 20:
            dormant_suspects.append(eu)

    # Try to reveal dormant suspect with recon drone
    for suspect in dormant_suspects:
        for sp in specials:
            uid = sp["unit_id"]
            u = my_units[uid]
            if u["type"] != "recon_drone":
                continue
            for act in sp["actions"]:
                if act["action"] == "reveal" and act.get("target_id") == suspect["id"]:
                    return ("special",
                            {"unit_id": uid, "action": "reveal",
                             "target_id": suspect["id"],
                             "rationale": f"DORMANT reveal: hidden enemy at ({suspect['col']},{suspect['row']}) for 20+ turns"},
                            "DORMANT_REV")

    stale_intruders = []
    for eu in st["enemy_units"]:
        if stale_ctr.get(eu["id"], 0) >= 2:
            stale_intruders.append(eu)
    stale_intruders.sort(key=dist_to_my_cit)

    # Try to attack stale intruder with strongest reachable unit
    for intr in stale_intruders:
        # Attack directly if we can kill it
        best_a = None
        best_a_atk = -1
        for aid, tid, _ in flat_attacks:
            if tid != intr["id"]:
                continue
            au = my_units[aid]
            if au["type"] == "fighter" and intr.get("category") in ("ground", "special"):
                continue
            if intr.get("type") == "mine_field" and au["type"] != "engineer":
                continue
            # Hacker vs Terminator auto-win
            if au["type"] == "hacker" and intr.get("type") == "terminator":
                return ("attack",
                        {"attacker_id": aid, "target_id": intr["id"],
                         "rationale": "INTRUDER: Hacker auto-kill Terminator"},
                        "INTR_HACK")
            e_atk = intr.get("attack", 0) if isinstance(intr.get("attack"), int) else 4
            my_atk = au.get("attack", 0)
            if my_atk > e_atk and my_atk > best_a_atk:
                best_a_atk = my_atk
                best_a = aid
        if best_a is not None:
            return ("attack",
                    {"attacker_id": best_a, "target_id": intr["id"],
                     "rationale": f"INTRUDER kill {intr.get('type','?')} stale {stale_ctr[intr['id']]}t"},
                    "INTR_KILL")

        # Else move strongest defender toward intruder
        best_mv = None
        best_score = -1.0
        for mv in moves:
            uid = mv["unit_id"]
            u = my_units.get(uid)
            if not u or u.get("category") != "ground":
                continue
            # Need somewhat strong (block_priority > 40 ish)
            if u.get("attack", 0) < 4:
                continue
            cur_d = hex_dist((u["col"], u["row"]), (intr["col"], intr["row"]))
            for tgt in mv["targets"]:
                new_d = hex_dist((tgt["col"], tgt["row"]), (intr["col"], intr["row"]))
                if new_d >= cur_d:
                    continue
                score = u.get("attack", 0) * 2 + (cur_d - new_d) * 5 - new_d
                if score > best_score:
                    best_score = score
                    best_mv = (uid, tgt["col"], tgt["row"], u)
        if best_mv:
            uid, c, r, u = best_mv
            return ("move",
                    {"unit_id": uid, "col": c, "row": r,
                     "rationale": f"INTRUDER intercept w/ {u['type']}"},
                    f"INTR_MV {u['type']}")

        # Else try to reveal it (recon drone) if hidden
        if not intr.get("revealed"):
            for sp in specials:
                uid = sp["unit_id"]
                u = my_units[uid]
                if u["type"] != "recon_drone":
                    continue
                for act in sp["actions"]:
                    if act.get("target_id") == intr["id"] and act["action"] == "reveal":
                        return ("special",
                                {"unit_id": uid, "action": "reveal",
                                 "target_id": intr["id"],
                                 "rationale": "INTRUDER reveal"},
                                "INTR_REV")

    # ------------------------------------------------------------
    # P2 — DEFEND (artillery/drone insta-kill → melee kill → block)
    # ------------------------------------------------------------
    threats = sorted(
        [u for u in revealed if dist_to_my_cit(u) <= CONFIG["defense_radius"]],
        key=dist_to_my_cit,
    )

    for threat in threats:
        # Artillery instakill
        for sp in specials:
            uid = sp["unit_id"]
            u = my_units[uid]
            if u["type"] != "artillery":
                continue
            if threat.get("category") not in ("ground", "special"):
                continue
            for act in sp["actions"]:
                if act.get("target_id") == threat["id"] and act["action"] == "artillery_fire":
                    return ("special",
                            {"unit_id": uid, "action": "artillery_fire",
                             "target_id": threat["id"],
                             "rationale": f"DEFENSE: arty insta-kill {threat['type']}"},
                            f"DEF_ARTY {threat['type']}")

        # Attack-drone strike if target ATK < threshold
        enemy_atk = threat.get("attack", 0) if isinstance(threat.get("attack"), int) else 99
        if enemy_atk < CONFIG["attack_drone_atk_threshold"]:
            for sp in specials:
                uid = sp["unit_id"]
                u = my_units[uid]
                if u["type"] != "attack_drone":
                    continue
                for act in sp["actions"]:
                    if act.get("target_id") == threat["id"] and act["action"] == "strike":
                        return ("special",
                                {"unit_id": uid, "action": "strike",
                                 "target_id": threat["id"],
                                 "rationale": f"DEFENSE: drone {threat['type']}(ATK {enemy_atk})"},
                                f"DEF_DRONE {threat['type']}")

    # Defense melee — prefer cheapest unit that strictly wins
    for threat in threats:
        enemy_atk = threat.get("attack", 0) if isinstance(threat.get("attack"), int) else 99
        best = None
        best_score = -1
        for aid, tid, _tinfo in flat_attacks:
            if tid != threat["id"]:
                continue
            au = my_units[aid]
            # Category safety
            if au["type"] == "fighter" and threat.get("category") == "ground":
                continue
            if threat["type"] == "mine_field" and au["type"] != "engineer":
                continue
            # Hacker auto-wins vs Terminator
            if au["type"] == "hacker" and threat["type"] == "terminator":
                return ("attack",
                        {"attacker_id": aid, "target_id": threat["id"],
                         "rationale": "Hacker auto-kill Terminator near citadel"},
                        "DEF_HACKER_TERM")
            # Engineer vs mine
            if threat["type"] == "mine_field" and au["type"] == "engineer":
                return ("attack",
                        {"attacker_id": aid, "target_id": threat["id"],
                         "rationale": "Engineer defuses mine near citadel"},
                        "DEF_DEFUSE")
            my_atk = au.get("attack", 0)
            margin = my_atk - enemy_atk
            if margin > 0:
                # Score: prefer cheaper unit still winning
                score = 10 - my_atk + margin * CONFIG["kill_margin_weight"] \
                    if "kill_margin_weight" in CONFIG else 10 - my_atk + margin * 0.5
                if CONFIG.get("prefer_cheap_killer", True):
                    pass
                else:
                    score = margin
                if score > best_score:
                    best_score = score
                    best = (aid, au)
        if best:
            aid, au = best
            return ("attack",
                    {"attacker_id": aid, "target_id": threat["id"],
                     "rationale": f"DEFENSE: {au['type']}({au['attack']}) > "
                                  f"{threat['type']}({threat.get('attack')})"},
                    f"DEF_ATK {au['type']}")

    # ------------------------------------------------------------
    # P3 — COUNTER-ARTILLERY
    # ------------------------------------------------------------
    for e in revealed:
        if e["type"] != "artillery":
            continue
        # My arty
        for sp in specials:
            uid = sp["unit_id"]
            u = my_units[uid]
            if u["type"] == "artillery":
                for act in sp["actions"]:
                    if act.get("target_id") == e["id"] and act["action"] == "artillery_fire":
                        return ("special",
                                {"unit_id": uid, "action": "artillery_fire",
                                 "target_id": e["id"],
                                 "rationale": "COUNTER-ARTY"},
                                "COUNTER_ARTY")
        # Attack drone
        for sp in specials:
            uid = sp["unit_id"]
            u = my_units[uid]
            if u["type"] == "attack_drone":
                for act in sp["actions"]:
                    if act.get("target_id") == e["id"] and act["action"] == "strike":
                        return ("special",
                                {"unit_id": uid, "action": "strike",
                                 "target_id": e["id"],
                                 "rationale": "COUNTER-ARTY drone"},
                                "COUNTER_ARTY_DR")
        # Direct melee (arty ATK 0 = easy kill)
        for aid, tid, _ in flat_attacks:
            if tid == e["id"]:
                au = my_units[aid]
                if au["type"] == "fighter":
                    continue
                return ("attack",
                        {"attacker_id": aid, "target_id": e["id"],
                         "rationale": "COUNTER-ARTY melee"},
                        "COUNTER_ARTY_MEL")

    # ------------------------------------------------------------
    # P4 — KNOWN KILL (any revealed enemy we outmatch)
    # ------------------------------------------------------------
    best_kill = None
    best_kill_score = -1.0
    for aid, tid, _ in flat_attacks:
        au = my_units[aid]
        tu = enemy_units.get(tid)
        if not tu or not tu.get("revealed"):
            continue
        if au["type"] == "fighter" and tu.get("category") == "ground":
            continue
        if tu["type"] == "mine_field" and au["type"] != "engineer":
            continue
        if au["type"] == "hacker" and tu["type"] == "terminator":
            return ("attack",
                    {"attacker_id": aid, "target_id": tid,
                     "rationale": "Hacker auto-kill Terminator"},
                    "HACKER_KILL_TERM")
        my_atk = au.get("attack", 0)
        enemy_atk = tu.get("attack", 0) if isinstance(tu.get("attack"), int) else 99
        if my_atk > enemy_atk:
            dist_gain = hex_dist((au["col"], au["row"]), enemy_citadel) \
                      - hex_dist((tu["col"], tu["row"]), enemy_citadel)
            score = (my_atk - enemy_atk) + 0.5 * dist_gain - 0.1 * my_atk
            if score > best_kill_score:
                best_kill_score = score
                best_kill = (aid, tid, au, tu)
    if best_kill:
        aid, tid, au, tu = best_kill
        return ("attack",
                {"attacker_id": aid, "target_id": tid,
                 "rationale": f"Kill {au['type']}({au.get('attack')}) "
                              f"> {tu['type']}({tu.get('attack')})"},
                f"KILL {au['type']}")

    # ------------------------------------------------------------
    # P4.5 — BLOCK unkillable threats
    # G13 fix: abort BLOCK loop if we've blocked the same threat for 8+ turns.
    # Without this, Terminator re-blocks the same enemy forever (50+ turn lock).
    # ------------------------------------------------------------
    block_lock_ctr = agent_state.setdefault("block_lock_count", {})
    if threats:
        block_threat = threats[0]
        lock_key = f"{block_threat['id']}:{block_threat['col']},{block_threat['row']}"
        # If we've blocked same threat at same hex 8+ turns → abandon BLOCK, fall through
        if block_lock_ctr.get(lock_key, 0) >= 8:
            # Blocked too long, don't BLOCK again — skip P4.5, fall to P7 ADVANCE
            block_threat = None
        else:
            enemy_atk = block_threat.get("attack", 0) \
                if isinstance(block_threat.get("attack"), int) \
                else CONFIG["min_blocker_atk_vs_unknown"]
            best_block = None
            best_score = -1.0
            for mv in moves:
                uid = mv["unit_id"]
                u = my_units.get(uid)
                if not u or u.get("category") != "ground":
                    continue
                if u.get("attack", 0) < enemy_atk:
                    continue
                current_d = hex_dist((u["col"], u["row"]),
                                      (block_threat["col"], block_threat["row"]))
                for tgt in mv["targets"]:
                    new_d = hex_dist((tgt["col"], tgt["row"]),
                                      (block_threat["col"], block_threat["row"]))
                    if new_d >= current_d:
                        continue
                    type_prio = CONFIG["block_priority"].get(u["type"], 30)
                    score = type_prio + (current_d - new_d) * CONFIG["block_progress_weight"] - new_d
                    if score > best_score:
                        best_score = score
                        best_block = (uid, tgt["col"], tgt["row"], u)
            if best_block:
                block_lock_ctr[lock_key] = block_lock_ctr.get(lock_key, 0) + 1
                # Clear other block-lock keys (only track current threat)
                for k in list(block_lock_ctr.keys()):
                    if k != lock_key:
                        block_lock_ctr.pop(k, None)
                uid, c, r, u = best_block
                return ("move",
                        {"unit_id": uid, "col": c, "row": r,
                         "rationale": f"BLOCK({block_lock_ctr[lock_key]}): {u['type']}({u.get('attack')}) "
                                      f"intercepts {block_threat['type']}"},
                        f"BLOCK {u['type']}")

    # ------------------------------------------------------------
    # P4.6 — ENDGAME AGGRESSION OVERRIDE (G13 fix for late-game passivity)
    # If T > 200 and we have kill lead >= 5, force ADVANCE with strongest ground rusher
    # toward enemy citadel — skip BLOCK/RECON. Convert material into victory.
    # ------------------------------------------------------------
    turns_played = agent_state.get("turns_played", 0)
    my_kills = agent_state.get("my_kills", 0)
    enemy_kills = agent_state.get("enemy_kills", 0)
    if turns_played > 180 and (my_kills - enemy_kills) >= 3:
        # Find strongest ground unit that can advance
        push_types = ("terminator", "tank", "paratrooper", "hacker", "scout")
        best_push = None
        best_score = -1
        for mv in moves:
            uid = mv["unit_id"]
            u = my_units.get(uid)
            if not u or u.get("type") not in push_types:
                continue
            cur_d = hex_dist((u["col"], u["row"]), enemy_citadel)
            for tgt in mv["targets"]:
                new_d = hex_dist((tgt["col"], tgt["row"]), enemy_citadel)
                delta = cur_d - new_d
                if delta <= 0:
                    continue
                # Score: prefer strongest unit closest to enemy citadel
                score = u.get("attack", 0) + delta * 5 - new_d * 0.5
                if score > best_score:
                    best_score = score
                    best_push = (uid, tgt["col"], tgt["row"], u, new_d)
        if best_push:
            uid, c, r, u, nd = best_push
            return ("move",
                    {"unit_id": uid, "col": c, "row": r,
                     "rationale": f"ENDGAME PUSH: {u['type']} → enemy citadel d={nd}"},
                    f"ENDGAME {u['type']}")

    # ------------------------------------------------------------
    # P5 — TRAINER BOOST best rusher
    # ------------------------------------------------------------
    for sp in specials:
        uid = sp["unit_id"]
        u = my_units[uid]
        if u["type"] != "trainer":
            continue
        best_act = None
        best_score = -1
        for act in sp["actions"]:
            if act["action"] != "boost":
                continue
            target = my_units.get(act["target_id"])
            if not target:
                continue
            if target.get("boost_count", 0) >= CONFIG["max_boost_per_unit"]:
                continue
            prio = CONFIG["boost_priority"].get(target["type"], 0)
            if prio <= 0:
                continue
            d = hex_dist((target["col"], target["row"]), enemy_citadel)
            score = prio - d * 0.5
            if score > best_score:
                best_score = score
                best_act = act
        if best_act:
            return ("special",
                    {"unit_id": uid, "action": "boost",
                     "target_id": best_act["target_id"],
                     "rationale": f"Boost {my_units[best_act['target_id']]['type']}"},
                    f"BOOST {my_units[best_act['target_id']]['type']}")

    # ------------------------------------------------------------
    # P6 — RECON hidden enemies near our citadel
    # ------------------------------------------------------------
    hidden_near = [u for u in st["enemy_units"]
                   if (not u.get("revealed")) and dist_to_my_cit(u) <= CONFIG["recon_radius"]]
    if hidden_near:
        for sp in specials:
            uid = sp["unit_id"]
            u = my_units[uid]
            if u["type"] != "recon_drone":
                continue
            best_rev = None
            best_d = 1e9
            for act in sp["actions"]:
                if act["action"] != "reveal":
                    continue
                e = enemy_units.get(act["target_id"])
                if not e:
                    continue
                d = dist_to_my_cit(e)
                if d < best_d:
                    best_d = d
                    best_rev = act
            if best_rev:
                return ("special",
                        {"unit_id": uid, "action": "reveal",
                         "target_id": best_rev["target_id"],
                         "rationale": "RECON hidden near citadel"},
                        "RECON")

    # ------------------------------------------------------------
    # P7 — ADVANCE rushers (with stall penalty + lateral escape for congestion)
    # G10 fix: rushers block each other in L2 → 173× fighter vortex.
    # If direct advance fails AND rusher has been stalled 3+ turns, allow LATERAL
    # move (sideways, same/slight backward row) to break congestion.
    # ------------------------------------------------------------
    type_rank = {t: i for i, t in enumerate(CONFIG["advance_priority_types"])}
    advance_candidates = []
    lateral_candidates = []
    for mv in moves:
        uid = mv["unit_id"]
        u = my_units.get(uid)
        if not u:
            continue
        if u["type"] in CONFIG["static_unit_types"]:
            continue
        if u["type"] in ("fighter", "helicopter"):
            continue
        if u["type"] not in type_rank:
            continue
        current_dist = hex_dist((u["col"], u["row"]), enemy_citadel)
        rank = type_rank[u["type"]]
        stall = agent_state["unit_stall_count"].get(uid, 0)
        for tgt in mv["targets"]:
            new_dist = hex_dist((tgt["col"], tgt["row"]), enemy_citadel)
            delta = current_dist - new_dist
            if delta > 0:
                score = (delta * CONFIG["advance_delta_weight"]
                         - rank * CONFIG["advance_type_rank_weight"]
                         - stall * CONFIG["stall_penalty_per_turn"]
                         - new_dist * CONFIG["advance_new_dist_weight"])
                advance_candidates.append((score, uid, tgt["col"], tgt["row"], u, new_dist))
            elif stall >= 3 and delta == 0:
                # Lateral escape: stalled rusher moves sideways (same distance to citadel)
                lateral_score = 3 - rank * 0.5 + stall * 0.5 - new_dist * 0.2
                lateral_candidates.append((lateral_score, uid, tgt["col"], tgt["row"], u, new_dist))
    if advance_candidates:
        advance_candidates.sort(reverse=True, key=lambda x: x[0])
        _, uid, c, r, u, new_dist = advance_candidates[0]
        return ("move",
                {"unit_id": uid, "col": c, "row": r,
                 "rationale": f"Advance {u['type']} d={new_dist}"},
                f"ADV {u['type']}")
    if lateral_candidates:
        lateral_candidates.sort(reverse=True, key=lambda x: x[0])
        _, uid, c, r, u, new_dist = lateral_candidates[0]
        return ("move",
                {"unit_id": uid, "col": c, "row": r,
                 "rationale": f"Lateral escape {u['type']} (stalled, unblocking)"},
                f"LAT {u['type']}")

    # ------------------------------------------------------------
    # P8 — AIR (helicopter advance, fighter hunts air)
    # ------------------------------------------------------------
    if CONFIG["helicopter_advances"]:
        for mv in moves:
            uid = mv["unit_id"]
            u = my_units.get(uid)
            if not u or u["type"] != "helicopter":
                continue
            current = hex_dist((u["col"], u["row"]), enemy_citadel)
            best = None
            best_d = current
            for tgt in mv["targets"]:
                d = hex_dist((tgt["col"], tgt["row"]), enemy_citadel)
                if d < best_d:
                    best_d = d
                    best = tgt
            if best:
                return ("move",
                        {"unit_id": uid, "col": best["col"], "row": best["row"],
                         "rationale": "Advance helicopter"},
                        "ADV_HELI")

    if CONFIG["fighter_hunts_air_only"]:
        air_enemies = [u for u in revealed if u.get("category") == "air"]
        # G10 fix: cap fighter-air runaway (173× looping in G10). Only allow one
        # fighter move per 8 turns, and only if it actually closes distance meaningfully.
        last_fighter_turn = agent_state.get("last_fighter_air_turn", -100)
        turns_since = agent_state["turns_played"] - last_fighter_turn
        if air_enemies and turns_since >= 8:
            target_pos = (air_enemies[0]["col"], air_enemies[0]["row"])
            for mv in moves:
                uid = mv["unit_id"]
                u = my_units.get(uid)
                if not u or u["type"] != "fighter":
                    continue
                cur_d_target = hex_dist((u["col"], u["row"]), target_pos)
                best = None
                best_d = cur_d_target  # only accept actual progress
                for tgt in mv["targets"]:
                    d = hex_dist((tgt["col"], tgt["row"]), target_pos)
                    if d < best_d:
                        best_d = d
                        best = tgt
                if best:
                    agent_state["last_fighter_air_turn"] = agent_state["turns_played"]
                    return ("move",
                            {"unit_id": uid, "col": best["col"], "row": best["row"],
                             "rationale": "Fighter hunts air"},
                            "FIGHTER_AIR")

    # ------------------------------------------------------------
    # P9 — FALLBACK: any forward move, then any legal attack, then pass
    # ------------------------------------------------------------
    generic_candidates = []
    for mv in moves:
        uid = mv["unit_id"]
        u = my_units.get(uid)
        if not u or u["type"] in CONFIG["static_unit_types"]:
            continue
        current = hex_dist((u["col"], u["row"]), enemy_citadel)
        for tgt in mv["targets"]:
            new_d = hex_dist((tgt["col"], tgt["row"]), enemy_citadel)
            if new_d < current:
                stall = agent_state["unit_stall_count"].get(uid, 0)
                score = (current - new_d) * CONFIG["advance_delta_weight"] \
                        - stall * CONFIG["stall_penalty_per_turn"] \
                        - new_d * 0.3
                generic_candidates.append((score, uid, tgt["col"], tgt["row"], u))
    if generic_candidates:
        generic_candidates.sort(reverse=True, key=lambda x: x[0])
        _, uid, c, r, u = generic_candidates[0]
        return ("move",
                {"unit_id": uid, "col": c, "row": r,
                 "rationale": f"Forward {u['type']}"},
                f"FWD {u['type']}")

    # Any special non-trainer action
    for sp in specials:
        uid = sp["unit_id"]
        u = my_units[uid]
        for act in sp["actions"]:
            if act["action"] in ("reveal", "conceal", "weaken"):
                return ("special",
                        {"unit_id": uid, "action": act["action"],
                         "target_id": act["target_id"],
                         "rationale": f"Fallback special {act['action']}"},
                        f"FB_SPECIAL {act['action']}")

    # Any legal attack (avoid wasted combos)
    for aid, tid, _ in flat_attacks:
        au = my_units[aid]
        tu = enemy_units.get(tid, {})
        if au["type"] == "fighter" and tu.get("category") in ("ground", "special"):
            continue
        if tu.get("type") == "mine_field" and au["type"] != "engineer":
            continue
        return ("attack",
                {"attacker_id": aid, "target_id": tid,
                 "rationale": "Last-resort attack"},
                "LAST_ATK")

    # Anything at all
    if moves:
        mv = moves[0]
        tgt = mv["targets"][0]
        return ("move",
                {"unit_id": mv["unit_id"], "col": tgt["col"], "row": tgt["row"],
                 "rationale": "Any move"},
                "ANY_MOVE")

    return ("pass", {"rationale": "nothing to do"}, "PASS")


# ============================================================
# Event tracking
# ============================================================

def process_events(events, me, agent_state):
    me_prefix = f"{me}_"
    for ev in events:
        agent_state["all_events"].append(ev)
        t = ev.get("type")
        atk = ev.get("attacker", "") or ev.get("unit", "") or ""
        tgt = ev.get("target", "") or ""

        if t == "attacker_wins":
            if atk.startswith(me_prefix):
                agent_state["my_kills"] += 1
                agent_state["enemy_units_lost"].append(tgt)
            else:
                agent_state["enemy_kills"] += 1
                agent_state["my_units_lost"].append(tgt)
        elif t == "defender_wins":
            if atk.startswith(me_prefix):
                agent_state["enemy_kills"] += 1
                agent_state["my_units_lost"].append(atk)
            else:
                agent_state["my_kills"] += 1
                agent_state["enemy_units_lost"].append(atk)
        elif t == "both_die":
            agent_state["my_kills"] += 1
            agent_state["enemy_kills"] += 1
            if atk.startswith(me_prefix):
                agent_state["my_units_lost"].append(atk)
                agent_state["enemy_units_lost"].append(tgt)
            else:
                agent_state["my_units_lost"].append(tgt)
                agent_state["enemy_units_lost"].append(atk)
        elif t in ("hacker_kills_terminator", "drone_kill", "artillery_kill"):
            if atk.startswith(me_prefix):
                agent_state["my_kills"] += 1
                agent_state["enemy_units_lost"].append(tgt)
            else:
                agent_state["enemy_kills"] += 1
                agent_state["my_units_lost"].append(tgt)
        elif t == "mine_kills_ground":
            if atk.startswith(me_prefix):
                agent_state["my_units_lost"].append(atk)
            else:
                agent_state["enemy_units_lost"].append(atk)
        elif t == "wasted_turn":
            agent_state["action_counts"]["wasted"] += 1


def update_stall(agent_state, uid, pre_row, post_row, me):
    """For player 1, forward = row +1; for player 2, forward = row -1."""
    if pre_row is None or post_row is None:
        return
    forward = (post_row > pre_row) if me == 1 else (post_row < pre_row)
    if forward:
        agent_state["unit_stall_count"][uid] = 0
    else:
        agent_state["unit_stall_count"][uid] = agent_state["unit_stall_count"].get(uid, 0) + 1


# ============================================================
# Main loop
# ============================================================

def log(state, msg):
    line = f"[t={state['turns_played']}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def play_turns(max_turns):
    cfg = load_game_cfg()
    gid = cfg["game_id"]
    me = cfg["me"]
    bh = {"X-API-Key": cfg["api_key"]}

    # Seed server-side bot map so actions attribute correctly
    if "bot_id" in cfg and gid not in server.BOT_PLAYERS:
        server.BOT_PLAYERS[gid] = {me: cfg["bot_id"]}

    agent_state = load_agent_state()
    done_this_run = 0

    while done_this_run < max_turns:
        r = cli.get(f"/api/bot/games/{gid}/state", headers=bh)
        if r.status_code != 200:
            log(agent_state, f"state error: {r.status_code} {r.text[:200]}")
            break
        st = r.json()
        if st.get("phase") == "finished":
            log(agent_state, f"GAME FINISHED: winner={st.get('winner')}, "
                              f"game_turn={st.get('turn')}, my_player={me}")
            save_agent_state(agent_state)
            return "finished"
        if st.get("current_player") != me:
            time.sleep(0.2)
            continue

        # Decide
        try:
            endpoint, payload, tag = pick_action(st, me, agent_state)
        except Exception as e:
            log(agent_state, f"pick_action error: {e}\n{traceback.format_exc()}")
            endpoint, payload, tag = ("pass", {"rationale": f"error-fallback: {e}"}, "ERROR")

        # Pre-state for stall tracking
        pre_row = None
        uid = payload.get("unit_id") or payload.get("attacker_id")
        if uid:
            u = next((u for u in st["my_units"] if u["id"] == uid), None)
            if u:
                pre_row = u["row"]

        resp = cli.post(f"/api/bot/games/{gid}/{endpoint}",
                         json=payload, headers=bh).json()
        if not resp.get("ok"):
            log(agent_state, f"action rejected: {endpoint} {payload} -> {resp}")
            cli.post(f"/api/bot/games/{gid}/pass",
                     json={"rationale": "fallback"}, headers=bh)
            agent_state["action_counts"]["pass"] += 1
        else:
            agent_state["action_counts"][endpoint] = \
                agent_state["action_counts"].get(endpoint, 0) + 1
            evs = resp.get("events", [])
            if evs:
                process_events(evs, me, agent_state)
            if resp.get("wasted"):
                agent_state["action_counts"]["wasted"] += 1
                log(agent_state, f"wasted: {tag}")

        if endpoint == "move" and uid:
            update_stall(agent_state, uid, pre_row, payload.get("row"), me)

        agent_state["turns_played"] += 1
        done_this_run += 1
        if agent_state["turns_played"] % CONFIG["progress_every"] == 0:
            log(agent_state,
                f"{tag} | kills me={agent_state['my_kills']} "
                f"enemy={agent_state['enemy_kills']} "
                f"counts={agent_state['action_counts']}")
        save_agent_state(agent_state)
    return "ongoing"


def main():
    p = argparse.ArgumentParser(description="Play one batch of Marshal & Spy turns.")
    p.add_argument("--turns", type=int,
                   default=int(os.environ.get("MS_TURNS", 50)),
                   help="Max turns to play in this invocation (default: 50)")
    p.add_argument("--reset", action="store_true",
                   help="Reset agent state before playing")
    args = p.parse_args()

    if args.reset:
        reset_agent_state()
        print("[reset] agent state cleared")
    status = play_turns(args.turns)
    print(f"[done] status={status}")


if __name__ == "__main__":
    main()

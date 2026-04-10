"""
AI Engine – scoring-based AI opponent for Maršál a Špión.
"""

import random
from game_engine import (
    GameState, Unit, UNIT_DEFS, CITADELS, ZONE_LIMITS,
    hex_distance, hex_neighbors, is_valid_hex, get_zone,
    RANGED_UNITS, NUM_ROWS, row_start, row_width,
)


class AI:
    def __init__(self, player: int = 2):
        self.player = player
        self.enemy = 3 - player
        self.decision_log = []  # detailed log of every decision

    # ========== PLACEMENT ==========

    def do_placement(self, gs: GameState):
        units = gs._player_units(self.player, placed=False)
        if not units:
            return

        level_assignments = {0: [], 1: [], 2: []}
        assigned = set()

        def can_assign(u, level):
            lim = ZONE_LIMITS[level]
            current = level_assignments[level]
            if u.is_special and sum(1 for x in current if x.is_special) >= lim["special"]:
                return False
            if u.type in ("assassin", "terminator") and any(x.type == u.type for x in current):
                return False
            if u.type == "artillery" and level != 0:
                return False
            return True

        def do_assign(u, level):
            level_assignments[level].append(u)
            assigned.add(u.id)

        # Phase 1: Place special units first (they have strict slot limits)
        specials = [u for u in units if u.is_special]
        # Artillery must go to level 0
        for u in specials:
            if u.type == "artillery" and u.id not in assigned:
                if can_assign(u, 0):
                    do_assign(u, 0)
        # Other specials: prefer spreading across levels
        for u in specials:
            if u.id not in assigned:
                for lvl in [0, 1, 2]:
                    if can_assign(u, lvl):
                        do_assign(u, lvl)
                        break

        # Phase 2: Place combat units – strongest to level 2 for max bonus
        combats = sorted([u for u in units if not u.is_special],
                         key=lambda u: u.base_attack, reverse=True)
        for u in combats:
            if u.id not in assigned:
                for lvl in [2, 1, 0]:
                    if can_assign(u, lvl):
                        do_assign(u, lvl)
                        break

        # Place units on the board
        for level, level_units in level_assignments.items():
            available_hexes = self._get_level_hexes(level)
            random.shuffle(available_hexes)
            for i, u in enumerate(level_units):
                if i < len(available_hexes):
                    col, row = available_hexes[i]
                    gs.place_unit(self.player, u.id, col, row)

    def _get_level_hexes(self, level: int) -> list:
        hexes = []
        for row_idx in range(NUM_ROWS):
            zt, zp, zl = get_zone(row_idx)
            if zp != self.player or zt != "level":
                continue
            if zl != level:
                continue
            s = row_start(row_idx)
            w = row_width(row_idx)
            for i in range(w):
                col = s + i
                from game_engine import MOUNTAINS
                if (col, row_idx) not in MOUNTAINS:
                    hexes.append((col, row_idx))
        return hexes

    # ========== BATTLE ==========

    def do_turn(self, gs: GameState) -> dict:
        if gs.phase != "battle" or gs.current_player != self.player:
            return {"action": "none"}

        turn_log = {
            "turn": gs.turn, "player": self.player,
            "my_units": [], "known_enemies": [], "unknown_enemies": 0,
            "options_evaluated": 0, "top_options": [], "chosen": None,
        }

        my_units = gs._player_units(self.player, placed=True)
        enemy_citadel = CITADELS[self.enemy]

        # Log what AI sees
        for u in my_units:
            turn_log["my_units"].append({
                "id": u.id, "type": u.type, "attack": u.attack,
                "pos": [u.col, u.row], "revealed": u.revealed,
            })
        for e in gs._player_units(self.enemy, placed=True):
            if e.revealed:
                turn_log["known_enemies"].append({
                    "id": e.id, "type": e.type, "attack": e.attack,
                    "pos": [e.col, e.row],
                })
            else:
                turn_log["unknown_enemies"] += 1

        best_score = -999
        best_action = None
        all_options = []

        for u in my_units:
            # Score movement options
            if u.movement > 0:
                reachable = gs.get_reachable(u)
                for (c, r) in reachable:
                    score = self._score_move(gs, u, c, r, enemy_citadel)
                    opt = {"action": "move", "unit_id": u.id, "unit": u.type,
                           "col": c, "row": r, "score": round(score, 2)}
                    all_options.append(opt)
                    if score > best_score:
                        best_score = score
                        best_action = {"action": "move", "unit_id": u.id, "col": c, "row": r}

            # Score attack options
            targets = gs.get_attack_targets(u)
            for t in targets:
                score = self._score_attack(gs, u, t)
                tgt = gs.unit(t["unit_id"])
                opt = {"action": "attack", "unit_id": u.id, "unit": u.type,
                       "target": tgt.type if tgt and tgt.revealed else "?",
                       "target_atk": tgt.attack if tgt and tgt.revealed else "?",
                       "score": round(score, 2)}
                all_options.append(opt)
                if score > best_score:
                    best_score = score
                    best_action = {"action": "attack", "unit_id": u.id, "target_id": t["unit_id"]}

            # Score special actions
            specials = gs.get_special_actions(u)
            for s in specials:
                score = self._score_special(gs, u, s)
                opt = {"action": "special", "unit_id": u.id, "unit": u.type,
                       "special": s["action"], "target_id": s["target_id"],
                       "score": round(score, 2)}
                all_options.append(opt)
                if score > best_score:
                    best_score = score
                    best_action = {"action": "special", "unit_id": u.id,
                                   "special_action": s["action"], "target_id": s["target_id"]}

        turn_log["options_evaluated"] = len(all_options)
        # Keep top 5 options for the log
        all_options.sort(key=lambda x: x["score"], reverse=True)
        turn_log["top_options"] = all_options[:5]

        # Execute best action or pass
        if best_action is None or best_score < -10:
            gs.pass_turn(self.player)
            turn_log["chosen"] = {"action": "pass", "reason": "no good options" if all_options else "no options"}
            self.decision_log.append(turn_log)
            return {"action": "pass", "ai_log": turn_log}

        turn_log["chosen"] = {**best_action, "score": round(best_score, 2)}

        if best_action["action"] == "move":
            gs.move_unit(self.player, best_action["unit_id"],
                         best_action["col"], best_action["row"])
        elif best_action["action"] == "attack":
            gs.attack_unit(self.player, best_action["unit_id"],
                           best_action["target_id"])
        elif best_action["action"] == "special":
            gs.do_special(self.player, best_action["unit_id"],
                          best_action["special_action"], best_action["target_id"])

        self.decision_log.append(turn_log)
        return {**best_action, "ai_log": turn_log}

    def _score_move(self, gs: GameState, u: Unit, col: int, row: int,
                    enemy_citadel: tuple) -> float:
        score = 0.0
        current_dist = hex_distance(u.col, u.row, enemy_citadel[0], enemy_citadel[1])
        new_dist = hex_distance(col, row, enemy_citadel[0], enemy_citadel[1])

        # Reward moving toward enemy citadel (ground units)
        if u.category == "ground":
            score += (current_dist - new_dist) * 3.0
            # Bonus for strong units moving forward
            score += u.attack * 0.3
            # Big bonus for reaching citadel
            if (col, row) == enemy_citadel:
                score += 100

        # Reward positioning for special units
        if u.type == "recon_drone":
            # Move toward unrevealed enemies
            enemies = [e for e in gs._player_units(self.enemy, placed=True) if not e.revealed]
            if enemies:
                closest = min(hex_distance(col, row, e.col, e.row) for e in enemies)
                score += max(0, 5 - closest) * 2

        if u.type in ("trainer", "corruptor"):
            # Stay near friendly combat units
            allies = [a for a in gs._player_units(self.player, placed=True)
                      if a.category in ("ground", "air") and a.id != u.id]
            if allies:
                closest = min(hex_distance(col, row, a.col, a.row) for a in allies)
                score += max(0, 3 - closest) * 1.5

        # Avoid moving into danger (adjacent to strong revealed enemies)
        for nc, nr in hex_neighbors(col, row):
            e = gs.unit_at(nc, nr)
            if e and e.owner == self.enemy and e.revealed:
                if e.attack > u.attack:
                    score -= 5
                elif e.attack <= u.attack:
                    score += 1  # Opportunity

        # Defend citadel
        my_citadel = CITADELS[self.player]
        dist_to_own = hex_distance(col, row, my_citadel[0], my_citadel[1])
        if dist_to_own <= 2 and u.category == "ground":
            # Some units should stay back
            nearby_enemies = sum(1 for e in gs._player_units(self.enemy, placed=True)
                                 if hex_distance(e.col, e.row, my_citadel[0], my_citadel[1]) <= 4)
            if nearby_enemies > 0:
                score += 2  # Reward staying near citadel when enemies are close

        # Small random factor to break ties
        score += random.uniform(-0.5, 0.5)
        return score

    def _score_attack(self, gs: GameState, u: Unit, target: dict) -> float:
        score = 0.0
        tgt = gs.unit(target["unit_id"])
        if not tgt:
            return -100

        at_type = target["attack_type"]

        if at_type == "ranged":
            # Ranged is safe for attacker
            if u.attack >= tgt.attack if tgt.revealed else u.attack >= 3:
                score += 15 + u.attack
            else:
                score += 5  # Still safe, reveals target
            if tgt.revealed:
                score += tgt.attack * 2  # Higher value targets

        elif at_type == "melee_air":
            # Helicopter vs ground - safe for helicopter
            if tgt.revealed:
                if u.attack >= tgt.attack:
                    score += 20 + tgt.attack * 2
                else:
                    score += 2  # Safe but no kill
            else:
                score += 8  # Unknown target, but safe

        else:
            # Melee combat
            if tgt.revealed:
                if u.attack > tgt.attack:
                    score += 20 + tgt.attack * 2
                elif u.attack == tgt.attack:
                    score += 5 if u.attack <= 3 else -2  # Trade cheap units
                else:
                    score -= 10  # Would die

                # Special: attacking assassin is good (it dies to anyone)
                if tgt.type == "assassin" and u.category == "ground":
                    score += 30
            else:
                # Unknown target - risky
                if u.attack >= 5:
                    score += 5  # Strong unit likely wins
                elif u.attack >= 3:
                    score += 1  # Might win, reveals target
                else:
                    score -= 3  # Weak unit, risky

            # Don't sacrifice unique high-value units blindly
            if u.type in ("terminator", "assassin") and not tgt.revealed:
                score -= 10

        score += random.uniform(-0.5, 0.5)
        return score

    def _score_special(self, gs: GameState, u: Unit, action: dict) -> float:
        score = 0.0
        act = action["action"]

        if act == "reveal":
            score += 12  # Information is valuable

        elif act == "boost":
            tgt = gs.unit(action["target_id"])
            if tgt and tgt.category in ("ground", "air"):
                score += 8 + tgt.attack * 0.5  # Boost stronger units more

        elif act == "weaken":
            tgt = gs.unit(action["target_id"])
            if tgt:
                score += 10 + tgt.attack * 0.5

        elif act == "conceal":
            score += 7  # Re-hiding is useful

        elif act == "defuse":
            score += 15  # Clearing mines is very useful

        score += random.uniform(-0.5, 0.5)
        return score

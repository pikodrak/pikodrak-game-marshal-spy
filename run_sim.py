#!/usr/bin/env python3
"""
AI vs AI simulation runner for Marshal & Spy.

Usage:
    python3 run_sim.py                        # single game, verbose
    python3 run_sim.py -n 10                  # 10 games
    python3 run_sim.py -n 50 --quiet          # 50 games, summary only
    python3 run_sim.py --max-turns 200        # custom turn limit
    python3 run_sim.py -n 100 --parallel 4    # 4 worker processes

External HTTP AIs should talk to the server's /api/bot/* endpoints directly;
this script only drives the built-in AI for regression / balance testing.
"""

import argparse
import json
import os
import time
from datetime import datetime
from multiprocessing import Pool

from game_engine import GameState
from ai_engine import AI

LOG_DIR = "game_logs"


def run_game(max_turns=300, verbose=True):
    gs = GameState(mode="sim")
    ai1 = AI(player=1)
    ai2 = AI(player=2)

    game_log = {
        "game_id": gs.game_id,
        "started_at": datetime.now().isoformat(),
        "mode": "sim",
        "max_turns": max_turns,
        "placement": {1: [], 2: []},
        "turns": [],
        "result": None,
    }

    # Placement
    ai1.do_placement(gs)
    ai2.do_placement(gs)

    for player in (1, 2):
        for u in gs.units:
            if u.owner == player and u.placed:
                game_log["placement"][player].append({
                    "unit": u.type, "col": u.col, "row": u.row,
                    "attack": u.attack, "level": u.placement_level,
                })

    gs.confirm_placement(1)
    gs.confirm_placement(2)

    if verbose:
        print(f"Game {gs.game_id} started")
        p1_atk = sum(u.attack for u in gs.units if u.owner == 1 and u.alive and u.type != "mine")
        p2_atk = sum(u.attack for u in gs.units if u.owner == 2 and u.alive and u.type != "mine")
        print(f"  P1 total attack: {p1_atk}, P2 total attack: {p2_atk}")

    # Battle
    turn_limit = max_turns * 2  # each "turn" is a half-turn (one player action)
    actions = 0
    while gs.phase == "battle" and actions < turn_limit:
        cp = gs.current_player
        ai = ai1 if cp == 1 else ai2

        log_before = len(gs.log)
        result = ai.do_turn(gs)
        new_logs = gs.log[log_before:]

        # AI.do_turn returns a dict with "kind" (move/attack/special/pass)
        turn_entry = {
            "turn": gs.turn,
            "player": cp,
            "action": result.get("kind") or result.get("action") or "pass",
            "details": {k: v for k, v in result.items() if k != "ai_log"},
            "ai_decision": result.get("ai_log"),
            "events": [l["msg"] for l in new_logs],
        }
        game_log["turns"].append(turn_entry)
        actions += 1

        if verbose and result.get("action") not in ("move", "pass", "none"):
            for l in new_logs:
                print(f"  [T{l['turn']}] {l['msg']}")

    # Result
    p1_alive = len([u for u in gs.units if u.owner == 1 and u.alive])
    p2_alive = len([u for u in gs.units if u.owner == 2 and u.alive])

    if gs.winner:
        result_str = f"Player {gs.winner} wins"
    elif gs.turn >= max_turns:
        result_str = "Draw (turn limit)"
    else:
        result_str = "Unknown"

    game_log["result"] = {
        "winner": gs.winner,
        "turns_played": gs.turn,
        "p1_alive": p1_alive,
        "p2_alive": p2_alive,
        "result": result_str,
        "finished_at": datetime.now().isoformat(),
    }

    if verbose:
        print(f"  Result: {result_str} after {gs.turn} turns")
        print(f"  P1 alive: {p1_alive}, P2 alive: {p2_alive}")

    # Save log
    save_game_log(game_log)
    return game_log


def save_game_log(log):
    os.makedirs(LOG_DIR, exist_ok=True)
    fname = f"{LOG_DIR}/{log['game_id']}.json"
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


def _worker(args_tuple):
    """Pool-worker wrapper: unpack args, run one game, return summary."""
    max_turns, quiet = args_tuple
    log = run_game(max_turns=max_turns, verbose=not quiet)
    return log["result"]


def main():
    parser = argparse.ArgumentParser(description="Marshal & Spy – AI Simulation")
    parser.add_argument("-n", "--num-games", type=int, default=1, help="Number of games")
    parser.add_argument("--max-turns", type=int, default=300, help="Max turns per game")
    parser.add_argument("--quiet", action="store_true", help="Summary only")
    parser.add_argument("--parallel", type=int, default=1, help="Parallel worker processes")
    args = parser.parse_args()

    results = {1: 0, 2: 0, "draw": 0}
    total_turns = 0
    t0 = time.time()

    quiet = args.quiet or args.parallel > 1  # parallel output would be tangled

    if args.parallel > 1:
        with Pool(args.parallel) as pool:
            summaries = pool.map(_worker, [(args.max_turns, quiet)] * args.num_games)
    else:
        summaries = []
        for i in range(args.num_games):
            if not quiet and args.num_games > 1:
                print(f"\n=== Game {i + 1}/{args.num_games} ===")
            log = run_game(max_turns=args.max_turns, verbose=not quiet)
            summaries.append(log["result"])

    for r in summaries:
        w = r["winner"]
        if w: results[w] += 1
        else: results["draw"] += 1
        total_turns += r["turns_played"]

    elapsed = time.time() - t0
    print(f"\n{'='*40}")
    print(f"SIMULATION SUMMARY ({args.num_games} games, parallel={args.parallel})")
    print(f"{'='*40}")
    print(f"Player 1 wins: {results[1]} ({results[1]/args.num_games*100:.0f}%)")
    print(f"Player 2 wins: {results[2]} ({results[2]/args.num_games*100:.0f}%)")
    print(f"Draws:         {results['draw']}")
    print(f"Avg turns:     {total_turns / args.num_games:.1f}")
    print(f"Time:          {elapsed:.1f}s ({elapsed/args.num_games:.2f}s/game wall)")
    print(f"Logs saved to: {LOG_DIR}/")


if __name__ == "__main__":
    main()

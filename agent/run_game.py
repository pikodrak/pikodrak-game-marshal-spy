"""Bootstrap and/or finish a game.

Usage:

  # Create a brand new game vs built-in CPU AI with the curated placement,
  # then play until it ends:
  python -m agent.run_game --new

  # Create a new game only (don't play):
  python -m agent.run_game --new --no-play

  # Continue an existing saved game in state/current_game.json:
  python -m agent.run_game

  # Start fresh (resets agent_state too) and cap at 200 turns:
  python -m agent.run_game --new --max-turns 200
"""
import os
import sys
import json
import argparse
import secrets
import string

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402
import auth  # noqa: E402
from game_engine import level_hexes  # noqa: E402

from agent.player import play_turns, reset_agent_state, GAME_FILE, STATE_DIR  # noqa: E402

cli = TestClient(server.app)

# Curated placement: fills all 51 hexes with a balanced composition.
# Edit this if you want to experiment with different formations.
FORMATION_PLAN = [
    # L0 (9 hexes): defensive wall + artillery + mine
    ("artillery",   0, 1),
    ("mine_field",  0, 1),
    ("engineer",    0, 7),
    # L1 (17 hexes): infantry backbone + mines + scouts
    ("mine_field",  1, 3),
    ("private",     1, 10),
    ("scout",       1, 4),
    # L2 (25 hexes): strike force
    ("paratrooper", 2, 5),
    ("tank",        2, 4),
    ("terminator",  2, 1),
    ("hacker",      2, 1),
    ("helicopter",  2, 4),
    ("fighter",     2, 4),
    ("scout",       2, 2),
    ("recon_drone", 2, 1),
    ("attack_drone", 2, 1),
    ("trainer",     2, 1),
    ("corruptor",   2, 1),
    ("engineer",    2, 1),
]


def _rand_suffix(n=6):
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def bootstrap_game(username_prefix="agent", bot_name_prefix="HeuristicBot"):
    """Register a fresh user, create a bot account, start a game vs AI,
    place units via FORMATION_PLAN, confirm placement."""
    uname = f"{username_prefix}_{_rand_suffix()}"
    pwd = _rand_suffix(12)

    tok = cli.post("/api/auth/register",
                   json={"username": uname, "password": pwd}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    bot_name = f"{bot_name_prefix}_{_rand_suffix()}"
    bot = cli.post("/api/bot-accounts",
                    json={"bot_name": bot_name}, headers=h).json()
    api_key = bot["api_key"]
    # Need bot_id for server.BOT_PLAYERS mapping so the replay records properly
    my_bots = cli.get("/api/bot-accounts", headers=h).json()["bots"]
    bot_id = next(b["id"] for b in my_bots if b["bot_name"] == bot_name)

    bh = {"X-API-Key": api_key}
    g = cli.post("/api/bot/games", json={"opponent": "ai"}, headers=bh).json()
    gid = g["game_id"]
    me = g["player"]

    # Placement
    hexes_by_lvl = {0: [], 1: [], 2: []}
    for (c, r) in level_hexes(me):
        hexes_by_lvl[server.GAMES[gid].board[(c, r)]["zone_level"]].append((c, r))
    front_sign = 1 if me == 1 else -1
    for lvl in (0, 1, 2):
        hexes_by_lvl[lvl].sort(key=lambda h: (-front_sign * h[1], h[0]))

    used = {0: 0, 1: 0, 2: 0}
    for utype, lvl, count in FORMATION_PLAN:
        for _ in range(count):
            if used[lvl] >= len(hexes_by_lvl[lvl]):
                break
            c, r = hexes_by_lvl[lvl][used[lvl]]
            cli.post(f"/api/bot/games/{gid}/place",
                     json={"utype": utype, "col": c, "row": r}, headers=bh)
            used[lvl] += 1

    # Fill any leftover hex with an engineer/private (safety net)
    st = cli.get(f"/api/bot/games/{gid}/state", headers=bh).json()
    placed = {(u["col"], u["row"]) for u in st["my_units"] if u["placed"]}
    for lvl in (0, 1, 2):
        for (c, r) in hexes_by_lvl[lvl]:
            if (c, r) in placed:
                continue
            for fb in ("engineer", "private", "scout", "paratrooper", "tank"):
                rr = cli.post(f"/api/bot/games/{gid}/place",
                               json={"utype": fb, "col": c, "row": r},
                               headers=bh).json()
                if rr.get("ok"):
                    placed.add((c, r))
                    break

    cli.post(f"/api/bot/games/{gid}/confirm", json={"force": True}, headers=bh)

    # Persist
    os.makedirs(STATE_DIR, exist_ok=True)
    cfg = {
        "game_id": gid,
        "me": me,
        "api_key": api_key,
        "bot_id": bot_id,
        "bot_name": bot_name,
        "username": uname,
    }
    json.dump(cfg, open(GAME_FILE, "w"), indent=2)
    print(f"[bootstrap] game_id={gid}, me=player{me}, placed={len(placed)}/51, "
          f"bot_name={bot_name}")
    return cfg


def final_report():
    cfg = json.load(open(GAME_FILE))
    gid = cfg["game_id"]
    me = cfg["me"]
    bh = {"X-API-Key": cfg["api_key"]}
    st = cli.get(f"/api/bot/games/{gid}/state", headers=bh).json()
    print("=" * 60)
    if st.get("phase") == "finished":
        winner = st.get("winner")
        print(f"FINAL: winner={winner} "
              f"({'YOU WIN' if winner == me else 'YOU LOSE' if winner else 'DRAW'})")
    else:
        print(f"ONGOING: turn={st.get('turn')}, phase={st.get('phase')}")
    # Agent stats
    from agent.player import AGENT_STATE_FILE
    if os.path.exists(AGENT_STATE_FILE):
        s = json.load(open(AGENT_STATE_FILE))
        print(f"Turns played: {s.get('turns_played')}")
        print(f"Kills: me={s.get('my_kills')} enemy={s.get('enemy_kills')}")
        print(f"Action counts: {s.get('action_counts')}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--new", action="store_true",
                   help="Create a fresh game (overwrites state/current_game.json)")
    p.add_argument("--no-play", action="store_true",
                   help="Just bootstrap, don't play")
    p.add_argument("--max-turns", type=int, default=300,
                   help="Max agent turns to play per game (safety cap)")
    p.add_argument("--batch", type=int, default=50,
                   help="Turns per inner batch (each batch re-saves agent state)")
    args = p.parse_args()

    if args.new:
        bootstrap_game()
        reset_agent_state()

    if args.no_play:
        return

    played = 0
    while played < args.max_turns:
        status = play_turns(min(args.batch, args.max_turns - played))
        played += args.batch
        if status == "finished":
            break
    final_report()


if __name__ == "__main__":
    main()

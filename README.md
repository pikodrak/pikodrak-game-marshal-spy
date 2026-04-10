# Marshal & Spy (Maršál a Špión)

2-player strategic board game with fog of war on a hexagonal diamond grid.
Hidden unit identities, special abilities, and tactical depth inspired by Stratego.

## Quick Start

```bash
pip install fastapi uvicorn
python3 server.py
# Open http://localhost:8030
```

## Game Overview

Two players deploy armies on opposite sides of a diamond-shaped hex board.
All units are **hidden** — the opponent sees positions but not types.
Goal: **capture the enemy citadel** with a ground unit.

### Board
- 17-row symmetric diamond, 153 hexes
- Zones per player: Citadel → Level 0 → Level 1 → Level 2 → Battlefield
- 4 fixed mountains in the battlefield (block ground movement)
- Players always see their own territory at the bottom

### Phases
1. **Deployment** — place all 23 units into Level 0/1/2 zones (not citadel or battlefield)
2. **Battle** — alternating turns, one action per turn: Move, Attack, or Special

### Units (23 per player)

| Type | Attack | Move | Category | Count | Notes |
|------|--------|------|----------|-------|-------|
| Engineer | 1 | 1 | Ground | 1 | Conceals allies, defuses mines |
| Scout | 2 | 2 | Ground | 2 | Fast recon |
| Private | 3 | 1 | Ground | 3 | Basic infantry |
| Paratrooper | 4 | 1 | Ground | 2 | Airborne infantry |
| Tank | 5 | 1 | Ground | 2 | Heavy armor |
| Terminator | 6 | 1 | Ground | 1 | Max 1 per level |
| Assassin | 8 | 1 | Ground | 1 | Kills all, dies when attacked. Max 1/level |
| Helicopter | 3 | 2 | Air | 2 | Attacks ground safely, ignores mountains |
| Fighter | 5 | 4 | Air | 1 | Air-to-air only, ranged |
| Recon Drone | 1 | 1 | Special | 1 | Reveals enemies at range |
| Attack Drone | 4 | 1 | Special | 1 | Ranged attack |
| Trainer | 1 | 1 | Special | 1 | Boosts ally attack +1 |
| Corruptor | 1 | 1 | Special | 1 | Weakens enemy attack -1 |
| Jammer | 1 | 1 | Special | 1 | Prevents nearby reveals |
| Mine | 99 | 0 | Special | 2 | Kills ground units on contact |
| Artillery | 6 | 0 | Special | 1 | Ranged ground attack. Level 0 only |

### Level Bonuses
Units placed at higher levels get permanent attack bonus:
- Level 0: +0
- Level 1: +1
- Level 2: +2

### Placement Limits
- Special units per level: max 5 + level number (L0=5, L1=6, L2=7)
- Max 1 Assassin and 1 Terminator per level
- Artillery: Level 0 only
- All ranged/recon abilities: range = 2 + placement level

### Combat
- **Melee**: higher attack wins. Equal = both die. Winner moves to target hex.
- **Ranged**: attacker ≥ target = kill. Attacker < target = miss. Attacker survives.
- **Helicopter vs Ground**: helicopter always survives (can't kill stronger, but safe).
- **Assassin defending**: dies to any ground unit attack.
- **Mine**: triggered when ground unit enters hex. Both destroyed.
- **Jammer**: nearby friendly units can't be revealed.

## Architecture

```
server.py          FastAPI server (port 8030)
game_engine.py     Core game logic, hex grid, combat
ai_engine.py       Scoring-based AI with decision logging
auth.py            Users, JWT, ELO, saves
run_sim.py         CLI AI vs AI simulation
static/index.html  Full SPA frontend (Canvas)
game_logs/         JSON logs of all completed games
```

## Game Modes
- **VS AI** — single player against AI opponent
- **Online PVP** — 2 players via server
- **Hot-seat** — 2 players on 1 device

## Features
- ELO ranking system with leaderboard
- Save/load games, autosave every turn
- Futuristic military UI theme
- Swappable sprite system (`static/img/units/{type}.png`)
- Zoom (scroll wheel), pan (drag), fullscreen
- Animated unit movement with camera follow
- AI decision logging for strategy analysis
- CLI simulation runner for balance testing

## API

See [API.md](API.md) for full API reference.

## Simulation

```bash
python3 run_sim.py -n 10 --max-turns 200          # 10 games, verbose
python3 run_sim.py -n 100 --max-turns 300 --quiet  # 100 games, summary only
```

Detailed AI decision logs saved to `game_logs/`.

## Deployment

Server runs on port 8030. Use nginx reverse proxy for production:

```nginx
server {
    listen 80;
    server_name game-kopstos.x86.cz;
    location / {
        proxy_pass http://localhost:8030;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

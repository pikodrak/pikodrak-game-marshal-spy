# API Reference – Marshal & Spy

Base URL: `http://localhost:8030`

All endpoints except register/login require `Authorization: Bearer <token>` header.

## Authentication

### POST /api/auth/register
```json
{ "username": "player1", "password": "secret" }
→ { "ok": true, "token": "...", "user_id": 1, "username": "player1" }
```

### POST /api/auth/login
```json
{ "username": "player1", "password": "secret" }
→ { "ok": true, "token": "...", "user_id": 1, "username": "player1" }
```

### GET /api/auth/me
```json
→ { "ok": true, "user_id": 1, "username": "player1", "stats": {...}, "email": "" }
```

### POST /api/auth/change_password
```json
{ "old_password": "old", "new_password": "new" }
→ { "ok": true }
```

### POST /api/auth/email
```json
{ "email": "user@example.com" }
→ { "ok": true }
```

## Leaderboard

### GET /api/leaderboard
```json
→ { "ok": true, "leaderboard": [
    { "username": "p1", "elo": 1032, "wins": 2, "losses": 1, "draws": 0, "games_played": 3 }
  ]}
```

### GET /api/stats/{user_id}
```json
→ { "ok": true, "stats": { "elo": 1032, "wins": 2, ... } }
```

## Game Management

### POST /api/game/new
```json
{ "mode": "ai" }   // "ai" | "online" | "hotseat"
→ { "ok": true, "game_id": "abc12345", "player": 1 }
```

### POST /api/game/join
```json
{ "game_id": "abc12345" }
→ { "ok": true, "game_id": "abc12345", "player": 2 }
```

### GET /api/game/list
```json
→ { "ok": true, "games": [
    { "game_id": "abc12345", "mode": "online", "phase": "battle", "players": 2, "joinable": false }
  ]}
```

## Game State

### GET /api/game/{game_id}/state
Returns filtered game state (fog of war applied).
```json
→ {
    "ok": true,
    "game_id": "abc12345",
    "phase": "battle",          // "placement" | "battle" | "finished"
    "current_player": 1,
    "turn": 5,
    "winner": null,
    "my_player": 1,
    "my_units": [
      { "id": "1_tank_0", "type": "tank", "attack": 7, "col": 5, "row": 8,
        "revealed": false, "movement": 1, "category": "ground", ... }
    ],
    "enemy_units": [
      { "id": "2_???_?", "type": "unknown", "attack": "?", "col": 3, "row": 5, "revealed": false }
    ],
    "unplaced_units": [...],    // during placement phase
    "dead_own": [...],
    "board": [...],             // all hex cells with zone info
    "log": [...],               // last 30 game events
    "available_actions": {      // only when it's your turn
      "moves": [{ "unit_id": "1_tank_0", "targets": [{"col":5,"row":9}, ...] }],
      "attacks": [{ "unit_id": "1_tank_0", "targets": [{"unit_id":"2_x","col":4,"row":8,"attack_type":"melee"}] }],
      "specials": [{ "unit_id": "1_recon_drone_0", "actions": [{"action":"reveal","target_id":"2_x","col":3,"row":7}] }]
    }
  }
```

## Game Actions (Placement Phase)

### POST /api/game/{game_id}/place
```json
{ "unit_id": "1_tank_0", "col": 5, "row": 6 }
→ { "ok": true }
```

### POST /api/game/{game_id}/unplace
```json
{ "unit_id": "1_tank_0" }
→ { "ok": true }
```

### POST /api/game/{game_id}/random_place
Auto-places all unplaced units using AI logic.
```json
→ { "ok": true, "placed": 23 }
```

### POST /api/game/{game_id}/confirm
Confirms placement. Battle starts when both players confirm.
```json
→ { "ok": true, "battle_started": true }
```

## Game Actions (Battle Phase)

### POST /api/game/{game_id}/move
```json
{ "unit_id": "1_tank_0", "col": 5, "row": 9 }
→ { "ok": true, "events": [...] }
```

### POST /api/game/{game_id}/attack
```json
{ "attacker_id": "1_tank_0", "target_id": "2_private_1" }
→ { "ok": true, "events": [{"type":"attacker_wins",...}], "attack_type": "melee" }
```

### POST /api/game/{game_id}/special
```json
{ "unit_id": "1_recon_drone_0", "action": "reveal", "target_id": "2_tank_0" }
→ { "ok": true, "events": [{"type":"revealed","unit_type":"tank","attack":7}] }
```
Special actions: `reveal`, `conceal`, `defuse`, `boost`, `weaken`

### POST /api/game/{game_id}/pass
```json
→ { "ok": true }
```

## Saves

### POST /api/game/{game_id}/save
```json
{ "name": "My Save" }
→ { "ok": true }
```

### GET /api/saves
```json
→ { "ok": true, "saves": [
    { "id": 1, "name": "My Save", "game_id": "abc12345", "is_autosave": 0, "created_at": 1712750000 }
  ]}
```

### POST /api/saves/{save_id}/load
```json
→ { "ok": true, "game_id": "abc12345" }
```

### DELETE /api/saves/{save_id}
```json
→ { "ok": true }
```

## Hot-seat Mode

### GET /api/game/{game_id}/state/{player_num}
Player-specific state view (1 or 2).

### POST /api/game/{game_id}/hotseat
Universal action endpoint for hot-seat games.
```json
{ "player": 1, "action": "move", "unit_id": "1_tank_0", "col": 5, "row": 9 }
```
Supported actions: `place`, `unplace`, `random_place`, `confirm`, `move`, `attack`, `special`, `pass_turn`

## Event Types

Events returned in action responses:
- `mine_triggered` — unit stepped on mine
- `citadel_captured` — game won
- `attacker_wins` — melee attacker won
- `defender_wins` — melee defender won
- `both_die` — equal combat
- `assassin_killed` — assassin vulnerability triggered
- `air_kills_ground` — helicopter destroyed ground unit
- `air_attack_fails` — helicopter couldn't kill stronger unit
- `ranged_kill` — ranged attack successful
- `ranged_fail` — ranged attack missed
- `concealed` — unit hidden by engineer
- `mine_defused` — mine cleared by engineer
- `revealed` — unit scouted by recon drone
- `boosted` — unit attack increased by trainer
- `weakened` — unit attack decreased by corruptor

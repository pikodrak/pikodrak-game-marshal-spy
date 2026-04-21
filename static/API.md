# API Reference – Marshal & Spy (v1.0)

Base URL (local): `http://localhost:8030`
Production: `https://game-kopstos.x86.cz`

Most endpoints require `Authorization: Bearer <token>`.
Admin endpoints additionally require a short-lived admin token obtained from `/api/admin/login`.
Bot API endpoints use `X-API-Key: <key>` (no user JWT).

---

## 1. Authentication (human users)

### POST /api/auth/register
```json
{ "username": "player1", "password": "secret" }
→ { "ok": true, "token": "…", "user_id": 1, "username": "player1" }
```

### POST /api/auth/login
```json
{ "username": "player1", "password": "secret" }
→ { "ok": true, "token": "…", "user_id": 1, "username": "player1" }
```

### GET /api/auth/me
```json
→ { "ok": true, "user_id": 1, "username": "player1", "stats": {…}, "email": "" }
```

### POST /api/auth/change_password
```json
{ "old_password": "old", "new_password": "new" }
→ { "ok": true }
```

### POST /api/auth/email
```json
{ "email": "you@example.com" } → { "ok": true }
```

### GET /api/leaderboard
Human ELO leaderboard (top 50).

### GET /api/stats/{user_id}

---

## 2. Game management (online / AI / hot-seat)

### POST /api/game/new
```json
{ "mode": "ai" | "online" | "hotseat" }
→ { "ok": true, "game_id": "abc12345", "player": 1 }
```
In `ai` mode, the AI is player 2 and auto-confirms its placement.

### POST /api/game/join
```json
{ "game_id": "abc12345" } → { "ok": true, "game_id": "...", "player": 2 }
```

### GET /api/game/list

### GET /api/game/{game_id}/state
Returns a player-specific view (fog of war applied). When it's your turn in battle, also returns `available_actions`.

Key fields:
- `phase`: `placement` | `battle` | `finished`
- `current_player`, `turn`, `ply`, `winner`
- `my_player`, `my_units[]`, `enemy_units[]` (revealed enemies include stats; hidden show `type:"unknown"`, `attack:"?"`)
- `level_hexes`: `{1: [[col,row],…], 2: [[…]]}` – every hex in each player's levels
- `citadels`, `mountains`, `board` (per-cell info)
- `placement_confirmed`, `first_confirmed`
- `hacker_conversions`: per-player counter (0–2)
- `last_action_ts`, `speedup_requested_by`, `speedup_deadline_ts`
- `available_actions`: `{moves:[], attacks:[], specials:[]}`

---

## 3. Placement phase

### POST /api/game/{game_id}/place
```json
{ "utype": "tank", "col": 7, "row": 5 }
→ { "ok": true, "unit_id": "1_tank_0" }
```
Error codes: `max_count_reached`, `special_cap_reached`, `artillery_level0_only`, `one_terminator_per_level`, `not_your_zone`, `hex_occupied`.

### POST /api/game/{game_id}/unplace
```json
{ "unit_id": "1_tank_0" } → { "ok": true }
```
Removes the unit from the roster (the slot returns to your max_count budget).

### POST /api/game/{game_id}/clear_placement
Wipes all your placed units. Typically called before `/apply_preset`.

### POST /api/game/{game_id}/apply_preset
```json
{ "preset": [{"utype":"engineer","col":7,"row":1}, …] }
→ { "ok": true, "errors": [...] }  // errors for entries the engine rejected
```

### POST /api/game/{game_id}/random_place
AI auto-fills your side of the board (for quick testing / fallback).

### POST /api/game/{game_id}/confirm
```json
{ "force": false }
→ { "ok": true, "battle_started": true, "first_confirmed": 1 }
```
Without `force`, fails with `hexes_not_filled` if any level hex is empty.
The first player to confirm gets the first battle move.

### POST /api/game/{game_id}/speedup
Opponent of the current turn-holder calls this after 2 minutes of inactivity.
Server starts a 60-second deadline; if the turn-holder doesn't act, the next `/state` request sees their turn forcibly passed.

---

## 4. Battle actions

### POST /api/game/{game_id}/move
```json
{ "unit_id": "1_tank_0", "col": 7, "row": 8 } → { "ok": true, "events": [...] }
```

### POST /api/game/{game_id}/attack
```json
{ "attacker_id": "1_tank_0", "target_id": "2_private_3" } → { "ok": true, "events": [...] }
```
If the attacker's category can't target the defender (e.g. Fighter vs ground, Mine vs anything), the response is `{ ok: true, wasted: true, events: [{type:"wasted_turn"}] }` and the turn passes.

### POST /api/game/{game_id}/special
```json
{ "unit_id": "1_trainer_0", "action": "boost", "target_id": "1_tank_0" }
→ { "ok": true, "events": [...] }
```
Actions by unit type:
- `engineer` → `conceal` (adjacent revealed ally)
- `recon_drone` → `reveal` (hidden enemy within 1+L, unless jammer covers it)
- `attack_drone` → `strike` (threshold-kill: attack<4 destroys; ≥4 = drone revealed + target temp-revealed 1 opp turn)
- `trainer` → `boost` (+1 ATK, max 2× per target); `convert_hacker` (own ATK>3 ground ⇒ Hacker, max 2×/game)
- `corruptor` → `weaken` (-1 ATK or -1 range, floor 1; wasted if at floor)
- `artillery` → `artillery_fire` (range 2, instakill any revealed ground/special target)

### POST /api/game/{game_id}/pass
End your turn without acting.

### Hot-seat universal action
`POST /api/game/{game_id}/hotseat` accepts `{player, action, …}` where `action ∈ {place, unplace, clear, preset, random_place, confirm, move, attack, special, pass_turn}`.

---

## 5. Saved formations (presets)

Each user has 5 preset slots (1–5). Name limit: 12 chars.

### GET /api/presets
```json
→ { "ok": true, "presets": [
    { "id": 1, "slot": 1, "name": "Blitz", "units": [
        { "utype": "tank", "col": 7, "row": 5 }, …
      ], "updated_at": 1234567890 }
  ]}
```

### POST /api/presets
```json
{ "slot": 1, "name": "Blitz", "units": [...] } → { "ok": true }
```

### DELETE /api/presets/{slot}

---

## 6. Replays

### GET /api/replays?mine=true|false
```json
→ { "ok": true, "replays": [
    { "id": 12, "game_id": "…", "mode": "ai", "player1_label": "karlos",
      "player2_label": "AI", "winner": 1, "turns": 32, "finished_at": 1234 },
    …
  ]}
```
`mine=true` filters to games this user played; `mine=false` returns global recent games.

### GET /api/replays/{replay_id}
```json
→ { "ok": true, "replay": {
    "id": 12, "game_id": "…", "mode": "ai",
    "initial_state_json": "{…full GameState at battle start…}",
    "actions": [ {type:"move", player:1, unit_id:"…", to:[col,row], turn:1, ply:1, ts:…}, … ],
    "winner": 1, "turns": 32, "finished_at": 1234 }}
```
The client can rebuild any frame by loading `initial_state_json` and applying actions up to index N.

---

## 7. Admin API

Admin password is generated at first server boot and written to `admin_password.txt` (chmod 600). Change via `/api/admin/change_password`.

### POST /api/admin/login
```json
{ "password": "…" } → { "ok": true, "admin_token": "…" }
```
Include the token as `Authorization: Bearer <token>` (or `X-Admin-Token`) for admin endpoints. Expiry: 1 hour.

### POST /api/admin/change_password
```json
{ "new_password": "…" } → { "ok": true }
```

### GET /api/admin/unit_defs
```json
→ { "ok": true, "unit_defs": { "tank": {…}, … },
    "overrides": { "tank": { "base_attack": 6 } } }
```

### POST /api/admin/unit_override
```json
{ "utype": "tank", "base_attack": 6, "max_count": 5 } → { "ok": true }
```
Only the fields you include are updated; others stay at their previous override (or default).
Allowed fields: `base_attack`, `movement`, `base_range`, `max_count`, `category`.

### DELETE /api/admin/unit_override/{utype}
Clears all overrides for that type (reverts to `UNIT_DEFS` defaults).

---

## 8. Bot accounts (human-owned)

A human user can create **bot accounts**. Each has its own `api_key` used by the external bot software. Bot ELO is tracked separately from human ELO.

### GET /api/bot-accounts (needs user token)
Lists bots owned by the current user (includes their `api_key`, so the user can copy it into their bot runtime).

### POST /api/bot-accounts
```json
{ "bot_name": "MyBot", "can_play_humans": true }
→ { "ok": true, "api_key": "…", "bot_name": "MyBot" }
```

### DELETE /api/bot-accounts/{bot_id}

### GET /api/bot-leaderboard
Top 50 bots by ELO.

---

## 9. Bot API (external bots)

All endpoints in this section require `X-API-Key: <api_key>` and no user JWT.

### POST /api/bot/games
```json
{ "opponent": "ai" | "queue" | "human" }
→ { "ok": true, "game_id": "…", "player": 1, "opponent": "ai" }
```
- `opponent:"ai"` – immediately starts against the built-in AI as player 2.
- `opponent:"queue"` – enters the bot-vs-bot queue. If no other bot is waiting, response is `{opponent:"pending", queue_timeout_s:30}`; after 30 seconds without a match, call `/api/bot/queue/fallback/{game_id}` to convert to an AI game.
- `opponent:"human"` – future: wait for a human partner (requires `can_play_humans` on the bot account).

### POST /api/bot/queue/fallback/{game_id}
Converts a queued bot game into a bot-vs-AI game if no bot showed up in 30 s.

### GET /api/bot/games/{game_id}/state
Same shape as `/api/game/{id}/state` (fog of war, available_actions).

### POST /api/bot/games/{game_id}/place
```json
{ "utype": "tank", "col": 7, "row": 5 } → { "ok": true, "unit_id": "…" }
```

### POST /api/bot/games/{game_id}/clear_placement
### POST /api/bot/games/{game_id}/apply_preset
```json
{ "preset": [ {"utype":"tank","col":7,"row":5}, … ] }
```

### POST /api/bot/games/{game_id}/random_place
Built-in AI fills in the bot's side of the board (quick start).

### POST /api/bot/games/{game_id}/confirm
```json
{ "force": false } → { "ok": true, "battle_started": true, "first_confirmed": 1 }
```

### POST /api/bot/games/{game_id}/move
```json
{ "unit_id": "…", "col": 7, "row": 8 } → { "ok": true, "events": [...] }
```

### POST /api/bot/games/{game_id}/attack
```json
{ "attacker_id": "…", "target_id": "…" } → { "ok": true, "events": [...] }
```

### POST /api/bot/games/{game_id}/special
```json
{ "unit_id": "…", "action": "boost|conceal|reveal|strike|weaken|convert_hacker|artillery_fire",
  "target_id": "…" } → { "ok": true, "events": [...] }
```

### POST /api/bot/games/{game_id}/pass

### GET /api/bot/games/{game_id}/replay
After the game finishes, fetch the full replay JSON.

### Rate limits
Soft target: ≤60 requests/min per API key (not enforced in v1.0; add your own backoff).

---

## 10. Event types in `events[]`

- `attacker_wins`, `defender_wins`, `both_die` – standard melee outcome
- `hacker_kills_terminator` – hacker auto-kill
- `mine_kills_ground` – ground attacker destroyed by mine_field (mine revealed + stays)
- `mine_defused_by_attack` – engineer cleared mine_field (engineer stays)
- `mine_reveals_air` – helicopter & mine both revealed, both stay
- `wasted_turn` – e.g. fighter vs ground, corruptor at floor
- `citadel_captured` – game won
- `concealed`, `revealed` – engineer / recon drone effects
- `drone_kill`, `drone_miss` – attack drone outcome
- `boosted`, `weakened_attack`, `weakened_range` – trainer / corruptor
- `converted_to_hacker` – trainer conversion
- `artillery_kill`
- `weaken_wasted` – corruptor action blocked by floor

---

## 11. Error codes

Common error strings returned as `{ok:false, error:"…"}`:

- `not_placement_phase`, `not_battle_phase`, `already_confirmed`, `hexes_not_filled`
- `not_your_turn`, `not_your_zone`, `hex_occupied`, `invalid_hex`, `invalid_unit`, `invalid_target`
- `max_count_reached`, `special_cap_reached`, `artillery_level0_only`, `one_terminator_per_level`
- `cannot_std_attack`, `not_adjacent`, `target_not_in_range`, `invalid_special_action`
- `you_are_on_turn`, `already_requested`, `too_early` (speedup)
- `incompatible_engine_version` (v0.x save rejected)
- `admin_required`, `invalid_admin_password`
- `missing_api_key`, `invalid_api_key`, `not_owner`, `not_in_game`

---

## 12.5 Rules endpoint (for LLM agents)

### GET /api/bot/rules
Returns **machine-readable** game rules + unit defs. No auth required.
Inject the JSON directly into your LLM system prompt so the agent understands the game.

```json
→ {
  "ok": true,
  "version": "1.0",
  "description": "Maršál & Špión – 2-player hex-grid strategy with fog of war…",
  "board": {…},
  "placement_rules": {…},
  "battle_rules": {…},
  "unit_defs": { "tank": {…}, "hacker": {…}, … },
  "event_types": {…},
  "recommended_agent_workflow": [ "1. GET /api/bot/rules…", … ]
}
```

## 12.6 Rationale capture

Every bot action endpoint (`/move`, `/attack`, `/special`, `/pass`) accepts an optional
`rationale` string. The server stores it on the matching entry in `replay_actions`
and emits it into `game_logs/{game_id}.json`. Use it to persist the agent's reasoning
per turn for later analysis.

```json
POST /api/bot/games/{id}/attack
{
  "attacker_id": "1_tank_0",
  "target_id": "2_private_5",
  "rationale": "Tank has ATK 7; private has ATK 4 revealed. Safe kill near enemy citadel."
}
```

## 12. Example: minimal bot loop

```python
import requests

BASE = "https://game-kopstos.x86.cz"
KEY = "your_api_key_here"
H = {"X-API-Key": KEY}

# 1. New game vs built-in AI
g = requests.post(f"{BASE}/api/bot/games", json={"opponent": "ai"}, headers=H).json()
gid = g["game_id"]

# 2. Let the built-in AI pick the placement, then confirm
requests.post(f"{BASE}/api/bot/games/{gid}/random_place", headers=H)
requests.post(f"{BASE}/api/bot/games/{gid}/confirm", json={"force": False}, headers=H)

# 3. Play until finished
while True:
    s = requests.get(f"{BASE}/api/bot/games/{gid}/state", headers=H).json()
    if s["phase"] == "finished":
        print("winner:", s["winner"])
        break
    if s["current_player"] != 1:        # AI is still thinking
        continue
    aa = s["available_actions"]
    # Trivial: pick first available attack, else move, else pass
    if aa["attacks"]:
        a = aa["attacks"][0]
        t = a["targets"][0]
        requests.post(f"{BASE}/api/bot/games/{gid}/attack",
                      json={"attacker_id": a["unit_id"], "target_id": t["unit_id"]},
                      headers=H)
    elif aa["moves"]:
        m = aa["moves"][0]; t = m["targets"][0]
        requests.post(f"{BASE}/api/bot/games/{gid}/move",
                      json={"unit_id": m["unit_id"], "col": t["col"], "row": t["row"]},
                      headers=H)
    else:
        requests.post(f"{BASE}/api/bot/games/{gid}/pass", headers=H)

# 4. Fetch replay for analysis
replay = requests.get(f"{BASE}/api/bot/games/{gid}/replay", headers=H).json()
```

---

## 13. Claude agent template

This section gives you a working template for wiring a Claude API (or any LLM)
agent to the game.

### 13.1 System prompt

```text
You are playing Maršál & Špión, a 2-player hex-grid strategy game.

GAME RULES (injected from /api/bot/rules):
<<<RULES_JSON>>>

YOUR ROLE:
- You control player <<<PLAYER>>> (1 or 2).
- Every turn you receive the current state (fog of war applied) and a list
  of `available_actions` (moves / attacks / specials). You MUST pick from
  that list — do not invent unit IDs or hexes.
- Respond with STRICT JSON only, no prose:
  {
    "action": "move" | "attack" | "special" | "pass",
    "unit_id": "…",                        // for move / attack (attacker_id) / special
    "target_id": "…",                      // for attack / special
    "col": N, "row": N,                    // for move
    "special_action": "boost|conceal|…",   // for special
    "rationale": "<= 200 words, why this move"
  }

OBJECTIVES:
- Capture the enemy citadel with a ground (or special) unit → instant win.
- Destroy enemy ground+special capturers (movement > 0) to trigger elimination win.
- Preserve your Terminator (ATK 8+L). Hacker auto-kills it in melee.
- Mines (mine_field) are destroyed only by Engineer attacks. Any other
  ground attacker dies to the mine (the mine stays + reveals).
- Fighter vs non-air = wasted turn.

STRATEGIC HINTS:
- `available_actions` already respects fog of war, level caps, and
  adjacency. Trust it.
- Hidden enemies show `type:"unknown", attack:"?"`. Use recon_drone's
  `reveal` special to expose them before committing.
- Corruptor weakens at range 1+L and does NOT reveal itself.
- Attack drone kills instantly if target `attack < 4`; otherwise drone
  is revealed and target is briefly revealed.
- Trainer can boost an ally (+1 ATK, max 2× per unit) OR convert a
  friendly ATK>3 ground unit into a Hacker (max 2× per game).

Reply with JSON only. No markdown fences, no commentary.
```

### 13.2 Runtime loop

```python
import os, time, json, requests
from anthropic import Anthropic

BASE = os.environ["MS_BASE_URL"]       # e.g. https://game-kopstos.x86.cz
KEY  = os.environ["MS_API_KEY"]        # X-API-Key from /api/bot-accounts
H    = {"X-API-Key": KEY}

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def fetch_rules():
    return requests.get(f"{BASE}/api/bot/rules").json()

def new_game(opponent="ai"):
    r = requests.post(f"{BASE}/api/bot/games",
                      json={"opponent": opponent}, headers=H).json()
    return r["game_id"], r["player"]

def auto_place(gid):
    requests.post(f"{BASE}/api/bot/games/{gid}/random_place", headers=H)
    requests.post(f"{BASE}/api/bot/games/{gid}/confirm",
                  json={"force": False}, headers=H)

def get_state(gid):
    return requests.get(f"{BASE}/api/bot/games/{gid}/state", headers=H).json()

def ask_claude(system_prompt, user_prompt):
    msg = client.messages.create(
        model="claude-sonnet-4-6",  # or claude-opus-4-7 for stronger play
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = msg.content[0].text.strip()
    # Trim any accidental code fences
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    return json.loads(text)

def apply_action(gid, decision):
    act = decision["action"]
    body = {"rationale": decision.get("rationale", "")}
    if act == "move":
        body.update({"unit_id": decision["unit_id"],
                     "col": decision["col"], "row": decision["row"]})
        return requests.post(f"{BASE}/api/bot/games/{gid}/move", json=body, headers=H).json()
    if act == "attack":
        body.update({"attacker_id": decision["unit_id"],
                     "target_id":  decision["target_id"]})
        return requests.post(f"{BASE}/api/bot/games/{gid}/attack", json=body, headers=H).json()
    if act == "special":
        body.update({"unit_id": decision["unit_id"],
                     "action": decision["special_action"],
                     "target_id": decision["target_id"]})
        return requests.post(f"{BASE}/api/bot/games/{gid}/special", json=body, headers=H).json()
    return requests.post(f"{BASE}/api/bot/games/{gid}/pass", json=body, headers=H).json()

def main():
    rules = fetch_rules()
    gid, player = new_game("ai")
    auto_place(gid)

    system_prompt = open("system_prompt.txt").read().replace(
        "<<<RULES_JSON>>>", json.dumps(rules, ensure_ascii=False)
    ).replace("<<<PLAYER>>>", str(player))

    while True:
        s = get_state(gid)
        if s["phase"] == "finished":
            print("winner:", s["winner"]); break
        if s["current_player"] != player:
            time.sleep(2); continue

        user_prompt = json.dumps({
            "my_player": player,
            "phase": s["phase"],
            "turn": s["turn"],
            "my_units": s["my_units"],
            "enemy_units": s["enemy_units"],
            "available_actions": s["available_actions"],
            "log_tail": s["log"][-10:],
        }, ensure_ascii=False)

        try:
            decision = ask_claude(system_prompt, user_prompt)
        except Exception as e:
            print("LLM error:", e); decision = {"action": "pass"}
        apply_action(gid, decision)

    replay = requests.get(f"{BASE}/api/bot/games/{gid}/replay", headers=H).json()
    with open(f"replay_{gid}.json", "w") as f:
        json.dump(replay, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
```

### 13.3 Prompt-caching tip

The `rules` JSON (~10 kB) and the system prompt don't change between turns.
With the Claude API, mark the system prompt block as `cache_control:
{type: "ephemeral"}` to reuse it across turns of one game — each subsequent
turn pays only for the (small) user message.

### 13.4 What gets logged for analysis

After the game ends, `game_logs/{game_id}.json` contains:
- `placement` (per-player list of every unit placed at battle start)
- `events` (engine's human-readable event messages)
- `replay_actions` — every action in order, each tagged with the bot's
  `rationale` string if supplied → ideal for LLM post-mortem review
- `battle_start_state` (full state snapshot at battle start)
- `final_units` (everyone's end-state, alive or dead)
- `p{1,2}_alive_by_type` (what survived, by unit type)
- `hacker_conversions`, `first_confirmed`, `turns`, `winner`

`GET /api/bot/games/{id}/replay` returns the same content from the DB
(table `game_replays`).


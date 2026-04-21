# Agent — heuristický hráč Maršál & Špión

Tohle je ručně napsaný heuristický hráč, který hraje přes `/api/bot/*` proti vestavěnému CPU AI. Strategii odvozená z co fungovalo v iteračních experimentech. **Nevyhrává vždy**, ale je použitelný jako baseline i výchozí bod pro vylepšování.

## Struktura

```
agent/
├── __init__.py        – modul marker
├── config.py          – VŠECHNA nastavení strategie (priority, váhy, prahy)
├── player.py          – hlavní rozhodovací loop (decision tree P1..P9)
├── run_game.py        – bootstrap + orchestrace her
├── README.md          – tohle
└── state/             – runtime data (gitignored)
    ├── current_game.json   – info o aktuální hře (game_id, api_key, bot_id)
    ├── agent_state.json    – kumulativní statistiky (kill count, stall tracking)
    └── agent_log.txt       – log co dělal v jednotlivých tazích
```

## Rychlý start

Z rootu projektu (`/projects/game03`):

```bash
# 1) Založit novou hru proti AI a dohrát ji (max 300 mých tahů)
python -m agent.run_game --new

# 2) Pokračovat v rozehrané hře (použije state/current_game.json)
python -m agent.run_game

# 3) Jen rozehrát, pak hrát ručně/testovat jinou strategii
python -m agent.run_game --new --no-play

# 4) Jen odehrát 20 tahů aktuální hry a přestat (pro pomalou iteraci)
python -m agent.player --turns 20

# 5) Reset agent state (zachová hru, zapomene počty/stall)
python -m agent.player --reset
```

Po dohrání zavolá `final_report()` a vypíše vítěze + statistiky.

## Jak agent rozhoduje (priority 1-9)

Každý tah projde tímto decision tree. Vrací první nalezenou akci:

| # | Priorita | Co dělá |
|---|---|---|
| 1 | **WIN** | Jestli je tah na `(8,16)` (nepřátelská citadela), vezmi ho |
| 2 | **DEFEND** | Odhalený nepřítel ≤ 5 hexů od mé citadely → arty/drone insta-kill → melee kill (prefer levný vítěz) → blok silnějším obráncem |
| 3 | **COUNTER-ARTY** | Odhalená nepřátelská DB → okamžitě zabít (arty/dron/melee) |
| 4 | **KNOWN KILL** | Odhalený nepřítel kterého přebijeme → útok (Hacker auto-wins vs Terminator) |
| 4.5 | **BLOCK** | Nezabitelná hrozba → přesunout obránce s ATK ≥ hrozby mezi ni a citadelu |
| 5 | **TRAINER BOOST** | Boostnout nejlepšího rushera blíž soupeřovy citadele |
| 6 | **RECON** | Skryté jednotky blízko mé citadely → odhalit recon dronem |
| 7 | **ADVANCE** | Tlačit rushery (tank/term/para/scout/hacker) blíž `(8,16)`, s anti-stall penalizací |
| 8 | **AIR** | Vrtulníky postupují, stíhači loví odhalený vzduch |
| 9 | **FALLBACK** | Jakýkoli pohyb vpřed → jakýkoli legální útok → pas |

Implementace viz `player.py` → funkce `pick_action()`.

## Ladění strategie (`config.py`)

Všechny znatelné parametry jsou v `CONFIG` dict v `config.py`. Typické úpravy:

### Konzervativnější vs agresivnější obrana

```python
"defense_radius": 5,   # ← 3 = agresivnější (méně úzkostné blokování), 7 = obranný hrobař
"unknown_threat_assumed_atk": 4,  # jak silná asi je skrytá hrozba
```

### Jiný typ rushe

```python
"advance_priority_types": [  # měnit pořadí = měnit koho tlačit první
    "tank", "terminator", "paratrooper", "scout", "hacker",
    "private", "engineer",
],
```

Chceš scout-rush? Dej `scout` nahoru. Chceš tanky až po pár tahů? Dej je níž.

### Přísnost killů

```python
"require_strict_kill": True,       # útočit jen když MY_ATK > ENEMY_ATK
"prefer_cheap_killer": True,       # obětovat levné jednotky místo drahých
```

Když vypneš `strict`, agent půjde i do `MY_ATK == ENEMY_ATK` (oba umřou) – ztráta materiálu, ale otevírá cestu.

### Stall detection

```python
"stall_threshold_turns": 3,     # po 3 tazích bez posunu unit "zablokován"
"stall_penalty_per_turn": 5,    # -5 ze score za každý zablokovaný tah
```

Zvýšíš → agent dřív přepne na jinou jednotku / jinou cestu. Sníží → víc se snaží prorazit stejným kusem.

### Advance scoring

```python
"advance_delta_weight": 10,           # body za 1 hex progress
"advance_type_rank_weight": 2,        # penalizace za nižší typ (private víc než tank)
"advance_new_dist_weight": 0.5,       # penalizace za stále-daleko (preferuje blízko citadely)
```

Větší `advance_delta_weight` → priorita progress, menší → priorita dobrý typ jednotky.

## Jak iterovat strategii

Doporučený workflow:

```bash
# 1. Zahrát baseline hru
python -m agent.run_game --new --max-turns 300 2>&1 | tee state/game1.out

# 2. Podívej se co se pokazilo
cat state/agent_log.txt | tail -50
cat state/agent_state.json | python -m json.tool

# 3. Identifikuj vzor (víc killů, méně blokování, něco podobného?) a uprav config.py

# 4. Spusť znovu — run_game.py bez --new pokračuje, s --new začne čistě
python -m agent.run_game --new
```

Každá hra má unikátní game_id a nové bot_account, DB si to ukládá do `game_replays`. Takže:

```bash
# Prohlédnout seznam hraných her (pro replay přes UI):
python -c "import sys; sys.path.insert(0, '/projects/game03'); import auth; \
  [print(r) for r in auth.list_replays(limit=20)]"
```

## Časté úpravy strategie

| Chceš | Změň |
|---|---|
| Agresivnější útok | `advance_priority_types` = tank/term/para první; snížit `stall_penalty_per_turn` |
| Silnější obranu | `defense_radius` 5 → 7; zvýšit `block_priority` pro tank/terminator |
| Menší ztráty trade | `require_strict_kill` = True (už je); `prefer_cheap_killer` = True |
| Rychlejší scout rush | `boost_priority["scout"]` 40 → 80 |
| Využít Hackera jinak | Změnit `pick_action` — Hacker zatím jen auto-kill Term. Přidat mu útok na revealed weak ground? |
| Využít koruptora | Přidat explicit prioritu pro `weaken` akci v novém P-kroku |

## Známá omezení

- **Nepamatuje si co dělal v předchozích hrách** – `agent_state.json` je per-hra. Pro multi-hra učení přidej persistenci napříč hrami.
- **Heuristika není ML** – je to if/else stack. Nemá curiosity, simulace dopředu, nic takového. Pro LLM-based agenta použij Anthropic SDK + `api/bot/rules` endpoint (viz `API.md` §13).
- **Naive opponent modeling** – předpokládá soupeřovu ATK = 4 pokud neznám. Sofistikovanější by byla heatmapa kde se dříve ukazovaly odhalené jednotky.
- **Placement je fixní** – `FORMATION_PLAN` v `run_game.py`. Měnit = experimentovat s různými formacemi. Engine to bez problému přijme dokud se respektují max counts a level caps.

## Rozšíření, která by dávala smysl

1. **Multi-game learning** – persistovat `win_rate` a automaticky rezonovat parametry mezi hrami (grid search over config)
2. **Placement variations** – testovat různé `FORMATION_PLAN`, najít optimální
3. **LLM fallback na klíčové tahy** – když heuristika dá "FALLBACK PASS", místo toho zavolat Claude API s rationale prompt
4. **Look-ahead 1 ply** – před každým útokem simulovat "co CPU udělá dál?" (engine `from_dict` → aplikovat → získat `available_actions`)
5. **Opponent mining mining** – udržovat `expected_enemy_types` podle jejich pohybů (unit co stojí 20 tahů = mina)

## Server integrace

Agent mluví s `server.py` skrze FastAPI TestClient (in-process, bez síťové vrstvy). Výhoda: rychlé, žádný uvicorn. Nevýhoda: `server.py` běžící jako produkční proces o tom neví.

Pro produkční použití agenta jako externího bota:
1. Nastartovat `server.py` jako HTTP server (port 8030)
2. V agentovi nahradit `cli = TestClient(server.app)` za `cli = requests.Session()` nebo `httpx.Client(base_url="http://localhost:8030")`
3. `api_key` získat přes web UI nebo `python -c "import auth; print(auth.create_bot_account(user_id, 'MyBot'))"`

## Licence + autor

Součást projektu Maršál & Špión. Heuristika vznikla iteračně přes 10 testovacích her + analýzy + refactor Opus 4.7 subagentem 2026-04-21.

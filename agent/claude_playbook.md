# Claude playbook – kumulativní lekce pro Maršál & Špión

Tento soubor je **injectován do system promptu každého subagenta** na začátku hry. Obsahuje lekce z předchozích her proti CPU AI – co fungovalo a co ne. Aktualizuje se po každé hře.

---

## Základní principy (od začátku)

1. **Obrana vlastní citadely je prioritou #0.** Jakýkoli nepřítel (i zahalený!) do 5 hexů od mé citadely = okamžitá reakce.
2. **Počítej s tím že nepřítel utíká po úzkém sloupci** – typicky Vojín/Průzkumník se tvrdohlavě tlačí skrz col 8 ke tvé citadele. Monitorovat celé pole.
3. **Hacker = auto-kill Terminátor.** Drž Hackera poblíž Terminátorova pravděpodobného vektoru.
4. **Artillery je instakill v rozsahu 2** – odhalené nepřátelské DB musím zabít okamžitě, moje vlastní DB je nejcennější defensivní jednotka.
5. **Fighter nesmí útočit na zemní jednotku** (wasted turn). Fighter jen létá na air.
6. **Pozemka na mine_field = umře.** Jen ženista to bezpečně zničí.
7. **Attack drone = threshold kill (<4 ATK).** Silnější cíl → drone se odhalí, cíl dočasně odhalen 1 tah.
8. **Trainer boost max 2× per unit.** Prioritu na nejsilnější rushera (Terminátor/Tank/Výsadkář).

## Lekce z her

### Hra 1 (vyhrál jsem T105, 15:5 killů)

- **Hacker JEN útočí na Terminátora.** Hacker ATK 3 vs Tank ATK 7 = Hacker umře (defender_wins). Auto-kill pravidlo platí striktně jen vs Terminator. Hacker je **assassin**, ne blocker.
- **Recon v otevírce, ne pozdě.** Prvních 30 tahů cyklit reveal každé kolo když je co odhalit. T8/23/25/28/30/32 jsem odhalil 5 jednotek → tanky padly → vyhráno. Bez recon by boostovaný Terminátor neznal cíle.
- **Trainer pre-boost T1-T4** zásadní. Hacker boost to ATK 3, Private to ATK 6 **než má CPU intel** = tempo. Trainer sedící na L2 má 2 boosty k využití v prvních 4 tazích.
- **Corruptor zasahuje tiše a kumulativně.** Pokud vidím event `weakened_attack` nebo tu jednotku mám a najednou má červený obvod → stahuju ji za linii, neposílám vpřed.
- **CPU nepronikla ani na 3 hexy od mojí citadely.** Nemusím se tolik bát defenzivních vln; soustředit na push. Ale stále sledovat sloupec u citadely – stačí jeden slabý Vojín dálkovým spojem a jsem v háji.

### Hra 9 (REMÍZA T300, killy 3:0 — over-correction z G8 loss)

**Přehnaná obrana po G8 loss.** Zvýšil jsem `defense_radius: 5→7`, `recon_radius: 6→8`, `min_blocker_atk_vs_unknown: 4→5`. Výsledek: agent v 300 tazích provedl **0 útoků**, 284 move-actions, většinou BLOCK paratrooperů kmitajících v defenzivě. 3 killy byly VŠECHNY artillery_kill (rozsah 2, pasivně). Terminator boostnut na ATK 12 ale nikdy neútočil — rusher se nedostal k enemy line kvůli priority-inversion: BLOCK skóre > ADVANCE skóre když je `defense_radius` velký.

- **Nezvyšuj `defense_radius` nad 6.** Po G9 jasné: radius 7 znamená že každý enemy unit dál než 7 hexů od citadely se nepočítá jako threat, ALE skoro celá enemy formation JE v tom radius většinu hry → pořád je co blokovat → rusher nikdy nepůjde vpřed.
- **Oprava infiltrator problému jinde:** místo velkého radius → přidat do player.py specifický **"hidden-unit-within-4-of-my-citadel" intercept rule** s vyšší prioritou než ADVANCE, ale pouze pro ty jednotky (ne globální block-all).
- **Recon drone v L1 back-row (row 4, col 8)** byl dobrý krok — už z 300 tahů neviděl infiltrátora (enemy se ani nepokusil, protože se bránila naše masivní paratrooper obrana). Potřebujeme zpět agresivitu.
- **Konfig pro G10: vrátit defense_radius na 5 nebo 6 MAX**, nechat recon_drone v L1 back, přidat `urgent_citadel_intercept_radius: 4` s dedikovanou high-priority rule v player.py.

### Hra 8 (PROHRA T284, killy 24:10 ALE CPU dobyla citadelu infiltrátorem)

**POZOR: CPU se naučila silent infiltrator trick, který Claude používá.** Zatímco jsem bil CPU v attrition 24:10, jeden její Vojín/Scout tiše došel na mou citadelu (8,0) a vyhrál hru.

- **Back-row patrol je povinný od T100.** Recon drone + fighter sweepuje moje řady 0-3 každých ~15 tahů. Pokud se zadnímu recon droneovi něco stane, okamžitě poslat náhradu (jinou jednotku do monitor-mode).
- **Nenechat se fixovat na jeden útok CPU.** Pokud se jeden CPU tank tlačí na mou linii, NEZANEDBÁVAT zbytek pole – druhý CPU unit může jít obchozí cestou.
- **Odhalené jednotky mají known position, ale i hidden má známé hex souřadnice** – i když nevím co to je, vím **kde to je**. Každý hidden CPU unit do 4 hexů od mé citadely = urgentní reveal + intercept.

### Hra 7 (REMÍZA T300, killy 15:23 v Claude neprospěch)

**VELMI tvrdá hra.** CPU konečně funguje. Její heli pronikla do row 1-2 a zabila arty + 4 ženisty během 5 tahů.

- **Nepošli všechny 4 helis do středu** – CPU fighters tam čekají. Jednoho nech na row 3-4 jako anti-air brankáře.
- **Row-15 wall ženistů CPU potřebuje Terminátora + recon**, ne scout swarm (scout má ATK 3, ženist ATK 1 → vítězí, ale za ženistou je mina/arty).
- **Terminátorův push musí začít do T40**, ne po T80 – jinak CPU stihne vybudovat wall + najít ho.

### Hra 5 (vyhrál jsem T84, 9:4) + Hra 6 (vyhrál jsem T96, 13:14)

- **CPU stále nestíhá dělat obranu** – arty nám zabije 4 jednotky v L0 předtím než ji najdeme. Proaktivně poslat recon na row 14-15 SOUPEŘE v tahu 1-5 aby se DB odhalila dřív než začne střílet.
- **Scout sneak** – v hře 6 můj scout_4 (ATK 2) seděl v col 7-8 row 14 patnáct tahů nepozorován, pak vstoupil na citadelu. CPU NENAŠLA ani odhalený scout blízko své citadely. Užitečné: "tichý scout" poblíž soupeřovy citadely = finální úder.
- **Nemusím vůbec používat Terminátora** pokud CPU nestihne obnovit obranu. Stačí L1 rusheri (scout, Výsadkář, boostnutý private) protože CPU má zamrzlé defenzivní jednotky.
- **Kontra-arty PRIO** – když ztratím první jednotku od `artillery_kill`, okamžitě send recon na enemy row 14-15.

### Hra 4 (vyhrál jsem T139, 16:3)

- **Arty na back-row obránce.** CPU drží Ženisty v řadě 15 přilepené k citadele – nikdy se nehýbou. Moje arty s dosahem 2 je z L2 (row 5) nedosáhne. **Posunout moji arty dopředu** jakmile mám tempo (L2 → row 6 → row 7). Arty rozstřílí obránce kteří se nehýbají.
- **Dva útoky z různých sloupců** rozbijí reserve – jeden sloupec se brání, druhý projde. Terminátor přes col 3-4 + Výsadkář přes col 10-11 současně.
- **Endgame recon je povinný** (T100+). U nepřátelské citadely jsou skryté miny + ženist s ATK 1. Reveal každé kolo než vstoupím, jinak můj dobyvatel vstoupí na minu.

### Hra 3 (vyhrál jsem T80, 9:1)

- **Tank = Corruptor magnet.** Jeden z mých tanků dostal 6× weaken (ATK 7→1). Když moji jednotku někdo zasáhne 2x koruptorem, **stáhni ji za L1** (ATK 1 tank je k ničemu vpředu) a místo ní pošli Výsadkáře/Terminátora.
- **Priorita #1 při odhalení nepřátelské arty:** do 3 tahů ji zabít (dron / tank adjacent / artillery counter-shot). V hře 3 jsem to neudělal rychle → 4 ztráty. Ignoruj i advance pokud arty žije.
- **Terminátor rampage funguje**: T14–T37 můj Terminátor sám zabil Tank + 3× Průzkumník + Výsadkář = 5 killů nedotčen. Skrytý rush přes col 7-9 stabilně porazí CPU heavies.

### Hra 2 (vyhrál jsem T133, 18:5)

- **Drž rushery SKRYTÉ** dokud nedojdou k frontě. CPU má patch "neutíká přímo do revealeného silnějšího" – ale to platí jen když jsem odhalený. Terminátor na L2 jde klidně až k battlefieldu, nepotřebuje se bít cestou. Jakmile útočí nebo je cílem útoku, odhalí se → pak už rush-in.
- **Recon cyklit I na straně soupeřovy citadely v endgame** (T100+). Před finálním push vyčistit cestu – hidden miny a obránci u citadely. Každý reveal = bezpečný další hex.
- **Drony > arty v počtu killů.** Arty láká counter-fire, dron je mobilní + threshold-kill < 4. V hře 2: 3 dronové killy : 0 ztrát, arty 4:4 remíza. Preferovat dron pokud cíl má ATK < 4.
- **Trainer pre-boost T1-T4 je klíč** – bez tempa soupeř dorazí k obraně první.

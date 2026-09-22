# EVE Market Engine — plan i faze

## Cilj

Dva alata na zajedničkom jezgru:

- **Modul A — market making advisor:** za izabranu stanicu predlaže koje buy/sell naloge postaviti, po kojoj ceni, sa očekivanim fill rate-om i profitom po danu po ISK-u vezanog kapitala.
- **Modul B — hauling optimizer:** za par stanica računa šta i koliko kupiti da se maksimizuje profit, pod ograničenjima budžeta, carga, izlazne likvidnosti i vrednosti tereta.

Glavna razlika u odnosu na postojeće alate (eveprofits i sl.): fill rate i konkurencija se procenjuju iz diffova snapshotova umesto fiksnog procenta, a Modul B koristi pravi MILP sa budžetom i cargom istovremeno, sa oba izlazna režima (instant i listing).

## Arhitektura

```
ESI + SDE → ingest → store (duckdb + parquet) → features → fill model
                                                      ↓
                                     fees (config) → optimize (LP/MILP)
                                                      ↓
                                   market_making (A)     hauling (B)
                                                      ↓
                                                     CLI
```

## Pravila rada

- Jedna faza = jedna ili više sesija u Claude Code-u, završava se zelenim testovima i commitom.
- Plan mode pre svake faze; kod tek posle odobrenog plana.
- Testovi nikad ne idu na mrežu; koriste snimljene ESI odgovore iz `tests/fixtures/`.
- Faza je gotova tek kad su ispunjeni svi kriterijumi prihvatanja.

---

## Faza 0 — Skelet projekta

**Cilj:** prazan, ali ispravan projekat.

**Zadaci**
- `uv init --package`, struktura foldera iz `CLAUDE.md`
- ruff, pytest, pre-commit (ruff format + ruff check)
- `config/settings.toml` + pydantic-settings model: skillovi, standings, budžet, cargo, parovi stanica, filteri
- `eve` CLI (typer) sa praznim komandama `ingest`, `features`, `mm`, `haul`

**Kriterijumi prihvatanja**
- `uv run pytest` prolazi (bar jedan smoke test)
- `uv run eve --help` prikazuje sve komande
- config se učitava i validira; neispravna vrednost daje jasnu grešku

**Prompt**
> Pročitaj CLAUDE.md i PLAN.md. Implementiraj Fazu 0. Prvo plan.

---

## Faza 1 — Ingest market ordera

**Cilj:** pouzdano skidanje svih ordera za region i snimanje snapshotova.

**Zadaci**
- async ESI klijent (httpx): `User-Agent`, retry sa backoff-om, poštovanje `Expires`, praćenje `X-ESI-Error-Limit-Remain/Reset`
- `/markets/{region_id}/orders/?order_type=all`, sve stranice preko `X-Pages`, ograničena konkurentnost (semaphore)
- svaki snapshot dobija `snapshot_id` i `fetched_at`
- raw snapshot → Parquet (`data/raw/region=.../date=.../`)
- najnoviji snapshot → duckdb tabela `orders_latest`
- retention za raw Parquet (konfigurabilan broj dana)
- `eve ingest --region 10000002`

**Kriterijumi prihvatanja**
- ceo The Forge snapshot se skida bez grešaka i bez prekoračenja error limita
- ponovni poziv pre isteka `Expires` ne ide na mrežu
- testovi sa fixture-ima pokrivaju paginaciju, retry i error-limit backoff
- broj ordera u snapshotu je logovan i razuman (stotine hiljada za The Forge)

**Prompt**
> Implementiraj Fazu 1 iz PLAN.md. Snimi jedan pravi odgovor (1–2 stranice) kao fixture. Prvo plan.

---

## Faza 2 — Referentni podaci i istorija

**Cilj:** volumeni itema, nazivi i dnevna istorija cena.

**Zadaci**
- SDE loader: `type_id`, naziv, `packaged_volume`, market group; jednom, sa proverom verzije
- nazivi stanica i regiona (SDE ili ESI, keširano)
- `/markets/{region_id}/history/?type_id=` jednom dnevno, samo za tipove koji imaju ordere
- tabele `types`, `locations`, `history`
- `eve ingest-history`, `eve load-sde`

**Kriterijumi prihvatanja**
- svaki `type_id` iz ordera ima volumen i naziv (ili je eksplicitno logovan kao nepoznat)
- history ingest poštuje rate/error limite i može se nastaviti posle prekida
- brodovi koriste packaged volumen

---

## Faza 3 — Features

**Cilj:** agregati po (lokacija, item) po snapshotu.

**Zadaci**
- filtriranje ordera: izbaci buy naloge sa `min_volume > 1` iz izlazne likvidnosti; buy nalozi sa `range != station` se vezuju za lokacije koje stvarno pokrivaju
- best bid/ask, dubina na N cenovnih nivoa, broj naloga unutar X% od vrha knjige
- validacija protiv istorije (odstupanje od 7/30-dnevnog proseka)
- tabela `book_features` (append po snapshotu)

**Kriterijumi prihvatanja**
- za Jita 4-4 rezultat se ručno slaže sa in-game market prozorom za 5 nasumičnih itema
- scam nalozi (`min_volume`, udaljeni range) ne pojavljuju se kao izlazna likvidnost

---

## Faza 4 — Snapshot diff i fill model

**Cilj:** proceniti stvarne trejdove i konkurenciju između snapshotova.

**Zadaci**
- diff uzastopnih snapshotova po `order_id`:
  - smanjena `volume_remain` → procenjen fill (količina, cena)
  - nestao nalog → fill ili otkazivanje (razlikovati heuristikom: poređenje sa history volumenom, `volume_remain` pre nestanka)
  - promenjena cena istog naloga → relist (signal konkurencije)
- metrike po itemu/lokaciji: procenjen volumen po satu na vrhu knjige, broj relistova po satu, broj aktivnih konkurenata na vrhu
- procenjen capture rate kao funkcija konkurencije
- kalibracija: zbir procenjenih fillova po danu vs. dnevni volumen iz history

**Kriterijumi prihvatanja**
- za likvidne iteme, procenjeni dnevni volumen iz diffova je u razumnom opsegu u odnosu na history (odstupanje dokumentovano)
- model radi i kad snapshot fali (gap u podacima se ne tretira kao fill)

**Napomena:** ingest cron (5–15 min) treba da radi od kraja Faze 1, da bi ovde bilo nekoliko dana podataka.

---

## Faza 5 — Takse

**Cilj:** jedan modul koji računa sve troškove trgovine.

**Zadaci**
- broker fee (skill + standings + tip lokacije: NPC stanica ili struktura)
- sales tax (Accounting)
- relist trošak
- minimalni broker fee po nalogu
- sve stope iz configa, nijedna hardkodovana

**Kriterijumi prihvatanja**
- testovi za kombinacije skillova sa ručno izračunatim vrednostima
- rezultat se slaže sa in-game prikazom taksi za bar jedan pravi primer

---

## Faza 6 — Modul B: hauling optimizer

**Cilj:** optimalna lista za kupovinu za jednu turu.

**Formulacija** (po paru stanica):

- promenljive `x[i,k] ≥ 0` — količina itema *i* kupljena na cenovnom nivou *k* izvorne sell knjige, `x[i,k] ≤ količina na nivou`
- dva izlazna režima po itemu:
  - *instant*: prodaja u buy naloge na odredištu, takođe po nivoima; samo sales tax
  - *listing*: prodaja preko sopstvenog sell naloga; kapacitet ≤ udeo dnevnog volumena; broker fee + sales tax + diskont za vreme čekanja
- maksimizuj ukupan neto profit
- ograničenja: investicija ≤ budžet, ukupan volumen ≤ cargo, prodaja ≤ kapacitet izlaza, vrednost tereta ≤ max vrednost
- celobrojnost samo za skupe iteme (prag iz configa); ostalo LP

**Zadaci**
- solver iza interfejsa (PuLP/CBC sada, OR-Tools opciono)
- provera posle rešavanja: svako ograničenje zadovoljeno, u suprotnom greška
- više parova stanica: rešava se po paru i rangira po profitu po satu (skokovi × vreme po skoku iz configa)
- `eve haul --from 60003760 --to 60008494 --budget 20e6 --cargo 5000`
- izlaz: shopping lista (item, količina, max kupovna cena, izlazni režim, ciljna prodajna cena), ukupna investicija, cargo, očekivani neto profit, profit po satu

**Kriterijumi prihvatanja**
- na sintetičkim primerima rezultat se slaže sa brute-force rešenjem
- na primerima gde budžet i cargo oba vezuju, MILP je bolji ili jednak greedy-ju (test to dokazuje)
- nikad ne izbacuje plan preko budžeta ili carga

---

## Faza 7 — Modul A: market making advisor

**Cilj:** predlog naloga i alokacije kapitala za jednu stanicu.

**Zadaci**
- po itemu: predloženi buy (bid + 0.01) i sell (ask − 0.01), neto spread posle svih taksi i očekivanih relistova
- očekivani fill po danu iz fill modela (Faza 4), korigovan za konkurenciju
- vezani kapital i očekivano vreme obrta
- rangiranje po profitu po danu po ISK-u vezanog kapitala
- alokacija portfolija: MILP sa ograničenjem kapitala i broja order slotova (iz configa, zavisi od skillova), sa max udelom kapitala po itemu
- `eve mm --station 60003760 --capital 500e6`

**Kriterijumi prihvatanja**
- nijedan predlog ne dolazi iz itema bez dovoljne istorije ili sa jednim nalogom na vrhu knjige
- rangiranje se menja sa konkurencijom (test: isti spread, više relistova → niže rangiranje)

---

## Faza 8 — Operativa i validacija

**Zadaci**
- cron/systemd timer za ingest (orders na 5–15 min, history dnevno)
- health check: poslednji uspešan snapshot, broj ordera, greške
- log sopstvenih trejdova (ručni unos ili kasnije ESI wallet sa auth-om) i poređenje očekivanog i ostvarenog profita
- jednostavan izveštaj: očekivano vs. ostvareno po itemu i po turi

**Kriterijumi prihvatanja**
- sistem radi nedelju dana bez ručne intervencije
- postoji bar jedna nedelja poređenja očekivanog i ostvarenog profita

---

## Kasnije (van v1)

- orderi iz player struktura (autentifikovani ESI)
- ESI auth za sopstvene naloge i wallet (automatsko praćenje pozicija)
- više regiona i parova stanica odjednom
- web dashboard

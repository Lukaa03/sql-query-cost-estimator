# Sistem za procenu izvršavanja SQL upita (BP2, 2025/26)

Sistem iz ispravno zadatog SQL upita i opisa šeme (u JSON formatu) određuje
**najbolji plan evaluacije**, za svaku operaciju iz plana bira **algoritam** i
procenjuje **cenu izraženu u broju blok transfera**.

Implementirano u Pythonu 3 (bez spoljnih biblioteka — koristi samo standardnu
biblioteku), pa se pokreće u svakom okruženju sa instaliranim Python-om.

## Obim (prema tekstu projekta)

Upit može biti nad najviše 4 tabele, bez podupita. `SELECT` lista sadrži
isključivo atribute (bez operacija i agregatnih funkcija), `FROM` navodi tabele
razdvojene zarezima (bez `JOIN` operatora), `WHERE` je konjunkcija od najviše 6
uslova, `ORDER BY` ima najviše jedan atribut, nema `GROUP BY`.

Podržane operacije i algoritmi: selekcija (jednakost, poređenje, složeni uslovi),
indeksi (B+ stablo i hash), spoljno objedinjeno sortiranje, spajanje (ugnežđenom
petljom, blok ugnežđenom petljom, indeks ugnežđenom petljom, objedinjeno/merge
spajanje, hash spajanje), projekcija i evaluacija kompletnog iskaza
materijalizacijom.

**Delovi koji su u tekstu projekta precrtani i koje sistem NE radi:** sintaksna i
semantička provera upita (pretpostavlja se ispravno zadat upit), spajanja sa
konjunkcijom i disjunkcijom uslova, agregacija i skupovne operacije.

## Pokretanje

```bash
# upit kao argument:
python main.py primeri/sema.json -q "SELECT ime FROM Student WHERE indeks = '2020/0001'"

# upit iz fajla:
python main.py primeri/sema.json -f upit.sql

# upit naveden unutar samog JSON-a (polje "query"):
python main.py primeri/sa_upitom.json

# svi pripremljeni primeri odjednom:
python pokreni_primere.py
```

## Format ulaza

Ulazni JSON sadrži veličinu bafera (`bufferBlocks`), opis šeme (`schema`) i
opciono sam upit (`query`). Za svaku tabelu se zadaje broj redova, broj blokova,
broj redova po bloku, lista atributa (naziv, tip, jedinstvenost, broj različitih
vrednosti) i lista indeksa (atributi, tip `B_PLUS_TREE`/`HASH`, da li je
sortirajući `clustered`, visina stabla `treeHeight`). Primer je u
`primeri/sema.json`.

## Arhitektura

| Datoteka | Uloga |
|----------|-------|
| `catalog.py`   | objektni model šeme i učitavanje iz JSON-a |
| `sqlparser.py` | parser podskupa SQL-a u strukturu (SELECT/FROM/WHERE/ORDER BY) |
| `planner.py`   | model cene, algoritmi i optimizator (izbor plana) |
| `main.py`      | ulazna tačka i ispis plana |
| `pokreni_primere.py` | pokretanje svih primera iz `primeri/upiti.sql` |

Tok rada: razrešavanje aliasa → klasifikacija WHERE uslova na selekcione i
spajajuće → izbor pristupnog puta po tabeli (gura se selekcija naniže) →
nabrajanje redosleda spajanja (sve permutacije, levo-duboko stablo, ≤ 4 tabele) i
za svako spajanje izbor najjeftinijeg algoritma → opciono sortiranje za
`ORDER BY` → projekcija u protoku. Bira se plan najmanje ukupne cene.

## Model cene (blok transferi)

Oznake: `n` = broj redova tabele, `b` = broj blokova, `f` = redova po bloku,
`V(A)` = broj različitih vrednosti atributa `A`, `M` = veličina bafera u
blokovima, `h` = visina B+ stabla.

### Procena selektivnosti
- Jednakost `A = c`: `1` red ako je `A` jedinstven, inače `n / V(A)` redova.
- Poređenje (`<`, `<=`, `>`, `>=`): pošto min/max vrednosti **nisu** date u
  ulazu, koristi se standardna pretpostavka iz literature — `n / 2` redova.
- `A <> c`: ~ `n` redova.
- Konjunkcija uslova: proizvod pojedinačnih selektivnosti.

### Selekcija — pristupni putevi
- **Linearno pretraživanje (scan):** `b`. Uz jednakost po ključu prosečno `b/2`.
- **B+ stablo, jednakost:** sortirajući `h + ⌈matching/f⌉`; nesortirajući
  `h + 1` (ključ) odnosno `h + matching` (po jedan pristup po pogotku).
- **B+ stablo, poređenje:** sortirajući `h + ⌈matching/f⌉`; nesortirajući
  `h + matching`.
- **Hash indeks (samo jednakost, nad svim atributima indeksa):**
  sortirajući `1 + ⌈matching/f⌉`; nesortirajući `1 + matching` (`1 + 1` za ključ).
- **Složeni uslov:** poštuje se prefiks pravilo indeksa (vodeći atributi sa
  jednakošću, prvi opseg prekida prefiks kod B+); bira se najjeftiniji put, a
  preostali uslovi se proveravaju nad pročitanim redovima (bez dodatne cene).

### Spoljno objedinjeno sortiranje
`b ≤ M` → `b`; inače `b · (2·⌈log_{M-1}(⌈b/M⌉)⌉ + 1)`.

### Spajanje (`r ⋈ s`), procena veličine `n_r·n_s / max(V(A_r), V(A_s))`
- **Ugnežđena petlja:** `b_spolja + n_spolja · b_unutra` (bira se manja kao spoljna).
- **Blok ugnežđena petlja:** `b_spolja + ⌈b_spolja/(M-2)⌉ · b_unutra`.
- **Indeks ugnežđena petlja:** `b_spolja + n_spolja · c`, gde je `c` cena jedne
  pretrage indeksa unutrašnje (bazne) tabele; primenljivo kad unutrašnja tabela
  ima indeks na atributu spajanja.
- **Objedinjeno (merge) spajanje:** `sort_r + sort_s + b_r + b_s` (sort se
  izostavlja za već sortiranu stranu); rezultat ostaje sortiran po atributu spajanja.
- **Hash spajanje:** `3·(b_r + b_s)`; uz rekurzivno particionisanje
  `(2·prolazi + 1)·(b_r + b_s)` kada manja relacija ne staje u bafer.

### Projekcija i materijalizacija
`SELECT` lista su samo atributi (nema `DISTINCT`), pa se projekcija izvršava u
protoku — bez dodatne cene. Evaluacija je materijalizacijom: izlaz svakog
operatora (selekcije, spajanja) upisuje se na disk (`+⌈rezultat/f⌉` blok
transfera), a naredni operator ga čita. Čitanje bazne tabele bez filtera plaća
operator koji je koristi (spajanje/sortiranje), bez suvišne kopije.

## Pretpostavke

Tamo gde tekst projekta nije precizan, usvojene su sledeće pretpostavke: upit je
sintaksno i semantički ispravan (provera je precrtana); za poređenja se bez
histograma/granica koristi selektivnost `1/2`; spajanja su po jednakosti
atributa; razmatraju se levo-duboka stabla planova (dovoljno za ≤ 4 tabele);
veličina reda međurezultata jednaka je zbiru širina ulaznih redova (procena broja
blokova preko `1/f_rez = 1/f_r + 1/f_s`); bafer ima bar 3 bloka.

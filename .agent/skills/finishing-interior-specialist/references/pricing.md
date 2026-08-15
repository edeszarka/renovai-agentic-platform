# Finishing & Interior — Reference Pricing

> **Single source of truth (owner-purchased, doc 04):** the *tulajdonosi
> beszerzés* product tables below (csempe/járólap kategóriák §1,
> laminált §4, beltéri festék árak §3, beltéri ajtók §6) are mirrored in
> `renovai/predictor/product_pricing.py` — the canonical structured
> product-pricing catalog that the estimate handlers consume at runtime.
> Edit the Python module, not this markdown, when prices change; this file
> remains as the human-readable reference.

## 1. Burkolás (Tiling) — Munkadíj és Anyag nm árak

### Munkadíj lapméret szerint

| Lapméret | Munkadíj (Ft/nm) | Anyag (Ft/nm) |
|---|---|---|
| 60x30 cm vagy 60x60 cm | 9 000 - 12 000 | 6 000 - 8 000 |
| 60x120 cm | 13 000 - 16 000 | 7 000 - 9 000 |

### Csempe / Járólap kategóriák (Tulajdonos által vásárolandó)

| Kategória | 60x30 (Ft/nm) | 60x60 (Ft/nm) | 60x120 (Ft/nm) | 80x80 (Ft/nm) | 90x90 (Ft/nm) | 120x120 (Ft/nm) | 100x280 (Ft/lap) |
|---|---|---|---|---|---|---|---|
| Alsó | 4 000 - 6 000 | 5 000 - 7 000 | 7 000 - 8 000 | — | — | — | — |
| Alsó közép | 5 000 - 7 000 | 5 000 - 7 000 | 7 000 - 8 000 | — | — | — | — |
| Közép közép | 7 000 - 9 000 | 7 000 - 9 000 | 10 000 - 11 000 | — | — | — | — |
| Felső közép | 10 000 - 12 000 | 10 000 - 13 000 | 12 000 - 14 000 | — | — | — | — |
| Prémium | 12 000 - 13 000 | 11 000 - 15 000 | 15 000 - 20 000 | 15 000 - 18 000 | 15 000 - 20 000 | 20 000 - 35 000 | 120 000 - 180 000 |
| Luxus | — | 18 000-tól | 20 000 - 25 000+ | 20 000-tól | 22 000 - 25 000+ | 30 000 - 35 000+ | 180 000 - 200 000+ |

## 2. Fal előkészítés (Wall Preparation)

### Fűrészporos tapétás vs meszes réteg — 50-60 nm lakás, 200-300 nm falfelület, Q3 glettelés

| Eset | Plusz anyag (Ft) | Plusz munkadíj (Ft) | Megjegyzés |
|---|---|---|---|
| Fűrészporos tapéta | 80 000 - 100 000 | 180 000 - 280 000 | Tapéta kaparás + csiszolás + glettelés + festés. Rosszabb állapot. |
| 40-60 éves meszes réteg (nincs tapéta) | 0 | 0 | Csak gépi csiszolás + glettelés + festés. Nincs plusz költség. |

## 3. Festék mennyiség (Paint Quantity Estimation)

### 50-60 nm lakás, 2.6-2.8m belmagasság, 150-200 nm felület/réteg, egy szín, fürdő+wc nélkül

| Festék típusa | Mennyiség |
|---|---|
| Színes festék (2 réteg) | 3 x 15 liter = 45 liter |
| Fehér festék (mennyezet) | 45 - 60 liter |

### Beltéri festék árak (15 literes vödör, diszperziós vizes bázisú, kevert)

| Típus | Ár (Ft / 15l) |
|---|---|
| Nem mosható, prémium színes | 18 000 - 25 000 |
| Prémium és mosható, színes | 38 000 - 50 000 |
| Fehér beltéri | 11 000 - 17 000 |

## 4. Laminált Padló (Laminate Flooring)

### Lerakás munkadíj

| Tétel | Ár |
|---|---|
| Munkadíj (7-12mm padló, XPS, párazáró fólia, szegőléc ragasztva, gérvágva) | 5 500 - 7 500 Ft/nm |
| Plusz ragasztó (burkolatváltókhoz + szegőlécekhez) | 9 000 - 14 000 Ft |

### Laminált padló árak (szegőléc, XPS, párazáró fólia nélkül)

| Vastagság | Ár (Ft/nm) |
|---|---|
| 7 mm | 4 000 - 5 000 |
| 8 mm | 4 500 - 5 500 |
| 10 mm | 6 000 - 7 500 |
| 12 mm | 6 500 - 9 500 |

### XPS alátét

| Vastagság | Ár (Ft/nm) |
|---|---|
| 3 mm | 550 - 700 |
| 5 mm | 750 - 1 000 |

## 5. Vízszigetelés (Waterproofing)

### 4 nm fürdő, 15 nm egy réteg / 30 nm két réteg, 2.2m magas, 14m hajlaterősítő szalag

| Típus | Mikor kell | Anyagköltség (Ft) |
|---|---|---|
| Cementbázisú (1 komponensű) | Épített zuhany esetén kötelező (nagy vízterhelés). 3-4 zsák. | 130 000 - 180 000 (3 zsák: 130-150e, 4 zsák: 160-180e) |
| Diszperziós | Zuhanytálca (2 oldal burkolt, 2 oldal üveg) vagy fürdőkád esetén elég. | 70 000 - 100 000 |

## 6. Beltéri Ajtók (Interior Doors)

### Külső zsanéros, CPL fóliás, méretre gyártott — 2.2m magasságig, 20cm tokig, 1m nyílásig

| Típus | Ajtó ára (Ft/db) | Beszerelés (Ft/db) |
|---|---|---|
| Üveg nélkül, kilinccsel | 90 000 - 130 000 | 23 000 - 27 000 |
| Üveggel és kilinccsel | 130 000 - 180 000 | 25 000 - 30 000 |

### Belső zsanéros, CPL fóliás, méretre gyártott — 2.2m magasságig, 20cm tokig, 2m nyílásig

| Típus | Ajtó ára (Ft/db) | Beszerelés (Ft/db) |
|---|---|---|
| Üveg nélkül, kilinccsel | 140 000 - 180 000 | 30 000 - 35 000 |
| Üveggel és kilinccsel | 170 000 - 230 000 | 32 000 - 37 000 |

### Egy szárnyas tolóajtó, CPL fóliás — 2.2m magasságig, 20cm tokig, 1m nyílásig

| Típus | Ajtó ára (Ft/db) | Beszerelés (Ft/db) |
|---|---|---|
| Üveg nélkül | 120 000 - 150 000 | 25 000 - 28 000 |
| Üveggel | 140 000 - 180 000 | 25 000 - 30 000 |

### Kétszárnyú tolóajtó, CPL fóliás — 2.2m magasságig, 20cm tokig, 2m nyílásig

| Típus | Ajtó ára (Ft/db) | Beszerelés (Ft/db) |
|---|---|---|
| Üveg nélkül | 180 000 - 240 000 | 35 000 - 45 000 |
| Üveggel | 200 000 - 280 000 | 34 000 - 45 000 |

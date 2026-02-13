# updateURLlist.py — Netskope URL List Updater

Skrypt Python do automatycznej aktualizacji URL List w Netskope na podstawie feedów domen.

Obsługuje dwa typy źródeł:
- **Plik CSV** — format tabelaryczny CERT.PL (kolumna `AdresDomeny`, separator tab) lub plain-text (jedna domena na linię)
- **URL endpoint** — plain-text, jedna domena na linię (np. `https://hole.cert.pl/domains/v2/domains.txt`)

Skrypt auto-wykrywa typ źródła na podstawie prefiksu `http://` / `https://`.

## Wymagania

- Python 3.6+
- Biblioteka `requests` (`pip install requests`)

## Parametry

| Parametr | Skrót | Opis | Wymagany |
|----------|-------|------|----------|
| `--source` | `-s` | Źródło domen: ścieżka do pliku CSV lub URL | Tak |
| `--urlist` | `-l` | Nazwa URL Listy w Netskope | Tak |
| `--token` | `-t` | Bearer token API Netskope | Tak |
| `--nskp` | `-n` | Adres tenanta Netskope (np. `pzusa.goskope.com`) | Tak |
| `--add` | `-a` | Tryb append (PATCH) — dodaje domeny do istniejącej listy | Nie |
| `--deploy` | `-d` | Automatyczny deploy zmian po aktualizacji | Nie |

Domyślnie (bez `--add`) skrypt **nadpisuje** całą listę (PUT). Z flagą `--add` — **dołącza** domeny (PATCH/append).

Domyślnie (bez `--deploy`) zmiany pozostają jako pending — deploy wykonujesz ręcznie w konsoli Netskope. Z flagą `--deploy` — deploy następuje automatycznie.

## Jak działa

1. **Parsowanie argumentów** — walidacja wymaganych parametrów
2. **Pobranie domen** — z pliku lokalnego (CSV/plain-text) lub z URL endpointa
3. **Czyszczenie domen** — usunięcie protokołów (`http://`, `https://`), deduplikacja, odrzucenie pustych/nieprawidłowych
4. **Chunking** — jeśli payload JSON przekracza 7 MB, dzieli listę na mniejsze kawałki
5. **Wyszukanie URL Listy** — `GET /api/v2/policy/urllist` → szuka listy po nazwie
6. **Aktualizacja** — `PUT` (nadpisanie) lub `PATCH/append` (dodanie) domen
7. **Deploy** (opcjonalnie) — `POST /api/v2/policy/urllist/deploy`
8. **Podsumowanie** — ile domen, ile chunków, status operacji

## Obsługa błędów

- **429 / 5xx** — automatyczny retry 3x z exponential backoff (1s, 2s, 4s)
- **401 / 403** — komunikat o błędnym tokenie
- **Brak URL Listy** — wypisuje dostępne listy w tenancie
- **Brak kolumny `AdresDomeny`** — fallback na tryb plain-text (jedna domena na linię)
- **Timeout** — 60s na requesty API, 30s na pobieranie źródła URL

## Przykłady użycia

### Nadpisanie listy z pliku CSV (146k domen CERT.PL)

```
$ python3 updateURLlist.py -s domains.csv -l UL-marek -n pzusa.goskope.com -t TOKEN

2026-02-13 23:57:46 [INFO] Pobrano 146491 domen (146491 unikalnych)
2026-02-13 23:57:46 [INFO] Rozmiar payloadu: 3.37 MB → 1 chunk(ów)
2026-02-13 23:57:47 [INFO] Znaleziono URL Listę 'UL-marek' (id=6)
2026-02-13 23:57:47 [INFO] PUT chunk 1/1 (146491 domen)...
2026-02-13 23:57:53 [INFO] PUT 146491 domen do listy 'UL-marek'

============================================================
PODSUMOWANIE
============================================================
  URL Lista:      UL-marek (id=6)
  Tryb:           REPLACE
  Źródło:         domains.csv
  Domen:          146491
  Chunków:        1
  Deploy:         NIE (pending)
  Status:         OK
============================================================
```

### Nadpisanie listy z pliku CSV + automatyczny deploy

```
$ python3 updateURLlist.py -s domains.csv -l UL-marek -n pzusa.goskope.com -t TOKEN -d

2026-02-13 23:58:34 [INFO] Pobrano 146491 domen (146491 unikalnych)
2026-02-13 23:58:34 [INFO] Rozmiar payloadu: 3.37 MB → 1 chunk(ów)
2026-02-13 23:58:35 [INFO] Znaleziono URL Listę 'UL-marek' (id=6)
2026-02-13 23:58:35 [INFO] PUT chunk 1/1 (146491 domen)...
2026-02-13 23:58:41 [INFO] PUT 146491 domen do listy 'UL-marek'
2026-02-13 23:58:41 [INFO] Deploying zmian...
2026-02-13 23:58:42 [INFO] Deploy zmian — OK

============================================================
PODSUMOWANIE
============================================================
  URL Lista:      UL-marek (id=6)
  Tryb:           REPLACE
  Źródło:         domains.csv
  Domen:          146491
  Chunków:        1
  Deploy:         TAK
  Status:         OK
============================================================
```

### Dodanie domen z pliku plain-text (append, 3 domeny)

```
$ python3 updateURLlist.py -s test.csv -l UL-marek -n pzusa.goskope.com -t TOKEN -a

2026-02-14 00:07:58 [WARNING] Brak kolumny 'AdresDomeny' w CSV — próbuję jako plain-text (jedna domena/linia)
2026-02-14 00:07:58 [INFO] Pobrano 3 domen (3 unikalnych)
2026-02-14 00:07:58 [INFO] Rozmiar payloadu: 0.00 MB → 1 chunk(ów)
2026-02-14 00:07:59 [INFO] Znaleziono URL Listę 'UL-marek' (id=6)
2026-02-14 00:07:59 [INFO] Append chunk 1/1 (3 domen)...
2026-02-14 00:08:01 [INFO] PATCH/append 3 domen

============================================================
PODSUMOWANIE
============================================================
  URL Lista:      UL-marek (id=6)
  Tryb:           APPEND
  Źródło:         test.csv
  Domen:          3
  Chunków:        1
  Deploy:         NIE (pending)
  Status:         OK
============================================================
```

### Nadpisanie listy z URL (CERT.PL) + deploy

```
$ python3 updateURLlist.py -s https://hole.cert.pl/domains/v2/domains.txt -l UL-marek -n pzusa.goskope.com -t TOKEN -d
```

### Dodanie domen (append) z URL

```
$ python3 updateURLlist.py -s https://hole.cert.pl/domains/v2/domains.txt -l UL-marek -n pzusa.goskope.com -t TOKEN --add
```

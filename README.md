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
| `--nskp` | `-n` | Adres tenanta Netskope (np. `your-tenant.goskope.com`) | Tak |
| `--add` | `-a` | Tryb append (PATCH) — dodaje domeny do istniejącej listy | Nie |
| `--create` | `-c` | Utwórz URL Listę jeśli nie istnieje | Nie |
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

### Append 3 domen z pliku plain-text + create + deploy

```
$ python3 updateURLlist.py -s test.csv -l UL-testowa -n your-tenant.goskope.com -t TOKEN -a -c -d

2026-02-14 00:27:16 [WARNING] Brak kolumny 'AdresDomeny' w CSV — próbuję jako plain-text (jedna domena/linia)
2026-02-14 00:27:16 [INFO] Pobrano 3 domen (3 unikalnych)
2026-02-14 00:27:16 [INFO] Rozmiar payloadu: 0.00 MB → 1 chunk(ów)
2026-02-14 00:27:17 [INFO] Znaleziono URL Listę 'UL-testowa' (id=7)
2026-02-14 00:27:17 [INFO] Aktualna liczba domen w liście: 2
2026-02-14 00:27:17 [INFO] Append chunk 1/1 (3 domen)...
2026-02-14 00:27:18 [INFO] PATCH/append 3 domen
2026-02-14 00:27:18 [INFO] Deploying zmian...
2026-02-14 00:27:19 [INFO] Deploy zmian — OK

============================================================
PODSUMOWANIE
============================================================
  URL Lista:      UL-testowa (id=7)
  Tryb:           APPEND
  Źródło:         test.csv
  Wysłano domen:  3
  Chunków:        1
  Przed:          2 domen
  Po:             5 domen (+3)
  Deploy:         TAK
  Status:         OK
============================================================
```

### Nadpisanie (replace) 146k domen z CSV + deploy

```
$ python3 updateURLlist.py -s domains.csv -l UL-testowa -n your-tenant.goskope.com -t TOKEN -c -d

2026-02-14 00:27:34 [INFO] Pobrano 146491 domen (146491 unikalnych)
2026-02-14 00:27:35 [INFO] Rozmiar payloadu: 3.37 MB → 1 chunk(ów)
2026-02-14 00:27:35 [INFO] Znaleziono URL Listę 'UL-testowa' (id=7)
2026-02-14 00:27:35 [INFO] Aktualna liczba domen w liście: 5
2026-02-14 00:27:35 [INFO] PUT chunk 1/1 (146491 domen)...
2026-02-14 00:27:41 [INFO] PUT 146491 domen do listy 'UL-testowa'
2026-02-14 00:27:42 [INFO] Deploying zmian...
2026-02-14 00:27:44 [INFO] Deploy zmian — OK

============================================================
PODSUMOWANIE
============================================================
  URL Lista:      UL-testowa (id=7)
  Tryb:           REPLACE
  Źródło:         domains.csv
  Wysłano domen:  146491
  Chunków:        1
  Przed:          5 domen
  Po:             146491 domen (+146486)
  Deploy:         TAK
  Status:         OK
============================================================
```

### Append 3 domen do listy ze 146k wpisami + deploy

```
$ python3 updateURLlist.py -s test.csv -l UL-testowa -n your-tenant.goskope.com -t TOKEN -a -c -d

2026-02-14 00:27:51 [WARNING] Brak kolumny 'AdresDomeny' w CSV — próbuję jako plain-text (jedna domena/linia)
2026-02-14 00:27:51 [INFO] Pobrano 3 domen (3 unikalnych)
2026-02-14 00:27:51 [INFO] Rozmiar payloadu: 0.00 MB → 1 chunk(ów)
2026-02-14 00:27:52 [INFO] Znaleziono URL Listę 'UL-testowa' (id=7)
2026-02-14 00:27:53 [INFO] Aktualna liczba domen w liście: 146491
2026-02-14 00:27:53 [INFO] Append chunk 1/1 (3 domen)...
2026-02-14 00:27:54 [INFO] PATCH/append 3 domen
2026-02-14 00:27:55 [INFO] Deploying zmian...
2026-02-14 00:27:57 [INFO] Deploy zmian — OK

============================================================
PODSUMOWANIE
============================================================
  URL Lista:      UL-testowa (id=7)
  Tryb:           APPEND
  Źródło:         test.csv
  Wysłano domen:  3
  Chunków:        1
  Przed:          146491 domen
  Po:             146494 domen (+3)
  Deploy:         TAK
  Status:         OK
============================================================
```

### Nadpisanie listy z URL (CERT.PL) + deploy

```
$ python3 updateURLlist.py -s https://hole.cert.pl/domains/v2/domains.txt -l UL-testowa -n your-tenant.goskope.com -t TOKEN -c -d

2026-02-14 00:29:17 [INFO] Pobieranie domen z: https://hole.cert.pl/domains/v2/domains.txt
2026-02-14 00:29:18 [INFO] Pobrano 146465 domen (146465 unikalnych)
2026-02-14 00:29:18 [INFO] Rozmiar payloadu: 3.37 MB → 1 chunk(ów)
2026-02-14 00:29:18 [INFO] Znaleziono URL Listę 'UL-testowa' (id=7)
2026-02-14 00:29:19 [INFO] Aktualna liczba domen w liście: 146494
2026-02-14 00:29:19 [INFO] PUT chunk 1/1 (146465 domen)...
2026-02-14 00:29:25 [INFO] PUT 146465 domen do listy 'UL-testowa'
2026-02-14 00:29:26 [INFO] Deploying zmian...
2026-02-14 00:29:27 [INFO] Deploy zmian — OK

============================================================
PODSUMOWANIE
============================================================
  URL Lista:      UL-testowa (id=7)
  Tryb:           REPLACE
  Źródło:         https://hole.cert.pl/domains/v2/domains.txt
  Wysłano domen:  146465
  Chunków:        1
  Przed:          146494 domen
  Po:             146465 domen (-29)
  Deploy:         TAK
  Status:         OK
============================================================
```

### Dodanie domen (append) z URL

```
$ python3 updateURLlist.py -s https://hole.cert.pl/domains/v2/domains.txt -l UL-testowa -n your-tenant.goskope.com -t TOKEN --add
```

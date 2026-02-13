#!/usr/bin/env python3
"""
updateURLlist.py — Netskope URL List Updater

Automatyczna aktualizacja URL List w Netskope na podstawie feedów domen
(plik CSV z CERT.PL, plain-text URL endpoint, itp.).

Używa Netskope REST API v2 do nadpisania (PUT) lub dodania (PATCH/append).
"""

import argparse
import csv
import io
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 7 * 1024 * 1024  # 7 MB
REQUEST_TIMEOUT = 60
RETRY_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_request(method: str, url: str, headers: dict, json_body: Optional[dict] = None,
                retries: int = MAX_RETRIES) -> requests.Response:
    """Execute an HTTP request with retry logic for transient errors."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(
                method, url, headers=headers, json=json_body, timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code in RETRY_CODES and attempt < retries:
                wait = 2 ** (attempt - 1)
                log.warning("HTTP %d from %s — retry %d/%d in %ds",
                            resp.status_code, url, attempt, retries, wait)
                time.sleep(wait)
                continue

            if resp.status_code in (401, 403):
                log.error("Autoryzacja nieudana (HTTP %d). Sprawdź token API.", resp.status_code)
                sys.exit(1)

            resp.raise_for_status()
            return resp

        except requests.ConnectionError as exc:
            log.error("Błąd połączenia z %s: %s", url, exc)
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))
                continue
            sys.exit(1)
        except requests.Timeout:
            log.error("Timeout (%ds) dla %s", REQUEST_TIMEOUT, url)
            if attempt < retries:
                time.sleep(2 ** (attempt - 1))
                continue
            sys.exit(1)
        except requests.HTTPError as exc:
            log.error("HTTP error: %s", exc)
            sys.exit(1)

    log.error("Wyczerpano liczbę prób (%d) dla %s", retries, url)
    sys.exit(1)


def clean_domain(raw: str) -> Optional[str]:
    """Strip protocol prefixes and whitespace; return None if invalid."""
    d = raw.strip()
    for prefix in ("https://", "http://"):
        if d.lower().startswith(prefix):
            d = d[len(prefix):]
    d = d.strip().rstrip("/")
    if not d or " " in d or "\t" in d:
        return None
    return d


# ---------------------------------------------------------------------------
# Domain sources
# ---------------------------------------------------------------------------

def load_domains_from_csv(path: str) -> List[str]:
    """Load domains from a tab-separated CSV with an 'AdresDomeny' column."""
    filepath = Path(path)
    if not filepath.is_file():
        log.error("Plik nie istnieje: %s", path)
        sys.exit(1)

    text = filepath.read_text(encoding="utf-8", errors="replace")

    # Try tab separator first (CERT.PL format), fall back to comma
    for delimiter in ("\t", ",", ";"):
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        if "AdresDomeny" in (reader.fieldnames or []):
            domains = []
            for row in reader:
                d = clean_domain(row.get("AdresDomeny", ""))
                if d:
                    domains.append(d)
            if domains:
                return domains

    # If no AdresDomeny column found, try reading as plain-text (one domain per line)
    log.warning("Brak kolumny 'AdresDomeny' w CSV — próbuję jako plain-text (jedna domena/linia)")
    domains = []
    for line in text.splitlines():
        d = clean_domain(line)
        if d:
            domains.append(d)

    if not domains:
        log.error("Nie znaleziono domen w pliku %s (brak kolumny 'AdresDomeny' ani domen plain-text)", path)
        sys.exit(1)

    return domains


def load_domains_from_url(url: str) -> List[str]:
    """Download a plain-text domain list from a URL."""
    log.info("Pobieranie domen z: %s", url)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Nie udało się pobrać URL %s: %s", url, exc)
        sys.exit(1)

    domains = []
    for line in resp.text.splitlines():
        d = clean_domain(line)
        if d:
            domains.append(d)

    if not domains:
        log.error("Brak domen w odpowiedzi z %s", url)
        sys.exit(1)

    return domains


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_domains(domains: List[str], max_bytes: int = MAX_PAYLOAD_BYTES) -> List[List[str]]:
    """Split domain list into chunks that fit within max_bytes when JSON-serialized."""
    # Estimate: full payload JSON envelope is ~100 bytes + per-domain overhead
    # We'll measure precisely by building chunks incrementally.
    chunks: List[List[str]] = []
    current: List[str] = []
    current_size = 100  # approximate envelope overhead

    for domain in domains:
        entry_size = len(domain.encode("utf-8")) + 4  # quotes + comma + newline
        if current and (current_size + entry_size) > max_bytes:
            chunks.append(current)
            current = []
            current_size = 100
        current.append(domain)
        current_size += entry_size

    if current:
        chunks.append(current)

    return chunks


# ---------------------------------------------------------------------------
# Netskope API operations
# ---------------------------------------------------------------------------

def get_urllist(tenant: str, token: str, list_name: str) -> Optional[dict]:
    """Find a URL List by name. Returns the list dict or None if not found."""
    url = f"https://{tenant}/api/v2/policy/urllist"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    resp = api_request("GET", url, headers)
    data = resp.json()
    urllists = data if isinstance(data, list) else data.get("data", data.get("urllists", []))

    for ul in urllists:
        if ul.get("name") == list_name:
            log.info("Znaleziono URL Listę '%s' (id=%s)", list_name, ul.get("id"))
            return ul

    available = [ul.get("name") for ul in urllists]
    log.warning("URL Lista '%s' nie istnieje w Netskope.", list_name)
    log.info("Dostępne listy: %s", ", ".join(sorted(available)) if available else "(brak)")
    return None


def create_urllist(tenant: str, token: str, list_name: str, domains: List[str]) -> dict:
    """Create a new URL List in Netskope with initial domains. Returns the created list dict."""
    url = f"https://{tenant}/api/v2/policy/urllist"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"name": list_name, "data": {"urls": domains, "type": "exact"}}
    resp = api_request("POST", url, headers, json_body=body)
    data = resp.json()
    log.debug("POST response: %s", json.dumps(data, indent=2)[:500])

    # Response may be: dict with "id", dict with "data" key, or a list
    if isinstance(data, dict):
        if "id" in data:
            created = data
        elif "data" in data and isinstance(data["data"], dict):
            created = data["data"]
        else:
            created = data
    elif isinstance(data, list) and len(data) > 0:
        created = data[-1]
    else:
        log.error("Nieoczekiwana odpowiedź API przy tworzeniu listy: %s", data)
        sys.exit(1)

    log.info("Utworzono URL Listę '%s' (id=%s) z %d domenami", list_name, created.get("id"), len(domains))
    return created


def get_urllist_count(tenant: str, token: str, list_id: int) -> int:
    """Get the current number of URLs in a URL List."""
    url = f"https://{tenant}/api/v2/policy/urllist/{list_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = api_request("GET", url, headers)
    data = resp.json()
    if isinstance(data, dict):
        urls = data.get("data", {}).get("urls", data.get("urls", []))
    else:
        urls = []
    return len(urls) if isinstance(urls, list) else 0


def update_urllist_put(tenant: str, token: str, list_id: int, list_name: str,
                       domains: List[str]) -> None:
    """Replace the URL list content (PUT)."""
    url = f"https://{tenant}/api/v2/policy/urllist/{list_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"name": list_name, "data": {"urls": domains, "type": "exact"}}
    api_request("PUT", url, headers, json_body=body)
    log.info("PUT %d domen do listy '%s'", len(domains), list_name)


def append_urllist(tenant: str, token: str, list_id: int, domains: List[str]) -> None:
    """Append domains to the URL list (PATCH)."""
    url = f"https://{tenant}/api/v2/policy/urllist/{list_id}/append"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"data": {"urls": domains, "type": "exact"}}
    api_request("PATCH", url, headers, json_body=body)
    log.info("PATCH/append %d domen", len(domains))


def deploy_changes(tenant: str, token: str) -> None:
    """Deploy pending URL list changes."""
    url = f"https://{tenant}/api/v2/policy/urllist/deploy"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    api_request("POST", url, headers)
    log.info("Deploy zmian — OK")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Netskope URL List Updater — aktualizacja URL Listy z pliku CSV lub URL endpointa.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Przykłady:
  # Nadpisanie z pliku CSV
  python3 updateURLlist.py -s domains.csv -l UL-test -t TOKEN -n tenant.goskope.com

  # Nadpisanie z URL + deploy
  python3 updateURLlist.py -s https://hole.cert.pl/domains/v2/domains.txt -l UL-test -t TOKEN -n tenant.goskope.com -d

  # Dodanie (append) z URL
  python3 updateURLlist.py -s https://hole.cert.pl/domains/v2/domains.txt -l UL-test -t TOKEN -n tenant.goskope.com -a

  # Utworzenie nowej listy (jeśli nie istnieje) i nadpisanie
  python3 updateURLlist.py -s domains.csv -l UL-nowa -t TOKEN -n tenant.goskope.com -c
""",
    )
    parser.add_argument("-s", "--source", required=True,
                        help="Źródło domen: ścieżka do pliku CSV lub URL endpointa")
    parser.add_argument("-l", "--urlist", required=True,
                        help="Nazwa URL Listy w Netskope")
    parser.add_argument("-t", "--token", required=True,
                        help="Bearer token API Netskope")
    parser.add_argument("-n", "--nskp", required=True,
                        help="Adres tenanta Netskope (np. pzusa.goskope.com)")
    parser.add_argument("-a", "--add", action="store_true",
                        help="Tryb append (PATCH) zamiast nadpisania (PUT)")
    parser.add_argument("-c", "--create", action="store_true",
                        help="Utwórz URL Listę jeśli nie istnieje")
    parser.add_argument("-d", "--deploy", action="store_true",
                        help="Automatyczny deploy zmian po aktualizacji")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    # --- 1. Load domains ---
    is_url = args.source.startswith("http://") or args.source.startswith("https://")

    if is_url:
        raw_domains = load_domains_from_url(args.source)
    else:
        raw_domains = load_domains_from_csv(args.source)

    # Deduplicate
    unique_domains = sorted(set(raw_domains))
    log.info("Pobrano %d domen (%d unikalnych)", len(raw_domains), len(unique_domains))

    if not unique_domains:
        log.error("Brak domen do przetworzenia.")
        sys.exit(1)

    # --- 2. Chunk if needed ---
    chunks = chunk_domains(unique_domains)
    total_payload = len(json.dumps(unique_domains).encode("utf-8"))
    log.info("Rozmiar payloadu: %.2f MB → %d chunk(ów)", total_payload / (1024 * 1024), len(chunks))

    # --- 3. Find or create URL List ---
    urllist = get_urllist(args.nskp, args.token, args.urlist)
    created_new = False

    if urllist is None:
        if args.create:
            # Create list with first chunk of domains
            log.info("Tworzenie nowej URL Listy '%s' z pierwszym chunkiem...", args.urlist)
            urllist = create_urllist(args.nskp, args.token, args.urlist, chunks[0])
            created_new = True
        else:
            log.error("Użyj flagi -c / --create aby automatycznie utworzyć listę.")
            sys.exit(1)

    list_id = urllist["id"]
    list_name = urllist["name"]

    # Count domains before update
    if created_new:
        count_before = 0
    else:
        count_before = get_urllist_count(args.nskp, args.token, list_id)
        log.info("Aktualna liczba domen w liście: %d", count_before)

    # --- 4. Update ---
    chunks_sent = 0

    if created_new:
        # First chunk already sent during creation
        chunks_sent = 1
        # Remaining chunks via PATCH/append
        for i, chunk in enumerate(chunks[1:], 2):
            log.info("Append chunk %d/%d (%d domen)...", i, len(chunks), len(chunk))
            append_urllist(args.nskp, args.token, list_id, chunk)
            chunks_sent += 1
    elif args.add:
        # Append mode: all chunks via PATCH
        for i, chunk in enumerate(chunks, 1):
            log.info("Append chunk %d/%d (%d domen)...", i, len(chunks), len(chunk))
            append_urllist(args.nskp, args.token, list_id, chunk)
            chunks_sent += 1
    else:
        # Replace mode: first chunk PUT, rest PATCH
        for i, chunk in enumerate(chunks, 1):
            if i == 1:
                log.info("PUT chunk %d/%d (%d domen)...", i, len(chunks), len(chunk))
                update_urllist_put(args.nskp, args.token, list_id, list_name, chunk)
            else:
                log.info("Append chunk %d/%d (%d domen)...", i, len(chunks), len(chunk))
                append_urllist(args.nskp, args.token, list_id, chunk)
            chunks_sent += 1

    # --- 5. Count after update ---
    count_after = get_urllist_count(args.nskp, args.token, list_id)
    delta = count_after - count_before
    if delta >= 0:
        delta_str = f"+{delta}"
    else:
        delta_str = str(delta)

    # --- 6. Deploy ---
    if args.deploy:
        log.info("Deploying zmian...")
        deploy_changes(args.nskp, args.token)

    # --- 7. Summary ---
    print("\n" + "=" * 60)
    print("PODSUMOWANIE")
    print("=" * 60)
    print(f"  URL Lista:      {list_name} (id={list_id})")
    print(f"  Tryb:           {'APPEND' if args.add else 'REPLACE'}")
    print(f"  Źródło:         {args.source}")
    print(f"  Wysłano domen:  {len(unique_domains)}")
    print(f"  Chunków:        {chunks_sent}")
    print(f"  Przed:          {count_before} domen")
    print(f"  Po:             {count_after} domen ({delta_str})")
    print(f"  Deploy:         {'TAK' if args.deploy else 'NIE (pending)'}")
    print(f"  Status:         OK")
    print("=" * 60)


if __name__ == "__main__":
    main()

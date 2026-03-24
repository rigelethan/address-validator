"""
geocoder.py — US Census Geocoder batch API client.

Uses the free, key-free Census locations/addressbatch endpoint.
Endpoint: POST https://geocoding.geo.census.gov/geocoder/locations/addressbatch

Input CSV format per row: ID, Street, City, State, ZIP
Response CSV format:      ID, Input, Match, MatchType, MatchedAddr, Coords, TigerID, Side

Municipality (place name) is extracted from the matched address string returned
by the Census API, which uses USPS city names — accurate enough for flagging
out-of-jurisdiction signers.
"""

from __future__ import annotations

import csv
import io
import time

import httpx

_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
_BENCHMARK = "Public_AR_Current"
_TIMEOUT = 300.0  # seconds — large batches can be slow


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_csv(addresses: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    for a in addresses:
        w.writerow([
            a['id'],
            a.get('street', ''),
            a.get('city', ''),
            a.get('state', 'AZ'),
            a.get('zip', ''),
        ])
    return buf.getvalue().encode('utf-8')


def _city_from_matched(matched: str) -> str:
    """
    Extract city from a Census matched address string.
    Format: '4331 W GOLDEN RANCH PL, MARANA, AZ, 85658'
    Returns 'MARANA', or '' if format is unexpected.
    """
    parts = [p.strip() for p in matched.split(',')]
    # Need at least: STREET, CITY, STATE, ZIP (4 parts)
    return parts[-3] if len(parts) >= 4 else ''


def _empty_result() -> dict:
    return {
        'status': 'unresolved',
        'match_type': '',
        'matched_address': '',
        'place_name': '',
    }


# ── public API ────────────────────────────────────────────────────────────────

def geocode_batch(addresses: list[dict], max_retries: int = 3) -> dict:
    """
    Geocode up to 1,000 addresses against the Census batch API.

    *addresses* is a list of dicts with keys:
        id, street, city, state, zip

    Returns a dict keyed by address id, each value containing:
        status          : 'resolved' | 'unresolved'
        match_type      : 'Exact' | 'Non_Exact' | 'Tie' | ''
        matched_address : normalized address string from Census
        place_name      : city extracted from matched_address
    """
    csv_data = _build_csv(addresses)
    resp = None

    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                _URL,
                data={'benchmark': _BENCHMARK},
                files={'addressFile': ('addresses.csv', csv_data, 'text/csv')},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            break
        except (httpx.HTTPError, httpx.TimeoutException):
            if attempt < max_retries - 1:
                time.sleep(2 ** (attempt + 1))  # 2s, 4s, 8s
            else:
                return {a['id']: _empty_result() for a in addresses}

    results: dict = {}
    for row in csv.reader(io.StringIO(resp.text)):
        if not row:
            continue
        addr_id      = row[0].strip().strip('"')
        match_status = row[2].strip('"').strip() if len(row) > 2 else ''
        match_type   = row[3].strip('"').strip() if len(row) > 3 else ''
        matched_addr = row[4].strip('"').strip() if len(row) > 4 else ''

        results[addr_id] = {
            'status': 'resolved' if match_status == 'Match' else 'unresolved',
            'match_type': match_type,
            'matched_address': matched_addr,
            'place_name': _city_from_matched(matched_addr) if matched_addr else '',
        }

    # Any address absent from the response is unresolved
    for a in addresses:
        results.setdefault(a['id'], _empty_result())

    return results


def geocode_all(addresses: list[dict], batch_size: int = 1000) -> dict:
    """
    Geocode an arbitrary number of addresses in batches of *batch_size*.
    Prints progress to stdout.
    """
    all_results: dict = {}
    n_batches = (len(addresses) + batch_size - 1) // batch_size

    for i in range(0, len(addresses), batch_size):
        batch = addresses[i : i + batch_size]
        batch_num = i // batch_size + 1
        print(f"    batch {batch_num}/{n_batches}  ({len(batch)} addresses)")
        all_results.update(geocode_batch(batch))

    return all_results

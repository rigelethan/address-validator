"""
petition_lookup.py — CLI for jurisdiction lookup on nomination petition PDFs.

Usage:
    python petition_lookup.py [OPTIONS] PDF [PDF...]

Options:
    --output-dir PATH   Directory for CSV output.          [default: ./output]
    --dpi INTEGER       OCR resolution in DPI.             [default: 300]
    --workers INTEGER   Parallel OCR processes.            [default: cpu count]
    --batch-size INT    Addresses per Census API request.  [default: 1000]
    --resume            Skip rows already present in the output CSV.

Example:
    python petition_lookup.py contreras_petitions_03.23.26.pdf --output-dir ./output
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import click
import usaddress

from geocoder import geocode_all
from ocr import extract_pages

# ── output schema ─────────────────────────────────────────────────────────────
_COLUMNS = [
    'pdf_file', 'page_num', 'row_num', 'printed_name', 'raw_address',
    'parsed_street', 'parsed_city', 'parsed_zip',
    'matched_address', 'place_name', 'match_type', 'status',
    'target_municipality', 'in_jurisdiction',
]

_MUNI_PREFIX_RE = re.compile(
    r'^(?:town|city|village|township)\s+of\s+', re.IGNORECASE
)


# ── address parsing ───────────────────────────────────────────────────────────

def parse_address(raw: str) -> dict:
    """
    Split a raw handwritten OCR address string into components.
    Returns: {street, city, state, zip}
    """
    raw = raw.strip()
    if not raw:
        return {'street': '', 'city': '', 'state': 'AZ', 'zip': ''}

    # Pull out ZIP (first 5-digit sequence)
    zip_m = re.search(r'\b(\d{5})\b', raw)
    zip_code = zip_m.group(1) if zip_m else ''

    # Remove ZIP and state abbreviation before parsing
    cleaned = re.sub(r'\b\d{5}\b', '', raw)
    cleaned = re.sub(r'\bAZ\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(' ,')

    street, city = cleaned, ''
    try:
        tagged, _ = usaddress.tag(cleaned)
        _street_labels = {
            'AddressNumber', 'StreetNamePreDirectional', 'StreetName',
            'StreetNamePostType', 'StreetNamePostDirectional',
            'OccupancyType', 'OccupancyIdentifier',
        }
        street = ' '.join(v for k, v in tagged.items() if k in _street_labels)
        city   = ' '.join(v for k, v in tagged.items() if k == 'PlaceName')
    except usaddress.RepeatedLabelError:
        pass  # keep street = cleaned, city = ''

    return {
        'street': street.strip(),
        'city':   city.strip(),
        'state':  'AZ',
        'zip':    zip_code,
    }


# ── jurisdiction check ────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Strip 'Town/City of' prefix and lowercase for comparison."""
    return _MUNI_PREFIX_RE.sub('', name).strip().lower()


def check_jurisdiction(place_name: str, target: str) -> str:
    """Returns 'true', 'false', or 'unknown'."""
    if not place_name or not target:
        return 'unknown'
    return 'true' if _norm(place_name) == _norm(target) else 'false'


# ── resume support ────────────────────────────────────────────────────────────

def load_done(path: Path) -> set[tuple]:
    """Return set of (pdf_file, page_num, row_num) already written to CSV."""
    if not path.exists():
        return set()
    with open(path, newline='') as f:
        return {
            (r['pdf_file'], r['page_num'], r['row_num'])
            for r in csv.DictReader(f)
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

@click.command()
@click.argument('pdfs', nargs=-1, required=True, type=click.Path(exists=True))
@click.option('--output-dir',  default='./output', show_default=True,
              help='Directory for CSV output.')
@click.option('--dpi',         default=300,         show_default=True,
              help='OCR resolution in DPI.')
@click.option('--workers',     default=None, type=int,
              help='Parallel OCR processes (default: CPU count).')
@click.option('--batch-size',  default=1000,        show_default=True,
              help='Addresses per Census API request.')
@click.option('--resume',      is_flag=True,
              help='Skip rows already present in the output CSV.')
def main(pdfs, output_dir, dpi, workers, batch_size, resume):
    """Look up municipal jurisdiction for addresses in nomination petition PDFs."""

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(pdfs[0]).stem}.csv"

    done = load_done(out_path) if resume else set()
    file_exists = out_path.exists() and resume

    fh = open(out_path, 'a' if file_exists else 'w', newline='')
    writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
    if not file_exists:
        writer.writeheader()

    total = blanks = resolved = unresolved = in_j = out_j = unk_j = 0

    try:
        for pdf in pdfs:
            pdf_name = Path(pdf).name
            click.echo(f"\n{pdf_name}")
            click.echo(f"  OCR-ing pages (dpi={dpi}, workers={workers or 'auto'})...")

            pages = extract_pages(pdf, dpi=dpi, workers=workers)
            click.echo(f"  {len(pages)} paper signer pages found")

            # Collect rows that need geocoding
            pending: list[tuple] = []
            for page in pages:
                for row in page['rows']:
                    total += 1
                    key = (pdf_name, str(page['page_num']), str(row['row_num']))

                    if not row['raw_address'].strip() and not row['printed_name'].strip():
                        blanks += 1
                        continue

                    if resume and key in done:
                        continue

                    pending.append((page, row, parse_address(row['raw_address'])))

            click.echo(f"  {len(pending)} rows to geocode")
            if not pending:
                continue

            # Build flat address list with stable IDs
            addr_list = [
                {
                    'id':     f"{page['page_num']}_{row['row_num']}_{i}",
                    'street': parsed['street'],
                    'city':   parsed['city'],
                    'state':  parsed['state'],
                    'zip':    parsed['zip'],
                }
                for i, (page, row, parsed) in enumerate(pending)
            ]

            click.echo(f"  Geocoding...")
            geo = geocode_all(addr_list, batch_size=batch_size)

            # Write one CSV row per signer row
            for i, (page, row, parsed) in enumerate(pending):
                gid = f"{page['page_num']}_{row['row_num']}_{i}"
                g   = geo.get(gid, {})

                status     = g.get('status', 'unresolved')
                place_name = g.get('place_name', '')
                target     = page.get('target_municipality', '')
                jurisdiction = check_jurisdiction(place_name, target)

                if status == 'resolved':
                    resolved += 1
                else:
                    unresolved += 1

                if jurisdiction == 'true':
                    in_j += 1
                elif jurisdiction == 'false':
                    out_j += 1
                else:
                    unk_j += 1

                writer.writerow({
                    'pdf_file':           pdf_name,
                    'page_num':           page['page_num'],
                    'row_num':            row['row_num'],
                    'printed_name':       row['printed_name'],
                    'raw_address':        row['raw_address'],
                    'parsed_street':      parsed['street'],
                    'parsed_city':        parsed['city'],
                    'parsed_zip':         parsed['zip'],
                    'matched_address':    g.get('matched_address', ''),
                    'place_name':         place_name,
                    'match_type':         g.get('match_type', ''),
                    'status':             status,
                    'target_municipality': target,
                    'in_jurisdiction':    jurisdiction,
                })
                fh.flush()

    finally:
        fh.close()

    geocoded = resolved + unresolved
    click.echo(f"""
Summary
-------
Total rows          : {total}
  Blank (skipped)   : {blanks}
  Geocoded          : {geocoded}
    Resolved        : {resolved}
    Unresolved      : {unresolved}
In jurisdiction     : {in_j}
Out of jurisdiction : {out_j}
Unknown             : {unk_j}
Output              : {out_path}
""")


if __name__ == '__main__':
    main()

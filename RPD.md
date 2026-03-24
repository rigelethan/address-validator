# RPD: Automated Jurisdiction Lookup for Candidate Nomination Petitions

## Overview

Automate the extraction of addresses from candidate nomination petition PDFs and determine which municipal jurisdiction each signer's address falls within, using only free APIs. Designed to process tens of thousands of signatures per run.

---

## Problem Statement

Election staff must manually cross-reference each signer's address from nomination petitions against municipal boundaries to verify that signers reside within the correct jurisdiction. This is time-consuming, error-prone, and does not scale for petitions with tens of thousands of signatures.

---

## Goals

- Extract all signer addresses from scanned nomination petition PDFs via OCR.
- Resolve each address to its municipality (city/town/township/village) using free APIs.
- Flag addresses that are outside the target municipality or that cannot be resolved.
- Produce a structured output report (CSV/JSON) that staff can review and act on.
- Handle tens of thousands of addresses per run efficiently.

---

## Non-Goals

- Voter registration status verification (out of scope; separate system).
- Signature authenticity or handwriting recognition beyond printed/typed form fields.
- Real-time/live petition review (batch processing is sufficient for v1).
- Congressional or state legislative district lookups (future phase).
- Support for non-US jurisdictions.
- Paid API integrations.

---

## User Stories

| # | As a... | I want to... | So that... |
|---|---------|-------------|------------|
| 1 | Election clerk | Run the tool against one or more petition PDFs | All addresses are extracted automatically |
| 2 | Election clerk | See each address mapped to a municipality | I can verify signers are in the correct jurisdiction |
| 3 | Election clerk | Download a CSV report of results | I can share findings with staff or candidates |
| 4 | Election supervisor | See a summary count of valid vs. invalid addresses | I can quickly assess petition validity |
| 5 | Developer | Re-run lookups on failed addresses without re-processing the whole PDF | I can recover from transient errors without wasting time |

---

## Functional Requirements

### 1. PDF Ingestion

- Accept one or more PDF file paths via CLI.
- All PDFs are scanned images — OCR is the primary (and only) text extraction path.
- Petitions follow a **consistent template**: same column layout and field positions across all files.
- Extract all pages; skip page headers and footers based on known template positions.

### 2. Address Extraction via OCR

- Convert each PDF page to a high-resolution image (≥300 DPI).
- Run Tesseract OCR on each page image.
- Parse OCR output according to the known template structure (fixed column positions or table rows).
- Extract per-signer row:
  - Street address (number + street name + unit if present)
  - City
  - State
  - ZIP code
- Post-process OCR output:
  - Fix common OCR errors (e.g., `0` vs `O`, `l` vs `1`).
  - Normalize whitespace and capitalization.
  - Strip non-address noise from fields.
- Flag rows where a required field is missing or confidence is low.

### 3. Jurisdiction Lookup

- Resolve each extracted address to its municipality using the **US Census Geocoder batch API**.
- The Census Geocoder is free, requires no API key, and returns the Census-designated place (municipality) via FIPS code and name.
- Batch addresses in groups of **1,000** (Census API limit per batch request).
- For each address return:
  - Matched/normalized address string from Census
  - State FIPS code
  - County FIPS code + name
  - Place FIPS code + name (municipality)
  - Match type (`Exact`, `Non_Exact`, `Tie`)
  - Match status (`Match`, `No_Match`, `Tie`)
- Retry failed batches up to 3 times with exponential backoff (2s, 4s, 8s).
- Addresses with `No_Match` are marked `unresolved` and included in the report for manual review.

**API:** `https://geocoding.geo.census.gov/geocoder/geographies/addressbatch`
- Method: POST, multipart form
- Benchmark input: vintage `Current_Current`, layers `Census2020`
- No authentication required
- Rate limit: none published; batch of 1,000 keeps request count manageable

### 4. Output Report

Produce a CSV with one row per signer:

| Column | Description |
|--------|-------------|
| `row_num` | Row number within the petition (1-based, across all pages) |
| `pdf_file` | Source PDF filename |
| `page_num` | Page number within the PDF |
| `raw_address` | Address string as extracted from OCR |
| `normalized_address` | Matched address returned by Census Geocoder |
| `state_fips` | 2-digit state FIPS code |
| `county_fips` | 5-digit county FIPS code |
| `county_name` | County name |
| `place_fips` | 7-digit place FIPS code |
| `place_name` | Municipality name (Census-designated place) |
| `match_type` | `Exact`, `Non_Exact`, `Tie`, or blank |
| `status` | `resolved`, `partial`, `unresolved` |
| `in_jurisdiction` | `true` / `false` / `unknown` |

- Export CSV to a configurable output directory.
- Print a summary to console on completion:
  - Total rows processed
  - Resolved count
  - Unresolved count
  - In-jurisdiction count
  - Out-of-jurisdiction count

### 5. Jurisdiction Filtering

- Accept a target municipality name or place FIPS code as a CLI argument.
- Mark each resolved address `in_jurisdiction=true` if the resolved place matches the target, `false` otherwise.
- Matching is case-insensitive; also match common name variants (e.g., "City of X" vs "X").

---

## Technical Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                            CLI                                   │
│   (PDF paths, target municipality, output dir)                   │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                  ┌──────▼──────┐
                  │  PDF → Image│  pdf2image (poppler)
                  │  Converter  │  300 DPI, per-page PNG
                  └──────┬──────┘
                         │  page images
                  ┌──────▼──────┐
                  │  OCR Engine │  pytesseract (Tesseract 5)
                  └──────┬──────┘
                         │  raw text / hOCR
                  ┌──────▼──────┐
                  │  Template   │  fixed-column parser for
                  │  Parser     │  known petition layout
                  └──────┬──────┘
                         │  structured address rows
                  ┌──────▼──────┐
                  │  Address    │  usaddress + regex cleanup
                  │  Normalizer │
                  └──────┬──────┘
                         │  clean address objects (batches of 1,000)
                  ┌──────▼──────┐
                  │  Census     │  POST addressbatch endpoint
                  │  Geocoder   │  free, no key, returns place FIPS
                  └──────┬──────┘
                         │  geocoded results
                  ┌──────▼──────┐
                  │  Jurisdiction│  compare place FIPS / name
                  │  Evaluator  │  against target municipality
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │  Report     │  CSV + console summary
                  │  Generator  │
                  └─────────────┘
```

### Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.11+ | Strong PDF/OCR/geo library ecosystem |
| PDF → image | `pdf2image` + `poppler-utils` | Reliable raster conversion at configurable DPI |
| OCR | `pytesseract` (Tesseract 5) | Industry-standard free OCR; good on printed forms |
| Address parsing | `usaddress` | US-specific; handles abbreviations and component splitting |
| HTTP client | `httpx` | Sync batch POST with retry; simple for staff environments |
| Jurisdiction API | US Census Geocoder batch API | Free, no key, returns place FIPS, handles 1k/batch |
| CLI | `click` | Lightweight, well-known UX for staff scripts |
| Output | `csv` stdlib | No extra dependencies |
| Testing | `pytest` + `responses` | Unit and integration tests with mocked HTTP |

---

## Performance Considerations

At tens of thousands of signers per run:

- **OCR is the bottleneck.** Use multiprocessing (`concurrent.futures.ProcessPoolExecutor`) to OCR multiple pages in parallel, limited to `os.cpu_count()` workers.
- **Census Geocoder batching.** 1,000-address batches mean ~10–50 HTTP requests for 10k–50k addresses. Run batches concurrently (up to 5 parallel requests) to stay within reasonable server load.
- **Deduplication.** De-duplicate addresses before geocoding; map results back to all matching rows. Large petitions often have repeated addresses.
- **Intermediate checkpointing.** After geocoding each batch, append results to the output CSV immediately so a crash does not lose all work. On restart, skip rows already present in the output file.

Estimated throughput targets:

| Stage | Rate | Time for 50k rows |
|-------|------|-------------------|
| OCR (8-core machine) | ~2 pages/sec | ~1–3 hrs (varies by page density) |
| Census Geocoder (5 parallel batches) | ~5k addresses/min | ~10 min |
| Total | — | ~1–3 hrs (OCR-bound) |

---

## Error Handling & Edge Cases

| Scenario | Handling |
|----------|----------|
| Low OCR confidence on a field | Flag row as `partial`; include raw OCR text in report |
| Missing ZIP code | Attempt geocode with street + city + state only; mark as `partial` |
| Census API returns `No_Match` | Mark `unresolved`; include in report for manual review |
| Census API HTTP error / timeout | Retry batch up to 3× with exponential backoff; if all retries fail, mark rows `unresolved` with error note |
| Duplicate addresses | Deduplicate before API calls; attribute result to all matching rows |
| PO Box address | Flag; Census Geocoder cannot resolve PO Boxes to a place |
| Multi-page petitions | Process all pages; detect and skip header/footer rows by template row-count rules |
| Partially visible row at page edge | Flag as `partial` if any required field is truncated |
| Run interrupted mid-way | Resume from checkpoint — skip rows already written to output CSV |

---

## Configuration

All configuration via CLI flags with optional `.env` file fallback:

```
TARGET_MUNICIPALITY=<name or place FIPS>  # e.g., "Springfield" or "1770000"
OUTPUT_DIR=./output
OUTPUT_FORMAT=csv
DPI=300                  # OCR scan resolution (300 recommended minimum)
BATCH_SIZE=1000          # Census Geocoder batch size (max 1000)
MAX_PARALLEL_BATCHES=5   # Concurrent geocoder requests
OCR_WORKERS=8            # Parallel OCR processes (default: cpu count)
RESUME=true              # Skip rows already in output file
```

---

## Acceptance Criteria

- [ ] All input PDFs are scanned images; the tool does not require selectable text.
- [ ] Addresses are extracted using the known petition template layout with ≥90% field extraction accuracy on clean scans.
- [ ] Each extracted address is submitted to the Census Geocoder in batches of ≤1,000.
- [ ] Resolved addresses include `place_name` and `place_fips` (municipality).
- [ ] Each address is marked `in_jurisdiction`, `out_of_jurisdiction`, or `unknown` based on the target municipality input.
- [ ] Unresolved addresses are flagged and included in the output — never silently dropped.
- [ ] Output CSV includes all required columns.
- [ ] Console summary is printed on completion.
- [ ] A run interrupted mid-way can be resumed without re-geocoding already-processed rows.
- [ ] Unit tests cover: OCR template parsing, address normalization, Census API response parsing (mocked), jurisdiction evaluation, and report writing.
- [ ] Tool processes 50,000 addresses end-to-end without out-of-memory errors on a standard 8GB RAM machine.

---

## Out of Scope (Future Phases)

- Congressional or state legislative district lookups.
- Web UI with drag-and-drop upload.
- Database storage of historical lookups.
- Integration with voter registration database for full signature validation.
- Paid API integrations (Geocodio, Smarty, Google Civic).
- Handwriting recognition (current scope covers printed/typed form fields only).

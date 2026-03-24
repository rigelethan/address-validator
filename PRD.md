# PRD: Automated Jurisdiction Lookup for Candidate Nomination Petitions

## Overview

Automate the extraction of signer addresses from scanned **paper** candidate nomination petition PDFs and determine whether each address falls within the target municipality, using only free APIs. Designed to process tens of thousands of signatures per run.

---

## Problem Statement

Election staff must manually cross-reference each signer's address from nomination petitions against municipal boundaries to verify that signers reside within the correct jurisdiction. This is time-consuming, error-prone, and does not scale for petitions with tens of thousands of signatures.

---

## Goals

- Extract all signer addresses from scanned paper nomination petition PDFs via OCR.
- Resolve each address to its municipality using free APIs.
- Flag addresses that are outside the target municipality or that cannot be resolved.
- Produce a structured CSV report that staff can review and act on.
- Handle tens of thousands of addresses per run efficiently.

---

## Non-Goals

- Processing of E-Qual (electronic) petition pages — those appear earlier in the same PDFs but use a different format and are out of scope for v1.
- Voter registration status verification (separate system).
- Signature field recognition (column is captured but not used for jurisdiction lookup).
- Real-time/live petition review (batch processing is sufficient for v1).
- Congressional or state legislative district lookups (future phase).
- Support for non-US jurisdictions or non-Arizona petition formats.
- Paid API integrations.

---

## Petition Template

All paper petitions follow the **Arizona Secretary of State Nonpartisan Nomination Petition** form (Revised 7/27/2021).

### PDF Structure

A single submitted PDF may contain:
1. **E-Qual petition pages** at the front (out of scope — skip these).
2. **Paper petition booklets** starting partway through the PDF, each booklet consisting of a repeating pair of pages:

#### Page A — Circulator Cover Page
- "Instructions for Circulators" box at top
- Circulator's name (handwritten or printed), county
- Circulator's signature
- Typed/printed name of circulator
- Circulator's actual residence address (handwritten)
- City, state, ZIP (handwritten)
- Footer: `Revised 7/27/2021, Secretary of State`

#### Page B — Signer Page
- **Header**: "Nonpartisan Nomination Petition"
- **Paragraph** naming: candidate, county, office, election, and **municipality** (e.g., `TOWN OF MARANA`) — target municipality is extracted from here
- **Candidate photo** (left side of header)
- **Clerk's office "RECEIVED" stamp** (top-right corner, variable position)
- **Signer table** — 10 rows:

| Column | Label | Content type |
|--------|-------|--------------|
| 1 | *(row number)* | Printed (1–10) |
| 2 | Signature | Handwritten — not used |
| 3 | Printed Name | Handwritten printed name |
| 4 | Actual residence address, description of place of residence or Arizona post off ce box address, city or town | **Primary extraction target** — handwritten full address |
| 5 | Date of Signing | Handwritten date |

- Footer: `Revised 7/27/2021, Secretary of State` + disclaimer text

### Address Format (Column 4)

Addresses are **handwritten** and vary in legibility. Common patterns:

```
7571 W. Desert Crystal Ct              (street only, city/zip on same line or next)
7578 W Disnt Crsytl CT  85743          (OCR noise expected)
9313 N. Desert Monsoon Pl.
12720 N Nop Ln  85653
7623 W Stoney Night Ln  85743
```

- State (`AZ`) is often omitted by signers; should be inferred from context.
- City is sometimes omitted; ZIP is often present.
- Handwriting quality varies significantly — low OCR confidence is expected on a portion of rows.
- No consistent delimiter between street, city, and ZIP.

### Detecting Paper vs. E-Qual Pages

| Signal | E-Qual page | Paper page |
|--------|-------------|------------|
| Footer text | `Petition ID: XXXX` | `Revised 7/27/2021, Secretary of State` |
| Address content | Typed (all caps, comma-separated) | Handwritten |
| Rows per signer page | 15 | 10 |
| Voter ID column | Yes | No |
| Candidate photo | No | Yes |

Detect paper signer pages by presence of `Revised 7/27/2021` in the footer OCR text.

---

## User Stories

| # | As a... | I want to... | So that... |
|---|---------|-------------|------------|
| 1 | Election clerk | Run the tool against one or more petition PDFs | All paper petition addresses are extracted automatically |
| 2 | Election clerk | See each address mapped to a municipality | I can verify signers are in the correct jurisdiction |
| 3 | Election clerk | Download a CSV report | I can share findings with staff or the candidate |
| 4 | Election supervisor | See a summary count of valid vs. invalid addresses | I can quickly assess petition validity |
| 5 | Developer | Re-run geocoding on failed rows without re-OCRing | I can recover from transient errors efficiently |

---

## Functional Requirements

### 1. PDF Ingestion & Page Classification

- Accept one or more PDF file paths via CLI.
- All PDFs are scanned images (Canon iR-ADV scanner) — OCR is the only extraction path.
- Classify each page as one of:
  - `cover_equal` — E-Qual circulator cover sheet (skip)
  - `signer_equal` — E-Qual signer page (skip)
  - `cover_paper` — Paper circulator cover page (skip content, note booklet boundary)
  - `signer_paper` — Paper signer page (**process**)
- Classification signal: presence of `Revised 7/27/2021` in footer OCR text → paper page.

### 2. Address Extraction via OCR

- Convert each paper signer page to a **300 DPI** PNG using `pdf2image` / `poppler`.
- Run Tesseract OCR (`--psm 6`, `--oem 3`) on the full page.
- Locate the signer table within the page by finding the header row containing "Printed name" and "residence address".
- For each of the 10 signer rows:
  - Extract the text content of column 4 (address field).
  - Use the known horizontal bounding region of column 4 for targeted extraction.
- Post-process OCR output:
  - Strip leading/trailing whitespace and OCR noise characters.
  - Normalize common handwriting OCR errors (e.g., `l`↔`1`, `0`↔`O`).
  - Attempt to identify street number, street name, city, and ZIP via regex.
- Flag rows where:
  - The address field is blank (no signer).
  - A street number cannot be identified.
  - Confidence is too low to parse components (mark `partial`).

### 3. Address Parsing

Parse each extracted address string into components for the Census Geocoder:

| Component | Strategy |
|-----------|----------|
| Street | Regex: leading digits + remaining text up to city/ZIP |
| City | Text between street and ZIP, or from known municipality context |
| State | Default `AZ` if absent |
| ZIP | 5-digit sequence anywhere in the string |

Use `usaddress` as a fallback parser for strings that don't match the primary regex.

If city cannot be determined, submit without city (Census Geocoder can often resolve from street + ZIP alone).

### 4. Jurisdiction Lookup via US Census Geocoder

- Submit addresses to the **US Census Geocoder batch API** — free, no key required.
- Endpoint: `POST https://geocoding.geo.census.gov/geocoder/geographies/addressbatch`
- Batch size: **1,000 addresses per request** (API maximum).
- For each address, capture:
  - Matched/normalized address string
  - State FIPS
  - County FIPS + name
  - Place FIPS + name (Census-designated place = municipality)
  - Match type: `Exact`, `Non_Exact`, or `Tie`
  - Match status: `Match` or `No_Match`
- Retry failed batches up to **3 times** with exponential backoff (2s, 4s, 8s).
- `No_Match` rows are marked `unresolved`.

### 5. Jurisdiction Evaluation

- Extract the target municipality from the header paragraph of each signer page (e.g., `TOWN OF MARANA`).
- Compare resolved `place_name` against target municipality (case-insensitive, strip `City of` / `Town of` prefixes).
- Assign `in_jurisdiction`: `true`, `false`, or `unknown` (for unresolved rows).

### 6. Output Report

One CSV row per signer line:

| Column | Description |
|--------|-------------|
| `pdf_file` | Source PDF filename |
| `page_num` | Page number within the PDF |
| `row_num` | Row number within the signer table (1–10) |
| `printed_name` | Signer's printed name (column 3, OCR) |
| `raw_address` | Address string as extracted by OCR |
| `parsed_street` | Street component after parsing |
| `parsed_city` | City component after parsing |
| `parsed_zip` | ZIP component after parsing |
| `normalized_address` | Matched address from Census Geocoder |
| `county_fips` | 5-digit county FIPS |
| `county_name` | County name |
| `place_fips` | 7-digit place FIPS |
| `place_name` | Municipality name from Census |
| `match_type` | `Exact`, `Non_Exact`, `Tie`, or blank |
| `status` | `resolved`, `partial`, `unresolved` |
| `target_municipality` | Target municipality extracted from petition header |
| `in_jurisdiction` | `true`, `false`, or `unknown` |

Console summary on completion:

```
Pages processed (paper signer pages): 110
Total signer rows:                   1,100
  Blank rows skipped:                  143
  Rows submitted for geocoding:        957
    Resolved:                          891  (93.1%)
    Unresolved:                         66   (6.9%)
In jurisdiction:                       804  (84.0%)
Out of jurisdiction:                    87   (9.1%)
Unknown (unresolved):                   66   (6.9%)
Output: ./output/contreras_petitions_2026-03-23.csv
```

---

## Technical Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                            CLI                                   │
│   petition_lookup.py [PDF...] --output ./output  --resume        │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                  ┌──────▼──────┐
                  │  PDF → Image│  pdf2image (poppler), 300 DPI
                  │  Converter  │  ProcessPoolExecutor (parallel pages)
                  └──────┬──────┘
                         │  per-page PNG
                  ┌──────▼──────┐
                  │  Page       │  OCR footer text →
                  │  Classifier │  paper vs. e-qual; cover vs. signer
                  └──────┬──────┘
                         │  paper signer pages only
                  ┌──────▼──────┐
                  │  OCR Engine │  pytesseract --psm 6 --oem 3
                  │             │  targeted crop on address column
                  └──────┬──────┘
                         │  raw address strings (10 per page)
                  ┌──────▼──────┐
                  │  Address    │  regex primary + usaddress fallback
                  │  Parser     │  → street / city / state / zip
                  └──────┬──────┘
                         │  batches of 1,000 address dicts
                  ┌──────▼──────┐
                  │  Census     │  POST addressbatch (free, no key)
                  │  Geocoder   │  returns place FIPS + name
                  └──────┬──────┘
                         │  geocoded results
                  ┌──────▼──────┐
                  │  Jurisdiction│  place_name vs. target municipality
                  │  Evaluator  │  (from petition header OCR)
                  └──────┬──────┘
                         │
                  ┌──────▼──────┐
                  │  Report     │  CSV appended per batch (checkpoint)
                  │  Writer     │  + console summary
                  └─────────────┘
```

### Technology Stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.11+ | Strong PDF/OCR/geo library ecosystem |
| PDF → image | `pdf2image` + `poppler-utils` | Reliable raster conversion at configurable DPI |
| OCR | `pytesseract` (Tesseract 5) | Industry-standard; `--psm 6` works well for table rows |
| Address parsing | regex + `usaddress` fallback | Handles partial/messy handwritten address strings |
| HTTP client | `httpx` | Sync batch POST to Census API with retry |
| Jurisdiction API | US Census Geocoder batch API | Free, no key, 1k/batch, returns place FIPS |
| CLI | `click` | Standard, lightweight UX for staff scripts |
| Output | `csv` stdlib | No extra dependencies |
| Testing | `pytest` + `responses` | Unit and integration tests with mocked HTTP |

---

## Performance Considerations

- **OCR is the bottleneck** — handwritten text requires full-page OCR at 300 DPI.
  - Use `ProcessPoolExecutor` for parallel page processing (default: `os.cpu_count()` workers).
- **Census Geocoder batching** — 1,000-address batches keep HTTP overhead low; run up to 5 batches concurrently.
- **Deduplication** — deduplicate addresses before geocoding; attribute result back to all matching rows.
- **Checkpointing** — append results to CSV after each batch; `--resume` skips rows already present in the output file (keyed on `pdf_file` + `page_num` + `row_num`).

Estimated throughput on an 8-core machine:

| Stage | Est. rate | Time for 50k rows (~5k pages) |
|-------|-----------|-------------------------------|
| PDF → image (parallel) | ~3–4 pages/sec | ~25 min |
| OCR (8 workers) | ~2–3 pages/sec | ~30–40 min |
| Census Geocoder (5 parallel batches) | ~5k rows/min | ~10 min |
| **Total** | | **~45–60 min** |

---

## Error Handling & Edge Cases

| Scenario | Handling |
|----------|----------|
| E-Qual pages at start of PDF | Classified and skipped; not included in output |
| Blank signer row | Detected by empty address + name fields; skipped with note in summary |
| Low-legibility handwriting | OCR returns garbled text; row marked `partial`; raw OCR included in output for manual review |
| Address missing city | Submit to Census with street + ZIP only; mark `partial` if resolved, `unresolved` if not |
| Address missing ZIP | Submit with street + city; mark `partial` |
| PO Box address | Flag; Census cannot resolve PO Boxes to a municipality |
| Census returns `No_Match` | Mark `unresolved`; include raw and parsed address in CSV |
| Census HTTP error / timeout | Retry up to 3× exponential backoff; mark `unresolved` if all fail |
| Run interrupted | `--resume` flag re-reads output CSV and skips already-processed rows |
| Duplicate addresses | Deduplicate before API; result attributed to all matching rows |
| Clerk "RECEIVED" stamp overlapping table | Stamp typically falls in header area; if it bleeds into table, affected row flagged `partial` |

---

## CLI Interface

```
python petition_lookup.py [OPTIONS] PDF [PDF...]

Options:
  --output-dir PATH      Directory for CSV output.  [default: ./output]
  --dpi INTEGER          OCR resolution in DPI.     [default: 300]
  --workers INTEGER      Parallel OCR processes.    [default: cpu count]
  --batch-size INTEGER   Addresses per Census API request. [default: 1000]
  --resume               Skip rows already in the output CSV.
  --help                 Show this message and exit.
```

Example:

```bash
python petition_lookup.py contreras_petitions_03.23.26.pdf \
  --output-dir ./output \
  --resume
```

The target municipality is extracted automatically from each petition's header text — no manual input needed.

---

## Acceptance Criteria

- [ ] E-Qual pages are detected and skipped; only paper signer pages are processed.
- [ ] Paper signer pages are correctly identified via footer text detection.
- [ ] Target municipality is extracted from each paper signer page header (e.g., `TOWN OF MARANA`).
- [ ] All 10 signer rows are parsed per paper signer page.
- [ ] Address column (column 4) is extracted from each non-blank row.
- [ ] Addresses are split into street / city / state / ZIP components before geocoding.
- [ ] Each address batch of ≤1,000 is submitted to the Census Geocoder.
- [ ] Resolved addresses include `place_name` and `place_fips`.
- [ ] Each row is marked `in_jurisdiction`, `out_of_jurisdiction`, or `unknown`.
- [ ] Unresolved rows appear in the output CSV and are never silently dropped.
- [ ] Output CSV includes all required columns.
- [ ] Console summary is printed on completion.
- [ ] `--resume` skips already-processed rows on restart.
- [ ] Unit tests cover: page classification, row extraction, address parsing, Census response parsing (mocked), jurisdiction evaluation, and report writing.
- [ ] Tool processes the 130-page sample PDF end-to-end without crashing on an 8 GB RAM machine.

---

## Out of Scope (Future Phases)

- E-Qual petition processing (typed addresses, 15-row format, Voter ID column).
- Congressional or state legislative district lookups.
- Web UI with drag-and-drop upload.
- Database storage of historical lookups.
- Integration with voter registration database for full signature validation.
- Paid API integrations.
- Support for other state petition formats.

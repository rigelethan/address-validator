# RPD: Automated Jurisdiction Lookup for Candidate Nomination Petitions

## Overview

Automate the extraction of addresses from candidate nomination petition PDFs and determine which political jurisdiction each address falls within by querying a geospatial/civic API.

---

## Problem Statement

Election staff must manually cross-reference each signer's address from nomination petitions against district maps to verify that signers are registered voters in the correct political jurisdiction. This is time-consuming, error-prone, and does not scale for petitions with hundreds or thousands of signatures.

---

## Goals

- Extract all signer addresses from nomination petition PDFs without manual data entry.
- Resolve each address to its political jurisdiction(s) (e.g., state legislative district, congressional district, city ward, county precinct).
- Flag addresses that are outside the target jurisdiction or that cannot be resolved.
- Produce a structured output report (CSV/JSON) that staff can review and act on.

---

## Non-Goals

- Voter registration status verification (out of scope; separate system).
- Signature authenticity or handwriting recognition for fully handwritten petitions.
- Real-time/live petition review (batch processing is sufficient for v1).

---

## User Stories

| # | As a... | I want to... | So that... |
|---|---------|-------------|------------|
| 1 | Election clerk | Upload one or more petition PDFs | All addresses are extracted automatically |
| 2 | Election clerk | See each address mapped to a jurisdiction | I can verify signers are in the correct district |
| 3 | Election clerk | Download a report of results | I can share findings with staff or candidates |
| 4 | Election supervisor | See a summary count of valid vs. invalid addresses | I can quickly assess petition validity |
| 5 | Developer | Re-run lookups on a subset of addresses | I can handle API failures or rate limits gracefully |

---

## Functional Requirements

### 1. PDF Ingestion
- Accept PDF files via a CLI, web upload, or watched directory.
- Support both digitally-generated PDFs (selectable text) and scanned PDFs (OCR fallback).
- Parse known petition form layouts (structured tables) and semi-structured/freeform layouts.

### 2. Address Extraction
- Extract the following fields per signer row where present:
  - Street address (number + street name)
  - City
  - State
  - ZIP code
- Normalize addresses (standardize abbreviations, remove extra whitespace).
- Flag rows where address extraction is uncertain or incomplete.

### 3. Jurisdiction Lookup
- Send each extracted address to a civic/geospatial API to resolve political jurisdictions.
- Capture at minimum:
  - Congressional district
  - State upper legislative chamber district (e.g., State Senate)
  - State lower legislative chamber district (e.g., State House/Assembly)
  - County
  - Municipality / city
  - Additional jurisdictions as needed (school board, water district, etc.)
- Handle API errors, rate limits, and unresolvable addresses with retries and graceful degradation.

**Candidate APIs:**
| API | Notes |
|-----|-------|
| Google Civic Information API | Returns elected officials and districts for a given address; well-documented |
| US Census Geocoder + TIGERweb | Free, no key required; returns FIPS codes for state/county/tract |
| Geocodio | Paid; returns congressional and state legislative districts in one call |
| SmartyStreets (now Smarty) | Paid; USPS-certified; returns county + congressional district |
| OpenStates Geo API | Open source; state legislative districts from shapefiles |

Recommended default: **Google Civic Information API** for richness of response + **US Census Geocoder** as a free fallback.

### 4. Output Report
- Produce a structured report with one row per signer containing:
  - Original extracted address
  - Normalized/verified address
  - Each jurisdiction field
  - Match confidence / status (`resolved`, `partial`, `unresolved`, `outside_jurisdiction`)
  - Source API used
- Export formats: CSV (primary), JSON (secondary).
- Summary section: total signers, resolved count, unresolved count, outside-jurisdiction count.

### 5. Jurisdiction Filtering
- Accept a target jurisdiction as input (e.g., "NY-14" congressional district or a specific state senate district ID).
- Mark each resolved address as `in_jurisdiction` or `out_of_jurisdiction`.

---

## Technical Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI / Web UI                         │
│          (upload PDFs, specify target jurisdiction)         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │  PDF Parser │  (pdfplumber / PyMuPDF + Tesseract OCR)
                    └──────┬──────┘
                           │  raw address strings
                    ┌──────▼──────┐
                    │  Address    │  (usaddress / libpostal / regex)
                    │  Normalizer │
                    └──────┬──────┘
                           │  structured address objects
                    ┌──────▼──────┐
                    │  Jurisdiction│  (Google Civic API primary,
                    │  Lookup      │   Census Geocoder fallback)
                    └──────┬──────┘
                           │  jurisdiction objects
                    ┌──────▼──────┐
                    │  Report     │  (CSV / JSON output)
                    │  Generator  │
                    └─────────────┘
```

### Technology Stack (recommended)

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.11+ | Strong PDF/NLP/geo library ecosystem |
| PDF parsing | `pdfplumber` (digital), `pytesseract` + `Pillow` (scanned) | Handles both PDF types |
| Address parsing | `usaddress` or `libpostal` | US-specific parsing; handles abbreviations |
| HTTP client | `httpx` (async) | Async batch API calls with retries |
| Jurisdiction API | Google Civic Information API | Rich district data |
| Fallback API | US Census Geocoder | Free, no key, FIPS codes |
| CLI | `click` or `typer` | Simple UX for staff |
| Output | `csv` / `json` stdlib | No extra dependencies |
| Testing | `pytest` + `responses` (mock HTTP) | Unit and integration tests |

---

## Data Flow

1. **Input**: One or more petition PDFs + target jurisdiction ID.
2. **Parse**: Extract text from PDF; identify signer rows by table structure or regex patterns.
3. **Extract**: Pull address fields from each row; flag ambiguous extractions.
4. **Normalize**: Standardize address format; remove noise.
5. **Lookup**: Batch-query the jurisdiction API; apply retry logic (3 attempts, exponential backoff).
6. **Evaluate**: Compare resolved jurisdiction against target jurisdiction; assign status.
7. **Output**: Write CSV/JSON report + print summary to console.

---

## Error Handling & Edge Cases

| Scenario | Handling |
|----------|----------|
| Scanned/handwritten PDF | OCR fallback; flag low-confidence extractions |
| Incomplete address (no ZIP) | Attempt lookup with city+state; mark as `partial` |
| API rate limit (429) | Exponential backoff; queue retry |
| Address not found by API | Mark `unresolved`; include in report for manual review |
| Duplicate addresses | Deduplicate before API calls; attribute results back to all matching rows |
| PO Box / non-residential | Flag; many districts cannot be determined from PO Boxes |
| Multi-page petition forms | Parse all pages; ignore header/footer rows |

---

## API Key & Configuration

Configuration via environment variables or a `.env` file:

```
GOOGLE_CIVIC_API_KEY=<your_key>
TARGET_JURISDICTION=<district_id>   # e.g., ocd-division/country:us/state:ny/cd:14
BATCH_SIZE=50                        # addresses per API batch (if supported)
OUTPUT_FORMAT=csv                    # csv | json
OUTPUT_DIR=./output
```

---

## Acceptance Criteria

- [ ] Given a digitally-generated petition PDF, all addresses are extracted with ≥95% accuracy on clean forms.
- [ ] Each extracted address returns at least congressional district and state legislative district when the address is valid and within the US.
- [ ] Addresses that cannot be resolved are flagged as `unresolved` rather than silently dropped.
- [ ] Output CSV includes all required columns (address, normalized address, all jurisdiction fields, status).
- [ ] Summary stats are printed to console on completion.
- [ ] Unit tests cover address normalization, jurisdiction lookup (mocked), and report generation.
- [ ] Tool runs end-to-end on a sample 50-row petition in under 60 seconds.

---

## Out of Scope (Future Phases)

- Web UI with drag-and-drop upload.
- Database storage of historical lookups.
- Integration with voter registration database for full signature validation.
- Support for non-US jurisdictions.
- Handwriting recognition for fully handwritten petitions.

---

## Open Questions

1. Which specific jurisdictions/districts are most commonly needed (congressional only, or also state legislative, municipal)?
2. Do petitions follow a consistent template, or do formats vary by candidate/office?
3. Is there a budget for paid APIs (Geocodio, Smarty), or should the solution be free/open-source only?
4. What volume of petitions/signers should the tool handle (100s vs. 10,000s per run)?
5. Should unresolved addresses be automatically sent to a secondary lookup (e.g., manual geocoder queue)?

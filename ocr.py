"""
ocr.py — PDF page extraction and paper petition parsing.

Uses tesserocr (Tesseract C API) which keeps the model loaded in memory,
making OCR calls ~1000x faster than spawning a subprocess per call.

Page classification:
  - Paper signer pages have "7/27/2021" in the footer.
  - All other pages (E-Qual, cover pages) are skipped.

Template layout (calibrated from sample petitions, 150 DPI, 1648x1274 px):
  Column header row : y = 0.28 – 0.39  (contains "Actual residence address...")
  Data rows 1-10    : y = 0.42 – 0.90
  Footer            : y = 0.90+

  Printed Name col  : x = 0.22 – 0.44
  Address col       : x = 0.46 – 0.73   (primary extraction target)
"""

from __future__ import annotations

import os
import re
from multiprocessing import Pool

from pdf2image import convert_from_path
from pdf2image.pdf2image import pdfinfo_from_path
from PIL import Image

# ── tesseract data path ───────────────────────────────────────────────────────
_TESS_DATA = '/usr/share/tesseract-ocr/5/tessdata'

# ── page classification signals ───────────────────────────────────────────────
_PAPER_SIGNAL  = '7/27/2021'
_SIGNER_SIGNAL = 'Printed name'  # column header unique to signer pages

# ── layout constants (as fraction of image dimensions) ───────────────────────
_NAME_X1, _NAME_X2     = 0.22, 0.44
_ADDR_X1, _ADDR_X2     = 0.46, 0.73
_TABLE_Y1, _TABLE_Y2   = 0.42, 0.90   # data rows only (column header excluded)
_FOOTER_Y1             = 0.90          # footer starts here
_HEADER_Y2             = 0.35          # municipality extraction crop
_ROWS = 10

# ── municipality pattern ──────────────────────────────────────────────────────
_MUNI_RE = re.compile(
    r'\b((?:TOWN|CITY|VILLAGE|TOWNSHIP)\s+OF\s+[A-Z][A-Z ]{1,30})',
    re.IGNORECASE,
)

# ── per-worker API (loaded once, reused across all pages in a worker) ─────────
_api = None


def _init_worker():
    """Initialise the Tesseract API once per worker process."""
    global _api
    from tesserocr import PyTessBaseAPI, PSM
    _api = PyTessBaseAPI(path=_TESS_DATA, psm=PSM.SINGLE_LINE)


def _ocr_block(img: Image.Image, psm_block=False) -> str:
    """OCR an image region. Uses SINGLE_BLOCK psm when psm_block=True."""
    from tesserocr import PSM
    if psm_block:
        _api.SetPageSegMode(PSM.SINGLE_BLOCK)
    else:
        _api.SetPageSegMode(PSM.SINGLE_LINE)
    _api.SetImage(img)
    return _api.GetUTF8Text()


def _clean(text: str) -> str:
    """Strip form-line artifacts and normalize whitespace."""
    text = re.sub(r'[^a-zA-Z0-9 .#\'\-]', ' ', text)
    return ' '.join(text.split()).strip()


def _is_paper_signer(footer_text: str, header_text: str) -> bool:
    return _PAPER_SIGNAL in footer_text and _SIGNER_SIGNAL in header_text


def _extract_municipality(header_text: str) -> str:
    m = _MUNI_RE.search(header_text)
    if not m:
        return ''
    raw = m.group(1).strip()
    return re.split(r'[^A-Za-z ]', raw)[0].strip().upper()


def _extract_rows(img: Image.Image) -> list[dict]:
    w, h = img.size
    ax1, ax2 = int(_ADDR_X1 * w), int(_ADDR_X2 * w)
    nx1, nx2 = int(_NAME_X1 * w), int(_NAME_X2 * w)
    ty1 = int(_TABLE_Y1 * h)
    ty2 = int(_TABLE_Y2 * h)
    row_h = (ty2 - ty1) / _ROWS

    rows: list[dict] = []
    for i in range(_ROWS):
        ry1 = int(ty1 + i * row_h)
        ry2 = int(ty1 + (i + 1) * row_h)
        rows.append({
            'row_num':      i + 1,
            'printed_name': _clean(_ocr_block(img.crop((nx1, ry1, nx2, ry2)))),
            'raw_address':  _clean(_ocr_block(img.crop((ax1, ry1, ax2, ry2)))),
        })
    return rows


def _process_page(args: tuple) -> dict | None:
    """
    Worker: convert one PDF page and return structured data if it is a
    paper signer page. Returns None for all other page types.
    """
    page_num, pdf_path, dpi = args

    images = convert_from_path(
        pdf_path, dpi=dpi, first_page=page_num, last_page=page_num
    )
    if not images:
        return None

    img = images[0].convert('L')  # grayscale — faster OCR
    w, h = img.size

    # Classify using footer + column-header strips (fast, narrow crops)
    footer_text = _ocr_block(img.crop((0, int(_FOOTER_Y1 * h), w, h)), psm_block=True)
    header_text = _ocr_block(img.crop((0, int(_HEADER_Y2 * h * 0.5), w, int(_HEADER_Y2 * h))), psm_block=True)

    if not _is_paper_signer(footer_text, header_text):
        return None

    # Extract municipality from the full header block
    full_header = _ocr_block(img.crop((0, 0, w, int(_HEADER_Y2 * h))), psm_block=True)
    municipality = _extract_municipality(full_header)

    return {
        'page_num':           page_num,
        'target_municipality': municipality,
        'rows':               _extract_rows(img),
    }


def extract_pages(
    pdf_path: str,
    dpi: int = 150,
    workers: int | None = None,
) -> list[dict]:
    """
    Extract all paper signer pages from *pdf_path* in parallel.

    Returns a list of page dicts sorted by page number:
        page_num           : int
        target_municipality: str
        rows               : list of {row_num, printed_name, raw_address}
    """
    n_pages = pdfinfo_from_path(pdf_path)['Pages']
    workers = workers or os.cpu_count() or 1
    args = [(p, pdf_path, dpi) for p in range(1, n_pages + 1)]

    with Pool(processes=workers, initializer=_init_worker) as pool:
        results = pool.map(_process_page, args)

    return sorted(
        [r for r in results if r is not None],
        key=lambda r: r['page_num'],
    )

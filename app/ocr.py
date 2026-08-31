import os
import re
import json
from datetime import date

import pytesseract
from PIL import Image

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
PDF_EXTENSIONS = {".pdf"}

# Lines that are aggregates/tax figures, not real purchased line items — never
# treated as a line item even though they end in a dollar amount.
_SKIP_LINE_KEYWORDS = (
    "subtotal", "sub-total", "sub total", "total", "gst", "tax", "amount due",
    "balance due", "amount paid", "grand total", "due date", "invoice date",
    "invoice number", "invoice no", "invoice #", "abn", "bsb", "account no",
    "payment", "bill to", "ship to",
)

_AMOUNT_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2}))\s*$")

_INVOICE_NUMBER_RE = re.compile(
    r"invoice\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-\/]{2,20})",
    re.IGNORECASE,
)
# Fallback for "Invoice: ABC123" with no "Number"/"No"/"#" keyword — the colon
# must come right after "invoice" so this doesn't fire on a "TAX INVOICE" header.
_INVOICE_NUMBER_FALLBACK_RE = re.compile(
    r"invoice\s*:\s*([A-Za-z0-9][A-Za-z0-9\-\/]{2,20})",
    re.IGNORECASE,
)

# Australian-style DD/MM/YYYY or DD-MM-YYYY, plus a "15 March 2026" fallback.
_DATE_NUMERIC_RE = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b")
_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_DATE_TEXT_RE = re.compile(
    r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{2,4})\b"
)


def extract_text(file_path: str) -> str:
    """OCR a PDF or image file and return the raw recognised text. Multi-page
    PDFs have each page's text concatenated in order.

    Uses Tesseract's PSM 4 ("assume a single column of variable-sized text")
    rather than its fully-automatic default — invoices with a multi-column
    line-item table read dramatically more coherently in this mode; the
    default mode frequently reads entire table columns in sequence (all
    descriptions, then all amounts) rather than row by row."""
    ext = os.path.splitext(file_path)[1].lower()
    tess_config = "--psm 4"

    if ext in IMAGE_EXTENSIONS:
        return pytesseract.image_to_string(Image.open(file_path), config=tess_config)

    if ext in PDF_EXTENSIONS:
        from pdf2image import convert_from_path
        pages = convert_from_path(file_path, dpi=300)
        return "\n".join(pytesseract.image_to_string(page, config=tess_config) for page in pages)

    return ""


def _parse_date(text: str):
    # Prefer a date specifically labelled as the issue date — invoices often
    # show several dates (issue, due), and we want the one the invoice was
    # actually raised on, not whichever happens to appear first in the text.
    issue_match = re.search(r"issue\s*date[^\d]{0,15}", text, re.IGNORECASE)
    if issue_match:
        window = text[issue_match.end():issue_match.end() + 20]
        found = _parse_date_in(window)
        if found:
            return found

    return _parse_date_in(text)


def _parse_date_in(text: str):
    m = _DATE_NUMERIC_RE.search(text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)  # Australian convention: DD/MM/YYYY
        except ValueError:
            pass

    m = _DATE_TEXT_RE.search(text)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        month = _MONTH_NAMES.get(month_name)
        if month:
            if year < 100:
                year += 2000
            try:
                return date(year, month, day)
            except ValueError:
                pass
    return None


_INVOICE_NUMBER_REJECT = {
    "number", "no", "date", "issue", "due", "tax", "name", "amount",
    "total", "paid", "balance", "gst", "invoice",
}


def _parse_invoice_number(text: str):
    def first_valid(pattern):
        for m in pattern.finditer(text):
            candidate = m.group(1).strip().rstrip(".:")
            if candidate.lower() not in _INVOICE_NUMBER_REJECT and any(ch.isdigit() for ch in candidate):
                return candidate
        return None

    return first_valid(_INVOICE_NUMBER_RE) or first_valid(_INVOICE_NUMBER_FALLBACK_RE)


_TRAILING_NOISE_RE = re.compile(
    r"\s+(?:GST|N-T|FRE|excl(?:uding)?|incl(?:uding)?|ex\.?|inc\.?)\.?\s*$",
    re.IGNORECASE,
)


def _clean_description(description: str) -> str:
    # Strip a trailing tax-code word left over from the same table row (e.g.
    # "Cleaning Services Hrs 4 60.00 GST" → drop the "GST").
    description = _TRAILING_NOISE_RE.sub("", description).strip()
    # Collapse an immediately-repeated word, a common OCR artefact where a
    # wrapped column header gets read twice in a row ("Cleaning Cleaning...").
    words = description.split()
    deduped = [w for i, w in enumerate(words) if i == 0 or w.lower() != words[i - 1].lower()]
    return " ".join(deduped)


def _parse_line_items(text: str):
    items = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 4:
            continue
        if set(line) <= set("-=_ "):
            continue  # a plain divider line like "-----"

        amount_match = _AMOUNT_RE.search(line)
        if not amount_match:
            continue

        description = _clean_description(line[:amount_match.start()].strip(" .:$-"))
        if not description:
            continue
        if any(kw in description.lower() for kw in _SKIP_LINE_KEYWORDS):
            continue

        try:
            amount = float(amount_match.group(1).replace(",", ""))
        except ValueError:
            continue

        items.append({"description": description, "amount": amount})
    return items


def _match_supplier(text: str, suppliers: list):
    """suppliers: list of (id, display_name) for this business's supplier contacts.
    Returns the id of the best match found in the OCR'd text, or None."""
    text_lower = text.lower()
    best_id, best_len = None, 0
    for contact_id, name in suppliers:
        name_clean = name.strip().lower()
        if len(name_clean) >= 4 and name_clean in text_lower:
            if len(name_clean) > best_len:
                best_id, best_len = contact_id, len(name_clean)
    return best_id


def parse_invoice_text(text: str, suppliers: list):
    """suppliers: list of (id, display_name) tuples for this business's existing
    supplier contacts, used only for matching — never for creating a new one."""
    return {
        "date": _parse_date(text),
        "invoice_number": _parse_invoice_number(text),
        "supplier_contact_id": _match_supplier(text, suppliers),
        "line_items": _parse_line_items(text),
    }


def line_items_to_json(line_items: list) -> str:
    return json.dumps(line_items)


def line_items_from_json(raw: str) -> list:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []

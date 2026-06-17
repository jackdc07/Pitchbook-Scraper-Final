"""PDF text extraction.

Wraps pdfplumber so the rest of the package can work on plain text.
"""
from __future__ import annotations

from pathlib import Path

try:
    import pdfplumber
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "pdfplumber is required. Install it with: pip install -r requirements.txt"
    ) from exc


def extract_text(pdf_path: str | Path) -> str:
    """Return the full text of a PDF, page by page.

    A form-feed marker is inserted between pages so downstream parsing can
    reason about page boundaries if it wants to.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    pages: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            # layout=True keeps columns roughly aligned, which matters for the
            # two-column PitchBook profile layout.
            text = page.extract_text(layout=True) or ""
            pages.append(text)
    return "\n\f\n".join(pages)


def extract_tables(pdf_path: str | Path) -> list[list[list[str]]]:
    """Return all tables found in a PDF as a list of row/column matrices."""
    pdf_path = Path(pdf_path)
    tables: list[list[list[str]]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                cleaned = [
                    [(cell or "").strip() for cell in row]
                    for row in table
                ]
                tables.append(cleaned)
    return tables

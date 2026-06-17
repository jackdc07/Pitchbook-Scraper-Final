"""Parse PitchBook company-profile PDFs into structured :class:`Company` data.

Strategy
--------
* The scalar "Highlights" / "General Information" fields (employees, last deal,
  total raised, description, financials, acquisition status) are read with
  regexes from the layout-preserving text. PitchBook lays these out very
  consistently, so text matching is both reliable and resilient.
* The "Current Team" roster is read with word coordinates. The table wraps
  names and titles across several visual lines and pdfplumber's table detector
  silently drops rows, so we cluster words by column using the horizontal gap
  between the name and title columns instead.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import Company, FinancingRound, TeamMember

try:
    import pdfplumber
except ImportError:  # pragma: no cover - extractor raises a friendlier error
    pdfplumber = None


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    """Strip leading/trailing whitespace from every line (keeps interior gaps)."""
    return "\n".join(line.strip() for line in text.splitlines())


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _search(pattern: str, text: str, flags: int = 0, group: int = 1) -> str | None:
    m = re.search(pattern, text, flags)
    return _clean(m.group(group)) if m else None


# Money like $1.00M, $3.00B, $310.22M, $5.00K
_MONEY = r"\$[\d,]+(?:\.\d+)?[KMB]?"
_DATE = r"\d{1,2}-[A-Za-z]{3}-\d{4}"


# --------------------------------------------------------------------------- #
# Scalar field extraction (regex over normalized text)
# --------------------------------------------------------------------------- #
def _parse_name(text: str) -> str | None:
    # "Alethea Medical | Private Company Profile"
    name = _search(r"^(.+?)\s*\|\s*(?:Private|Public)\s+Company Profile", text, re.M)
    if name:
        return name
    # Fallback: the page-1 H1 before "| ... Profile" without the company class
    return _search(r"^(.+?)\s*\|\s*.*Profile\b", text, re.M)


def _parse_employees(text: str) -> str | None:
    # Highlights box: "Employees\n15" ; General Info: "Employees   15"
    return _search(r"\bEmployees\s+([\d,]+)\b", text)


def _parse_website(text: str) -> str | None:
    return _search(r"\bWebsite\s+(\S+)", text)


# Lead nouns PitchBook uses to open a description ("Operator of ...").
_DESC_LEAD = (
    r"Operator|Developer|Provider|Manufacturer|Producer|Designer|Creator|"
    r"Distributor|Owner|Builder|Maker|Supplier|Marketer|Publisher|Retailer"
)


def _parse_keywords(description: str | None) -> str | None:
    """Rule-based 1-10 word summary of *what the company does*.

    PitchBook descriptions almost always open with
    "<Lead> of <a/an> <core thing> designed/intended to ...". The core thing is
    the most useful short label, so we capture it and cap it at 10 words. No AI
    involved -- pure pattern matching.
    """
    if not description:
        return None

    m = re.match(
        r"\s*(?:" + _DESC_LEAD + r")\s+of\s+(?:a |an |the )?(.+?)"
        r"(?:\s+(?:designed|intended|that|which|based|used|aimed|focused|"
        r"providing|offering|enabling|for|to)\b|[.,;])",
        description,
        re.I,
    )
    phrase = m.group(1) if m else description
    words = re.split(r"\s+", phrase.strip())
    keywords = " ".join(words[:10]).strip(" ,.;")
    return keywords or None


def _parse_description(text: str) -> str | None:
    m = re.search(
        r"\bDescription\s*\n(.*?)\n\s*(?:Most Recent Financing Status|"
        r"Most Recent Financials|General Information|Contact Information|"
        r"Key Metrics|Highlights)",
        text,
        re.S,
    )
    return _clean(m.group(1)) if m else None


def _parse_last_deal(text: str) -> tuple[str | None, str | None, str | None]:
    """Return (round_type, amount, date) from the Highlights "Last Deal Details"."""
    m = re.search(
        r"Last Deal Details[^\n]*\n\s*(" + _MONEY + r")[^\n]*\n\s*"
        r"([A-Za-z][^\n]*?)(?:\s+(" + _DATE + r"))?(?:\s{2,}|\s*As of|\s*Post|\s*$)",
        text,
        re.S,
    )
    if not m:
        return None, None, None
    amount = _clean(m.group(1))
    round_type = _clean(m.group(2))
    date = _clean(m.group(3))
    return round_type, amount, date


def _parse_total_raised(text: str) -> str | None:
    return _search(r"Total Raised to Date[^\n]*\n\s*(" + _MONEY + r")", text, re.S)


def _parse_financials(text: str) -> tuple[str | None, str | None, dict[str, str]]:
    """Return (most_recent_revenue, revenue_date, financials_dict)."""
    financials: dict[str, str] = {}

    units_thousands = "Amounts in thousands" in text
    units_millions = "Amounts in millions" in text

    # Period labels (e.g. "12 Months Ending Dec 2024", "Fiscal Year 2024").
    period = None
    pm = re.search(r"(12 Months Ending[^\n]*?)(?:\s{2,}|$)", text)
    if pm:
        period = _clean(pm.group(1))

    # Period-end date of the most recent column ("End: 31-Dec-2024" ... last one).
    revenue_date = None
    ends = re.findall(r"End:\s*(" + _DATE + r")", text)
    if ends:
        revenue_date = _clean(ends[-1])

    most_recent_revenue = None
    rm = re.search(r"\bTotal Revenue\b\s+([\d,.\s]+?)(?:\n|$)", text)
    if rm:
        nums = re.findall(r"[\d,]+(?:\.\d+)?", rm.group(1))
        if nums:
            raw = nums[-1]  # right-most column == most recent period
            financials["Total Revenue (all periods)"] = " | ".join(nums)
            most_recent_revenue = _format_money_from_table(
                raw, units_thousands, units_millions
            )

    gm = re.search(r"Revenue\s*% ?Growth\s+([+\-\d,.%\s]+?)(?:\n|$)", text)
    if gm:
        growths = re.findall(r"[+\-][\d.]+%", gm.group(1))
        if growths:
            financials["Revenue % Growth (most recent)"] = growths[-1]

    if most_recent_revenue:
        financials["Total Revenue (most recent)"] = most_recent_revenue
    if period:
        financials["Most recent period"] = period
    if revenue_date:
        financials["Most recent period end"] = revenue_date
    if units_thousands:
        financials["Reported units"] = "thousands USD"
    elif units_millions:
        financials["Reported units"] = "millions USD"

    return most_recent_revenue, revenue_date, financials


def _format_money_from_table(raw: str, thousands: bool, millions: bool) -> str:
    """Turn a raw table number + unit context into e.g. '$2.77M'."""
    try:
        n = float(raw.replace(",", ""))
    except ValueError:
        return raw
    if thousands:
        n *= 1_000
    elif millions:
        n *= 1_000_000
    return _humanize_money(n)


def _humanize_money(n: float) -> str:
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}${n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{sign}${n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{sign}${n / 1_000:.2f}K"
    return f"{sign}${n:,.0f}"


def _parse_acquisition(text: str) -> tuple[bool | None, str | None, str | None]:
    """Detect whether the *subject* company has been acquired."""
    acquired = False
    acquirer = None
    acq_date = None

    ownership = _search(r"Ownership Status\s+([^\n]+)", text) or ""
    financing = _search(r"Financing Status\s+([^\n]+)", text) or ""
    status_blob = f"{ownership} {financing}"
    if re.search(r"\b(Acquired|Merged|Out of Business)\b", status_blob, re.I):
        acquired = True

    # "...was acquired by Foo on ..." / "...acquired by Foo."
    m = re.search(
        r"acquired by ([A-Z][\w .,&'\-]+?)(?:\s+(?:on|in)\s+([\w ,\-]+?))?[.\n]",
        text,
    )
    if m:
        acquired = True
        acquirer = _clean(m.group(1))
        acq_date = _clean(m.group(2))

    # A completed Merger/Acquisition or Buyout deal where the company is target.
    if re.search(r"Deal Types?\s+(?:Merger/Acquisition|Buyout/LBO)", text):
        acquired = True

    return acquired, acquirer, acq_date


def _parse_offices(text: str) -> tuple[str | None, list[str]]:
    """Return (hq, alternate_offices) parsed from text.

    HQ is taken from the clean "Site City, Country" / "HQ Location" labels;
    :func:`_extract_hq` refines it with coordinates when a PDF is available.
    Alternates come only from the "Alternate Offices" section.
    """
    hq = _search(
        r"\bSite\s+([A-Z][A-Za-z .'\-]+,\s*[A-Z][A-Za-z .'\-]+?)(?:\s{2,}|$)",
        text, re.M,
    ) or _search(
        r"\bHQ Location\s+([A-Z][A-Za-z .'\-]+,\s*[A-Z][A-Za-z .'\-]+?)(?:\s{2,}|$)",
        text, re.M,
    )

    alternates: list[str] = []
    alt = re.search(r"Alternate Offices(.*?)(?:\bFinancials\b|\Z)", text, re.S)
    region = alt.group(1) if alt else ""
    for line in region.splitlines():
        m = re.match(
            r"([A-Z][A-Za-z .'\-]+,\s+[A-Z][A-Za-z .'\-]+?)(?:\s{2,}.*)?$",
            line.strip(),
        )
        if m:
            loc = _clean(m.group(1))
            if loc and loc not in alternates:
                alternates.append(loc)

    return hq, alternates


def _extract_hq(pdf) -> str | None:
    """Read the full primary-office address via word coordinates.

    The contact page interleaves "Primary Contact" (left) and "Primary Office"
    (right) columns on shared lines, so plain text mixes them; coordinates keep
    the right column clean.
    """
    for page in pdf.pages:
        words = page.extract_words(use_text_flow=False)
        # Find the "Office" header whose preceding word on the row is "Primary".
        header = None
        for w in words:
            if w["text"] != "Office":
                continue
            same_row = [
                o for o in words
                if abs(o["top"] - w["top"]) < 4 and o["x1"] <= w["x0"] and o["text"] == "Primary"
            ]
            if same_row:
                header = max(same_row, key=lambda o: o["x1"])  # the "Primary" of "Primary Office"
                break
        if header is None:
            continue

        col_x0 = header["x0"] - 6
        loc_rows: dict[int, list] = {}
        for w in words:
            if w["x0"] < col_x0:
                continue
            if not (header["top"] + 2 < w["top"] < header["top"] + 70):
                continue
            t = w["text"]
            if t.startswith("Phone") or "@" in t or t.startswith("Alternate"):
                continue
            loc_rows.setdefault(round(w["top"]), []).append(w)

        parts: list[str] = []
        for rtop in sorted(loc_rows):
            row = sorted(loc_rows[rtop], key=lambda w: w["x0"])
            line = " ".join(w["text"] for w in row).strip()
            # Address rows come first; stop at the phone/email/contact rows.
            if not line or line.lower().startswith("alternate"):
                break
            if re.search(r"(?:Phone|Fax|@|\+?\d[\d()\-\s]{6,})", line):
                break
            parts.append(line)
        hq = _clean(", ".join(parts))
        if hq:
            return hq
    return None


# --------------------------------------------------------------------------- #
# Coordinate-based team extraction
# --------------------------------------------------------------------------- #
_SECTION_HEADERS = {
    "Deal", "Investors", "Lead", "Board", "Advisors", "Signal",
    "Financials", "Contact", "General", "Sourcing", "Comparisons",
    "Similar", "Patent", "News",
}

_COLUMN_GAP = 18  # pt; horizontal gap that separates the name and title columns


def _next_section_top(words, after_top, left_x, page_height):
    """Top y of the next left-margin section header below ``after_top``."""
    candidates = [
        w["top"]
        for w in words
        if w["top"] > after_top + 5
        and w["x0"] < left_x + 25
        and w["text"] in _SECTION_HEADERS
    ]
    return min(candidates) if candidates else page_height


def _extract_team_from_page(page) -> list[TeamMember]:
    text = page.extract_text() or ""
    if "Current Team" not in text:
        return []

    words = page.extract_words(use_text_flow=False)
    # Header row of the team table.
    name_hdr = next((w for w in words if w["text"] == "Name"), None)
    title_hdr = next((w for w in words if w["text"] == "Title"), None)
    if not name_hdr or not title_hdr:
        return []

    header_top = name_hdr["top"]
    name_x0 = name_hdr["x0"]
    # Right edge of the name+title block = start of the Office/Phone/Email cols.
    right_cols = [
        w["x0"] for w in words
        if w["text"] in {"Office", "Phone", "Email", "Board", "Seats"}
        and abs(w["top"] - header_top) < 6
    ]
    right_start = min(right_cols) if right_cols else (title_hdr["x0"] + 120)

    end_top = _next_section_top(words, header_top, name_x0, page.height)

    # Each member is anchored by their email address (one per member).
    anchors = sorted(
        (w for w in words if "@" in w["text"] and header_top < w["top"] < end_top),
        key=lambda w: w["top"],
    )
    if not anchors:
        return []
    bounds = [a["top"] for a in anchors] + [end_top]

    bands: list[list] = []
    for i, anchor in enumerate(anchors):
        band_top = anchor["top"] - 4
        band_bot = bounds[i + 1] - 4
        band = [
            w for w in words
            if band_top <= w["top"] < band_bot
            and name_x0 - 3 <= w["x0"] < right_start - 4
        ]
        bands.append(band)

    # The name and title columns are separated by a horizontal gap. Find where
    # the title column begins from each band's anchor (first) row, then use the
    # tightest such boundary for the whole page. This correctly classifies
    # title fragments that wrap onto their own line (where there is no gap to
    # measure locally).
    title_left = _title_column_left(bands, title_hdr["x0"])

    members: list[TeamMember] = []
    for band, anchor in zip(bands, anchors):
        rows: dict[int, list] = {}
        for w in band:
            rows.setdefault(round(w["top"]), []).append(w)

        name_parts: list[str] = []
        title_parts: list[str] = []
        for rtop in sorted(rows):
            row = sorted(rows[rtop], key=lambda w: w["x0"])
            for w in row:
                if w["x0"] < title_left - 4:
                    name_parts.append(w["text"])
                else:
                    title_parts.append(w["text"])

        name = _clean(" ".join(name_parts))
        title = _clean(" ".join(title_parts))
        email = anchor["text"].strip().rstrip(".,;")
        if name:
            members.append(TeamMember(name=name, title=title, email=email))

    return members


def _title_column_left(bands: list[list], title_hdr_x0: float) -> float:
    """Estimate the x0 where the title column starts.

    Uses the first large gap on each band's top row; falls back to an estimate
    derived from the 'Title' header position when no gap can be measured.
    """
    candidates: list[float] = []
    for band in bands:
        if not band:
            continue
        top = min(w["top"] for w in band)
        first_row = sorted(
            (w for w in band if abs(w["top"] - top) < 4), key=lambda w: w["x0"]
        )
        for prev, cur in zip(first_row, first_row[1:]):
            if cur["x0"] - prev["x1"] > _COLUMN_GAP:
                candidates.append(cur["x0"])
                break
    if candidates:
        return min(candidates)
    return title_hdr_x0 - 22


def _extract_team(pdf) -> list[TeamMember]:
    members: list[TeamMember] = []
    seen: set[str] = set()
    for page in pdf.pages:
        for member in _extract_team_from_page(page):
            if member.name not in seen:
                seen.add(member.name)
                members.append(member)
    return members


def _team_size_reported(text: str) -> int | None:
    m = re.search(r"Current Team \((\d+)\)", text)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# Deal history (best-effort, from text)
# --------------------------------------------------------------------------- #
def _extract_deal_history(text: str) -> list[FinancingRound]:
    """Best-effort financing rounds from the 'Deal History' table text."""
    m = re.search(
        r"Deal History\b(.*?)(?:†\s*Indicates|\bDeal #\d|\Z)", text, re.S
    )
    if not m:
        return []
    block = m.group(1)
    lines = [ln.strip() for ln in block.splitlines()]
    rounds: list[FinancingRound] = []
    # A deal row begins with "N." and the type runs until a date, money value,
    # a status word, or a multi-space column break.
    row_re = re.compile(
        r"^\d+\.\s+(.+?)(?=\s+(?:\d{1,2}-[A-Za-z]{3}-?|\$|Completed|Announced|Cancelled)|\s{2,}|$)"
    )
    partial_date = re.compile(r"(\d{1,2}-[A-Za-z]{3}-)(?=\s|$)")
    for i, line in enumerate(lines):
        rm = row_re.match(line)
        if not rm:
            continue
        round_type = _clean(rm.group(1))
        amount = _search(r"(" + _MONEY + r")", line)
        date = _search(r"(" + _DATE + r")", line)
        if not date:
            # Dates often wrap: "31-Dec-" on this line, "2023" on the next.
            pm = partial_date.search(line)
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            ym = re.match(r"(\d{4})\b", nxt)
            if pm and ym:
                date = _clean(pm.group(1) + ym.group(1))
        rounds.append(
            FinancingRound(date=date, round_type=round_type, amount_raised=amount)
        )
    return rounds


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def parse_text(text: str) -> Company:
    """Parse the scalar fields from extracted PDF text (no coordinates)."""
    norm = _normalize(text)
    company = Company()

    company.name = _parse_name(norm)
    company.website = _parse_website(norm)
    company.description = _parse_description(norm)
    company.keywords = _parse_keywords(company.description)
    company.employees = _parse_employees(norm)

    rt, amt, date = _parse_last_deal(norm)
    company.last_round_type = rt
    company.last_round_amount = amt
    company.last_round_date = date

    company.total_raised = _parse_total_raised(norm)
    hq, alternates = _parse_offices(norm)
    company.primary_offices = ([hq] if hq else []) + alternates

    revenue, revenue_date, financials = _parse_financials(norm)
    company.most_recent_revenue = revenue
    company.revenue_date = revenue_date
    company.financials = financials

    acquired, acquirer, acq_date = _parse_acquisition(norm)
    company.acquired = acquired
    company.acquirer = acquirer
    company.acquisition_date = acq_date

    company.financing_rounds = _extract_deal_history(norm)
    return company


def parse_company(pdf_path: str | Path) -> Company:
    """Parse a PitchBook PDF into a :class:`Company`, including the team roster."""
    if pdfplumber is None:  # pragma: no cover
        raise ImportError("pdfplumber is required. pip install -r requirements.txt")

    pdf_path = Path(pdf_path)
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages_text = [p.extract_text(layout=True) or "" for p in pdf.pages]
        full_text = "\n\f\n".join(pages_text)
        company = parse_text(full_text)
        company.team = _extract_team(pdf)

        # Refine the HQ/primary office using coordinates (cleaner than text),
        # then keep the alternate offices parsed from text.
        coord_hq = _extract_hq(pdf)
        _, alternates = _parse_offices(_normalize(full_text))
        if coord_hq:
            company.primary_offices = [coord_hq] + [
                a for a in alternates if a.split(",")[0] != coord_hq.split(",")[0]
            ]

    company.source_file = pdf_path.name

    # Reconcile the parsed roster against the reported "Current Team (N)" count.
    reported = _team_size_reported(_normalize(full_text))
    if reported is not None and reported != company.team_size:
        # Keep what we parsed but surface the discrepancy for the caller.
        company.financials.setdefault(
            "_team_count_note",
            f"Profile reports {reported} team members; parsed {company.team_size}.",
        )

    return company

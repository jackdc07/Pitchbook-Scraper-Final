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


# Normalized funding-stage buckets. "Substantive" rounds are the real priced/
# venture rounds; accelerator/angel/grant/etc. are only used as a fallback when
# a company has nothing else.
_SUBSTANTIVE_STAGES = {
    "Pre-Seed", "Seed", "Early Stage VC", "Later Stage VC",
    "PE Growth/Expansion", "Buyout/LBO", "Acquisition",
}


def _normalize_stage(raw: str | None) -> str | None:
    """Map a raw PitchBook deal type to a standard stage label."""
    if not raw:
        return None
    t = raw.strip()
    tl = t.lower()
    m = re.search(r"series\s+([A-K])\b", t, re.I)
    if m:
        return "Series " + m.group(1).upper()
    if "pre-seed" in tl or "pre seed" in tl or "preseed" in tl:
        return "Pre-Seed"
    if "seed" in tl:
        return "Seed"
    if "early stage vc" in tl or "early stage venture" in tl:
        return "Early Stage VC"
    if "later stage vc" in tl or "later stage venture" in tl:
        return "Later Stage VC"
    if "angel" in tl:
        return "Angel"
    if "accelerator" in tl or "incubator" in tl:
        return "Accelerator/Incubator"
    if "grant" in tl:
        return "Grant"
    if "crowdfunding" in tl:
        return "Equity Crowdfunding"
    if "pe growth" in tl or "growth/expansion" in tl or "pe expansion" in tl:
        return "PE Growth/Expansion"
    if "buyout" in tl or "lbo" in tl:
        return "Buyout/LBO"
    if "merger" in tl or "acquisition" in tl:
        return "Acquisition"
    if "spin-out" in tl or "spinout" in tl or "spin out" in tl:
        return "Spin-Out"
    return _clean(t)


def _is_substantive(stage: str | None) -> bool:
    return bool(stage and (stage in _SUBSTANTIVE_STAGES or stage.startswith("Series ")))


def _derive_last_round_stage(company: Company) -> str | None:
    """Most recent round that is a real funding stage (Seed / Series / VC / ...).

    Falls back to the most recent round of any kind (e.g. Accelerator, Angel,
    Grant) when the company has no substantive round.
    """
    # Highlights gives the cleanest most-recent label (keeps the Series letter);
    # the deal history (most-recent-first) provides the rest of the sequence.
    candidates: list[str] = []
    if company.last_round_type:
        candidates.append(company.last_round_type)
    candidates += [r.round_type for r in company.financing_rounds if r.round_type]

    for raw in candidates:
        stage = _normalize_stage(raw)
        if _is_substantive(stage):
            return stage
    return _normalize_stage(candidates[0]) if candidates else None


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


_NONOFFICE_WORDS = re.compile(
    r"\b(Source|Site|Financing|Deal|Provided|Backed|Software|Officer|President|"
    r"Vice|contract|subscriber|Capital|Venture|Round|Investor|Status|Industry|"
    r"Founder|Director|Manager|Chief|Board|Corporation|Equity|Private|Media|"
    r"Services|Other|Inc|LLC|Ltd|Holdings)\b",
    re.I,
)
_CITY_REGION = re.compile(
    r"^([A-Z][A-Za-z.'\-]+(?: [A-Z][A-Za-z.'\-]+)?,\s+"
    r"[A-Z][A-Za-z.'\-]+(?: [A-Z][A-Za-z.'\-]+){0,2})(?:\s+[A-Z0-9][A-Z0-9 ]*)?$"
)


def _extract_alt_offices(pdf) -> list[str]:
    """Alternate office locations, read from the bounded 'Alternate Offices'
    block by coordinates so it can't run off into later sections.
    """
    offices: list[str] = []
    for page in pdf.pages:
        words = page.extract_words(use_text_flow=False)
        alt = _label_position(words, "Alternate Offices")
        if not alt:
            continue
        alt_x0, alt_top = alt

        end = _next_section_top(words, alt_top, alt_x0, page.height)
        footer = [
            w["top"] for w in words
            if w["top"] > alt_top
            and ("PitchBook" in w["text"] or "reserved" in w["text"] or w["text"] == "©")
        ]
        if footer:
            end = min(end, min(footer))

        rows: dict[int, list] = {}
        for w in words:
            if alt_top + 4 < w["top"] < end:
                rows.setdefault(round(w["top"]), []).append(w)

        # The office blocks are compact; a big vertical gap means we've reached
        # whatever table follows, so stop there.
        ordered = sorted(rows)
        kept: list[int] = []
        for t in ordered:
            if kept and t - kept[-1] > 40:
                break
            kept.append(t)

        for top in kept:
            row = sorted(rows[top], key=lambda w: w["x0"])
            # Split the row into its two columns at the wide gap between blocks.
            cells: list[list] = [[row[0]]]
            for prev, cur in zip(row, row[1:]):
                if cur["x0"] - prev["x1"] > 20:
                    cells.append([cur])
                else:
                    cells[-1].append(cur)
            for cell in cells:
                text = " ".join(w["text"] for w in cell).strip()
                m = _CITY_REGION.match(text)
                if m:
                    loc = _clean(m.group(1))
                    if loc and loc not in offices and not _NONOFFICE_WORDS.search(loc):
                        offices.append(loc)
        break
    return offices


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
            # Match a real phone (\d{3}-\d{4}) so street numbers like
            # "700-510 Seymour Street" don't end the address early.
            if not line or line.lower().startswith("alternate"):
                break
            if re.search(r"Phone|Fax|@|\d{3}-\d{4}\b", line):
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
    "Similar", "Patent", "News", "Current",  # "Current Board Members" ends the team
}

# A phone-number fragment like "834-1689" — reliably present once per member row.
_PHONE_TOKEN = re.compile(r"\d{3}-\d{4}")

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


def _is_team_continuation_page(page) -> bool:
    """True if the roster table wraps onto this page without repeating the
    "Current Team" label -- it opens directly with the table's own header row.

    Distinguished from the differently-shaped "Current Board Members" header
    (Name/Title/Representing/Role Since/Phone/Email) by requiring an "Office"
    column, which only the team roster header has, right at the page top.
    """
    words = page.extract_words(use_text_flow=False)
    if not words:
        return False
    page_top = min(w["top"] for w in words)
    header = {w["text"] for w in words if w["top"] - page_top < 20}
    return {"Name", "Title", "Office"} <= header


def _extract_team_from_page(page, *, continuation: bool = False) -> list[TeamMember]:
    text = page.extract_text() or ""
    if not continuation and "Current Team" not in text:
        return []

    words = page.extract_words(use_text_flow=False)

    # A page can hold other tables with their own "Name"/"Title" headers (e.g.
    # "Similar Companies"). Anchor on the team table's header by taking the
    # first one that appears *below* the "Current Team" label. On a
    # continuation page there is no label to anchor on -- the table's own
    # header is the first thing on the page, so start from the top.
    if continuation:
        ct_top = 0.0
    else:
        ct = _label_position(words, "Current Team")
        ct_top = ct[1] if ct else 0
    name_hdr = next(
        (w for w in sorted(words, key=lambda w: w["top"])
         if w["text"] == "Name" and w["top"] > ct_top), None
    )
    title_hdr = next(
        (w for w in sorted(words, key=lambda w: w["top"])
         if w["text"] == "Title" and w["top"] > ct_top), None
    )
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

    # Each member's first row carries a phone and/or an email in the right-hand
    # columns. Anchor on either, because some members have a phone but no email
    # (so anchoring on email alone would merge them). One anchor per row top.
    anchor_tops = sorted({
        round(w["top"])
        for w in words
        if header_top < w["top"] < end_top
        and w["x0"] >= right_start - 6
        and ("@" in w["text"] or _PHONE_TOKEN.fullmatch(w["text"]))
    })
    if not anchor_tops:
        return []
    bounds = anchor_tops + [end_top]

    bands: list[list] = []
    for i, top in enumerate(anchor_tops):
        band_top = top - 4
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
    for i, band in enumerate(bands):
        rows: dict[int, list] = {}
        for w in band:
            rows.setdefault(round(w["top"]), []).append(w)

        # Keep only rows contiguous with the member's first row. A large vertical
        # gap means we've run past the member into a footer/page number/trailer
        # (e.g. the bottom-of-page "8" or "© PitchBook" line).
        ordered = sorted(rows)
        kept: list[int] = []
        for t in ordered:
            if kept and t - kept[-1] > 28:
                break
            kept.append(t)

        name_parts: list[str] = []
        title_parts: list[str] = []
        for rtop in kept:
            row = sorted(rows[rtop], key=lambda w: w["x0"])
            for w in row:
                if w["x0"] < title_left - 4:
                    name_parts.append(w["text"])
                else:
                    title_parts.append(w["text"])

        # Email (if any) sits in the Email column within this member's rows.
        email = None
        for w in words:
            if anchor_tops[i] - 4 <= w["top"] < bounds[i + 1] - 4 and "@" in w["text"]:
                email = w["text"].strip().rstrip(".,;")
                break

        name = _clean(" ".join(name_parts))
        title = _clean(" ".join(title_parts))
        if name:
            members.append(TeamMember(name=name, title=title, email=email))

    return members


def _title_column_left(bands: list[list], title_hdr_x0: float) -> float:
    """Estimate the x0 where the title column starts.

    Names sit flush at the left margin and titles begin further right, with a
    clear empty band between them. We find that band as the widest horizontal
    gap between consecutive word x-positions across the whole roster, which
    adapts to each PDF and correctly handles wrapped title lines that start a
    little left of the first-row title column. Falls back to the 'Title' header
    position if no clear gap is found.
    """
    xs = sorted({round(w["x0"], 1) for band in bands for w in band})
    best_gap, boundary = 0.0, None
    for a, b in zip(xs, xs[1:]):
        if b - a > best_gap:
            best_gap, boundary = b - a, (a + b) / 2
    if boundary is not None and best_gap >= _COLUMN_GAP:
        return boundary
    return title_hdr_x0 - 22


def _extract_team(pdf) -> list[TeamMember]:
    members: list[TeamMember] = []
    seen: set[str] = set()
    awaiting_continuation = False
    for page in pdf.pages:
        text = page.extract_text() or ""
        has_label = "Current Team" in text
        # Keep watching subsequent pages for a wrapped table only while the
        # chain is unbroken: the label page itself, or a continuation page
        # that matches the roster's own header shape.
        is_continuation = not has_label and awaiting_continuation and _is_team_continuation_page(page)

        if has_label:
            page_members = _extract_team_from_page(page)
        elif is_continuation:
            page_members = _extract_team_from_page(page, continuation=True)
        else:
            page_members = []

        for member in page_members:
            if member.name not in seen:
                seen.add(member.name)
                members.append(member)

        awaiting_continuation = has_label or is_continuation
    return members


def _team_size_reported(text: str) -> int | None:
    m = re.search(r"Current Team \((\d+)\)", text)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# Coordinate-based "Highlights" box (last deal + total raised)
# --------------------------------------------------------------------------- #
def _label_position(words, label: str):
    """Return (x0, top) of a (possibly multi-word) label in the word list."""
    toks = label.split()
    rows: dict[int, list] = {}
    for w in words:
        rows.setdefault(round(w["top"]), []).append(w)
    for top in sorted(rows):
        row = sorted(rows[top], key=lambda w: w["x0"])
        texts = [w["text"] for w in row]
        for i in range(len(texts) - len(toks) + 1):
            if texts[i : i + len(toks)] == toks:
                return row[i]["x0"], top
    return None


def _column_lines(words, col_x0: float, label_top: float, n: int) -> list[str]:
    """The first ``n`` text lines directly below a label, within its column."""
    sel = [
        w for w in words
        if col_x0 - 6 <= w["x0"] < col_x0 + 200 and w["top"] > label_top + 6
    ]
    rows: dict[int, list] = {}
    for w in sel:
        rows.setdefault(round(w["top"]), []).append(w)
    lines: list[str] = []
    for top in sorted(rows):
        row = sorted(rows[top], key=lambda w: w["x0"])
        lines.append(" ".join(w["text"] for w in row).strip())
        if len(lines) >= n:
            break
    return lines


def _extract_highlights(pdf) -> dict:
    """Read the Highlights box by column position (robust to 2-column layout).

    Returns any of: last_round_amount, last_round_type, last_round_date,
    total_raised. The box puts "Last Deal Details" / "Total Raised to Date" in
    either column, and lines from the two columns interleave in plain text, so
    we follow each label's own column downward.
    """
    out: dict = {}
    for page in pdf.pages:
        text = page.extract_text() or ""
        if "Last Deal Details" not in text and "Total Raised to Date" not in text:
            continue
        words = page.extract_words(use_text_flow=False)

        ld = _label_position(words, "Last Deal Details")
        if ld:
            lines = _column_lines(words, ld[0], ld[1], 2)
            if lines:
                out["last_round_amount"] = _clean(lines[0])
            if len(lines) > 1:
                td = lines[1]
                dm = re.search(r"(" + _DATE + r")", td)
                if dm:
                    out["last_round_date"] = _clean(dm.group(1))
                    td = td.replace(dm.group(1), "")
                out["last_round_type"] = _clean(td)

        tr = _label_position(words, "Total Raised to Date")
        if tr:
            for line in _column_lines(words, tr[0], tr[1], 2):
                mm = re.search(r"(" + _MONEY + r")", line)
                if mm:
                    out["total_raised"] = _clean(mm.group(1))
                    break
        break  # highlights live on the first matching page
    return out


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
    company.last_round_stage = _derive_last_round_stage(company)
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

        # The Highlights box is two-column; read it by coordinates and let those
        # values win over the text-regex guesses (which can grab the wrong
        # column or miss a non-$ value like "Undisclosed").
        hl = _extract_highlights(pdf)
        if hl.get("last_round_amount") is not None:
            company.last_round_amount = hl["last_round_amount"]
        if hl.get("last_round_type") is not None:
            company.last_round_type = hl["last_round_type"]
        if hl.get("last_round_date") is not None:
            company.last_round_date = hl["last_round_date"]
        if hl.get("total_raised") is not None:
            company.total_raised = hl["total_raised"]

        # Recompute the normalized stage now that the highlights values are in.
        company.last_round_stage = _derive_last_round_stage(company)

        # Refine offices using coordinates: HQ from the Primary Office block and
        # alternates from the bounded Alternate Offices block (text parsing of
        # these is unreliable because the columns interleave and the section can
        # run into later content).
        coord_hq = _extract_hq(pdf)
        alternates = _extract_alt_offices(pdf)
        hq = coord_hq or (company.primary_offices[0] if company.primary_offices else None)
        if hq:
            company.primary_offices = [hq] + [
                a for a in alternates if a.split(",")[0].strip() != hq.split(",")[0].strip()
            ]
        elif alternates:
            company.primary_offices = alternates

    company.source_file = pdf_path.name

    # Team Size is authoritative from the reported "Current Team (N)" header,
    # not just the count of rows the roster parser managed to extract (which
    # can undercount on odd wraps/formatting). Keep the parsed roster for
    # names/titles/emails, but surface a note if the two disagree.
    reported = _team_size_reported(_normalize(full_text))
    if reported is not None:
        company.team_size_reported = reported
        if reported != len(company.team):
            company.financials.setdefault(
                "_team_count_note",
                f"Profile reports {reported} team members; parsed {len(company.team)}.",
            )

    return company

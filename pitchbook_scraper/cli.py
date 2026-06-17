"""Command-line interface for the PitchBook PDF scraper.

Examples
--------
    # Print one profile as JSON
    python -m pitchbook_scraper samples/PitchBook_Alethea_Medical.pdf

    # Scrape a whole folder of PDFs into a CSV
    python -m pitchbook_scraper ./pdfs --format csv -o companies.csv

    # Pretty human-readable summary
    python -m pitchbook_scraper profile.pdf --format text
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .parser import parse_company
from .models import Company


def _iter_pdfs(paths: list[str]):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from sorted(p.rglob("*.pdf"))
        elif p.suffix.lower() == ".pdf":
            yield p
        else:
            print(f"warning: skipping non-PDF path {p}", file=sys.stderr)


# CSV columns: flat, one row per company.
_CSV_FIELDS = [
    "name", "website", "source_file", "keywords", "employees",
    "last_round_stage", "last_round_type", "last_round_amount", "last_round_date",
    "total_raised", "most_recent_revenue", "revenue_date",
    "acquired", "acquirer", "acquisition_date",
    "team_size", "team_names", "team_emails", "team",
    "primary_offices", "description",
]


def _company_csv_row(c: Company) -> dict:
    team = "; ".join(
        f"{m.name} ({m.title})" if m.title else m.name for m in c.team
    )
    return {
        "name": c.name or "",
        "website": c.website or "",
        "source_file": c.source_file or "",
        "keywords": c.keywords or "",
        "employees": c.employees or "",
        "last_round_stage": c.last_round_stage or "",
        "last_round_type": c.last_round_type or "",
        "last_round_amount": c.last_round_amount or "",
        "last_round_date": c.last_round_date or "",
        "total_raised": c.total_raised or "",
        "most_recent_revenue": c.most_recent_revenue or "",
        "revenue_date": c.revenue_date or "",
        "acquired": "" if c.acquired is None else ("Yes" if c.acquired else "No"),
        "acquirer": c.acquirer or "",
        "acquisition_date": c.acquisition_date or "",
        "team_size": c.team_size,
        "team_names": "; ".join(c.team_names),
        "team_emails": "; ".join(c.team_emails),
        "team": team,
        "primary_offices": "; ".join(c.primary_offices),
        "description": c.description or "",
    }


def _render_text(c: Company) -> str:
    rev = c.most_recent_revenue or "N/A"
    if c.most_recent_revenue and c.revenue_date:
        rev = f"{c.most_recent_revenue} (as of {c.revenue_date})"
    lines = [
        "=" * 70,
        f"Company:        {c.name or '(unknown)'}",
        f"Website:        {c.website or 'N/A'}",
        f"Keywords:       {c.keywords or 'N/A'}",
        f"Source file:    {c.source_file or ''}",
        "-" * 70,
        f"Employees:      {c.employees or 'N/A'}",
        f"Last round:     {c.last_round_amount or 'N/A'} "
        f"{('(' + c.last_round_type + ')') if c.last_round_type else ''} "
        f"{('on ' + c.last_round_date) if c.last_round_date else ''}".rstrip(),
        f"Round stage:    {c.last_round_stage or 'N/A'}   (raw: {c.last_round_type or 'N/A'})",
        f"Total raised:   {c.total_raised or 'N/A'}",
        f"Most recent rev:{rev}",
        f"Acquired:       {'Unknown' if c.acquired is None else ('Yes' if c.acquired else 'No')}"
        + (f" by {c.acquirer}" if c.acquirer else ""),
        f"Primary offices:{('; '.join(c.primary_offices)) or 'N/A'}",
        "-" * 70,
        f"Current team ({c.team_size}):",
    ]
    for m in c.team:
        line = f"  - {m.name}"
        if m.title:
            line += f" — {m.title}"
        if m.email:
            line += f"  <{m.email}>"
        lines.append(line)
    if not c.team:
        lines.append("  (none parsed)")
    if c.financials:
        lines.append("-" * 70)
        lines.append("Financials:")
        for k, v in c.financials.items():
            if k.startswith("_"):
                continue
            lines.append(f"  {k}: {v}")
    lines.append("-" * 70)
    lines.append("Description:")
    lines.append(f"  {c.description or 'N/A'}")
    lines.append("=" * 70)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pitchbook_scraper",
        description="Scrape PitchBook company-profile PDFs into structured data.",
    )
    parser.add_argument("paths", nargs="+", help="PDF file(s) or directory(ies).")
    parser.add_argument(
        "-f", "--format", choices=["json", "csv", "text", "xlsx"], default="json",
        help="Output format (default: json). 'xlsx' requires -o.",
    )
    parser.add_argument(
        "-o", "--output", help="Write to this file instead of stdout.",
    )
    args = parser.parse_args(argv)

    pdfs = list(_iter_pdfs(args.paths))
    if not pdfs:
        print("error: no PDF files found.", file=sys.stderr)
        return 1

    companies: list[Company] = []
    for pdf in pdfs:
        try:
            companies.append(parse_company(pdf))
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"error: failed to parse {pdf}: {exc}", file=sys.stderr)

    if not companies:
        return 1

    if args.format == "xlsx":
        if not args.output:
            print("error: --format xlsx requires -o/--output FILE.xlsx", file=sys.stderr)
            return 1
        from .excel import write_xlsx
        write_xlsx(companies, args.output)
        print(f"Wrote {len(companies)} compan{'y' if len(companies)==1 else 'ies'} to {args.output}",
              file=sys.stderr)
        return 0

    out = open(args.output, "w", newline="", encoding="utf-8") if args.output else sys.stdout
    try:
        if args.format == "json":
            payload = [c.to_dict() for c in companies]
            json.dump(payload if len(payload) > 1 else payload[0], out, indent=2, ensure_ascii=False)
            out.write("\n")
        elif args.format == "csv":
            writer = csv.DictWriter(out, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for c in companies:
                writer.writerow(_company_csv_row(c))
        else:  # text
            out.write("\n\n".join(_render_text(c) for c in companies) + "\n")
    finally:
        if args.output:
            out.close()
            print(f"Wrote {len(companies)} compan{'y' if len(companies)==1 else 'ies'} to {args.output}",
                  file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

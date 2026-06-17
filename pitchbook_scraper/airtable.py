"""Push scraped companies into an Airtable base.

Uses only the Python standard library (urllib) so it bundles cleanly into a
standalone .exe. Airtable's REST API is documented at https://airtable.com/api.

You need three things (entered in the app and saved locally):
  * a Personal Access Token  (https://airtable.com/create/tokens, scope:
    data.records:write, and access to your base)
  * the Base ID             (starts with "app...", from the API docs of your base)
  * the Table name          (e.g. "Companies")
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterable

from .models import Company

API_ROOT = "https://api.airtable.com/v0"

# Maps our Company data to Airtable column names. Create these columns in your
# table (typecast lets Airtable coerce text into number/date/checkbox fields).
def _fields(c: Company) -> dict:
    return {
        "Company": c.name or "",
        "Website": c.website or "",
        "Keywords": c.keywords or "",
        "Employees": c.employees or "",
        "Last Round Stage": c.last_round_stage or "",
        "Last Round Type": c.last_round_type or "",
        "Last Round Amount": c.last_round_amount or "",
        "Last Round Date": c.last_round_date or "",
        "Total Raised": c.total_raised or "",
        "Most Recent Revenue": c.most_recent_revenue or "",
        "Revenue Date": c.revenue_date or "",
        "Acquired": "" if c.acquired is None else ("Yes" if c.acquired else "No"),
        "Acquirer": c.acquirer or "",
        "Acquisition Date": c.acquisition_date or "",
        "Team Size": c.team_size,
        "Team Names": "; ".join(c.team_names),
        "Team Emails": "; ".join(c.team_emails),
        "Team": "; ".join(
            f"{m.name} ({m.title})" if m.title else m.name for m in c.team
        ),
        "Primary Offices": "; ".join(c.primary_offices),
        "Description": c.description or "",
        "Source File": c.source_file or "",
    }


# The column names above — handy for telling users what to create in Airtable.
COLUMNS = list(_fields(Company()).keys())


def _post_batch(token: str, base_id: str, table: str, records: list[dict]) -> dict:
    url = f"{API_ROOT}/{base_id}/{urllib.request.quote(table)}"
    body = json.dumps({"records": records, "typecast": True}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Airtable error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error talking to Airtable: {exc.reason}") from exc


def sync_companies(
    companies: Iterable[Company],
    token: str,
    base_id: str,
    table: str,
) -> int:
    """Create one Airtable record per company. Returns the number created."""
    records = [{"fields": _fields(c)} for c in companies]
    created = 0
    # Airtable accepts up to 10 records per request.
    for i in range(0, len(records), 10):
        chunk = records[i : i + 10]
        result = _post_batch(token, base_id, table, chunk)
        created += len(result.get("records", []))
    return created

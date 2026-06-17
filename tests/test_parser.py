"""Tests for the PitchBook scraper.

These run against a real PitchBook profile placed at
``samples/PitchBook_Alethea_Medical.pdf``. That PDF is proprietary PitchBook
data ("for the exclusive use of subscriber") and is intentionally **not**
committed, so the tests skip themselves when it is absent. Drop your own
PitchBook profile export at that path to exercise the suite.

    python -m pytest          # or: PYTHONPATH=. python tests/test_parser.py
"""
from pathlib import Path

from pitchbook_scraper import parse_company

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "PitchBook_Alethea_Medical.pdf"
_HAVE_SAMPLE = SAMPLE.exists()

try:
    import pytest
    pytestmark = pytest.mark.skipif(not _HAVE_SAMPLE, reason=f"sample PDF not present at {SAMPLE}")
except ImportError:  # pytest is optional; the __main__ runner handles skipping
    pytest = None


def _company():
    return parse_company(SAMPLE)


def test_identity_and_description():
    c = _company()
    assert c.name == "Alethea Medical"
    assert c.description and c.description.startswith("Operator of an e-consult")


def test_website_and_keywords():
    c = _company()
    assert c.website == "www.aletheamedical.com"
    assert c.keywords and 1 <= len(c.keywords.split()) <= 10
    assert "platform" in c.keywords


def test_employees():
    assert _company().employees == "15"


def test_last_round():
    c = _company()
    assert c.last_round_type == "Seed Round"
    assert c.last_round_amount == "$1.00M"
    assert c.last_round_date == "31-Dec-2023"


def test_total_raised():
    assert _company().total_raised == "$1.29M"


def test_team():
    c = _company()
    assert c.team_size == 5
    assert c.team_names == [
        "Steven Pilz",
        "Rob Bevis",
        "Heiko Peters",
        "Devon Livingstone MD",
        "David Sheps",
    ]
    assert c.team[0].title == "Chief Executive Officer"
    assert c.team[0].email == "steven@aletheamedical.com"
    assert c.team_emails == [
        "steven@aletheamedical.com",
        "rob@aletheamedical.com",
        "heiko@aletheamedical.com",
        "devon@aletheamedical.com",
        "david@aletheamedical.com",
    ]


def test_offices():
    offices = _company().primary_offices
    assert offices[0].startswith("Calgary")
    assert any("Halifax" in o for o in offices)
    assert any("Vancouver" in o for o in offices)


def test_financials():
    c = _company()
    assert c.most_recent_revenue == "$2.77M"
    assert c.revenue_date == "31-Dec-2024"
    assert c.financials.get("Reported units") == "thousands USD"


def test_not_acquired():
    assert _company().acquired is False


def test_financing_rounds():
    rounds = _company().financing_rounds
    assert len(rounds) == 6
    assert rounds[0].round_type == "Seed Round"
    assert rounds[0].date == "31-Dec-2023"


if __name__ == "__main__":
    if not _HAVE_SAMPLE:
        print(f"SKIP: sample PDF not present at {SAMPLE}")
        raise SystemExit(0)
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")

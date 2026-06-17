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
    assert c.last_round_stage == "Seed"
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


SAMPLES_DIR = SAMPLE.parent

# Expected values for additional real profiles (skipped when the PDF is absent).
# These cover the two-column Highlights box, "Undisclosed" amounts, members
# without emails, the "Current Board Members" boundary, and multi-office layouts.
_EXPECTED = {
    "Moment_Energy.pdf": dict(
        name="Moment Energy", last_round_type="Later Stage VC (Series C)",
        last_round_stage="Series C",
        last_round_amount="Undisclosed", total_raised="$68.80M", team_size=4,
    ),
    "2S_Water.pdf": dict(
        name="2S Water", last_round_type="Accelerator/Incubator",
        last_round_stage="Seed",  # most recent substantive round (skips accelerator)
        last_round_amount="$0.15M", total_raised="$1.99M", team_size=3,
    ),
    "WaitWell.pdf": dict(
        name="WaitWell", last_round_type="Accelerator/Incubator",
        last_round_stage="Seed", total_raised="$2.90M",
    ),
    "AandK_Robotics.pdf": dict(
        name="A&K Robotics", last_round_stage="Series A",
        last_round_amount="$8.00M", total_raised="$14.28M", team_size=2,
    ),
    "Beatdapp.pdf": dict(
        name="Beatdapp", last_round_stage="Series A",
        last_round_amount="$23.23M", total_raised="$28.83M", team_size=5,
    ),
    "PrePad.pdf": dict(name="PrePad", last_round_type="Seed Round",
                       last_round_stage="Seed", team_size=5),
    "Spexi.pdf": dict(name="Spexi", last_round_type="Later Stage VC (Series A)",
                      last_round_stage="Series A", team_size=5),
}


def test_additional_profiles():
    checked = 0
    for fname, expected in _EXPECTED.items():
        path = SAMPLES_DIR / fname
        if not path.exists():
            continue
        checked += 1
        c = parse_company(path)
        for attr, want in expected.items():
            got = getattr(c, attr)
            assert got == want, f"{fname}: {attr} = {got!r}, expected {want!r}"
        # Offices should never contain obvious non-office junk.
        joined = " ".join(c.primary_offices)
        for bad in ("Financing", "Source", "Provided", "subscriber", "Corporation"):
            assert bad not in joined, f"{fname}: polluted offices: {c.primary_offices}"
    if checked == 0:
        print("SKIP: no additional sample PDFs present")


if __name__ == "__main__":
    if not _HAVE_SAMPLE:
        print(f"SKIP: sample PDF not present at {SAMPLE}")
        raise SystemExit(0)
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")

"""Data models for scraped PitchBook company profiles."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TeamMember:
    """A single person on the company's current team / board / executives."""

    name: str
    title: Optional[str] = None
    email: Optional[str] = None


@dataclass
class FinancingRound:
    """A single financing/fundraising round."""

    date: Optional[str] = None
    round_type: Optional[str] = None      # e.g. "Series B", "Seed", "Later Stage VC"
    amount_raised: Optional[str] = None   # e.g. "$25.00M"
    post_valuation: Optional[str] = None
    investors: list[str] = field(default_factory=list)


@dataclass
class Company:
    """Structured representation of a PitchBook company profile."""

    # Identity
    name: Optional[str] = None
    source_file: Optional[str] = None
    website: Optional[str] = None

    # Requested fields
    description: Optional[str] = None
    keywords: Optional[str] = None                  # rule-based 1-10 word "what they do"
    employees: Optional[str] = None                 # headcount
    employees_updated_date: Optional[str] = None    # "as of" date for the headcount
    last_round_type: Optional[str] = None
    last_round_stage: Optional[str] = None          # normalized: Seed, Series A, Later Stage VC, ...
    last_round_amount: Optional[str] = None
    last_round_date: Optional[str] = None
    total_raised: Optional[str] = None
    primary_offices: list[str] = field(default_factory=list)
    most_recent_revenue: Optional[str] = None
    revenue_date: Optional[str] = None              # period end of the most recent revenue
    financials: dict[str, str] = field(default_factory=dict)   # e.g. {"Revenue": "...", "EBITDA": "..."}
    acquired: Optional[bool] = None
    acquirer: Optional[str] = None
    acquisition_date: Optional[str] = None

    # Current team
    team: list[TeamMember] = field(default_factory=list)

    @property
    def team_size(self) -> int:
        return len(self.team)

    @property
    def team_names(self) -> list[str]:
        return [m.name for m in self.team]

    @property
    def team_emails(self) -> list[str]:
        return [m.email for m in self.team if m.email]

    # All financing rounds (last_round_* are derived from the most recent of these)
    financing_rounds: list[FinancingRound] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["team_size"] = self.team_size
        d["team_names"] = self.team_names
        d["team_emails"] = self.team_emails
        return d

"""PitchBook PDF scraper.

Extract structured company data from PitchBook company-profile PDFs.
"""
from .models import Company, FinancingRound, TeamMember
from .extractor import extract_text
from .parser import parse_company

__all__ = [
    "Company",
    "FinancingRound",
    "TeamMember",
    "extract_text",
    "parse_company",
]

__version__ = "0.1.0"

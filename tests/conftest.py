from datetime import date
from pathlib import Path

import pytest
import yaml

from matcher.schemas import JobPosting

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile() -> dict:
    return yaml.safe_load((ROOT / "config" / "profile.yaml").read_text())


@pytest.fixture
def thesis() -> dict:
    return yaml.safe_load((ROOT / "config" / "thesis.yaml").read_text())


@pytest.fixture
def today() -> date:
    return date(2026, 10, 11)


def make_posting(**overrides) -> JobPosting:
    """A fresh, eligible, full-time London posting; override fields to test each rule."""
    base = dict(
        is_specific_posting=True, source_id="x", title="Graduate Analyst", employer="Acme", location="London, UK",
        url=None, role_type="full_time", seat_type="investing_analysis", employment_type="Full-time graduate programme",
        application_route="https://careers.example.com/x", start_date="2027-09", start_text="September 2027",
        posted_date="2026-10-08", posted_evidence="careers page", closing_date="2026-12-15",
        min_years_experience=None, graduation_years_eligible=[2027], responsibilities=[], requirements=[], summary="",
    )
    return JobPosting(**{**base, **overrides})

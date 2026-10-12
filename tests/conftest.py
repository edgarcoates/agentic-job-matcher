from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile() -> dict:
    return yaml.safe_load((ROOT / "config" / "profile.yaml").read_text())


@pytest.fixture
def today() -> date:
    return date(2026, 10, 11)

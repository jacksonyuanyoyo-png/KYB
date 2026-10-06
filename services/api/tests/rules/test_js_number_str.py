"""JavaScript String(number) formatting used in ownership messages."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest

from fcc_api.rules.domain import js_number_str


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "0"),
        (0.0, "0"),
        (-0.0, "0"),
        (60, "60"),
        (60.0, "60"),
        (91, "91"),
        (24.5, "24.5"),
        (10.5, "10.5"),
        (100, "100"),
        (99.99, "99.99"),
        (0.1 + 0.2, "0.30000000000000004"),
        (1e21, "1e+21"),
        (1e20, "100000000000000000000"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (100.005, "100.005"),
        (-24.5, "-24.5"),
    ],
)
def test_js_number_str_matches_javascript(value: float, text: str) -> None:
    assert js_number_str(value) == text


def test_sixty_percent_has_no_decimal_zero() -> None:
    rendered = f"ownership totals {js_number_str(60)}%."
    assert rendered == "ownership totals 60%."
    assert "60.0" not in rendered

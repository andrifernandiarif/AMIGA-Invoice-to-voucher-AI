"""
Unit tests for CalculationValidator (task 18.2).
Uses plain dataclasses to stand in for HeaderFields and LineItem ORM objects —
zero database dependencies.

Requirements tested: 7.1, 7.2, 7.3
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.validation.calculation import CalculationValidator


# ---------------------------------------------------------------------------
# Lightweight stand-ins for the ORM models
# ---------------------------------------------------------------------------


@dataclass
class FakeHeader:
    subtotal: float | None = None
    total_amount: float | None = None
    tax_amount: float | None = None
    fee_amount: float | None = None
    rounding_adj: float | None = None


@dataclass
class FakeLine:
    line_total: float | None = None


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

TOLERANCE = Decimal("0.01")


def _validate(header, lines):
    return CalculationValidator.validate(header, lines, TOLERANCE)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExactToleranceBoundaryNoFlag:
    """
    Req 7.1 — discrepancy == tolerance exactly → no flag (not strictly greater).
    """

    def test_subtotal_discrepancy_exactly_at_tolerance_no_flag(self):
        # line_total sum = 10.00, header subtotal = 10.01 → diff = 0.01 == TOLERANCE
        header = FakeHeader(subtotal=10.01, total_amount=10.01)
        lines = [FakeLine(line_total=10.00)]
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "subtotal" not in flagged_fields, (
            f"Discrepancy equal to tolerance should NOT flag subtotal. Got: {result}"
        )

    def test_total_discrepancy_exactly_at_tolerance_no_flag(self):
        # subtotal=10.00, tax=0.00, fee=0.00, rounding=0.00
        # computed_total = 10.00, extracted_total = 10.01 → diff = 0.01 == TOLERANCE
        header = FakeHeader(subtotal=10.00, total_amount=10.01, tax_amount=None, fee_amount=None, rounding_adj=None)
        lines = [FakeLine(line_total=10.00)]
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "total_amount" not in flagged_fields, (
            f"Discrepancy equal to tolerance should NOT flag total_amount. Got: {result}"
        )


class TestJustOverToleranceFlag:
    """
    Req 7.1 — discrepancy > tolerance → flag raised.
    """

    def test_subtotal_just_over_tolerance_is_flagged(self):
        # line_total sum = 10.00, header subtotal = 10.011 → diff = 0.011 > 0.01
        header = FakeHeader(subtotal=10.011, total_amount=10.011)
        lines = [FakeLine(line_total=10.00)]
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "subtotal" in flagged_fields, (
            f"Discrepancy just over tolerance SHOULD flag subtotal. Got: {result}"
        )

    def test_flagged_dict_has_required_keys(self):
        """Req 7.3 — discrepancy dicts expose computed, extracted, difference."""
        header = FakeHeader(subtotal=10.011, total_amount=10.011)
        lines = [FakeLine(line_total=10.00)]
        result = _validate(header, lines)
        subtotal_flag = next(d for d in result if d["field"] == "subtotal")
        assert "computed" in subtotal_flag
        assert "extracted" in subtotal_flag
        assert "difference" in subtotal_flag

    def test_total_just_over_tolerance_is_flagged(self):
        # subtotal=10.00, total_amount=10.011 → diff = 0.011 > 0.01
        header = FakeHeader(subtotal=10.00, total_amount=10.011)
        lines = []  # no line items → subtotal check skipped
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "total_amount" in flagged_fields, (
            f"Total discrepancy just over tolerance SHOULD flag total_amount. Got: {result}"
        )


class TestJustUnderToleranceNoFlag:
    """
    Req 7.1 — discrepancy < tolerance → no flag.
    """

    def test_subtotal_just_under_tolerance_no_flag(self):
        # line_total sum = 10.00, header subtotal = 10.009 → diff = 0.009 < 0.01
        header = FakeHeader(subtotal=10.009, total_amount=10.009)
        lines = [FakeLine(line_total=10.00)]
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "subtotal" not in flagged_fields, (
            f"Discrepancy just under tolerance should NOT flag subtotal. Got: {result}"
        )

    def test_total_just_under_tolerance_no_flag(self):
        # subtotal=10.00, total_amount=10.009 → diff = 0.009 < 0.01
        header = FakeHeader(subtotal=10.00, total_amount=10.009)
        lines = []
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "total_amount" not in flagged_fields


class TestBothChecksDiscrepant:
    """
    Req 7.1 + 7.2 — when both sum-of-lines and total-composition fail,
    both fields must be present in the result.
    """

    def test_both_subtotal_and_total_flagged(self):
        # line totals sum to 100.00, header says subtotal = 110.00 → diff = 10 (flagged)
        # subtotal(110) + tax(0) + fee(0) + rounding(0) = 110, total_amount = 125 → diff = 15 (flagged)
        header = FakeHeader(subtotal=110.00, total_amount=125.00)
        lines = [FakeLine(line_total=50.00), FakeLine(line_total=50.00)]
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "subtotal" in flagged_fields, f"Expected subtotal flagged. Got: {result}"
        assert "total_amount" in flagged_fields, f"Expected total_amount flagged. Got: {result}"
        assert len(result) == 2


class TestNoLineItemsNoFlag:
    """
    Req 7.1 — empty line_items list → subtotal check is skipped entirely,
    even when header.subtotal is set.
    """

    def test_empty_line_items_skips_subtotal_check(self):
        header = FakeHeader(subtotal=50.00, total_amount=50.00)
        lines = []
        result = _validate(header, lines)
        flagged_fields = [d["field"] for d in result]
        assert "subtotal" not in flagged_fields, (
            f"Empty line items should skip subtotal check. Got: {result}"
        )

    def test_empty_line_items_with_matching_total_no_flags(self):
        """Total check still runs when subtotal and total_amount are both set."""
        header = FakeHeader(subtotal=50.00, total_amount=50.00)
        lines = []
        result = _validate(header, lines)
        assert result == []


class TestAllNoneFieldsNoFlag:
    """
    Req 7.1 + 7.2 — all financial fields None → no flags (no false positives).
    """

    def test_header_with_all_none_financials_no_flags(self):
        header = FakeHeader(
            subtotal=None,
            total_amount=None,
            tax_amount=None,
            fee_amount=None,
            rounding_adj=None,
        )
        lines = [FakeLine(line_total=None)]
        result = _validate(header, lines)
        assert result == [], f"All-None header should produce no flags. Got: {result}"

    def test_no_header_no_flags(self):
        """None header short-circuits the whole validator."""
        result = _validate(None, [FakeLine(line_total=100.00)])
        assert result == []

    def test_subtotal_none_skips_subtotal_check(self):
        """subtotal=None → subtotal check skipped even with line items."""
        header = FakeHeader(subtotal=None, total_amount=None)
        lines = [FakeLine(line_total=50.00)]
        result = _validate(header, lines)
        assert result == []


class TestFloatNeverUsed:
    """
    Ensure calculation.py uses Decimal arithmetic and never contains float
    literals (e.g. 1.0, 0.01 as raw floats) for financial logic.
    """

    def test_no_float_literals_in_arithmetic(self):
        """
        Parse calculation.py AST and verify there are no float literals.
        String-wrapped values like Decimal(str(x)) are the correct pattern.
        """
        import app.validation.calculation as calc_module

        source = inspect.getsource(calc_module)
        tree = ast.parse(textwrap.dedent(source))

        float_literals = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, float)
        ]
        assert float_literals == [], (
            f"Found float literals in calculation.py: "
            f"{[n.value for n in float_literals]}. Use Decimal(str(...)) instead."
        )

    def test_decimal_import_present(self):
        """calculation.py must import Decimal from the decimal module."""
        import app.validation.calculation as calc_module

        source = inspect.getsource(calc_module)
        assert "from decimal import Decimal" in source or "import decimal" in source

    def test_result_values_are_decimal_strings(self):
        """
        Req 7.3 — computed, extracted, and difference values in the result
        must be representable as Decimal (i.e., returned as strings, not floats).
        """
        header = FakeHeader(subtotal=10.011, total_amount=10.011)
        lines = [FakeLine(line_total=10.00)]
        result = _validate(header, lines)
        for flag in result:
            # All three value fields should be parseable as Decimal
            Decimal(flag["computed"])
            Decimal(flag["extracted"])
            Decimal(flag["difference"])

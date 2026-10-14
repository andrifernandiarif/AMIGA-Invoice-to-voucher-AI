# Feature: invoice-to-voucher, Property 7: Calculation Discrepancies Flagged Accurately
#
# For any invoice where |sum(line_totals) - subtotal| > tolerance OR
# |subtotal + tax + fees + rounding - total| > tolerance, the validator SHALL
# flag the discrepant field and include computed vs. extracted values.
# Within tolerance → no flag.
#
# **Validates: Requirements 7.1, 7.2, 7.3**

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.validation.calculation import CalculationValidator


# ---------------------------------------------------------------------------
# Lightweight stand-ins for the ORM models — no database required
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
# Strategies
# ---------------------------------------------------------------------------

# Decimal strategy that avoids NaN/Inf and stays within realistic invoice range.
_financial = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("99999"),
    allow_nan=False,
    allow_infinity=False,
    places=4,  # match NUMERIC(18,4) precision
)

_tolerance_strategy = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("1"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)


def _to_float(d: Decimal) -> float:
    """
    Convert Decimal to float for the FakeHeader/FakeLine fields, mirroring
    how SQLAlchemy's Numeric(18,4) values arrive as Python floats/Decimals
    in practice. CalculationValidator always wraps them with Decimal(str(x)).
    """
    return float(d)


# ---------------------------------------------------------------------------
# Property 7a — subtotal check: discrepancy > tolerance → flagged
# ---------------------------------------------------------------------------


@given(
    line_total=_financial,
    subtotal_offset=st.decimals(
        min_value=Decimal("0.0001"),
        max_value=Decimal("100"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
    tolerance=_tolerance_strategy,
)
@settings(max_examples=100)
def test_property_7a_subtotal_discrepancy_over_tolerance_is_flagged(
    line_total: Decimal,
    subtotal_offset: Decimal,
    tolerance: Decimal,
) -> None:
    """
    When |sum(line_totals) - extracted_subtotal| > tolerance,
    the validator MUST flag 'subtotal' and include computed + extracted values.
    Validates: Requirements 7.1, 7.3
    """
    # extracted_subtotal differs from actual sum by (tolerance + offset), so
    # the difference is strictly greater than tolerance.
    extracted_subtotal = line_total + tolerance + subtotal_offset

    header = FakeHeader(
        subtotal=_to_float(extracted_subtotal),
        total_amount=_to_float(extracted_subtotal),  # keep total check neutral
    )
    lines = [FakeLine(line_total=_to_float(line_total))]

    result = CalculationValidator.validate(header, lines, tolerance)
    flagged_fields = [d["field"] for d in result]

    assert "subtotal" in flagged_fields, (
        f"Expected 'subtotal' flagged when |{line_total} - {extracted_subtotal}| "
        f"= {abs(line_total - extracted_subtotal)} > tolerance={tolerance}. "
        f"Got flags: {flagged_fields}"
    )

    # Req 7.3 — verify computed, extracted, difference are all present
    subtotal_entry = next(d for d in result if d["field"] == "subtotal")
    assert "computed" in subtotal_entry
    assert "extracted" in subtotal_entry
    assert "difference" in subtotal_entry
    # Values must be parseable as Decimal (returned as strings)
    Decimal(subtotal_entry["computed"])
    Decimal(subtotal_entry["extracted"])
    Decimal(subtotal_entry["difference"])


# ---------------------------------------------------------------------------
# Property 7b — subtotal check: discrepancy <= tolerance → no flag
# ---------------------------------------------------------------------------


@given(
    line_total=_financial,
    tolerance=_tolerance_strategy,
    within_fraction=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
)
@settings(max_examples=100)
def test_property_7b_subtotal_discrepancy_within_tolerance_no_flag(
    line_total: Decimal,
    tolerance: Decimal,
    within_fraction: Decimal,
) -> None:
    """
    When |sum(line_totals) - extracted_subtotal| <= tolerance, 'subtotal'
    MUST NOT be flagged.
    Validates: Requirements 7.1
    """
    # extracted_subtotal = line_total + tolerance * within_fraction
    # diff = tolerance * within_fraction <= tolerance
    extracted_subtotal = line_total + (tolerance * within_fraction)

    header = FakeHeader(
        subtotal=_to_float(extracted_subtotal),
        total_amount=_to_float(extracted_subtotal),
    )
    lines = [FakeLine(line_total=_to_float(line_total))]

    result = CalculationValidator.validate(header, lines, tolerance)
    flagged_fields = [d["field"] for d in result]

    assert "subtotal" not in flagged_fields, (
        f"Expected NO 'subtotal' flag when diff={tolerance * within_fraction} <= tolerance={tolerance}. "
        f"Got flags: {flagged_fields}"
    )


# ---------------------------------------------------------------------------
# Property 7c — total composition check: discrepancy > tolerance → flagged
# ---------------------------------------------------------------------------


@given(
    subtotal=_financial,
    tax=_financial,
    fee=_financial,
    rounding=st.decimals(
        min_value=Decimal("-1"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
    total_offset=st.decimals(
        min_value=Decimal("0.0001"),
        max_value=Decimal("100"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
    tolerance=_tolerance_strategy,
)
@settings(max_examples=100)
def test_property_7c_total_discrepancy_over_tolerance_is_flagged(
    subtotal: Decimal,
    tax: Decimal,
    fee: Decimal,
    rounding: Decimal,
    total_offset: Decimal,
    tolerance: Decimal,
) -> None:
    """
    When |subtotal + tax + fees + rounding - extracted_total| > tolerance,
    the validator MUST flag 'total_amount' and include computed + extracted values.
    Validates: Requirements 7.2, 7.3
    """
    computed_total = subtotal + tax + fee + rounding
    extracted_total = computed_total + tolerance + total_offset  # strictly exceeds tolerance

    header = FakeHeader(
        subtotal=_to_float(subtotal),
        total_amount=_to_float(extracted_total),
        tax_amount=_to_float(tax),
        fee_amount=_to_float(fee),
        rounding_adj=_to_float(rounding),
    )
    # No line items → subtotal check skipped, only total_amount check runs
    result = CalculationValidator.validate(header, [], tolerance)
    flagged_fields = [d["field"] for d in result]

    assert "total_amount" in flagged_fields, (
        f"Expected 'total_amount' flagged when "
        f"|{computed_total} - {extracted_total}| > tolerance={tolerance}. "
        f"Got flags: {flagged_fields}"
    )

    total_entry = next(d for d in result if d["field"] == "total_amount")
    assert "computed" in total_entry
    assert "extracted" in total_entry
    assert "difference" in total_entry
    Decimal(total_entry["computed"])
    Decimal(total_entry["extracted"])
    Decimal(total_entry["difference"])


# ---------------------------------------------------------------------------
# Property 7d — total composition check: discrepancy <= tolerance → no flag
# ---------------------------------------------------------------------------


@given(
    subtotal=_financial,
    tax=_financial,
    fee=_financial,
    rounding=st.decimals(
        min_value=Decimal("-1"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
    tolerance=_tolerance_strategy,
    within_fraction=st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("1"),
        allow_nan=False,
        allow_infinity=False,
        places=4,
    ),
)
@settings(max_examples=100)
def test_property_7d_total_discrepancy_within_tolerance_no_flag(
    subtotal: Decimal,
    tax: Decimal,
    fee: Decimal,
    rounding: Decimal,
    tolerance: Decimal,
    within_fraction: Decimal,
) -> None:
    """
    When |subtotal + tax + fees + rounding - extracted_total| <= tolerance,
    'total_amount' MUST NOT be flagged.
    Validates: Requirements 7.2
    """
    computed_total = subtotal + tax + fee + rounding
    extracted_total = computed_total + (tolerance * within_fraction)

    header = FakeHeader(
        subtotal=_to_float(subtotal),
        total_amount=_to_float(extracted_total),
        tax_amount=_to_float(tax),
        fee_amount=_to_float(fee),
        rounding_adj=_to_float(rounding),
    )
    result = CalculationValidator.validate(header, [], tolerance)
    flagged_fields = [d["field"] for d in result]

    assert "total_amount" not in flagged_fields, (
        f"Expected NO 'total_amount' flag when diff={tolerance * within_fraction} <= tolerance={tolerance}. "
        f"Got flags: {flagged_fields}"
    )


# ---------------------------------------------------------------------------
# Property 7e — no header → no flags (defensive edge case)
# ---------------------------------------------------------------------------


@given(line_total=_financial, tolerance=_tolerance_strategy)
@settings(max_examples=50)
def test_property_7e_no_header_produces_no_flags(
    line_total: Decimal,
    tolerance: Decimal,
) -> None:
    """
    None header must always produce an empty result regardless of line items.
    Validates: Requirements 7.1, 7.2 (defensive guard)
    """
    result = CalculationValidator.validate(None, [FakeLine(_to_float(line_total))], tolerance)
    assert result == [], f"None header must never produce flags. Got: {result}"

"""Money and hours arithmetic.

Payroll is not a place for floating point. Everything here is Decimal, and
every value that will ever be shown to a human or written to a CSV is
quantized to a fixed number of places at the moment it is created.
"""
from decimal import Decimal, ROUND_HALF_UP

CENTS = Decimal("0.01")
HOURS = Decimal("0.01")
RATE = Decimal("0.0001")


def money(value) -> Decimal:
    """Parse anything the export throws at us into a dollar amount."""
    return _to_decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def hours(value) -> Decimal:
    """Parse into an hours figure, rounded to hundredths."""
    return _to_decimal(value).quantize(HOURS, rounding=ROUND_HALF_UP)


def rate(value) -> Decimal:
    """Parse into an hourly rate, kept to four places so weighted averages
    stay honest before the final dollar rounding."""
    return _to_decimal(value).quantize(RATE, rounding=ROUND_HALF_UP)


def _to_decimal(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("refusing to treat a boolean as a number")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    text = str(value).strip().replace("$", "").replace(",", "")
    if text in ("", "-", "--", "n/a", "N/A", "None"):
        return Decimal("0")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    result = Decimal(text)
    return -result if negative else result


def is_blank(value) -> bool:
    """True when the export gave us nothing at all.

    This matters: Sitterwise writes a blank for 'nobody asked' and the string
    '0.00' for 'asked, and the answer was zero'. Those are different facts and
    the app must not confuse them.
    """
    return value is None or (isinstance(value, str) and value.strip() == "")


def fmt_money(value) -> str:
    return f"${money(value):,.2f}"


def fmt_hours(value) -> str:
    h = hours(value)
    return f"{h:,.2f}".rstrip("0").rstrip(".") if h == h.to_integral_value() else f"{h:,.2f}"

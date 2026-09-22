"""Paycheck dates label documents; they never change payroll calculations."""
from datetime import date, timedelta
import re


def suggested_pay_date(period_end: date) -> date:
    """Sitterwise's usual payday is the Friday following the worked period."""
    return period_end + timedelta(days=(4 - period_end.weekday()) % 7 or 7)


def checked_pay_date(value, period_end: date) -> date:
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Choose the pay date shown in OnPay.')
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise ValueError('Choose a valid pay date.') from None
    if result < period_end:
        raise ValueError('The pay date cannot be before this pay period ends.')
    return result

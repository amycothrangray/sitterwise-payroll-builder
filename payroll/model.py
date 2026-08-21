"""The shapes payroll data takes as it moves through the app.

A Job is one Sitterwise booking after the importer has read it. It carries
both what the export said and what the app worked out, side by side, so that
every number on screen can be traced back to a cell in the spreadsheet.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from decimal import Decimal


@dataclass
class Job:
    # --- straight from the export, never altered -----------------------
    row_number: int
    booking_id: str
    ulid: str
    caregiver_name: str
    client_name: str
    service_type: str
    location_type: str
    hotel: str
    status: str
    payment_status: str
    admin_notes: str
    start: datetime | None
    end: datetime | None
    hours_exported: Decimal | None          # Sitterwise's 'Total Hours'
    paid_to_caregiver: Decimal
    charge_to_client: Decimal
    sitterwise_cut: Decimal
    tip: Decimal
    tip_was_blank: bool
    reimbursement: Decimal
    reimbursement_was_blank: bool
    bonus: Decimal
    lifesaver_bonus: Decimal

    # --- worked out by the app ----------------------------------------
    hours_worked: Decimal = Decimal("0")     # from start/end, the truth
    workday: date | None = None              # which day overtime counts it on
    tier_key: str = "unknown"
    tier_label: str = "Unknown rate"
    rate: Decimal = Decimal("0")
    rate_basis: str = "none"                 # how the rate was worked out
    minimum_applied: bool = False
    hours_paid: Decimal = Decimal("0")       # worked, or the 4-hour minimum
    guarantee_hours: Decimal = Decimal("0")  # paid but not worked
    straight_pay: Decimal = Decimal("0")
    guarantee_pay: Decimal = Decimal("0")
    mileage_miles: Decimal | None = None        # the round trip
    mileage_payable_miles: Decimal | None = None  # what policy pays for
    mileage_policy_amount: Decimal | None = None  # what policy says to pay
    mileage_rate: Decimal | None = None
    mileage_amount: Decimal = Decimal("0")
    other_reimbursement: Decimal = Decimal("0")
    mileage_rejected_reason: str = ""   # service_type | under_minimum

    # --- bookkeeping ---------------------------------------------------
    is_payable: bool = False
    exclusion_reason: str = ""
    import_notes: list[str] = field(default_factory=list)

    @property
    def caregiver_key(self) -> str:
        """Matching key for a caregiver.

        Sitterwise gives us a display name and no ID, so this is the best we
        can do: collapse whitespace and case. Once Sitterwise stores a real
        caregiver ID this becomes that ID instead.
        """
        return " ".join(str(self.caregiver_name or "").split()).casefold()

    @property
    def display_name(self) -> str:
        return " ".join(str(self.caregiver_name or "").split())

    @property
    def total_reimbursement(self) -> Decimal:
        return self.mileage_amount + self.other_reimbursement

    @property
    def expected_pay(self) -> Decimal:
        return self.straight_pay + self.guarantee_pay

    def to_dict(self) -> dict:
        out = asdict(self)
        for key, value in list(out.items()):
            if isinstance(value, Decimal):
                out[key] = str(value)
            elif isinstance(value, datetime):
                out[key] = value.isoformat(sep=" ", timespec="minutes")
            elif isinstance(value, date):
                out[key] = value.isoformat()
        out["display_name"] = self.display_name
        out["total_reimbursement"] = str(self.total_reimbursement)
        return out


@dataclass
class ImportResult:
    jobs: list[Job]
    source_filename: str
    source_sha256: str
    row_count: int
    header: list[str]
    unmapped_columns: list[str]
    missing_columns: list[str]
    parse_errors: list[dict]
    min_date: date | None
    max_date: date | None

    @property
    def payable_jobs(self) -> list[Job]:
        return [j for j in self.jobs if j.is_payable]

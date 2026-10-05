from datetime import date

from pydantic import Field

from src.schemas import CustomModel


class PriceBase(CustomModel):
    cpt: str
    procedure_name: str
    ccn: str
    hospital_name: str
    city: str
    payer: str = Field(description="Insurer, `Medicare`, or `CASH` for self-pay")
    plan_name: str
    billing_class: str = Field(description="facility, professional, or both")
    cash_price: float | None = Field(description="The hospital's discounted cash price")
    negotiated_rate: float | None = Field(description="Rate this payer/plan pays the hospital")
    negotiated_min: float | None = Field(description="Lowest rate across this hospital's payers")
    negotiated_max: float | None = Field(description="Highest rate across this hospital's payers")
    overall_stars: float | None = Field(description="Hospital's CMS star rating, 1-5")
    psi90_composite: float | None = Field(description="Hospital's patient-safety composite; lower is better")


class PriceRow(PriceBase):
    state: str
    source: str = Field(description="Where the price came from, e.g. the hospital's machine-readable file")
    measure_date: date | None
    distance_miles: float | None = Field(description="Distance from the searched place (radius searches only)")


class PriceComparisonRow(PriceBase):
    pass

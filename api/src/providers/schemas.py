from pydantic import Field

from src.schemas import CustomModel


class ProviderSummary(CustomModel):
    npi: str = Field(description="National Provider Identifier")
    first_name: str
    last_name: str
    credential: str | None
    specialty: str | None
    city: str | None
    state: str | None
    patient_rating: float | None
    num_reviews: int | None
    volume_bucket: str | None = Field(description="LOW, MEDIUM, HIGH")
    wrvu_estimate: float | None = Field(description="Estimated work RVUs, a measure of procedure volume")
    distance_miles: float | None = Field(description="Distance from the searched place (radius searches only)")


class ProviderAffiliation(CustomModel):
    ccn: str
    name: str
    city: str
    state: str
    overall_stars: float | None
    psi90_composite: float | None
    is_primary: bool


class ProviderDetail(CustomModel):
    npi: str
    first_name: str
    last_name: str
    credential: str | None
    specialty: str | None
    taxonomy_code: str | None = Field(description="NUCC provider taxonomy code")
    gender: str | None
    medical_school: str | None
    graduation_year: int | None
    city: str | None
    state: str | None
    patient_rating: float | None
    num_reviews: int | None
    total_medicare_services: int | None
    total_medicare_beneficiaries: int | None
    wrvu_estimate: float | None
    volume_bucket: str | None
    trilliant_specialty: str | None
    trilliant_active: bool | None
    patient_demographics: dict[str, float] | None
    has_sanctions: bool | None
    affiliations: list[ProviderAffiliation] = Field(description="Hospitals, primary first")

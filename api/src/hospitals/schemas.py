from pydantic import Field

from src.schemas import CustomModel


class HospitalBase(CustomModel):
    ccn: str = Field(description="CMS Certification Number")
    name: str
    address: str | None
    city: str
    state: str
    zip: str | None
    phone: str | None
    hospital_type: str | None = Field(description="acute_care, critical_access, ...")
    ownership: str | None = Field(description="government, proprietary, voluntary_nonprofit")
    emergency_services: bool | None
    lat: float | None
    lng: float | None


class HospitalSummary(HospitalBase):
    distance_miles: float | None = Field(description="Distance from the searched place (radius searches only)")


class HospitalDetail(HospitalBase):
    overall_stars: float | None = Field(description="CMS overall star rating, 1-5")
    mortality_group: str | None = Field(description="Compared with the national average")
    safety_group: str | None
    readmission_group: str | None
    patient_experience_group: str | None
    timely_care_group: str | None
    psi90_composite: float | None = Field(description="Patient-safety composite; lower is better")
    hai_sirs: dict[str, float] | None = Field(description="Infection ratios (CLABSI, CAUTI, SSI, MRSA, CDI)")
    readmission_hip_knee: float | None = Field(description="Hip/knee replacement readmission rate, %")
    complication_hip_knee: float | None = Field(description="Hip/knee replacement complication rate, %")
    mortality_cabg: float | None = Field(description="Bypass surgery (CABG) mortality rate, %")
    measure_period: str | None


class HospitalProvider(CustomModel):
    npi: str
    first_name: str
    last_name: str
    credential: str | None
    specialty: str | None
    patient_rating: float | None
    num_reviews: int | None
    volume_bucket: str | None = Field(description="LOW, MEDIUM, HIGH")
    wrvu_estimate: float | None = Field(description="Estimated work RVUs, a measure of procedure volume")
    is_primary: bool = Field(description="This is the provider's primary hospital")

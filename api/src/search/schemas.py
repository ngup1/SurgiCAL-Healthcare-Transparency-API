from uuid import UUID

from pydantic import Field

from src.schemas import CustomModel

RELEVANCE = Field(description="Word similarity to the query, 0-1 (higher is closer)")


class ProcedureMatch(CustomModel):
    code: str = Field(description="CPT code")
    description: str
    category: str | None
    is_surgical: bool | None
    relevance: float = RELEVANCE


class ProviderMatch(CustomModel):
    npi: str
    first_name: str
    last_name: str
    specialty: str | None
    city: str | None
    state: str | None
    relevance: float = RELEVANCE


class HospitalMatch(CustomModel):
    ccn: str
    name: str
    city: str
    state: str
    relevance: float = RELEVANCE


class DeviceMatch(CustomModel):
    id: UUID
    brand_name: str
    manufacturer: str
    medical_specialty: str | None
    relevance: float = RELEVANCE


class SearchResults(CustomModel):
    procedures: list[ProcedureMatch]
    providers: list[ProviderMatch]
    hospitals: list[HospitalMatch]
    devices: list[DeviceMatch]

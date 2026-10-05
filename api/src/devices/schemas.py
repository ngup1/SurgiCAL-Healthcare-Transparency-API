from datetime import date
from uuid import UUID

from pydantic import Field

from src.schemas import CustomModel


class DeviceSummary(CustomModel):
    id: UUID
    fda_product_code: str | None = Field(description="3-letter FDA product code")
    brand_name: str
    generic_name: str | None
    manufacturer: str
    device_class: str | None = Field(description="FDA class I, II, or III (III = highest risk)")
    medical_specialty: str | None
    premarket_number: str | None = Field(description="510(k) (K...) or PMA (P...) clearance number")


class DeviceForProcedure(CustomModel):
    id: UUID
    fda_product_code: str | None
    brand_name: str
    generic_name: str | None
    manufacturer: str
    device_class: str | None
    medical_specialty: str | None
    usage_type: str | None = Field(description="implant, instrument, or consumable")


class RecallSummary(CustomModel):
    recall_number: str
    recall_class: str = Field(description="Class I is the most serious")
    reason: str | None
    status: str | None
    recall_date: date | None
    termination_date: date | None


class EventCount(CustomModel):
    event_type: str
    count: int


class DeviceDetail(DeviceSummary):
    description: str | None
    recent_recalls: list[RecallSummary] = Field(description="Up to 10, newest first")
    adverse_event_summary: list[EventCount] = Field(description="Adverse-event reports by type")


class Recall(RecallSummary):
    product_code: str | None
    brand_name: str | None
    manufacturer: str | None
    quantity: str | None
    distribution: str | None


class AdverseEvent(CustomModel):
    mdr_report_key: str | None = Field(description="FDA MAUDE report key")
    product_code: str | None
    brand_name: str | None
    manufacturer: str | None
    event_type: str
    event_date: date | None
    patient_outcomes: list[str] | None
    device_problems: list[str] | None
    event_narrative: str | None

from pydantic import Field

from src.schemas import CustomModel


class HealthStatus(CustomModel):
    status: str
    commit: str = Field(description="Git commit the running image was built from")

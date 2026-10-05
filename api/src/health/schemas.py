from pydantic import Field

from src.schemas import CustomModel


class HealthStatus(CustomModel):
    status: str
    commit: str = Field(description="Git commit the running image was built from")


class ReadinessStatus(CustomModel):
    status: str = Field(description="`ok` or `unavailable`")
    database: str = Field(description="`ok` or `unreachable`")

from dataclasses import dataclass

from pydantic import Field

from src.locations.constants import PlaceType
from src.schemas import CustomModel


class Place(CustomModel):
    name: str = Field(description="City or county name, or a ZIP code")
    type: PlaceType
    county: str


@dataclass(frozen=True)
class LocationFilter:
    """A resolved location: exactly one of city / county / point (radius search), or none."""

    city: str | None = None
    county: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius_miles: float | None = None

    @property
    def is_radius(self) -> bool:
        return self.lat is not None

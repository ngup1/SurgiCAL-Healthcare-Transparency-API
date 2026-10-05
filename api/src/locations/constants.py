from typing import Literal

COVERED_STATE = "CA"
# California ZIP codes span 90001-96162.
CA_ZIP_RANGE = (90001, 96162)
DEFAULT_ZIP_RADIUS_MILES = 10
MAX_RADIUS_MILES = 100
ZIP_PATTERN = r"^\d{5}$"

PlaceType = Literal["city", "county", "zip"]

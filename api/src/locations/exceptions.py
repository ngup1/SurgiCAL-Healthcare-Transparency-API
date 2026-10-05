from src.exceptions import RequestError


class LocationOutsideCoverage(RequestError):
    def __init__(self, field: str, value: str, message: str) -> None:
        super().__init__("location_outside_coverage", field, message, value)


class LocationNotFound(RequestError):
    def __init__(self, field: str, value: str, suggestions: list[str]) -> None:
        kind = "county" if field == "county" else "city"
        message = f"'{value}' is not a California {kind} in our coverage area."
        if suggestions:
            message += f" Did you mean: {', '.join(suggestions)}?"
        super().__init__("location_not_found", field, message, value, suggestions)


class InvalidLocationQuery(RequestError):
    def __init__(self, field: str, value, message: str) -> None:
        super().__init__("invalid_location_query", field, message, value)

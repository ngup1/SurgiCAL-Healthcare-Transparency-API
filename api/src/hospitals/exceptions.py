from src.exceptions import NotFound


class HospitalNotFound(NotFound):
    DETAIL = "Hospital not found"

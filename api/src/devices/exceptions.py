from src.exceptions import NotFound


class DeviceNotFound(NotFound):
    DETAIL = "Device not found"

from src.exceptions import NotFound


class ProviderNotFound(NotFound):
    DETAIL = "Provider not found"

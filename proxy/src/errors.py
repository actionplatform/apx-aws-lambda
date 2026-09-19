"""What the proxy answers instead of a result."""


class Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status

class FoundryError(Exception):
    pass


class AzureRequestError(FoundryError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Azure returned {status_code}: {message}")
        self.status_code = status_code


class NotFoundError(FoundryError, LookupError):
    pass


class DeploymentFailedError(FoundryError):
    pass

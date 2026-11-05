"""Stable image gateway error; raw provider messages are not public."""

class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 provider_request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.provider_request_id = provider_request_id

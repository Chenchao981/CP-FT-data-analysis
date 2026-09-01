from __future__ import annotations


class AgentError(RuntimeError):
    """A safe, user-facing Local Agent failure."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ManifestError(AgentError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(code, message, status_code)


class RunnerError(AgentError):
    pass

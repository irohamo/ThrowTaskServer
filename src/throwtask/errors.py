from typing import Any


class TodoError(Exception):
    def __init__(self, code: str, status: int = 400, **details: Any):
        self.code = code
        self.status = status
        self.details = details
        super().__init__(code)

    def envelope(self) -> dict:
        return {"version": 1, "ok": False, "error": {"code": self.code, **self.details}}


def success(data: Any) -> dict:
    return {"version": 1, "ok": True, "data": data}

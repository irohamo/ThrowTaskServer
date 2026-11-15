import re
from pathlib import Path
from uuid import UUID

import httpx

from .config import Settings, default_config, load_settings
from .errors import TodoError
from .models import ProjectCreate, ProjectPatch, TaskCreate, TaskPatch, parse_model


class ApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @classmethod
    def from_config(cls, path: Path | None = None):
        return cls(load_settings(path or default_config()))

    def request(self, method, path, *, data=None, revision=None, params=None, record_id=None):
        headers = {"Authorization": "Bearer " + self.settings.credential(), "Accept": "application/json"}
        if revision is not None:
            if type(revision) is not int or not 0 < revision < 10**15:
                raise TodoError("INVALID_REVISION")
            headers["If-Match"] = f'"{revision}"'
        try:
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as http:
                response = http.request(
                    method, self.settings.url + path, json=data, params=params, headers=headers
                )
        except httpx.HTTPError:
            code = "RESULT_UNKNOWN" if method in {"POST", "PATCH", "DELETE"} else "NETWORK_ERROR"
            raise TodoError(code, 503, **({"recordId": record_id} if record_id else {})) from None
        if 300 <= response.status_code < 400:
            raise TodoError("REDIRECT_REFUSED", response.status_code)
        try:
            result = response.json()
            if result.get("version") != 1 or type(result.get("ok")) is not bool:
                raise ValueError
            if not response.is_success or result["ok"] is False:
                error = result.get("error", {})
                code = error.get("code", "API_ERROR")
                if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,64}", code):
                    code = "API_ERROR"
                details = {}
                if type(error.get("currentRevision")) is int:
                    details["currentRevision"] = error["currentRevision"]
                if record_id:
                    details["recordId"] = record_id
                raise TodoError(code, response.status_code, **details)
            return result["data"]
        except (ValueError, KeyError, AttributeError, TypeError):
            raise TodoError("INVALID_SERVER_RESPONSE", 502) from None

    def list(self, kind="tasks", **params):
        return self.request("GET", f"/v1/{kind}", params={k: v for k, v in params.items() if v is not None})

    def get(self, kind, record_id, *, include_deleted=False):
        record_id = str(UUID(str(record_id)))
        return self.request("GET", f"/v1/{kind}/{record_id}", params={"include_deleted": include_deleted})

    def create(self, kind, value):
        model = parse_model(TaskCreate if kind == "tasks" else ProjectCreate, value)
        payload = model.model_dump(mode="json")
        return self.request("POST", f"/v1/{kind}", data=payload, record_id=payload["id"])

    def update(self, kind, record_id, revision, changes):
        record_id = str(UUID(str(record_id)))
        model = parse_model(TaskPatch if kind == "tasks" else ProjectPatch, changes)
        return self.request(
            "PATCH",
            f"/v1/{kind}/{record_id}",
            data=model.model_dump(mode="json", exclude_unset=True),
            revision=revision,
            record_id=record_id,
        )

    def delete(self, kind, record_id, revision, *, confirm=False, dry_run=True):
        record_id = str(UUID(str(record_id)))
        if dry_run:
            return self.request("GET", f"/v1/{kind}/{record_id}/delete-preview", revision=revision)
        if not confirm:
            raise TodoError("CONFIRMATION_REQUIRED")
        return self.request(
            "DELETE",
            f"/v1/{kind}/{record_id}",
            revision=revision,
            params={"confirm": True},
            record_id=record_id,
        )

    def restore(self, kind, record_id, revision):
        record_id = str(UUID(str(record_id)))
        return self.request("POST", f"/v1/{kind}/{record_id}/restore", revision=revision, record_id=record_id)

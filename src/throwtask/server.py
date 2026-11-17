import argparse
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import uvicorn
from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import AwareDatetime, ValidationError
from starlette.exceptions import HTTPException

from . import __version__
from .auth_models import PasskeyProof, RecoveryStart, RefreshCredential, RegistrationStart
from .errors import TodoError, success
from .models import (
    ExternalID,
    ProjectCreate,
    ProjectPatch,
    Source,
    TaskCreate,
    TaskPatch,
    TaskState,
    revision_from_header,
)
from .passkeys import PasskeyService, PasskeySettings
from .storage import Database, token_digest


def default_data_dir() -> Path:
    current = Path.home() / ".local/share/throwtask-server"
    candidates = (
        current,
        Path.home() / ".local/share/toidone-server",
        Path.home() / ".local/share/mytodo-server",
    )
    return next((path for path in candidates if path.exists() or path.is_symlink()), current)


def create_app(data_dir: Path | None = None, *, passkey_settings: PasskeySettings | None = None) -> FastAPI:
    database = Database(data_dir or default_data_dir())
    passkeys = PasskeyService(
        database, passkey_settings or PasskeySettings.load(database.path.parent / "auth.json")
    )

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        yield

    app = FastAPI(title="ThrowTask API", version=__version__, lifespan=lifespan, redirect_slashes=False)
    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or not re.fullmatch(r"(?:mtd|tta)_[A-Za-z0-9_-]{43}", credentials.credentials):
            raise TodoError("AUTHENTICATION_REQUIRED", 401)
        digest = token_digest(credentials.credentials)
        database.auth_status(digest)
        return digest

    Auth = Annotated[str, Depends(authenticate)]
    Match = Annotated[str | None, Header(alias="If-Match")]

    @app.exception_handler(TodoError)
    async def domain_error(request, error):
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else {}
        return JSONResponse(error.envelope(), status_code=error.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request, error):
        # Pydantic's default response echoes rejected input, which might contain a secret.
        return JSONResponse(TodoError("VALIDATION_ERROR", 422).envelope(), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse(
            TodoError("HTTP_ERROR", error.status_code).envelope(), status_code=error.status_code
        )

    @app.exception_handler(Exception)
    async def internal_error(request, error):
        return JSONResponse(TodoError("INTERNAL_ERROR", 500).envelope(), status_code=500)

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 1024 * 1024:
                    return JSONResponse(TodoError("BODY_TOO_LARGE", 413).envelope(), status_code=413)
                body.extend(chunk)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/healthz")
    def health():
        return success({"status": "ok", "version": __version__})

    @app.get("/v1/auth/status")
    def auth_status(auth: Auth):
        return success(database.auth_status(auth))

    def auth_limit(request: Request, *, maximum=30):
        address = request.client.host if request.client else "unknown"
        passkeys.limit(address + ":" + request.url.path, maximum)

    @app.get("/v1/auth/config")
    def auth_configuration():
        return success(passkeys.configuration())

    @app.get("/.well-known/apple-app-site-association")
    def associated_domains():
        settings = passkeys.configured()
        return {"webcredentials": {"apps": list(settings.apple_app_ids)}}

    @app.post("/v1/auth/register/options")
    def registration_options(payload: RegistrationStart, request: Request):
        auth_limit(request)
        return success(passkeys.registration_options(payload.invitation.get_secret_value(), payload.username))

    @app.post("/v1/auth/register/verify")
    def registration_verify(payload: PasskeyProof, request: Request):
        auth_limit(request)
        return success(passkeys.registration_verify(str(payload.challengeID), payload.credential))

    @app.post("/v1/auth/login/options")
    def login_options(request: Request):
        auth_limit(request)
        return success(passkeys.login_options())

    @app.post("/v1/auth/login/verify")
    def login_verify(payload: PasskeyProof, request: Request):
        auth_limit(request)
        return success(passkeys.login_verify(str(payload.challengeID), payload.credential))

    @app.post("/v1/auth/session/refresh")
    def session_refresh(payload: RefreshCredential, request: Request):
        auth_limit(request)
        return success(passkeys.refresh(payload.refreshToken.get_secret_value()))

    @app.post("/v1/auth/session/logout")
    def session_logout(payload: RefreshCredential):
        return success(passkeys.logout(payload.refreshToken.get_secret_value()))

    @app.post("/v1/auth/recovery/options")
    def recovery_options(payload: RecoveryStart, request: Request):
        auth_limit(request, maximum=10)
        return success(passkeys.recovery_options(payload.username, payload.recoveryCode.get_secret_value()))

    @app.post("/v1/auth/recovery-codes")
    def recovery_codes(auth: Auth):
        return success(passkeys.regenerate_codes(auth))

    @app.get("/v1/snapshot")
    def snapshot(auth: Auth):
        return success(database.snapshot(auth))

    @app.get("/v1/changes")
    def changes(
        auth: Auth,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        since: AwareDatetime | None = None,
    ):
        return success(database.changes(auth, after, limit, since=since))

    @app.get("/v1/tasks")
    def list_tasks(
        auth: Auth,
        q: Annotated[str, Query(max_length=500)] = "",
        status: Literal["all", "open", "completed"] = "all",
        state: TaskState | None = None,
        external_id: ExternalID | None = None,
        source: Source | None = None,
        project_id: UUID | None = None,
        after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        include_deleted: bool = False,
    ):
        return success(
            database.list(
                auth,
                "task",
                query=q,
                status=status,
                state=state,
                external_id=external_id,
                source=source,
                project_id=str(project_id) if project_id else None,
                after=str(after) if after else "",
                limit=limit,
                include_deleted=include_deleted,
            )
        )

    @app.get("/v1/projects")
    def list_projects(
        auth: Auth,
        q: Annotated[str, Query(max_length=500)] = "",
        after: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        include_deleted: bool = False,
    ):
        return success(
            database.list(
                auth,
                "project",
                query=q,
                after=str(after) if after else "",
                limit=limit,
                include_deleted=include_deleted,
            )
        )

    def register_resource(kind, path, create_model, patch_model):
        def created(payload: create_model, response: Response, auth: Auth):
            data = database.create(auth, kind, payload.model_dump(mode="json"))
            response.headers["ETag"] = f'"{data["revision"]}"'
            return success(data)

        def retrieved(record_id: UUID, response: Response, auth: Auth, include_deleted: bool = False):
            data = database.get(auth, kind, str(record_id), include_deleted=include_deleted)
            response.headers["ETag"] = f'"{data["revision"]}"'
            return success(data)

        def patched(
            record_id: UUID, payload: patch_model, response: Response, auth: Auth, if_match: Match = None
        ):
            data = database.update(
                auth,
                kind,
                str(record_id),
                revision_from_header(if_match),
                payload.model_dump(mode="json", exclude_unset=True),
            )
            response.headers["ETag"] = f'"{data["revision"]}"'
            return success(data)

        def preview(record_id: UUID, auth: Auth, if_match: Match = None):
            return success(
                database.preview_delete(auth, kind, str(record_id), revision_from_header(if_match))
            )

        def deleted(record_id: UUID, auth: Auth, if_match: Match = None, confirm: bool = False):
            return success(
                database.delete(auth, kind, str(record_id), revision_from_header(if_match), confirm=confirm)
            )

        def restored(record_id: UUID, auth: Auth, if_match: Match = None):
            return success(database.restore(auth, kind, str(record_id), revision_from_header(if_match)))

        for suffix, method, endpoint in [
            ("", "POST", created),
            ("/{record_id}", "GET", retrieved),
            ("/{record_id}", "PATCH", patched),
            ("/{record_id}/delete-preview", "GET", preview),
            ("/{record_id}", "DELETE", deleted),
            ("/{record_id}/restore", "POST", restored),
        ]:
            app.add_api_route(path + suffix, endpoint, methods=[method], name=f"{kind}_{endpoint.__name__}")

    register_resource("task", "/v1/tasks", TaskCreate, TaskPatch)
    register_resource("project", "/v1/projects", ProjectCreate, ProjectPatch)
    return app


def main():
    parser = argparse.ArgumentParser(description="ThrowTask self-hosted API")
    parser.add_argument("--version", action="version", version=f"ThrowTask {__version__}")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.data_dir),
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="warning",
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()

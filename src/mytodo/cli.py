import argparse
import json
import sys
from getpass import getpass
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .client import ApiClient
from .config import Settings, default_config, keyring_backend, validate_token
from .errors import TodoError, success


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes command-line values.
        print(json.dumps(TodoError("INVALID_ARGUMENTS").envelope()))
        raise SystemExit(2)


def parser():
    root = Parser(description="MyToDo CLI")
    root.add_argument("--config", type=Path, default=default_config())
    root.add_argument("--json", action="store_true")
    groups = root.add_subparsers(dest="group", required=True)
    configure = groups.add_parser("configure")
    configure.add_argument("--url", required=True)
    configure.add_argument("--credential-file", type=Path)
    configure.add_argument("--allow-insecure-http", action="store_true")
    auth = groups.add_parser("auth").add_subparsers(dest="action", required=True)
    auth.add_parser("status")
    groups.add_parser("snapshot")
    changes = groups.add_parser("changes")
    changes.add_argument("--after", type=int, default=0)
    changes.add_argument("--limit", type=int, default=100)
    for kind in ("tasks", "projects"):
        actions = groups.add_parser(kind).add_subparsers(dest="action", required=True)
        listing = actions.add_parser("list")
        listing.add_argument("--query", default="")
        listing.add_argument("--after", type=UUID)
        listing.add_argument("--limit", type=int, default=100)
        listing.add_argument("--include-deleted", action="store_true")
        if kind == "tasks":
            listing.add_argument("--status", choices=("all", "open", "completed"), default="all")
            listing.add_argument("--project", type=UUID)
        create = actions.add_parser("create")
        create.add_argument("title", nargs="?")
        create.add_argument("--id", type=UUID)
        create.add_argument("--data", type=Path, help="JSON fields; '-' reads stdin")
        create.add_argument("--notes")
        edit = actions.add_parser("edit")
        edit.add_argument("id", type=UUID)
        edit.add_argument("--revision", type=int, required=True)
        edit.add_argument("--title")
        edit.add_argument("--notes")
        edit.add_argument("--data", type=Path)
        edit.add_argument(
            "--clear",
            action="append",
            default=[],
            choices=(
                "scheduledDay",
                "deadlineDay",
                "deadlineTime",
                "deadlineTimeZone",
                "projectID",
                "sharedRepeat",
                "scheduledRepeat",
                "deadlineRepeat",
                "notes",
            ),
        )
        if kind == "tasks":
            for command in (create, edit):
                command.add_argument("--when")
                command.add_argument("--deadline")
                command.add_argument("--project", type=UUID)
                command.add_argument("--tag", action="append")
                command.add_argument("--someday", action=argparse.BooleanOptionalAction, default=None)
        get = actions.add_parser("get")
        get.add_argument("id", type=UUID)
        get.add_argument("--include-deleted", action="store_true")
        for action in (("complete", "reopen") if kind == "tasks" else ()) + ("delete", "restore"):
            sub = actions.add_parser(action)
            sub.add_argument("id", type=UUID)
            sub.add_argument("--revision", type=int, required=True)
            if action == "delete":
                sub.add_argument("--confirm", action="store_true")
                sub.add_argument("--dry-run", action="store_true")
        for sub in actions.choices.values():
            sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return root


def fields(args):
    value = {}
    path = getattr(args, "data", None)
    if path:
        value = json.loads(sys.stdin.read() if str(path) == "-" else path.read_text())
        if not isinstance(value, dict):
            raise TodoError("VALIDATION_ERROR")
    for option, key in (
        ("title", "title"),
        ("notes", "notes"),
        ("when", "scheduledDay"),
        ("deadline", "deadlineDay"),
        ("project", "projectID"),
        ("tag", "tags"),
        ("someday", "isSomeday"),
    ):
        content = getattr(args, option, None)
        if content is not None:
            value[key] = str(content) if isinstance(content, UUID) else content
    for key in getattr(args, "clear", []):
        value[key] = "" if key == "notes" and args.group == "tasks" else None
    if args.action == "create" and args.id:
        value["id"] = str(args.id)
    return value


def execute(args):
    if args.group == "configure":
        settings = Settings(args.url, args.credential_file, args.allow_insecure_http)
        if args.credential_file is not None:
            settings.credential()
        else:
            if not sys.stdin.isatty():
                raise TodoError("INTERACTIVE_CONFIGURATION_REQUIRED")
            token = validate_token(getpass("API key (hidden): ").strip())
            try:
                keyring_backend().set_password(settings.keyring_service, "api", token)
            except Exception:
                raise TodoError("KEYRING_UNAVAILABLE") from None
        settings.save(args.config)
        return {"configured": True, "url": settings.url}
    client = ApiClient.from_config(args.config)
    if args.group == "auth":
        return client.request("GET", "/v1/auth/status")
    if args.group == "snapshot":
        return client.request("GET", "/v1/snapshot")
    if args.group == "changes":
        return client.request("GET", "/v1/changes", params={"after": args.after, "limit": args.limit})
    kind = args.group
    if args.action == "list":
        return client.list(
            kind,
            q=args.query,
            after=str(args.after) if args.after else None,
            limit=args.limit,
            include_deleted=args.include_deleted,
            status=getattr(args, "status", None),
            project_id=str(args.project) if getattr(args, "project", None) else None,
        )
    if args.action == "get":
        return client.get(kind, args.id, include_deleted=args.include_deleted)
    if args.action == "create":
        return client.create(kind, fields(args))
    if args.action == "edit":
        return client.update(kind, args.id, args.revision, fields(args))
    if args.action in {"complete", "reopen"}:
        return client.update(kind, args.id, args.revision, {"isCompleted": args.action == "complete"})
    if args.action == "delete":
        return client.delete(
            kind, args.id, args.revision, confirm=args.confirm, dry_run=args.dry_run or not args.confirm
        )
    return client.restore(kind, args.id, args.revision)


def main():
    args = parser().parse_args()
    try:
        result, code = success(execute(args)), 0
    except TodoError as error:
        local_errors = {
            "CONFIGURATION_REQUIRED",
            "INVALID_SERVER_URL",
            "HTTPS_REQUIRED",
            "CREDENTIAL_UNAVAILABLE",
            "UNSAFE_CREDENTIAL_FILE",
            "INVALID_CREDENTIAL",
            "KEYRING_UNAVAILABLE",
            "INTERACTIVE_CONFIGURATION_REQUIRED",
            "INVALID_REVISION",
        }
        result, code = error.envelope(), 2 if error.code in local_errors else 1
    except (ValidationError, ValueError, TypeError, OSError):
        result, code = TodoError("VALIDATION_ERROR").envelope(), 2
    except Exception:
        result, code = TodoError("LOCAL_ERROR").envelope(), 2
    print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

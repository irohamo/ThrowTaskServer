import argparse
import json
from pathlib import Path
from uuid import UUID

from . import __version__
from .errors import TodoError, success
from .server import default_data_dir
from .storage import Database


def main():
    parser = argparse.ArgumentParser(description="ToiDone server administration (never prints API keys)")
    parser.add_argument("--version", action="version", version=f"ToiDone {__version__}")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    token = commands.add_parser("token").add_subparsers(dest="action", required=True)
    create = token.add_parser("create")
    create.add_argument("label")
    create.add_argument("--output", type=Path, required=True, help="New owner-only credential file")
    token.add_parser("list")
    revoke = token.add_parser("revoke")
    revoke.add_argument("id", type=UUID)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    try:
        database = Database(args.data_dir)
        if args.command == "restore":
            data = database.restore_backup(args.backup)
        else:
            database.initialize()
            if args.command == "init":
                data = {"initialized": True}
            elif args.command == "backup":
                data = database.backup(args.output)
            elif args.action == "create":
                data = database.issue_token(args.label, args.output)
            elif args.action == "list":
                data = database.list_tokens()
            else:
                data = database.revoke_token(str(args.id))
        result, code = success(data), 0
    except TodoError as error:
        result, code = error.envelope(), 1
    except (OSError, ValueError):
        result, code = TodoError("LOCAL_IO_ERROR").envelope(), 2
    except Exception:
        result, code = TodoError("ADMIN_ERROR").envelope(), 2
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

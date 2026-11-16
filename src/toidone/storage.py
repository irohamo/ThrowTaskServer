import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from .errors import TodoError
from .models import ProjectCreate, ProjectFields, TaskCreate, TaskFields, parse_model
from .recurrence import next_occurrence

TASK_DEFAULTS = {"externalID": None, "source": None, "state": "active"}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(body) -> str:
    # Keep retries of pre-extension creates compatible with their stored fingerprints.
    value = dict(body)
    for key, default in TASK_DEFAULTS.items():
        if value.get(key, default) == default:
            value.pop(key, None)
    return hashlib.sha256(encode(value).encode()).hexdigest()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Database:
    def __init__(self, data_dir: Path):
        current = data_dir / "toidone.sqlite3"
        legacy = data_dir / "mytodo.sqlite3"
        # Open an existing database in place so WAL files and issued keys stay together.
        current_present = current.exists() or current.is_symlink()
        legacy_present = legacy.exists() or legacy.is_symlink()
        self.path = legacy if not current_present and legacy_present else current

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise TodoError("UNSAFE_DATABASE_PATH")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise TodoError("UNSUPPORTED_SCHEMA")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tokens (
                    id TEXT PRIMARY KEY, label TEXT NOT NULL, digest TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                    body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    deleted_at TEXT, fingerprint TEXT NOT NULL, PRIMARY KEY(kind,id)
                );
                CREATE TABLE IF NOT EXISTS changes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    record_id TEXT NOT NULL, record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recurrence_links (
                    parent_id TEXT PRIMARY KEY, child_id TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS task_external_id
                    ON records(json_extract(body,'$.externalID'))
                    WHERE kind='task' AND json_extract(body,'$.externalID') IS NOT NULL;
                PRAGMA user_version=1;
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def transaction(self, digest: str | None = None, *, write=False):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if digest is not None:
                token = db.execute(
                    "SELECT id,label FROM tokens WHERE digest=? AND revoked_at IS NULL", (digest,)
                ).fetchone()
                if token is None:
                    raise TodoError("AUTHENTICATION_REQUIRED", 401)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def issue_token(self, label: str, output: Path) -> dict:
        if not label.strip() or len(label) > 100:
            raise TodoError("INVALID_LABEL")
        # A separate credential for each device can be revoked independently.
        token = "mtd_" + secrets.token_urlsafe(32)
        token_id = str(uuid4())
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(token + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with self.transaction(write=True) as db:
                db.execute(
                    "INSERT INTO tokens VALUES (?,?,?,?,NULL)", (token_id, label, token_digest(token), now())
                )
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        return {"id": token_id, "label": label, "credentialFile": str(output), "expiresAt": None}

    def list_tokens(self):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,label,created_at AS createdAt,revoked_at AS revokedAt "
                    "FROM tokens ORDER BY created_at"
                )
            ]

    def revoke_token(self, token_id: str):
        with self.transaction(write=True) as db:
            if not db.execute("SELECT id FROM tokens WHERE id=?", (token_id,)).fetchone():
                raise TodoError("NOT_FOUND", 404)
            db.execute("UPDATE tokens SET revoked_at=COALESCE(revoked_at,?) WHERE id=?", (now(), token_id))
        return {"id": token_id, "revoked": True}

    def auth_status(self, digest):
        with self.transaction(digest) as db:
            row = db.execute("SELECT id,label FROM tokens WHERE digest=?", (digest,)).fetchone()
            return {"authenticated": True, **dict(row), "expiresAt": None}

    @staticmethod
    def materialize(row):
        return {
            **(TASK_DEFAULTS if row["kind"] == "task" else {}),
            **json.loads(row["body"]),
            "id": row["id"],
            "revision": row["revision"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "deletedAt": row["deleted_at"],
        }

    @staticmethod
    def _row(db, kind, record_id, *, include_deleted=False):
        row = db.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        if row is None or (row["deleted_at"] is not None and not include_deleted):
            raise TodoError("NOT_FOUND", 404)
        return row

    @staticmethod
    def _check_revision(row, revision):
        if row["revision"] != revision:
            raise TodoError("CONFLICT", 412, currentRevision=row["revision"])

    def _emit(self, db, kind, record_id):
        record = self.materialize(self._row(db, kind, record_id, include_deleted=True))
        db.execute(
            "INSERT INTO changes(kind,record_id,record) VALUES (?,?,?)", (kind, record_id, encode(record))
        )
        return record

    def _check_project(self, db, kind, body):
        if kind == "task" and body.get("projectID"):
            try:
                self._row(db, "project", body["projectID"])
            except TodoError:
                raise TodoError("PROJECT_NOT_FOUND", 409) from None

    @staticmethod
    def _check_external_id(db, kind, body, record_id):
        if kind == "task" and body.get("externalID") is not None:
            existing = db.execute(
                "SELECT id FROM records WHERE kind='task' AND json_extract(body,'$.externalID')=? AND id<>?",
                (body["externalID"], record_id),
            ).fetchone()
            if existing is not None:
                raise TodoError("EXTERNAL_ID_ALREADY_EXISTS", 409, existingRecordId=existing["id"])

    def create(self, digest, kind, value):
        model = parse_model(TaskCreate if kind == "task" else ProjectCreate, value)
        body = model.model_dump(mode="json")
        record_id = body.pop("id")
        creation_fingerprint = fingerprint(body)
        with self.transaction(digest, write=True) as db:
            existing = db.execute("SELECT * FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
            if existing is not None:
                if existing["fingerprint"] != creation_fingerprint or existing["deleted_at"] is not None:
                    raise TodoError("ID_ALREADY_EXISTS", 409, recordId=record_id)
                return self.materialize(existing)
            self._check_project(db, kind, body)
            self._check_external_id(db, kind, body, record_id)
            timestamp = now()
            db.execute(
                "INSERT INTO records VALUES (?,?,1,?,?,?,NULL,?)",
                (kind, record_id, encode(body), timestamp, timestamp, creation_fingerprint),
            )
            return self._emit(db, kind, record_id)

    def get(self, digest, kind, record_id, *, include_deleted=False):
        with self.transaction(digest) as db:
            return self.materialize(self._row(db, kind, record_id, include_deleted=include_deleted))

    def list(
        self,
        digest,
        kind,
        *,
        query="",
        status="all",
        state=None,
        external_id=None,
        source=None,
        project_id=None,
        after="",
        limit=100,
        include_deleted=False,
    ):
        with self.transaction(digest) as db:
            rows = db.execute("SELECT * FROM records WHERE kind=? AND id>? ORDER BY id", (kind, after))
            items = []
            for row in rows:
                record = self.materialize(row)
                if record["deletedAt"] and not include_deleted:
                    continue
                if query.casefold() not in (record["title"] + "\n" + (record.get("notes") or "")).casefold():
                    continue
                if kind == "task":
                    if status != "all" and record["isCompleted"] != (status == "completed"):
                        continue
                    if state is not None and record["state"] != state:
                        continue
                    if external_id is not None and record["externalID"] != external_id:
                        continue
                    if source is not None and record["source"] != source:
                        continue
                    if project_id is not None and record["projectID"] != project_id:
                        continue
                items.append(record)
                if len(items) > limit:
                    break
            return {
                "items": items[:limit],
                "nextCursor": items[limit - 1]["id"] if len(items) > limit else None,
            }

    def update(self, digest, kind, record_id, revision, changes):
        if not changes:
            raise TodoError("NO_CHANGES")
        with self.transaction(digest, write=True) as db:
            row = self._row(db, kind, record_id)
            self._check_revision(row, revision)
            body = {**json.loads(row["body"]), **changes}
            model = parse_model(TaskFields if kind == "task" else ProjectFields, body)
            body = model.model_dump(mode="json")
            self._check_project(db, kind, body)
            self._check_external_id(db, kind, body, record_id)
            if encode(body) == row["body"]:
                return self.materialize(row)
            db.execute(
                "UPDATE records SET body=?,revision=revision+1,updated_at=? WHERE kind=? AND id=?",
                (encode(body), now(), kind, record_id),
            )
            result = self._emit(db, kind, record_id)
            if kind == "task" and body["isCompleted"] != json.loads(row["body"])["isCompleted"]:
                self._transition_recurrence(db, record_id, body)
            return result

    def _transition_recurrence(self, db, record_id, body):
        link = db.execute("SELECT * FROM recurrence_links WHERE parent_id=?", (record_id,)).fetchone()
        if not body["isCompleted"]:
            if link is None:
                return
            child = self._row(db, "task", link["child_id"], include_deleted=True)
            if child["revision"] == 1 and child["deleted_at"] is None:
                timestamp = now()
                db.execute(
                    "UPDATE records SET deleted_at=?,updated_at=?,revision=revision+1 "
                    "WHERE kind='task' AND id=?",
                    (timestamp, timestamp, link["child_id"]),
                )
                self._emit(db, "task", link["child_id"])
                db.execute("UPDATE recurrence_links SET undone=1 WHERE parent_id=?", (record_id,))
            return
        if link is not None and not link["undone"]:
            return
        if link is None:
            # Imported local histories may already contain the following occurrence.
            for existing in db.execute("SELECT * FROM records WHERE kind='task' AND deleted_at IS NULL"):
                if json.loads(existing["body"]).get("recurrenceParentID") == record_id:
                    db.execute("INSERT INTO recurrence_links VALUES (?,?,0)", (record_id, existing["id"]))
                    return
        try:
            today = datetime.now(ZoneInfo(body.get("deadlineTimeZone") or "UTC")).date()
            following = next_occurrence(body, today)
            if following is None:
                return
            following["recurrenceParentID"] = record_id
            following = parse_model(TaskFields, following).model_dump(mode="json")
        except (ValueError, OverflowError):
            raise TodoError("RECURRENCE_RANGE_EXCEEDED", 409) from None
        child_id = str(uuid4())
        timestamp = now()
        creation_fingerprint = fingerprint(following)
        db.execute(
            "INSERT INTO records VALUES ('task',?,1,?,?,?,NULL,?)",
            (child_id, encode(following), timestamp, timestamp, creation_fingerprint),
        )
        db.execute(
            "INSERT INTO recurrence_links VALUES (?,?,0) ON CONFLICT(parent_id) "
            "DO UPDATE SET child_id=excluded.child_id,undone=0",
            (record_id, child_id),
        )
        self._emit(db, "task", child_id)

    def preview_delete(self, digest, kind, record_id, revision):
        with self.transaction(digest) as db:
            row = self._row(db, kind, record_id)
            self._check_revision(row, revision)
            detached = []
            if kind == "project":
                detached = [
                    r["id"]
                    for r in db.execute(
                        "SELECT id,body FROM records WHERE kind='task' AND deleted_at IS NULL"
                    )
                    if json.loads(r["body"]).get("projectID") == record_id
                ]
            return {"wouldDelete": self.materialize(row), "detachedTaskIds": detached}

    def delete(self, digest, kind, record_id, revision, *, confirm):
        if not confirm:
            raise TodoError("CONFIRMATION_REQUIRED")
        with self.transaction(digest, write=True) as db:
            row = self._row(db, kind, record_id)
            self._check_revision(row, revision)
            if kind == "project":
                for task in db.execute(
                    "SELECT * FROM records WHERE kind='task' AND deleted_at IS NULL"
                ).fetchall():
                    body = json.loads(task["body"])
                    if body.get("projectID") == record_id:
                        body["projectID"] = None
                        db.execute(
                            "UPDATE records SET body=?,revision=revision+1,updated_at=? "
                            "WHERE kind='task' AND id=?",
                            (encode(body), now(), task["id"]),
                        )
                        self._emit(db, "task", task["id"])
            timestamp = now()
            db.execute(
                "UPDATE records SET deleted_at=?,updated_at=?,revision=revision+1 WHERE kind=? AND id=?",
                (timestamp, timestamp, kind, record_id),
            )
            return self._emit(db, kind, record_id)

    def restore(self, digest, kind, record_id, revision):
        with self.transaction(digest, write=True) as db:
            row = self._row(db, kind, record_id, include_deleted=True)
            self._check_revision(row, revision)
            if row["deleted_at"] is None:
                return self.materialize(row)
            self._check_project(db, kind, json.loads(row["body"]))
            db.execute(
                "UPDATE records SET deleted_at=NULL,updated_at=?,revision=revision+1 WHERE kind=? AND id=?",
                (now(), kind, record_id),
            )
            return self._emit(db, kind, record_id)

    def snapshot(self, digest):
        with self.transaction(digest) as db:
            records = [
                self.materialize(row) | {"kind": row["kind"]}
                for row in db.execute("SELECT * FROM records ORDER BY kind,id")
            ]
            cursor = db.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
            return {"records": records, "cursor": cursor}

    def changes(self, digest, after, limit, *, since=None):
        since_utc = (
            since.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z") if since else None
        )
        with self.transaction(digest) as db:
            latest = db.execute("SELECT COALESCE(MAX(seq),0) FROM changes").fetchone()[0]
            if after > latest:
                raise TodoError("CURSOR_INVALID", 409)
            rows = db.execute(
                "SELECT * FROM changes WHERE seq>? AND (? IS NULL OR json_extract(record,'$.updatedAt')>=?) "
                "ORDER BY seq LIMIT ?",
                (after, since_utc, since_utc, limit + 1),
            ).fetchall()
            page = rows[:limit]
            return {
                "items": [
                    {
                        "cursor": r["seq"],
                        "kind": r["kind"],
                        "record": {
                            **(TASK_DEFAULTS if r["kind"] == "task" else {}),
                            **json.loads(r["record"]),
                        },
                    }
                    for r in page
                ],
                "cursor": page[-1]["seq"] if len(rows) > limit else latest,
                "hasMore": len(rows) > limit,
                "latestCursor": latest,
            }

    def backup(self, output: Path):
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            source = self.connect()
            destination = sqlite3.connect(output)
            try:
                source.backup(destination)
            finally:
                source.close()
                destination.close()
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        return {"backup": str(output)}

    def restore_backup(self, source: Path):
        if self.path.exists():
            raise TodoError("RESTORE_TARGET_EXISTS")
        origin = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if origin.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise TodoError("UNSUPPORTED_SCHEMA")
            if origin.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise TodoError("INVALID_BACKUP")
            tables = {row[0] for row in origin.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"tokens", "records", "changes"} <= tables:
                raise TodoError("INVALID_BACKUP")
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            destination = sqlite3.connect(self.path)
            try:
                origin.backup(destination)
            finally:
                destination.close()
        finally:
            origin.close()
        return {"restored": True}

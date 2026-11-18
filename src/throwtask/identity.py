"""User boundaries and the additive migration from the single-owner database."""

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from .errors import TodoError


class ScopedConnection(sqlite3.Connection):
    owner_id: str | None = None
    principal: dict | None = None


def timestamp():
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def legacy_owner(db):
    return db.execute("SELECT id FROM users WHERE is_legacy=1").fetchone()[0]


def migrate(db):
    """One transaction preserves UUIDs, creation fingerprints, keys and legacy change cursors."""
    db.execute("BEGIN IMMEDIATE")
    try:
        owner = str(uuid4())
        db.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, "
            "created_at TEXT NOT NULL, is_legacy INTEGER NOT NULL DEFAULT 0)"
        )
        db.execute("INSERT INTO users VALUES (?,NULL,?,1)", (owner, timestamp()))
        db.execute(f"ALTER TABLE tokens ADD COLUMN owner_id TEXT NOT NULL DEFAULT '{owner}'")
        db.execute("ALTER TABLE tokens ADD COLUMN can_write INTEGER NOT NULL DEFAULT 1")
        db.execute("DROP INDEX IF EXISTS task_external_id")
        db.execute("ALTER TABLE records RENAME TO records_v1")
        db.execute(
            "CREATE TABLE records (owner_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL, "
            "id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, deleted_at TEXT, fingerprint TEXT NOT NULL, "
            "PRIMARY KEY(owner_id,kind,id))"
        )
        db.execute(
            "INSERT INTO records SELECT ?,kind,id,revision,body,created_at,updated_at,deleted_at,fingerprint "
            "FROM records_v1",
            (owner,),
        )
        db.execute("DROP TABLE records_v1")
        db.execute(
            "CREATE UNIQUE INDEX task_external_id_owner ON "
            "records(owner_id,json_extract(body,'$.externalID')) "
            "WHERE kind='task' AND json_extract(body,'$.externalID') IS NOT NULL"
        )
        db.execute("ALTER TABLE changes RENAME TO changes_v1")
        db.execute(
            "CREATE TABLE changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT "
            "NOT NULL REFERENCES users(id), "
            "owner_seq INTEGER NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL, record TEXT NOT NULL, "
            "UNIQUE(owner_id,owner_seq))"
        )
        db.execute("INSERT INTO changes SELECT seq,?,seq,kind,record_id,record FROM changes_v1", (owner,))
        db.execute("DROP TABLE changes_v1")
        db.execute("ALTER TABLE recurrence_links RENAME TO recurrence_links_v1")
        db.execute(
            "CREATE TABLE recurrence_links (owner_id TEXT NOT NULL REFERENCES "
            "users(id), parent_id TEXT NOT NULL, "
            "child_id TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(owner_id,parent_id))"
        )
        db.execute(
            "INSERT INTO recurrence_links SELECT ?,parent_id,child_id,undone FROM recurrence_links_v1",
            (owner,),
        )
        db.execute("DROP TABLE recurrence_links_v1")
        statements = [
            "CREATE TABLE auth_limits (id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
            "attempts INTEGER NOT NULL)",
            "CREATE TABLE invitations (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), "
            "digest TEXT NOT NULL UNIQUE, purpose TEXT NOT NULL, expires_at TEXT NOT NULL, used_at TEXT)",
            "CREATE TABLE challenges (id TEXT PRIMARY KEY, challenge BLOB NOT NULL, purpose TEXT NOT NULL, "
            "owner_id TEXT REFERENCES users(id), invitation_id TEXT, username TEXT, "
            "expires_at TEXT NOT NULL, used_at TEXT, recovery_digest TEXT)",
            "CREATE TABLE passkeys (credential_id TEXT PRIMARY KEY, owner_id TEXT NOT "
            "NULL REFERENCES users(id), "
            "public_key BLOB NOT NULL, sign_count INTEGER NOT NULL, created_at TEXT NOT "
            "NULL, revoked_at TEXT)",
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), "
            "access_digest TEXT NOT NULL UNIQUE, refresh_digest TEXT NOT NULL UNIQUE, "
            "access_expires_at TEXT NOT NULL, "
            "refresh_expires_at TEXT NOT NULL, authenticated_at TEXT NOT NULL, revoked_at TEXT)",
            "CREATE TABLE used_refreshes (digest TEXT PRIMARY KEY, session_id TEXT NOT "
            "NULL REFERENCES sessions(id))",
            "CREATE TABLE recovery_codes (digest TEXT PRIMARY KEY, owner_id TEXT NOT "
            "NULL REFERENCES users(id), "
            "created_at TEXT NOT NULL, used_at TEXT)",
            "CREATE INDEX challenge_expiry ON challenges(expires_at)",
        ]
        for statement in statements:
            db.execute(statement)
        db.execute("PRAGMA user_version=2")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def resolve(db, digest, *, write=False):
    row = db.execute(
        "SELECT id,label,owner_id,can_write FROM tokens WHERE digest=? AND revoked_at IS NULL", (digest,)
    ).fetchone()
    if row is None:
        raise TodoError("AUTHENTICATION_REQUIRED", 401)
    principal = {**dict(row), "method": "apiKey", "expiresAt": None}
    user = db.execute(
        "SELECT id,username,is_legacy FROM users WHERE id=?", (principal["owner_id"],)
    ).fetchone()
    if user is None:
        raise TodoError("AUTHENTICATION_REQUIRED", 401)
    if write and not principal["can_write"]:
        raise TodoError("FORBIDDEN", 403)
    principal["user"] = {"id": user["id"], "username": user["username"], "isLegacy": bool(user["is_legacy"])}
    db.owner_id = principal["owner_id"]
    db.principal = principal
    return principal

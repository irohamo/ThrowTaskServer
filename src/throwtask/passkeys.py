"""Invite-bound passkeys and revocable device sessions; no secrets are written to logs."""

import hashlib
import ipaddress
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .errors import TodoError
from .identity import legacy_owner, timestamp


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def future(seconds):
    return (
        (datetime.now(UTC) + timedelta(seconds=seconds))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def user_name(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", value):
        raise TodoError("INVALID_USERNAME", 422)
    return value


@dataclass(frozen=True)
class PasskeySettings:
    rp_id: str
    server_url: str
    origins: tuple[str, ...]
    apple_app_ids: tuple[str, ...] = ()

    def __post_init__(self):
        try:
            host = self.rp_id
            if not isinstance(host, str) or not isinstance(self.server_url, str):
                raise ValueError
            if not isinstance(self.origins, tuple) or not isinstance(self.apple_app_ids, tuple):
                raise ValueError
            if (
                host != host.lower()
                or len(host) > 253
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            ):
                raise ValueError
            if any(
                not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
                for part in host.split(".")
            ):
                raise ValueError
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError
            server = urlsplit(self.server_url)
            _ = server.port
            if not server.hostname or server.username or server.password or server.query or server.fragment:
                raise ValueError
            if server.scheme != "https" and not (
                server.scheme == "http" and server.hostname in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError
            if not self.origins:
                raise ValueError
            for origin in self.origins:
                if not isinstance(origin, str):
                    raise ValueError
                parsed = urlsplit(origin)
                _ = parsed.port
                if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
                    raise ValueError
                if parsed.hostname != host and not (parsed.hostname or "").endswith("." + host):
                    raise ValueError
                if parsed.scheme != "https" and not (parsed.scheme == "http" and host == "localhost"):
                    raise ValueError
            if any(
                not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{10}\.[A-Za-z0-9.-]+", value)
                for value in self.apple_app_ids
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise TodoError("INVALID_PASSKEY_CONFIGURATION") from None

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text())
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("origins"), list)
                or not isinstance(value.get("appleAppIDs", []), list)
            ):
                raise ValueError
            if set(value) - {"rpID", "serverURL", "origins", "appleAppIDs"}:
                raise ValueError
            return cls(
                value["rpID"],
                value["serverURL"],
                tuple(value["origins"]),
                tuple(value.get("appleAppIDs", [])),
            )
        except (ValueError, TypeError, KeyError, OSError):
            raise TodoError("INVALID_PASSKEY_CONFIGURATION") from None


class PasskeyService:
    def __init__(self, database, settings: PasskeySettings | None):
        self.database, self.settings = database, settings

    def configured(self):
        if self.settings is None:
            raise TodoError("PASSKEY_NOT_CONFIGURED", 503)
        return self.settings

    def configuration(self):
        return {
            "passkeysEnabled": self.settings is not None,
            "rpID": self.settings.rp_id if self.settings else None,
        }

    def limit(self, bucket, maximum=30):
        key = digest(bucket)
        with self.database.transaction(write=True) as db:
            cutoff = future(-300)
            db.execute("DELETE FROM auth_limits WHERE started_at<?", (cutoff,))
            row = db.execute("SELECT attempts FROM auth_limits WHERE id=?", (key,)).fetchone()
            if row and row["attempts"] >= maximum:
                raise TodoError("AUTH_RATE_LIMITED", 429)
            db.execute(
                "INSERT INTO auth_limits VALUES (?,?,1) ON CONFLICT(id) DO UPDATE SET attempts=attempts+1",
                (key, timestamp()),
            )

    def issue_invitation(
        self, output: Path, *, claim_legacy=False, qr_output: Path | None = None, lifetime=1800
    ):
        settings = self.configured()
        if not 60 <= lifetime <= 86400:
            raise TodoError("INVALID_INVITATION_LIFETIME")
        invitation_id, secret = str(uuid4()), "tti_" + secrets.token_urlsafe(32)
        expires = future(lifetime)
        created = []
        try:
            with self.database.transaction(write=True) as db:
                if claim_legacy:
                    owner = legacy_owner(db)
                    if (
                        db.execute("SELECT username FROM users WHERE id=?", (owner,)).fetchone()[0]
                        is not None
                    ):
                        raise TodoError("LEGACY_OWNER_ALREADY_CLAIMED", 409)
                else:
                    owner = str(uuid4())
                    db.execute("INSERT INTO users VALUES (?,NULL,?,0)", (owner, timestamp()))
                payload = {
                    "version": 1,
                    "serverURL": settings.server_url,
                    "invitation": secret,
                    "expiresAt": expires,
                }
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created.append(output)
                with os.fdopen(fd, "w") as stream:
                    stream.write(text + "\n")
                if qr_output is not None:
                    import qrcode

                    fd = os.open(qr_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    created.append(qr_output)
                    with os.fdopen(fd, "wb") as stream:
                        qrcode.make(text).save(stream, format="PNG")
                db.execute(
                    "UPDATE invitations SET used_at=? WHERE owner_id=? AND used_at IS NULL",
                    (timestamp(), owner),
                )
                db.execute(
                    "INSERT INTO invitations VALUES (?,?,?,'register',?,NULL)",
                    (invitation_id, owner, digest(secret), expires),
                )
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return {
            "id": invitation_id,
            "userID": owner,
            "expiresAt": expires,
            "invitationFile": str(output),
            "qrFile": str(qr_output) if qr_output else None,
        }

    def users(self):
        with self.database.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,username,is_legacy AS isLegacy,created_at AS createdAt FROM "
                    "users ORDER BY created_at,id"
                )
            ]

    def _challenge(self, db, purpose, *, owner=None, invitation=None, username=None, recovery=None):
        db.execute("DELETE FROM challenges WHERE expires_at<?", (timestamp(),))
        challenge_id, value = str(uuid4()), secrets.token_bytes(32)
        db.execute(
            "INSERT INTO challenges VALUES (?,?,?,?,?,?,?,NULL,?)",
            (challenge_id, value, purpose, owner, invitation, username, future(300), recovery),
        )
        return challenge_id, value

    def _registration_options(
        self, db, owner, username, *, invitation=None, purpose="register", recovery=None
    ):
        settings = self.configured()
        challenge_id, challenge = self._challenge(
            db, purpose, owner=owner, invitation=invitation, username=username, recovery=recovery
        )
        options = generate_registration_options(
            rp_id=settings.rp_id,
            rp_name="ThrowTask",
            user_id=UUID(owner).bytes,
            user_name=username,
            challenge=challenge,
            timeout=300000,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        return {"challengeID": challenge_id, "options": json.loads(options_to_json(options))}

    def registration_options(self, invitation, username):
        self.configured()
        username = user_name(username)
        with self.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM invitations WHERE digest=? AND used_at IS NULL AND expires_at>?",
                (digest(invitation), timestamp()),
            ).fetchone()
            if row is None:
                raise TodoError("INVITATION_INVALID", 401)
            if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                raise TodoError("USERNAME_UNAVAILABLE", 409)
            return self._registration_options(db, row["owner_id"], username, invitation=row["id"])

    def _take_challenge(self, challenge_id, purposes):
        with self.database.transaction(write=True) as db:
            row = db.execute(
                "SELECT * FROM challenges WHERE id=? AND used_at IS NULL AND expires_at>?",
                (challenge_id, timestamp()),
            ).fetchone()
            if row is None or row["purpose"] not in purposes:
                raise TodoError("CHALLENGE_INVALID", 401)
            db.execute("UPDATE challenges SET used_at=? WHERE id=?", (timestamp(), challenge_id))
            return dict(row)

    def _codes(self, db, owner):
        codes = ["ttc_" + secrets.token_urlsafe(24) for _ in range(8)]
        db.execute("DELETE FROM recovery_codes WHERE owner_id=?", (owner,))
        db.executemany(
            "INSERT INTO recovery_codes VALUES (?,?,?,NULL)",
            [(digest(code), owner, timestamp()) for code in codes],
        )
        return codes

    def _session(self, db, owner, *, existing=None):
        access, refresh = "tta_" + secrets.token_urlsafe(32), "ttr_" + secrets.token_urlsafe(32)
        access_expiry = future(900)
        refresh_expiry = existing["refresh_expires_at"] if existing else future(30 * 86400)
        if existing:
            db.execute(
                "INSERT INTO used_refreshes VALUES (?,?)", (existing["refresh_digest"], existing["id"])
            )
            db.execute(
                "UPDATE sessions SET access_digest=?,refresh_digest=?,access_expires_at=? WHERE id=?",
                (digest(access), digest(refresh), access_expiry, existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    str(uuid4()),
                    owner,
                    digest(access),
                    digest(refresh),
                    access_expiry,
                    refresh_expiry,
                    timestamp(),
                ),
            )
        user = db.execute("SELECT id,username,is_legacy FROM users WHERE id=?", (owner,)).fetchone()
        return {
            "user": {"id": user["id"], "username": user["username"], "isLegacy": bool(user["is_legacy"])},
            "session": {
                "accessToken": access,
                "refreshToken": refresh,
                "accessExpiresAt": access_expiry,
                "refreshExpiresAt": refresh_expiry,
            },
        }

    def registration_verify(self, challenge_id, credential):
        settings = self.configured()
        challenge = self._take_challenge(challenge_id, {"register", "recover"})
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge["challenge"],
                expected_rp_id=settings.rp_id,
                expected_origin=list(settings.origins),
                require_user_verification=True,
            )
        except Exception:
            raise TodoError("PASSKEY_VERIFICATION_FAILED", 401) from None
        owner = challenge["owner_id"]
        with self.database.transaction(write=True) as db:
            if challenge["purpose"] == "register":
                invitation = db.execute(
                    "SELECT * FROM invitations WHERE id=? AND owner_id=? AND used_at IS NULL "
                    "AND expires_at>?",
                    (challenge["invitation_id"], owner, timestamp()),
                ).fetchone()
                if invitation is None:
                    raise TodoError("INVITATION_INVALID", 401)
                if db.execute("SELECT id FROM users WHERE username=?", (challenge["username"],)).fetchone():
                    raise TodoError("USERNAME_UNAVAILABLE", 409)
                db.execute(
                    "UPDATE users SET username=? WHERE id=? AND username IS NULL",
                    (challenge["username"], owner),
                )
                db.execute("UPDATE invitations SET used_at=? WHERE id=?", (timestamp(), invitation["id"]))
            else:
                if not db.execute(
                    "SELECT 1 FROM recovery_codes WHERE digest=? AND owner_id=? AND used_at IS NOT NULL",
                    (challenge["recovery_digest"], owner),
                ).fetchone():
                    raise TodoError("CHALLENGE_INVALID", 401)
                db.execute(
                    "UPDATE passkeys SET revoked_at=? WHERE owner_id=? AND revoked_at IS NULL",
                    (timestamp(), owner),
                )
                db.execute(
                    "UPDATE sessions SET revoked_at=? WHERE owner_id=? AND revoked_at IS NULL",
                    (timestamp(), owner),
                )
            credential_id = bytes_to_base64url(verified.credential_id)
            existing = db.execute(
                "SELECT owner_id FROM passkeys WHERE credential_id=?", (credential_id,)
            ).fetchone()
            if existing:
                raise TodoError("PASSKEY_ALREADY_REGISTERED", 409)
            db.execute(
                "INSERT INTO passkeys VALUES (?,?,?,?,?,NULL)",
                (credential_id, owner, verified.credential_public_key, verified.sign_count, timestamp()),
            )
            result = self._session(db, owner)
            result["recoveryCodes"] = self._codes(db, owner)
            return result

    def login_options(self):
        settings = self.configured()
        with self.database.transaction(write=True) as db:
            challenge_id, challenge = self._challenge(db, "login")
        options = generate_authentication_options(
            rp_id=settings.rp_id, challenge=challenge, user_verification=UserVerificationRequirement.REQUIRED
        )
        return {"challengeID": challenge_id, "options": json.loads(options_to_json(options))}

    def login_verify(self, challenge_id, credential):
        settings = self.configured()
        challenge = self._take_challenge(challenge_id, {"login"})
        if not isinstance(credential.get("id"), str):
            raise TodoError("PASSKEY_VERIFICATION_FAILED", 401)
        with self.database.transaction(write=True) as db:
            key = db.execute(
                "SELECT * FROM passkeys WHERE credential_id=? AND revoked_at IS NULL", (credential.get("id"),)
            ).fetchone()
            if key is None:
                raise TodoError("PASSKEY_VERIFICATION_FAILED", 401)
            try:
                handle = credential["response"]["userHandle"]
                if not isinstance(handle, str) or base64url_to_bytes(handle) != UUID(key["owner_id"]).bytes:
                    raise ValueError
                verified = verify_authentication_response(
                    credential=credential,
                    expected_challenge=challenge["challenge"],
                    expected_rp_id=settings.rp_id,
                    expected_origin=list(settings.origins),
                    credential_public_key=key["public_key"],
                    credential_current_sign_count=key["sign_count"],
                    require_user_verification=True,
                )
            except Exception:
                raise TodoError("PASSKEY_VERIFICATION_FAILED", 401) from None
            db.execute(
                "UPDATE passkeys SET sign_count=? WHERE credential_id=?",
                (verified.new_sign_count, key["credential_id"]),
            )
            return self._session(db, key["owner_id"])

    def refresh(self, refresh_token):
        invalid = False
        result = None
        with self.database.transaction(write=True) as db:
            used = db.execute(
                "SELECT session_id FROM used_refreshes WHERE digest=?", (digest(refresh_token),)
            ).fetchone()
            if used:
                db.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (timestamp(), used["session_id"]))
                invalid = True
            else:
                session = db.execute(
                    "SELECT * FROM sessions WHERE refresh_digest=? AND revoked_at IS NULL AND "
                    "refresh_expires_at>?",
                    (digest(refresh_token), timestamp()),
                ).fetchone()
                if session is None:
                    invalid = True
                else:
                    result = self._session(db, session["owner_id"], existing=session)
        if invalid:
            raise TodoError("AUTHENTICATION_REQUIRED", 401)
        return result

    def logout(self, refresh_token):
        with self.database.transaction(write=True) as db:
            db.execute(
                "UPDATE sessions SET revoked_at=? WHERE refresh_digest=? OR id IN "
                "(SELECT session_id FROM used_refreshes WHERE digest=?)",
                (timestamp(), digest(refresh_token), digest(refresh_token)),
            )
        return {"signedOut": True}

    def recovery_options(self, username, code):
        self.configured()
        username = user_name(username)
        with self.database.transaction(write=True) as db:
            record = db.execute(
                "SELECT c.owner_id FROM recovery_codes c JOIN users u ON u.id=c.owner_id "
                "WHERE c.digest=? AND c.used_at IS NULL AND u.username=?",
                (digest(code.strip()), username),
            ).fetchone()
            if record is None:
                raise TodoError("RECOVERY_INVALID", 401)
            db.execute(
                "UPDATE recovery_codes SET used_at=? WHERE digest=?", (timestamp(), digest(code.strip()))
            )
            return self._registration_options(
                db, record["owner_id"], username, purpose="recover", recovery=digest(code.strip())
            )

    def regenerate_codes(self, access_digest):
        with self.database.transaction(access_digest, write=True) as db:
            if db.principal["method"] != "session":
                raise TodoError("PASSKEY_REAUTHENTICATION_REQUIRED", 403)
            session = db.execute(
                "SELECT authenticated_at FROM sessions WHERE id=?", (db.principal["id"],)
            ).fetchone()
            if session["authenticated_at"] < future(-300):
                raise TodoError("PASSKEY_REAUTHENTICATION_REQUIRED", 403)
            return {"recoveryCodes": self._codes(db, db.owner_id)}

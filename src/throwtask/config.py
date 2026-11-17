import hashlib
import json
import os
import platform
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import TodoError


def default_config() -> Path:
    current = Path.home() / ".config/throwtask/config.json"
    candidates = (
        current,
        Path.home() / ".config/toidone/config.json",
        Path.home() / ".config/mytodo/config.json",
    )
    return next((path for path in candidates if path.exists() or path.is_symlink()), current)


def validate_token(value: str) -> str:
    if not re.fullmatch(r"mtd_[A-Za-z0-9_-]{43}", value):
        raise TodoError("INVALID_CREDENTIAL")
    return value


def read_credential(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise TodoError("UNSAFE_CREDENTIAL_FILE")
            if info.st_size > 128:
                raise TodoError("INVALID_CREDENTIAL")
            return validate_token(stream.read(129).strip())
    except (OSError, UnicodeError):
        raise TodoError("CREDENTIAL_UNAVAILABLE") from None


def keyring_backend():
    try:
        if platform.system() == "Darwin":
            from keyring.backends.macOS import Keyring
        else:
            from keyring.backends.SecretService import Keyring
        backend = Keyring()
        if backend.priority <= 0:
            raise TodoError("KEYRING_UNAVAILABLE")
        return backend
    except Exception:
        raise TodoError("KEYRING_UNAVAILABLE") from None


@dataclass(frozen=True)
class Settings:
    url: str
    credential_file: Path | None = None
    allow_insecure_http: bool = False

    def __post_init__(self):
        try:
            parsed = urlsplit(self.url)
            _ = parsed.port
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.scheme not in {"http", "https"}
            ):
                raise ValueError
            if (
                parsed.scheme == "http"
                and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                and not self.allow_insecure_http
            ):
                raise TodoError("HTTPS_REQUIRED")
            normalized = urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
            object.__setattr__(self, "url", normalized)
        except ValueError:
            raise TodoError("INVALID_SERVER_URL") from None

    @property
    def keyring_service(self):
        return "throwtask:" + hashlib.sha256(self.url.encode()).hexdigest()

    @property
    def legacy_keyring_service(self):
        return "mytodo:" + hashlib.sha256(self.url.encode()).hexdigest()

    @property
    def legacy_keyring_services(self):
        return ("toidone:" + hashlib.sha256(self.url.encode()).hexdigest(), self.legacy_keyring_service)

    def credential(self):
        if self.credential_file is not None:
            return read_credential(self.credential_file)
        try:
            backend = keyring_backend()
            for service in (self.keyring_service, *self.legacy_keyring_services):
                token = backend.get_password(service, "api")
                if token is not None:
                    return validate_token(token)
            raise TodoError("CREDENTIAL_UNAVAILABLE")
        except Exception:
            raise TodoError("CREDENTIAL_UNAVAILABLE") from None

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = {"url": self.url, "allowInsecureHttp": self.allow_insecure_http}
        if self.credential_file:
            value["credentialFile"] = str(self.credential_file.resolve())
        fd, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream)
                stream.write("\n")
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def load_settings(path: Path) -> Settings:
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or set(data) - {"url", "credentialFile", "allowInsecureHttp"}:
            raise ValueError
        if not isinstance(data["url"], str) or type(data.get("allowInsecureHttp", False)) is not bool:
            raise ValueError
        return Settings(
            data["url"],
            Path(data["credentialFile"]).expanduser() if data.get("credentialFile") else None,
            data.get("allowInsecureHttp", False),
        )
    except (OSError, ValueError, KeyError, TypeError):
        raise TodoError("CONFIGURATION_REQUIRED") from None

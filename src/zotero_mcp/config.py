"""Local library selection and local API authorization storage."""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def auth_path() -> Path:
    """Use the user's application data directory, never the working directory."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "zotero-mcp" / "local-auth.json"


@dataclass(frozen=True)
class LocalAuth:
    server_id: str
    key: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.server_id, str) or not self.server_id.strip():
            raise ValueError("Local authorization requires a Zotero server ID.")
        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("Local authorization requires a local API key.")


@dataclass(frozen=True)
class Settings:
    library_id: str = "0"
    library_type: str = "user"

    def __post_init__(self):
        if self.library_type not in {"user", "group"}:
            raise ValueError("ZOTERO_LIBRARY_TYPE must be user or group.")
        if not self.library_id.isascii() or not self.library_id.isdigit():
            raise ValueError("ZOTERO_LIBRARY_ID must contain digits only.")
        if self.library_type == "group" and int(self.library_id) == 0:
            raise ValueError("A group library requires its nonzero Zotero group ID.")

    @classmethod
    def from_env(cls):
        # Legacy cloud credentials are intentionally never read.
        return cls(
            library_id=os.environ.get("ZOTERO_LIBRARY_ID", "0"),
            library_type=os.environ.get("ZOTERO_LIBRARY_TYPE", "user"),
        )


@dataclass(frozen=True)
class HttpSettings:
    """Streamable HTTP transport. The endpoint is unauthenticated, so it binds locally."""

    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self):
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("The HTTP host must be a hostname or address.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("The HTTP port must be between 1 and 65535.")

    @classmethod
    def from_env(cls, host: str | None = None, port: int | None = None):
        """Command-line values win; otherwise read ZOTERO_MCP_HTTP_HOST and _PORT."""
        if host is None:
            host = os.environ.get("ZOTERO_MCP_HTTP_HOST", "127.0.0.1")
        if port is None:
            raw = os.environ.get("ZOTERO_MCP_HTTP_PORT", "8000")
            try:
                port = int(raw)
            except ValueError as exc:
                raise ValueError("ZOTERO_MCP_HTTP_PORT must be a number.") from exc
        return cls(host=host, port=port)


def load_auth(path: Path | None = None) -> LocalAuth | None:
    path = path if path is not None else auth_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LocalAuth(server_id=data["server_id"], key=data["key"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            f"Cannot read local authorization at {path}; run zotero-mcp authorize."
        ) from exc


def save_auth(auth: LocalAuth, path: Path | None = None) -> None:
    path = path if path is not None else auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp is user-only on POSIX; Windows inherits the user profile's ACL.
    fd, temporary = tempfile.mkstemp(prefix=".local-auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"server_id": auth.server_id, "key": auth.key}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

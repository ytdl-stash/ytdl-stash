"""Optional app password protection: hashing, session tokens, and CLI to set/remove password."""

import base64
import binascii
import getpass
import hashlib
import hmac
import json
import secrets
import sys
from pathlib import Path

# PBKDF2 parameters
_PBKDF2_ITERATIONS = 600_000
_PBKDF2_KEY_LEN = 32
_SESSION_HMAC_DIGEST = "sha256"
_COOKIE_NAME = "ytdl_session"


def _auth_file_path() -> Path:
    """Resolve path to auth.json in the app data directory."""
    from app.config import get_settings

    settings = get_settings()
    return Path(settings.data_dir) / "auth.json"


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256 and random salt. Returns stored hash string."""
    salt = secrets.token_bytes(32)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=_PBKDF2_KEY_LEN,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    key_b64 = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt_b64}${key_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored hash. Uses timing-safe comparison."""
    if not stored_hash or not password:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False
    try:
        iterations = int(parts[1])
        if iterations < 1 or iterations > _PBKDF2_ITERATIONS_MAX:
            return False
        pad = (4 - len(parts[2]) % 4) % 4
        salt = base64.urlsafe_b64decode(parts[2] + "=" * pad)
        pad = (4 - len(parts[3]) % 4) % 4
        expected = base64.urlsafe_b64decode(parts[3] + "=" * pad)
    except (ValueError, TypeError, binascii.Error):
        return False
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=_PBKDF2_KEY_LEN,
    )
    return hmac.compare_digest(key, expected)


def get_password_hash() -> str | None:
    """Read the stored password hash from auth.json. Returns None if no file or no hash."""
    path = _auth_file_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("password_hash") or None
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def set_password(password: str) -> None:
    """Write the password hash to auth.json. Overwrites any existing file."""
    path = _auth_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stored = hash_password(password)
    path.write_text(
        json.dumps({"password_hash": stored}, indent=2),
        encoding="utf-8",
    )


def remove_password() -> None:
    """Remove password protection by deleting auth.json."""
    path = _auth_file_path()
    if path.exists():
        path.unlink()


def create_session_token(password_hash: str) -> str:
    """Create a session cookie value: nonce:hmac_signature. HMAC key derived from password hash."""
    nonce = secrets.token_urlsafe(32)
    sig = hmac.new(
        password_hash.encode("utf-8"),
        nonce.encode("utf-8"),
        _SESSION_HMAC_DIGEST,
    ).hexdigest()
    return f"{nonce}:{sig}"


def verify_session_token(token: str, password_hash: str) -> bool:
    """Verify a session cookie value. Returns True only if token is valid for the given hash."""
    if not token or ":" not in token:
        return False
    nonce, sig = token.split(":", 1)
    expected = hmac.new(
        password_hash.encode("utf-8"),
        nonce.encode("utf-8"),
        _SESSION_HMAC_DIGEST,
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def get_cookie_name() -> str:
    """Return the session cookie name for use by routes/middleware."""
    return _COOKIE_NAME


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("set", "remove"):
        print("Usage: python -m app.auth set | remove", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "set":
        pw = getpass.getpass("Password: ")
        if not pw:
            print("Password cannot be empty.", file=sys.stderr)
            sys.exit(1)
        pw2 = getpass.getpass("Confirm password: ")
        if pw != pw2:
            print("Passwords do not match.", file=sys.stderr)
            sys.exit(1)
        set_password(pw)
        print("Password set. Restart the app if it is running to apply.")
    else:
        remove_password()
        print("Password protection removed.")

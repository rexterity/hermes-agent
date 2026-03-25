"""Drive-memory plugin — core implementation.

Provides Google Drive state persistence via the ``gws`` CLI tool.
Designed for v0.2.0 hardening: input validation, path sanitization,
exponential-backoff retry, and TTL-based in-memory caching.

The ``gws`` CLI is expected to support at minimum::

    gws drive upload <local_path> <remote_path>
    gws drive download <remote_path> <local_path>
    gws drive list <remote_path>

Environment:
    GWS_ENABLED  — set to any truthy value to activate the plugin.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GWS_CMD = os.environ.get("GWS_COMMAND", "gws")

# Remote base directory inside Google Drive
_REMOTE_BASE = os.environ.get(
    "DRIVE_MEMORY_REMOTE_BASE",
    "hermes-agent/state",
)

# Local cache directory for downloaded state files
_LOCAL_CACHE_DIR = os.path.join(
    os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")),
    "drive-memory-cache",
)

# Safety limits
MAX_STATE_SIZE_BYTES = 5 * 1024 * 1024   # 5 MiB per state blob
MAX_MEDIA_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB per media file
MAX_KEY_LENGTH = 200
MAX_RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 1.0

# Cache TTL — avoid redundant Drive reads within this window
CACHE_TTL_S = float(os.environ.get("DRIVE_MEMORY_CACHE_TTL", "60"))

# Debounce — minimum interval between auto-saves triggered by hooks
AUTO_SAVE_DEBOUNCE_S = float(os.environ.get("DRIVE_MEMORY_DEBOUNCE", "30"))

# Path component whitelist (letters, digits, hyphens, underscores, dots)
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

class _StateCache:
    """Simple TTL cache keyed by remote path."""

    def __init__(self) -> None:
        self._store: Dict[str, tuple[float, str]] = {}  # key -> (ts, json_str)

    def get(self, key: str) -> Optional[str]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if (time.monotonic() - ts) > CACHE_TTL_S:
            del self._store[key]
            return None
        return value

    def put(self, key: str, value: str) -> None:
        self._store[key] = (time.monotonic(), value)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


_cache = _StateCache()

# Timestamp of last auto-save (monotonic clock) — used for debouncing
_last_auto_save_ts: float = 0.0

# Default state key used by hooks for the "current session" state
_DEFAULT_STATE_KEY = "session_state"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_key(key: str) -> str:
    """Sanitise and validate a state key (used as a Drive path component).

    Raises ValueError on invalid input.  Returns the cleaned key.
    """
    if not isinstance(key, str):
        raise ValueError("key must be a string")
    key = key.strip()
    if len(key) == 0:
        raise ValueError("key must not be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(
            f"key exceeds {MAX_KEY_LENGTH} characters"
        )
    # Reject path traversal attempts
    if ".." in key:
        raise ValueError("key must not contain '..'")
    if "/" in key:
        raise ValueError("key must not contain '/'")
    if "\\" in key:
        raise ValueError("key must not contain '\\'")
    if not _SAFE_KEY_RE.match(key):
        raise ValueError(
            f"key contains disallowed characters: {key!r}  "
            "(allowed: a-z A-Z 0-9 . _ -)"
        )
    return key


def _validate_state_payload(payload: str) -> None:
    """Ensure the state payload is valid JSON within size limits."""
    if not isinstance(payload, str):
        raise ValueError("payload must be a string")
    size = len(payload.encode("utf-8"))
    if size > MAX_STATE_SIZE_BYTES:
        raise ValueError(
            f"state payload too large: {size} bytes "
            f"(limit {MAX_STATE_SIZE_BYTES})"
        )
    # Must be valid JSON
    json.loads(payload)


def _validate_local_path(path_str: str) -> Path:
    """Resolve and validate a local file path for media upload.

    Prevents traversal outside of allowed directories.
    """
    if not isinstance(path_str, str):
        raise ValueError("path must be a string")
    path = Path(path_str).resolve()
    if not path.exists():
        raise ValueError(f"file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"not a regular file: {path}")
    size = path.stat().st_size
    if size > MAX_MEDIA_SIZE_BYTES:
        raise ValueError(
            f"file too large: {size} bytes (limit {MAX_MEDIA_SIZE_BYTES})"
        )
    return path


# ---------------------------------------------------------------------------
# gws CLI wrapper with retry
# ---------------------------------------------------------------------------

def _gws_available() -> bool:
    """Return True when gws is installed and GWS_ENABLED is set."""
    if not os.environ.get("GWS_ENABLED"):
        return False
    return shutil.which(_GWS_CMD) is not None


def _run_gws(args: list[str], *, timeout_s: int = 60) -> subprocess.CompletedProcess:
    """Execute a gws CLI command with exponential-backoff retry.

    Retries on non-zero exit codes up to MAX_RETRY_ATTEMPTS times.
    Only retries when the exit code suggests a transient failure
    (network error, rate limit, server error).
    """
    cmd = [_GWS_CMD] + args
    last_exc: Optional[Exception] = None

    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            if result.returncode == 0:
                return result

            # Non-zero exit — decide whether to retry
            stderr_lower = result.stderr.lower()
            transient = any(
                tok in stderr_lower
                for tok in ("timeout", "rate limit", "429", "500", "503", "network")
            )
            if not transient or attempt == MAX_RETRY_ATTEMPTS - 1:
                return result  # permanent failure or final attempt

            delay = RETRY_BASE_DELAY_S * (2 ** attempt)
            logger.warning(
                "gws transient failure (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, MAX_RETRY_ATTEMPTS, delay, result.stderr[:200],
            )
            time.sleep(delay)

        except subprocess.TimeoutExpired as exc:
            last_exc = exc
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY_S * (2 ** attempt)
                logger.warning(
                    "gws timeout (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, MAX_RETRY_ATTEMPTS, delay,
                )
                time.sleep(delay)
            else:
                raise

    # Unreachable if MAX_RETRY_ATTEMPTS >= 1, but satisfy the type checker
    assert last_exc is not None
    raise last_exc  # pragma: no cover


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def save_state(key: str, state_json: str) -> Dict[str, Any]:
    """Upload a JSON state blob to Google Drive.

    Args:
        key: Logical name for the state (e.g. ``session_state``).
             Used as the filename on Drive.
        state_json: JSON-serialised state payload.

    Returns:
        Result dict with ``success``, ``remote_path``, ``size_bytes``, etc.
    """
    key = _validate_key(key)
    _validate_state_payload(state_json)

    remote_path = f"{_REMOTE_BASE}/{key}.json"
    size_bytes = len(state_json.encode("utf-8"))

    # Write to a temp file, then upload
    tmp_dir = tempfile.mkdtemp(prefix="hermes_drive_")
    tmp_file = os.path.join(tmp_dir, f"{key}.json")
    try:
        with open(tmp_file, "w", encoding="utf-8") as fh:
            fh.write(state_json)

        result = _run_gws(["drive", "upload", tmp_file, remote_path])
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"gws upload failed (exit {result.returncode}): "
                         f"{result.stderr[:500]}",
            }

        # Update cache so subsequent loads are fast
        _cache.put(key, state_json)

        checksum = hashlib.sha256(state_json.encode("utf-8")).hexdigest()[:16]
        return {
            "success": True,
            "remote_path": remote_path,
            "size_bytes": size_bytes,
            "sha256_prefix": checksum,
        }
    finally:
        # Clean up temp files
        try:
            os.unlink(tmp_file)
            os.rmdir(tmp_dir)
        except OSError:
            pass


def load_state(key: str) -> Dict[str, Any]:
    """Download a JSON state blob from Google Drive.

    Returns the parsed state under the ``state`` key, or an error dict.
    Uses the in-memory TTL cache to skip redundant downloads.
    """
    key = _validate_key(key)

    # Check cache first
    cached = _cache.get(key)
    if cached is not None:
        return {
            "success": True,
            "source": "cache",
            "key": key,
            "state": json.loads(cached),
        }

    remote_path = f"{_REMOTE_BASE}/{key}.json"

    # Download to a temp file, then read
    os.makedirs(_LOCAL_CACHE_DIR, exist_ok=True)
    tmp_file = os.path.join(_LOCAL_CACHE_DIR, f"{key}.json")
    try:
        result = _run_gws(["drive", "download", remote_path, tmp_file])
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # Distinguish "not found" from other errors
            if any(tok in stderr.lower() for tok in ("not found", "404", "no such")):
                return {
                    "success": False,
                    "error": f"state key '{key}' not found on Drive",
                    "key": key,
                }
            return {
                "success": False,
                "error": f"gws download failed (exit {result.returncode}): "
                         f"{stderr[:500]}",
            }

        with open(tmp_file, "r", encoding="utf-8") as fh:
            raw = fh.read()

        _validate_state_payload(raw)
        state = json.loads(raw)

        # Populate cache
        _cache.put(key, raw)

        return {
            "success": True,
            "source": "drive",
            "key": key,
            "state": state,
        }
    finally:
        # Clean up downloaded temp file
        try:
            if os.path.exists(tmp_file):
                os.unlink(tmp_file)
        except OSError:
            pass


def store_media(local_path_str: str, remote_name: str) -> Dict[str, Any]:
    """Upload a local file to Google Drive under the media subfolder.

    Args:
        local_path_str: Absolute path to the local file.
        remote_name: Desired filename on Drive (sanitised).

    Returns:
        Result dict with ``success``, ``remote_path``, ``size_bytes``.
    """
    local_path = _validate_local_path(local_path_str)
    remote_name = _validate_key(remote_name)

    remote_path = f"{_REMOTE_BASE}/media/{remote_name}"
    size_bytes = local_path.stat().st_size

    result = _run_gws(["drive", "upload", str(local_path), remote_path])
    if result.returncode != 0:
        return {
            "success": False,
            "error": f"gws upload failed (exit {result.returncode}): "
                     f"{result.stderr[:500]}",
        }

    return {
        "success": True,
        "remote_path": remote_path,
        "size_bytes": size_bytes,
        "original_name": local_path.name,
    }


# ---------------------------------------------------------------------------
# Hook callbacks
# ---------------------------------------------------------------------------

def on_pre_llm_call(**kwargs: Any) -> None:
    """Hook: pre_llm_call — ensure cached state is warm.

    If the default session state has expired from cache, trigger a
    background load so the next tool call has fresh data.  This is
    best-effort and must never block the LLM call.
    """
    try:
        if not _gws_available():
            return
        # Only pre-warm if cache is cold
        if _cache.get(_DEFAULT_STATE_KEY) is None:
            load_state(_DEFAULT_STATE_KEY)
    except Exception as exc:
        # Hooks must never crash the agent loop
        logger.debug("drive-memory pre_llm_call hook failed (non-fatal): %s", exc)


def on_post_tool_call(**kwargs: Any) -> None:
    """Hook: post_tool_call — debounced auto-save of session state.

    Writes the latest result summary to Drive so operational context
    survives restarts.  Debounced to avoid spamming Drive on rapid
    successive tool calls.
    """
    global _last_auto_save_ts

    try:
        if not _gws_available():
            return

        now = time.monotonic()
        if (now - _last_auto_save_ts) < AUTO_SAVE_DEBOUNCE_S:
            return  # too soon since last save

        tool_name = kwargs.get("tool_name", "")
        result_str = kwargs.get("result", "")

        # Build a minimal state snapshot from the tool call
        snapshot = {
            "last_tool": tool_name,
            "timestamp_utc": int(time.time()),
            "result_preview": result_str[:500] if isinstance(result_str, str) else "",
        }

        state_json = json.dumps(snapshot, ensure_ascii=False)
        outcome = save_state(_DEFAULT_STATE_KEY, state_json)
        if outcome.get("success"):
            _last_auto_save_ts = now
            logger.debug("drive-memory auto-save succeeded for key=%s", _DEFAULT_STATE_KEY)
        else:
            logger.debug("drive-memory auto-save failed: %s", outcome.get("error"))

    except Exception as exc:
        # Hooks must never crash the agent loop
        logger.debug("drive-memory post_tool_call hook failed (non-fatal): %s", exc)

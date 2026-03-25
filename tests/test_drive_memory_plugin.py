"""Tests for the drive-memory plugin (GWS state persistence).

Covers:
    - Input validation (key sanitisation, payload limits, path traversal)
    - State cache (TTL expiry, put/get/invalidate)
    - save_state / load_state / store_media (happy + failure paths, mocked gws)
    - Hook callbacks (pre_llm_call, post_tool_call, debouncing)
    - Tool handler wrappers
    - Plugin registration via register(ctx)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Reset environment and module-level state for every test."""
    monkeypatch.setenv("GWS_ENABLED", "1")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("DRIVE_MEMORY_CACHE_TTL", "60")
    monkeypatch.setenv("DRIVE_MEMORY_DEBOUNCE", "0")  # disable debounce for tests

    # Ensure plugin package is importable
    import plugins.drive_memory.drive_state as ds

    ds._cache.clear()
    ds._last_auto_save_ts = 0.0
    yield


@pytest.fixture()
def _gws_ok(monkeypatch):
    """Patch _run_gws to always succeed (returncode 0, empty stderr)."""
    import plugins.drive_memory.drive_state as ds

    def _fake_run(args: list, *, timeout_s: int = 60):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ds, "_run_gws", _fake_run)


@pytest.fixture()
def _gws_fail(monkeypatch):
    """Patch _run_gws to always fail."""
    import plugins.drive_memory.drive_state as ds

    def _fake_run(args: list, *, timeout_s: int = 60):
        return subprocess.CompletedProcess(
            args=args, returncode=1, stdout="", stderr="upload error: network timeout"
        )

    monkeypatch.setattr(ds, "_run_gws", _fake_run)


@pytest.fixture()
def sample_file(tmp_path) -> Path:
    """Create a small sample file for media upload tests."""
    p = tmp_path / "sample.txt"
    p.write_text("hello world")
    return p


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Input validation for keys, payloads, and local paths."""

    def test_valid_key_passes(self):
        from plugins.drive_memory.drive_state import _validate_key

        assert _validate_key("session_state") == "session_state"
        assert _validate_key("my-key.v2") == "my-key.v2"
        assert _validate_key("A123") == "A123"

    def test_key_rejects_empty(self):
        from plugins.drive_memory.drive_state import _validate_key

        with pytest.raises(AssertionError, match="must not be empty"):
            _validate_key("")

    def test_key_rejects_traversal(self):
        from plugins.drive_memory.drive_state import _validate_key

        with pytest.raises(AssertionError, match="must not contain"):
            _validate_key("../etc/passwd")

    def test_key_rejects_slash(self):
        from plugins.drive_memory.drive_state import _validate_key

        with pytest.raises(AssertionError, match="must not contain"):
            _validate_key("foo/bar")

    def test_key_rejects_special_chars(self):
        from plugins.drive_memory.drive_state import _validate_key

        with pytest.raises(ValueError, match="disallowed characters"):
            _validate_key("key with spaces")

    def test_key_rejects_too_long(self):
        from plugins.drive_memory.drive_state import _validate_key

        with pytest.raises(AssertionError, match="exceeds"):
            _validate_key("a" * 201)

    def test_payload_rejects_invalid_json(self):
        from plugins.drive_memory.drive_state import _validate_state_payload

        with pytest.raises(json.JSONDecodeError):
            _validate_state_payload("not json")

    def test_payload_rejects_oversized(self):
        from plugins.drive_memory.drive_state import (
            MAX_STATE_SIZE_BYTES,
            _validate_state_payload,
        )

        big = json.dumps({"x": "a" * (MAX_STATE_SIZE_BYTES + 1)})
        with pytest.raises(AssertionError, match="too large"):
            _validate_state_payload(big)

    def test_validate_local_path_rejects_missing(self, tmp_path):
        from plugins.drive_memory.drive_state import _validate_local_path

        with pytest.raises(AssertionError, match="does not exist"):
            _validate_local_path(str(tmp_path / "nope.txt"))

    def test_validate_local_path_rejects_directory(self, tmp_path):
        from plugins.drive_memory.drive_state import _validate_local_path

        with pytest.raises(AssertionError, match="not a regular file"):
            _validate_local_path(str(tmp_path))

    def test_validate_local_path_rejects_oversized(self, tmp_path):
        from plugins.drive_memory.drive_state import (
            MAX_MEDIA_SIZE_BYTES,
            _validate_local_path,
        )

        big = tmp_path / "huge.bin"
        big.write_bytes(b"\x00" * (MAX_MEDIA_SIZE_BYTES + 1))
        with pytest.raises(AssertionError, match="too large"):
            _validate_local_path(str(big))


# ---------------------------------------------------------------------------
# State cache
# ---------------------------------------------------------------------------


class TestStateCache:
    """In-memory TTL cache behaviour."""

    def test_put_and_get(self):
        from plugins.drive_memory.drive_state import _StateCache

        cache = _StateCache()
        cache.put("k", '{"a":1}')
        assert cache.get("k") == '{"a":1}'

    def test_get_returns_none_for_missing(self):
        from plugins.drive_memory.drive_state import _StateCache

        cache = _StateCache()
        assert cache.get("missing") is None

    def test_ttl_expiry(self, monkeypatch):
        from plugins.drive_memory.drive_state import _StateCache

        cache = _StateCache()
        cache.put("k", '{"a":1}')
        # Fast-forward monotonic time
        entry_ts = cache._store["k"][0]
        cache._store["k"] = (entry_ts - 9999, '{"a":1}')
        assert cache.get("k") is None

    def test_invalidate(self):
        from plugins.drive_memory.drive_state import _StateCache

        cache = _StateCache()
        cache.put("k", "v")
        cache.invalidate("k")
        assert cache.get("k") is None

    def test_clear(self):
        from plugins.drive_memory.drive_state import _StateCache

        cache = _StateCache()
        cache.put("a", "1")
        cache.put("b", "2")
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------


class TestSaveState:
    """Core save_state function (gws upload)."""

    def test_save_success(self, _gws_ok):
        from plugins.drive_memory.drive_state import save_state

        result = save_state("my_key", json.dumps({"hello": "world"}))
        assert result["success"] is True
        assert "remote_path" in result
        assert result["size_bytes"] > 0
        assert "sha256_prefix" in result

    def test_save_populates_cache(self, _gws_ok):
        from plugins.drive_memory.drive_state import _cache, save_state

        payload = json.dumps({"cached": True})
        save_state("ck", payload)
        assert _cache.get("ck") == payload

    def test_save_failure(self, _gws_fail):
        from plugins.drive_memory.drive_state import save_state

        result = save_state("fail_key", json.dumps({"x": 1}))
        assert result["success"] is False
        assert "error" in result

    def test_save_rejects_bad_key(self, _gws_ok):
        from plugins.drive_memory.drive_state import save_state

        with pytest.raises(AssertionError):
            save_state("../hack", json.dumps({"x": 1}))

    def test_save_rejects_bad_payload(self, _gws_ok):
        from plugins.drive_memory.drive_state import save_state

        with pytest.raises(json.JSONDecodeError):
            save_state("ok_key", "not json")


# ---------------------------------------------------------------------------
# load_state
# ---------------------------------------------------------------------------


class TestLoadState:
    """Core load_state function (gws download)."""

    def test_load_from_cache(self, _gws_ok):
        from plugins.drive_memory.drive_state import _cache, load_state

        _cache.put("cached_key", json.dumps({"from": "cache"}))
        result = load_state("cached_key")
        assert result["success"] is True
        assert result["source"] == "cache"
        assert result["state"]["from"] == "cache"

    def test_load_from_drive(self, monkeypatch, tmp_path):
        from plugins.drive_memory import drive_state as ds

        payload = json.dumps({"from": "drive"})

        def _fake_run(args: list, *, timeout_s: int = 60):
            # Simulate gws writing the downloaded file
            if "download" in args:
                local_dest = args[-1]
                Path(local_dest).write_text(payload)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(ds, "_run_gws", _fake_run)

        result = ds.load_state("drive_key")
        assert result["success"] is True
        assert result["source"] == "drive"
        assert result["state"]["from"] == "drive"

    def test_load_not_found(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        def _fake_run(args: list, *, timeout_s: int = 60):
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="Error: file not found"
            )

        monkeypatch.setattr(ds, "_run_gws", _fake_run)

        result = ds.load_state("missing_key")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_load_rejects_bad_key(self):
        from plugins.drive_memory.drive_state import load_state

        with pytest.raises(AssertionError):
            load_state("../../etc/passwd")


# ---------------------------------------------------------------------------
# store_media
# ---------------------------------------------------------------------------


class TestStoreMedia:
    """Core store_media function (gws upload for files)."""

    def test_store_success(self, _gws_ok, sample_file):
        from plugins.drive_memory.drive_state import store_media

        result = store_media(str(sample_file), "sample.txt")
        assert result["success"] is True
        assert "remote_path" in result
        assert result["original_name"] == "sample.txt"

    def test_store_failure(self, _gws_fail, sample_file):
        from plugins.drive_memory.drive_state import store_media

        result = store_media(str(sample_file), "sample.txt")
        assert result["success"] is False
        assert "error" in result

    def test_store_rejects_missing_file(self, _gws_ok, tmp_path):
        from plugins.drive_memory.drive_state import store_media

        with pytest.raises(AssertionError, match="does not exist"):
            store_media(str(tmp_path / "nope"), "nope.txt")

    def test_store_rejects_bad_remote_name(self, _gws_ok, sample_file):
        from plugins.drive_memory.drive_state import store_media

        with pytest.raises(AssertionError, match="must not contain"):
            store_media(str(sample_file), "../evil.txt")


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class TestHooks:
    """Hook callbacks: pre_llm_call and post_tool_call."""

    def test_pre_llm_call_loads_state(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        load_calls: List[str] = []

        def _fake_load(key):
            load_calls.append(key)
            return {"success": True, "state": {}}

        monkeypatch.setattr(ds, "load_state", _fake_load)
        monkeypatch.setattr(ds, "_gws_available", lambda: True)
        ds._cache.clear()

        ds.on_pre_llm_call()
        assert len(load_calls) == 1
        assert load_calls[0] == "session_state"

    def test_pre_llm_call_skips_when_cached(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        load_calls: List[str] = []

        def _fake_load(key):
            load_calls.append(key)
            return {"success": True, "state": {}}

        monkeypatch.setattr(ds, "load_state", _fake_load)
        monkeypatch.setattr(ds, "_gws_available", lambda: True)
        ds._cache.put("session_state", '{"warm": true}')

        ds.on_pre_llm_call()
        assert len(load_calls) == 0  # cache was warm, no load

    def test_pre_llm_call_no_crash_on_error(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        monkeypatch.setattr(ds, "_gws_available", lambda: True)
        monkeypatch.setattr(ds, "load_state", lambda k: (_ for _ in ()).throw(RuntimeError("boom")))
        ds._cache.clear()

        # Must not raise
        ds.on_pre_llm_call()

    def test_pre_llm_call_skips_when_gws_unavailable(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        monkeypatch.setattr(ds, "_gws_available", lambda: False)
        load_calls: List[str] = []
        monkeypatch.setattr(ds, "load_state", lambda k: load_calls.append(k))

        ds.on_pre_llm_call()
        assert len(load_calls) == 0

    def test_post_tool_call_saves_state(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        save_calls: List[tuple] = []

        def _fake_save(key, payload):
            save_calls.append((key, payload))
            return {"success": True}

        monkeypatch.setattr(ds, "save_state", _fake_save)
        monkeypatch.setattr(ds, "_gws_available", lambda: True)
        ds._last_auto_save_ts = 0.0

        ds.on_post_tool_call(tool_name="test_tool", result="ok")

        assert len(save_calls) == 1
        key, payload_json = save_calls[0]
        assert key == "session_state"
        payload = json.loads(payload_json)
        assert payload["last_tool"] == "test_tool"

    def test_post_tool_call_debounce(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        save_calls: List[tuple] = []

        def _fake_save(key, payload):
            save_calls.append((key, payload))
            return {"success": True}

        monkeypatch.setattr(ds, "save_state", _fake_save)
        monkeypatch.setattr(ds, "_gws_available", lambda: True)

        # Set debounce to a large value
        monkeypatch.setattr(ds, "AUTO_SAVE_DEBOUNCE_S", 9999.0)
        ds._last_auto_save_ts = time.monotonic()  # just saved

        ds.on_post_tool_call(tool_name="tool2", result="ok")

        assert len(save_calls) == 0  # debounced

    def test_post_tool_call_no_crash_on_error(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        monkeypatch.setattr(ds, "_gws_available", lambda: True)
        monkeypatch.setattr(ds, "save_state", lambda k, p: (_ for _ in ()).throw(RuntimeError("oops")))
        ds._last_auto_save_ts = 0.0

        # Must not raise
        ds.on_post_tool_call(tool_name="x", result="y")


# ---------------------------------------------------------------------------
# Tool handlers (__init__.py wrappers)
# ---------------------------------------------------------------------------


class TestToolHandlers:
    """Thin handler wrappers in __init__.py."""

    def test_handle_save(self, _gws_ok):
        from plugins.drive_memory import _handle_drive_state_save

        result_str = _handle_drive_state_save({"key": "k1", "state": {"a": 1}})
        result = json.loads(result_str)
        assert result["success"] is True

    def test_handle_save_missing_key(self):
        from plugins.drive_memory import _handle_drive_state_save

        with pytest.raises(AssertionError):
            _handle_drive_state_save({"state": {"a": 1}})

    def test_handle_load_from_cache(self, _gws_ok):
        from plugins.drive_memory import _handle_drive_state_load
        from plugins.drive_memory.drive_state import _cache

        _cache.put("cl", json.dumps({"b": 2}))
        result_str = _handle_drive_state_load({"key": "cl"})
        result = json.loads(result_str)
        assert result["success"] is True
        assert result["state"]["b"] == 2

    def test_handle_media(self, _gws_ok, sample_file):
        from plugins.drive_memory import _handle_drive_media_store

        result_str = _handle_drive_media_store({
            "local_path": str(sample_file),
            "remote_name": "sample.txt",
        })
        result = json.loads(result_str)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


class TestRegistration:
    """register(ctx) wires up tools and hooks correctly."""

    def test_register_calls_ctx(self):
        from plugins.drive_memory import register

        ctx = MagicMock()
        register(ctx)

        # 3 tools registered
        assert ctx.register_tool.call_count == 3
        tool_names = [call.kwargs["name"] for call in ctx.register_tool.call_args_list]
        assert "drive_state_save" in tool_names
        assert "drive_state_load" in tool_names
        assert "drive_media_store" in tool_names

        # 2 hooks registered
        assert ctx.register_hook.call_count == 2
        hook_names = [call.args[0] for call in ctx.register_hook.call_args_list]
        assert "pre_llm_call" in hook_names
        assert "post_tool_call" in hook_names

    def test_register_tool_schemas_valid(self):
        from plugins.drive_memory import (
            DRIVE_MEDIA_STORE_SCHEMA,
            DRIVE_STATE_LOAD_SCHEMA,
            DRIVE_STATE_SAVE_SCHEMA,
        )

        for schema in (DRIVE_STATE_SAVE_SCHEMA, DRIVE_STATE_LOAD_SCHEMA, DRIVE_MEDIA_STORE_SCHEMA):
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema
            assert schema["parameters"]["type"] == "object"
            assert "required" in schema["parameters"]

    def test_check_gws_respects_env(self, monkeypatch):
        from plugins.drive_memory import _check_gws

        monkeypatch.setenv("GWS_ENABLED", "1")
        assert _check_gws() is True

        monkeypatch.delenv("GWS_ENABLED", raising=False)
        assert _check_gws() is False


# ---------------------------------------------------------------------------
# gws CLI retry logic
# ---------------------------------------------------------------------------


class TestGwsRetry:
    """Exponential-backoff retry in _run_gws."""

    def test_retries_on_transient_failure(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        call_count = 0

        def _fake_subprocess_run(cmd, *, capture_output, text, timeout):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="network timeout"
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="ok", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)
        monkeypatch.setattr(ds, "RETRY_BASE_DELAY_S", 0.01)  # fast tests

        result = ds._run_gws(["drive", "upload", "/a", "/b"])
        assert result.returncode == 0
        assert call_count == 3

    def test_no_retry_on_permanent_failure(self, monkeypatch):
        from plugins.drive_memory import drive_state as ds

        call_count = 0

        def _fake_subprocess_run(cmd, *, capture_output, text, timeout):
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="permission denied"
            )

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run)
        monkeypatch.setattr(ds, "RETRY_BASE_DELAY_S", 0.01)

        result = ds._run_gws(["drive", "upload", "/a", "/b"])
        assert result.returncode == 1
        assert call_count == 1  # no retry for permanent errors

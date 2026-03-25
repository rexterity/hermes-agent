"""Drive-memory plugin — Google Drive state persistence for Hermes Agent.

Registers three tools and two hooks that let the agent save/load
operational state and media to Google Drive via the ``gws`` CLI.

Tools:
    drive_state_save   — persist a JSON state blob to Drive
    drive_state_load   — retrieve a JSON state blob from Drive
    drive_media_store  — upload a local file to Drive

Hooks:
    pre_llm_call   — warm the state cache before LLM inference
    post_tool_call — debounced auto-save after every tool execution
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

DRIVE_STATE_SAVE_SCHEMA = {
    "name": "drive_state_save",
    "description": (
        "Save a JSON state blob to Google Drive.  The state is stored "
        "under a logical key (e.g. 'session_state') and can be loaded "
        "later with drive_state_load.  Max payload size: 5 MiB."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "Logical name for the state (alphanumeric, hyphens, "
                    "underscores, dots; max 200 chars).  Example: 'session_state'"
                ),
            },
            "state": {
                "type": "object",
                "description": "The state object to persist (serialised as JSON).",
            },
        },
        "required": ["key", "state"],
    },
}

DRIVE_STATE_LOAD_SCHEMA = {
    "name": "drive_state_load",
    "description": (
        "Load a previously saved JSON state blob from Google Drive.  "
        "Returns the state object, or an error if the key does not exist.  "
        "Uses an in-memory TTL cache to avoid redundant downloads."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "Logical name of the state to load.  Must match the "
                    "key used when saving."
                ),
            },
        },
        "required": ["key"],
    },
}

DRIVE_MEDIA_STORE_SCHEMA = {
    "name": "drive_media_store",
    "description": (
        "Upload a local file (image, document, etc.) to Google Drive.  "
        "The file is stored under the 'media/' subfolder.  Max file "
        "size: 50 MiB."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "local_path": {
                "type": "string",
                "description": "Absolute path to the local file to upload.",
            },
            "remote_name": {
                "type": "string",
                "description": (
                    "Desired filename on Drive (alphanumeric, hyphens, "
                    "underscores, dots).  Example: 'screenshot-2024.png'"
                ),
            },
        },
        "required": ["local_path", "remote_name"],
    },
}


# ---------------------------------------------------------------------------
# Tool handlers — thin wrappers that delegate to drive_state module
# ---------------------------------------------------------------------------

def _handle_drive_state_save(args: dict, **kwargs: Any) -> str:
    """Handler for the drive_state_save tool."""
    from plugins.drive_memory import drive_state as _ds  # noqa: WPS433

    if not isinstance(args, dict):
        raise ValueError("args must be a dict")

    key = args.get("key", "")
    state_obj = args.get("state")

    if not key:
        raise ValueError("key is required")
    if state_obj is None:
        raise ValueError("state is required")

    state_json = json.dumps(state_obj, ensure_ascii=False)
    result = _ds.save_state(key, state_json)
    return json.dumps(result)


def _handle_drive_state_load(args: dict, **kwargs: Any) -> str:
    """Handler for the drive_state_load tool."""
    from plugins.drive_memory import drive_state as _ds  # noqa: WPS433

    if not isinstance(args, dict):
        raise ValueError("args must be a dict")

    key = args.get("key", "")
    if not key:
        raise ValueError("key is required")

    result = _ds.load_state(key)
    return json.dumps(result)


def _handle_drive_media_store(args: dict, **kwargs: Any) -> str:
    """Handler for the drive_media_store tool."""
    from plugins.drive_memory import drive_state as _ds  # noqa: WPS433

    if not isinstance(args, dict):
        raise ValueError("args must be a dict")

    local_path = args.get("local_path", "")
    remote_name = args.get("remote_name", "")

    if not local_path:
        raise ValueError("local_path is required")
    if not remote_name:
        raise ValueError("remote_name is required")

    result = _ds.store_media(local_path, remote_name)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def _check_gws() -> bool:
    """Return True when GWS_ENABLED is set and gws CLI is installed."""
    from plugins.drive_memory import drive_state as _ds  # noqa: WPS433
    return _ds._gws_available()


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx: "PluginContext") -> None:
    """Register drive-memory tools and hooks with the Hermes plugin system."""
    from plugins.drive_memory import drive_state as _ds  # noqa: WPS433

    assert ctx is not None, "PluginContext must not be None"

    # -- Tools ---------------------------------------------------------------
    ctx.register_tool(
        name="drive_state_save",
        toolset="drive-memory",
        schema=DRIVE_STATE_SAVE_SCHEMA,
        handler=_handle_drive_state_save,
        check_fn=_check_gws,
        requires_env=["GWS_ENABLED"],
        is_async=False,
        description="Save operational state to Google Drive",
        emoji="\U0001f4be",  # floppy disk
    )

    ctx.register_tool(
        name="drive_state_load",
        toolset="drive-memory",
        schema=DRIVE_STATE_LOAD_SCHEMA,
        handler=_handle_drive_state_load,
        check_fn=_check_gws,
        requires_env=["GWS_ENABLED"],
        is_async=False,
        description="Load operational state from Google Drive",
        emoji="\U0001f4c2",  # open file folder
    )

    ctx.register_tool(
        name="drive_media_store",
        toolset="drive-memory",
        schema=DRIVE_MEDIA_STORE_SCHEMA,
        handler=_handle_drive_media_store,
        check_fn=_check_gws,
        requires_env=["GWS_ENABLED"],
        is_async=False,
        description="Upload media file to Google Drive",
        emoji="\U0001f4f7",  # camera
    )

    # -- Hooks ---------------------------------------------------------------
    ctx.register_hook("pre_llm_call", _ds.on_pre_llm_call)
    ctx.register_hook("post_tool_call", _ds.on_post_tool_call)

    logger.info("drive-memory plugin registered (3 tools, 2 hooks)")

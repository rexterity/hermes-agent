"""Devin Communication Tool -- full session lifecycle + bidirectional messaging.

Enables Hermes Agent to autonomously create, manage, and communicate with
Devin sessions via the Devin REST API.  No hardcoded session ID needed --
the agent can spin up a new Devin session on demand, switch between sessions,
and persist the active session across restarts.

Tools provided:
    devin_create   -- Create a new Devin session with a prompt
    devin_send     -- Send a message to the active (or specified) session
    devin_read     -- Read messages from the active session (cursor-based polling)
    devin_status   -- Check session status (active/working/waiting/suspended)
    devin_list     -- List recent sessions (find existing ones to reconnect)
    devin_switch   -- Switch the active session to an existing one

Setup:
    Set environment variables:
        DEVIN_API_KEY  -- Service user API key (starts with cog_)
        DEVIN_ORG_ID   -- Your Devin organization ID

    The tool automatically persists the active session to ~/.hermes/devin_session.json
    so it survives agent restarts.

API Reference:
    https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions
    https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions-messages
    https://docs.devin.ai/api-reference/v3/sessions/get-organizations-session-messages
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEVIN_API_BASE = "https://api.devin.ai/v3"
SESSION_STATE_FILE = os.path.expanduser("~/.hermes/devin_session.json")


def _get_config():
    """Read Devin API configuration from environment."""
    api_key = os.getenv("DEVIN_API_KEY", "")
    org_id = os.getenv("DEVIN_ORG_ID", "")
    return api_key, org_id


def _check_devin():
    """Gate: tool is available only when API key and org ID are configured."""
    api_key, org_id = _get_config()
    return bool(api_key and org_id)


# ---------------------------------------------------------------------------
# Active session state (in-memory + persisted to disk)
# ---------------------------------------------------------------------------

_active_session: Optional[dict] = None  # { session_id, url, title, created_at }
_last_read_cursor: Optional[str] = None


def _load_session_state():
    """Load persisted session state from disk."""
    global _active_session, _last_read_cursor
    try:
        data = json.loads(Path(SESSION_STATE_FILE).read_text())
        _active_session = data.get("active_session")
        _last_read_cursor = data.get("last_read_cursor")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def _save_session_state():
    """Persist current session state to disk so it survives restarts."""
    try:
        path = Path(SESSION_STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "active_session": _active_session,
            "last_read_cursor": _last_read_cursor,
            "updated_at": int(time.time()),
        }, indent=2))
    except OSError as e:
        logger.warning("Failed to save Devin session state: %s", e)


def _get_active_session_id() -> Optional[str]:
    """Return the active session ID, loading from disk if needed."""
    global _active_session
    if _active_session is None:
        _load_session_state()
    return _active_session["session_id"] if _active_session else None


def _devin_id(session_id: str) -> str:
    """Format session ID as devin_id for API calls."""
    return f"devin-{session_id}" if not session_id.startswith("devin-") else session_id


# Load on import so the agent picks up the last session automatically
_load_session_state()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

DEVIN_CREATE_SCHEMA = {
    "name": "devin_create",
    "description": (
        "Create a new Devin AI session. Devin will start working on the given prompt "
        "immediately. The new session becomes the active session for subsequent "
        "devin_send / devin_read calls. Use this when you need Devin to work on a "
        "new task, or when no active session exists.\n\n"
        "Optional: specify repos (e.g. 'owner/repo') for Devin to access, "
        "a playbook_id to follow a saved workflow, or tags for organization."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task or instruction for Devin to work on"
            },
            "title": {
                "type": "string",
                "description": "Optional session title for easy identification"
            },
            "repos": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of GitHub repos in 'owner/repo' format for Devin to access"
            },
            "playbook_id": {
                "type": "string",
                "description": "Optional playbook ID to use for this session"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for organizing sessions (e.g. 'viper', 'mirofish', 'trading')"
            }
        },
        "required": ["prompt"]
    }
}

DEVIN_SEND_SCHEMA = {
    "name": "devin_send",
    "description": (
        "Send a message to the active Devin session (or a specific session by ID). "
        "Use this to communicate with Devin -- ask questions, share findings, "
        "request code changes, or coordinate on tasks. If the session is suspended, "
        "it will automatically resume."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message to send to Devin"
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID to send to (defaults to active session)"
            }
        },
        "required": ["message"]
    }
}

DEVIN_READ_SCHEMA = {
    "name": "devin_read",
    "description": (
        "Read recent messages from the active Devin session. Returns messages "
        "in chronological order with source (devin/user), content, and timestamp. "
        "Automatically tracks a cursor so subsequent calls return only NEW messages. "
        "Use 'all_messages: true' to see the full conversation instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of messages to return (default: 10, max: 200)"
            },
            "all_messages": {
                "type": "boolean",
                "description": "If true, return all messages from the start (ignore cursor). Default: false"
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID to read from (defaults to active session)"
            }
        },
        "required": []
    }
}

DEVIN_STATUS_SCHEMA = {
    "name": "devin_status",
    "description": (
        "Check the current status of the active Devin session (or a specific one). "
        "Returns status (running/suspended/exit), detail (working/waiting_for_user/"
        "finished), title, URL, and pull requests if any were created."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID to check (defaults to active session)"
            }
        },
        "required": []
    }
}

DEVIN_LIST_SCHEMA = {
    "name": "devin_list",
    "description": (
        "List recent Devin sessions. Use this to find existing sessions to reconnect "
        "to, check which sessions are still running, or see what work Devin has done. "
        "Returns session ID, status, title, URL, and creation time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of sessions to return (default: 10)"
            },
            "status": {
                "type": "string",
                "enum": ["running", "suspended", "exit", "error"],
                "description": "Optional: filter to sessions with this status"
            }
        },
        "required": []
    }
}

DEVIN_SWITCH_SCHEMA = {
    "name": "devin_switch",
    "description": (
        "Switch the active Devin session to an existing one by session ID. "
        "Subsequent devin_send / devin_read calls will target this session. "
        "Resets the read cursor so you see the full conversation history on next read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The session ID to switch to"
            }
        },
        "required": ["session_id"]
    }
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _api_request(method: str, path: str, body: dict = None) -> dict:
    """Make an authenticated request to the Devin API."""
    try:
        import httpx
    except ImportError:
        try:
            import aiohttp
            return await _api_request_aiohttp(method, path, body)
        except ImportError:
            return {"error": "Neither httpx nor aiohttp installed. Run: pip install httpx"}

    api_key, org_id = _get_config()
    url = f"{DEVIN_API_BASE}/organizations/{org_id}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=body or {})
        else:
            return {"error": f"Unsupported method: {method}"}

        if resp.status_code >= 400:
            return {"error": f"Devin API error ({resp.status_code}): {resp.text[:500]}"}
        return resp.json()


async def _api_request_aiohttp(method: str, path: str, body: dict = None) -> dict:
    """Fallback using aiohttp if httpx is not available."""
    import aiohttp

    api_key, org_id = _get_config()
    url = f"{DEVIN_API_BASE}/organizations/{org_id}{path}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        if method == "GET":
            async with session.get(url, headers=headers) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return {"error": f"Devin API error ({resp.status}): {text[:500]}"}
                return await resp.json()
        elif method == "POST":
            async with session.post(url, headers=headers, json=body or {}) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return {"error": f"Devin API error ({resp.status}): {text[:500]}"}
                return await resp.json()
    return {"error": f"Unsupported method: {method}"}


# ---------------------------------------------------------------------------
# Tool handlers (async)
# ---------------------------------------------------------------------------

async def _devin_create_async(args: dict) -> str:
    """Create a new Devin session."""
    global _active_session, _last_read_cursor

    prompt = args.get("prompt", "").strip()
    if not prompt:
        return json.dumps({"error": "Prompt cannot be empty"})

    body = {"prompt": prompt}
    if args.get("title"):
        body["title"] = args["title"]
    if args.get("repos"):
        body["repos"] = args["repos"]
    if args.get("playbook_id"):
        body["playbook_id"] = args["playbook_id"]
    if args.get("tags"):
        body["tags"] = args["tags"]

    result = await _api_request("POST", "/sessions", body)

    if "error" in result:
        return json.dumps(result)

    session_id = result.get("session_id", "")
    _active_session = {
        "session_id": session_id,
        "url": result.get("url", ""),
        "title": args.get("title", ""),
        "created_at": result.get("created_at", int(time.time())),
    }
    _last_read_cursor = None
    _save_session_state()

    return json.dumps({
        "success": True,
        "session_id": session_id,
        "url": result.get("url", ""),
        "status": result.get("status", "new"),
        "message": f"Session created and set as active. Devin is now working on: {prompt[:120]}",
    })


async def _devin_send_async(args: dict) -> str:
    """Send a message to a Devin session."""
    message = args.get("message", "").strip()
    if not message:
        return json.dumps({"error": "Message cannot be empty"})

    session_id = args.get("session_id") or _get_active_session_id()
    if not session_id:
        return json.dumps({
            "error": "No active Devin session. Use devin_create to start one, "
                     "devin_list to find existing ones, or devin_switch to reconnect."
        })

    result = await _api_request("POST", f"/sessions/{_devin_id(session_id)}/messages", {
        "message": message,
    })

    if "error" in result:
        return json.dumps(result)

    return json.dumps({
        "success": True,
        "session_id": session_id,
        "status": result.get("status", "unknown"),
        "status_detail": result.get("status_detail"),
        "message_preview": message[:100] + ("..." if len(message) > 100 else ""),
    })


async def _devin_read_async(args: dict) -> str:
    """Read messages from a Devin session."""
    global _last_read_cursor

    session_id = args.get("session_id") or _get_active_session_id()
    if not session_id:
        return json.dumps({
            "error": "No active Devin session. Use devin_create or devin_switch first."
        })

    limit = min(args.get("limit", 10), 200)
    all_messages = args.get("all_messages", False)

    query = f"?first={limit}"
    if not all_messages and _last_read_cursor:
        query += f"&after={_last_read_cursor}"

    result = await _api_request("GET", f"/sessions/{_devin_id(session_id)}/messages{query}")

    if "error" in result:
        return json.dumps(result)

    messages = []
    for item in result.get("items", []):
        messages.append({
            "source": item.get("source", "unknown"),
            "message": item.get("message", ""),
            "timestamp": item.get("created_at", 0),
            "event_id": item.get("event_id", ""),
        })

    end_cursor = result.get("end_cursor")
    if end_cursor:
        _last_read_cursor = end_cursor
        _save_session_state()

    return json.dumps({
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "has_more": result.get("has_next_page", False),
        "end_cursor": end_cursor,
    })


async def _devin_status_async(args: dict) -> str:
    """Check status of a Devin session."""
    session_id = args.get("session_id") or _get_active_session_id()
    if not session_id:
        return json.dumps({
            "error": "No active Devin session. Use devin_create or devin_switch first."
        })

    result = await _api_request("GET", f"/sessions/{_devin_id(session_id)}")

    if "error" in result:
        return json.dumps(result)

    prs = result.get("pull_requests", [])
    pr_info = [{"url": pr.get("pr_url"), "state": pr.get("pr_state")} for pr in prs]

    return json.dumps({
        "session_id": session_id,
        "status": result.get("status", "unknown"),
        "status_detail": result.get("status_detail"),
        "title": result.get("title", ""),
        "url": result.get("url", ""),
        "updated_at": result.get("updated_at", 0),
        "pull_requests": pr_info if pr_info else None,
        "is_active_session": session_id == _get_active_session_id(),
    })


async def _devin_list_async(args: dict) -> str:
    """List recent Devin sessions."""
    limit = min(args.get("limit", 10), 50)
    status_filter = args.get("status")

    query = f"?first={limit}"
    if status_filter:
        query += f"&status={status_filter}"

    result = await _api_request("GET", f"/sessions{query}")

    if "error" in result:
        return json.dumps(result)

    items = result if isinstance(result, list) else result.get("items", result.get("sessions", []))
    sessions = []
    for s in items[:limit]:
        sessions.append({
            "session_id": s.get("session_id", ""),
            "status": s.get("status", ""),
            "status_detail": s.get("status_detail", ""),
            "title": s.get("title", ""),
            "url": s.get("url", ""),
            "created_at": s.get("created_at", 0),
            "updated_at": s.get("updated_at", 0),
            "is_active": s.get("session_id") == _get_active_session_id(),
        })

    active_id = _get_active_session_id()
    return json.dumps({
        "sessions": sessions,
        "count": len(sessions),
        "active_session_id": active_id,
        "tip": "Use devin_switch(session_id='...') to reconnect to any session",
    })


async def _devin_switch_async(args: dict) -> str:
    """Switch active session."""
    global _active_session, _last_read_cursor

    session_id = args.get("session_id", "").strip()
    if not session_id:
        return json.dumps({"error": "session_id is required"})

    # Verify the session exists
    result = await _api_request("GET", f"/sessions/{_devin_id(session_id)}")
    if "error" in result:
        return json.dumps(result)

    _active_session = {
        "session_id": session_id,
        "url": result.get("url", ""),
        "title": result.get("title", ""),
        "created_at": result.get("created_at", 0),
    }
    _last_read_cursor = None  # Reset cursor for new session
    _save_session_state()

    return json.dumps({
        "success": True,
        "session_id": session_id,
        "status": result.get("status", "unknown"),
        "status_detail": result.get("status_detail"),
        "title": result.get("title", ""),
        "url": result.get("url", ""),
        "message": f"Switched active session to {session_id}. Read cursor reset.",
    })


# ---------------------------------------------------------------------------
# Sync wrappers (registry dispatches sync; async bridged by model_tools)
# ---------------------------------------------------------------------------

def devin_create_tool(args, **kw):
    from model_tools import _run_async
    return _run_async(_devin_create_async(args))

def devin_send_tool(args, **kw):
    from model_tools import _run_async
    return _run_async(_devin_send_async(args))

def devin_read_tool(args, **kw):
    from model_tools import _run_async
    return _run_async(_devin_read_async(args))

def devin_status_tool(args, **kw):
    from model_tools import _run_async
    return _run_async(_devin_status_async(args))

def devin_list_tool(args, **kw):
    from model_tools import _run_async
    return _run_async(_devin_list_async(args))

def devin_switch_tool(args, **kw):
    from model_tools import _run_async
    return _run_async(_devin_switch_async(args))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

from tools.registry import registry

registry.register(
    name="devin_create",
    toolset="devin",
    schema=DEVIN_CREATE_SCHEMA,
    handler=devin_create_tool,
    check_fn=_check_devin,
    requires_env=["DEVIN_API_KEY", "DEVIN_ORG_ID"],
    description="Create a new Devin AI session and start it working on a task",
)

registry.register(
    name="devin_send",
    toolset="devin",
    schema=DEVIN_SEND_SCHEMA,
    handler=devin_send_tool,
    check_fn=_check_devin,
    requires_env=["DEVIN_API_KEY", "DEVIN_ORG_ID"],
    description="Send a message to a Devin AI session for collaborative engineering",
)

registry.register(
    name="devin_read",
    toolset="devin",
    schema=DEVIN_READ_SCHEMA,
    handler=devin_read_tool,
    check_fn=_check_devin,
    requires_env=["DEVIN_API_KEY", "DEVIN_ORG_ID"],
    description="Read messages from a Devin AI session",
)

registry.register(
    name="devin_status",
    toolset="devin",
    schema=DEVIN_STATUS_SCHEMA,
    handler=devin_status_tool,
    check_fn=_check_devin,
    requires_env=["DEVIN_API_KEY", "DEVIN_ORG_ID"],
    description="Check the status of a Devin AI session",
)

registry.register(
    name="devin_list",
    toolset="devin",
    schema=DEVIN_LIST_SCHEMA,
    handler=devin_list_tool,
    check_fn=_check_devin,
    requires_env=["DEVIN_API_KEY", "DEVIN_ORG_ID"],
    description="List recent Devin sessions to find and reconnect to existing ones",
)

registry.register(
    name="devin_switch",
    toolset="devin",
    schema=DEVIN_SWITCH_SCHEMA,
    handler=devin_switch_tool,
    check_fn=_check_devin,
    requires_env=["DEVIN_API_KEY", "DEVIN_ORG_ID"],
    description="Switch the active Devin session to an existing one",
)

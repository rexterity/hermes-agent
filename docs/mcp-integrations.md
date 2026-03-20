# MCP Integration Layer

Wire external services into Hermes Agent as first-class MCP tools.
All integrations are configured in `cli-config.yaml` under `mcp_servers:` and
are auto-discovered by `tools/mcp_tool.py`.

## Quick Reference

| # | Integration       | Type            | Auth Required                  |
|---|-------------------|-----------------|--------------------------------|
| 1 | GitHub            | npx (official)  | `GITHUB_TOKEN`                 |
| 2 | Linear            | npx (community) | `LINEAR_API_KEY`               |
| 3 | Notion            | npx (community) | `NOTION_API_KEY`               |
| 4 | Google Workspace  | npx (community) | OAuth (client ID/secret/token) |
| 5 | Devin             | custom bridge   | `DEVIN_API_KEY` + `DEVIN_ORG_ID` |
| 6 | Jules             | custom bridge   | `JULES_API_KEY`                |

## Setup

### 1. GitHub MCP

Zero custom code. Uses the official `@modelcontextprotocol/server-github`.

```yaml
mcp_servers:
  github:
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    timeout: 120
```

**Tools exposed:** `create_issue`, `create_pull_request`, `search_repositories`,
`get_file_contents`, `push_files`, `list_commits`, `create_branch`, etc.

### 2. Linear MCP

Zero custom code. Uses `@mseep/linear-mcp`.

```yaml
mcp_servers:
  linear:
    command: "npx"
    args: ["-y", "@mseep/linear-mcp"]
    env:
      LINEAR_API_KEY: "${LINEAR_API_KEY}"
    timeout: 120
```

**Tools exposed:** `create_issue`, `update_issue`, `search_issues`,
`list_projects`, `create_comment`, `assign_issue`, `manage_cycles`, etc.

### 3. Notion MCP

Zero custom code. Uses `@mieubrisse/notion-mcp-server`.

Create an integration at <https://notion.so/my-integrations> and add it to
the pages/databases you want the agent to access.

```yaml
mcp_servers:
  notion:
    command: "npx"
    args: ["-y", "@mieubrisse/notion-mcp-server"]
    env:
      NOTION_API_KEY: "${NOTION_API_KEY}"
    timeout: 120
```

**Tools exposed:** `search`, `create_page`, `update_page`, `query_database`,
`create_database`, `append_blocks`, etc.

### 4. Google Workspace MCP

Uses `@presto-ai/google-workspace-mcp`. Evaluate against the existing
`gws-auth-env` CLI before switching — the MCP server needs the same OAuth
credentials.

```yaml
mcp_servers:
  google:
    command: "npx"
    args: ["-y", "@presto-ai/google-workspace-mcp"]
    env:
      GOOGLE_CLIENT_ID: "${GOOGLE_CLIENT_ID}"
      GOOGLE_CLIENT_SECRET: "${GOOGLE_CLIENT_SECRET}"
      GOOGLE_REFRESH_TOKEN: "${GOOGLE_REFRESH_TOKEN}"
    timeout: 120
```

**Tools exposed:** `send_email`, `search_email`, `create_event`,
`list_events`, `upload_to_drive`, `search_drive`, `create_doc`,
`create_sheet`, etc.

### 5. Devin MCP Bridge

Custom stdio MCP server wrapping the Devin REST API. Source lives in
`mcp-bridges/devin-mcp/`.

**Install dependencies:**

```bash
cd mcp-bridges/devin-mcp && npm install
```

**Config:**

```yaml
mcp_servers:
  devin:
    command: "node"
    args: ["mcp-bridges/devin-mcp/index.js"]
    env:
      DEVIN_API_KEY: "${DEVIN_API_KEY}"
      DEVIN_ORG_ID: "${DEVIN_ORG_ID}"
    timeout: 300
```

**Tools exposed:**

| Tool                        | Description                                |
|-----------------------------|--------------------------------------------|
| `devin_create_session`      | Create a new Devin session                 |
| `devin_get_session`         | Get session status, details, and PRs       |
| `devin_send_message`        | Send a message (auto-resumes suspended)    |
| `devin_list_sessions`       | List sessions with optional status filter  |
| `devin_get_session_events`  | Paginated event/message history            |

> **Note:** The agent also has a native `devin` toolset (`tools/devin_tool.py`)
> that provides the same functionality without MCP. Use the MCP bridge when you
> want a unified MCP-only tool surface; use the native toolset when you want
> tighter integration (e.g., active-session tracking persisted to disk).

### 6. Jules MCP Bridge

Custom stdio MCP server wrapping the Jules (Google) API. Source lives in
`mcp-bridges/jules-mcp/`.

**Install dependencies:**

```bash
cd mcp-bridges/jules-mcp && npm install
```

**Config:**

```yaml
mcp_servers:
  jules:
    command: "node"
    args: ["mcp-bridges/jules-mcp/index.js"]
    env:
      JULES_API_KEY: "${JULES_API_KEY}"
    timeout: 300
```

**Tools exposed:**

| Tool                | Description                                  |
|---------------------|----------------------------------------------|
| `jules_create_task` | Create a coding task against a GitHub repo   |
| `jules_get_task`    | Get task status, PR URL, and output          |
| `jules_list_tasks`  | List tasks with optional status filter       |
| `jules_approve_plan`| Approve a proposed plan so Jules implements  |

## Architecture

```
cli-config.yaml
  mcp_servers:
    github  -> @modelcontextprotocol/server-github   (npx)
    linear  -> @mseep/linear-mcp                     (npx)
    notion  -> @mieubrisse/notion-mcp-server          (npx)
    google  -> @presto-ai/google-workspace-mcp        (npx)
    devin   -> mcp-bridges/devin-mcp/index.js         (node)
    jules   -> mcp-bridges/jules-mcp/index.js         (node)
```

All servers are launched as stdio subprocesses by `tools/mcp_tool.py`. The
MCP client handles:

- Automatic tool discovery and registration into the agent tool registry
- Reconnection with exponential backoff (up to 5 retries)
- Environment variable filtering (security — only safe vars + explicit env)
- Credential stripping in error messages
- Configurable per-server timeouts
- Sampling support (server-initiated LLM requests)

## Environment Variables

Set these in `~/.hermes/.env` or export them before running the agent:

```bash
# GitHub
GITHUB_TOKEN=ghp_...

# Linear
LINEAR_API_KEY=lin_api_...

# Notion
NOTION_API_KEY=ntn_...

# Google Workspace
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...

# Devin
DEVIN_API_KEY=cog_...
DEVIN_ORG_ID=org_...

# Jules
JULES_API_KEY=...
```

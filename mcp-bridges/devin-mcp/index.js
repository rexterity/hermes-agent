#!/usr/bin/env node
/**
 * Devin MCP Bridge — stdio MCP server wrapping the Devin REST API.
 *
 * Exposes Devin session management as MCP tools so any MCP-compatible agent
 * can create, monitor, message, and list Devin sessions.
 *
 * Environment variables:
 *   DEVIN_API_KEY  — Service-user API key (starts with cog_)
 *   DEVIN_ORG_ID   — Devin organization ID
 *
 * Usage:
 *   DEVIN_API_KEY=cog_... DEVIN_ORG_ID=org_... node index.js
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const API_BASE = "https://api.devin.ai/v3";
const API_KEY = process.env.DEVIN_API_KEY || "";
const ORG_ID = process.env.DEVIN_ORG_ID || "";

// ---------------------------------------------------------------------------
// HTTP helper
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const url = `${API_BASE}/organizations/${ORG_ID}${path}`;
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);

  const res = await fetch(url, opts);
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`Devin API ${res.status}: ${text.slice(0, 500)}`);
  }
  return text ? JSON.parse(text) : {};
}

function devinId(sessionId) {
  return sessionId.startsWith("devin-") ? sessionId : `devin-${sessionId}`;
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "devin-mcp",
  version: "1.0.0",
});

// --- devin_create_session ---------------------------------------------------

server.tool(
  "devin_create_session",
  "Create a new Devin session. Devin starts working on the prompt immediately.",
  {
    prompt: z.string().describe("Task or instruction for Devin"),
    title: z.string().optional().describe("Session title for identification"),
    repos: z
      .array(z.string())
      .optional()
      .describe("GitHub repos in owner/repo format"),
    playbook_id: z
      .string()
      .optional()
      .describe("Playbook ID to follow"),
    tags: z
      .array(z.string())
      .optional()
      .describe("Tags for organizing sessions"),
  },
  async ({ prompt, title, repos, playbook_id, tags }) => {
    const body = { prompt };
    if (title) body.title = title;
    if (repos) body.repos = repos;
    if (playbook_id) body.playbook_id = playbook_id;
    if (tags) body.tags = tags;

    const result = await api("POST", "/sessions", body);
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              session_id: result.session_id,
              url: result.url,
              status: result.status,
            },
            null,
            2
          ),
        },
      ],
    };
  }
);

// --- devin_get_session -------------------------------------------------------

server.tool(
  "devin_get_session",
  "Get the current status, details, and pull requests of a Devin session.",
  {
    session_id: z.string().describe("Devin session ID"),
  },
  async ({ session_id }) => {
    const result = await api("GET", `/sessions/${devinId(session_id)}`);
    const prs = (result.pull_requests || []).map((pr) => ({
      url: pr.pr_url,
      state: pr.pr_state,
    }));
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              session_id,
              status: result.status,
              status_detail: result.status_detail,
              title: result.title,
              url: result.url,
              updated_at: result.updated_at,
              pull_requests: prs.length ? prs : undefined,
            },
            null,
            2
          ),
        },
      ],
    };
  }
);

// --- devin_send_message ------------------------------------------------------

server.tool(
  "devin_send_message",
  "Send a message to a Devin session. Resumes suspended sessions automatically.",
  {
    session_id: z.string().describe("Devin session ID"),
    message: z.string().describe("Message to send"),
  },
  async ({ session_id, message }) => {
    const result = await api(
      "POST",
      `/sessions/${devinId(session_id)}/messages`,
      { message }
    );
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              success: true,
              session_id,
              status: result.status,
              status_detail: result.status_detail,
            },
            null,
            2
          ),
        },
      ],
    };
  }
);

// --- devin_list_sessions -----------------------------------------------------

server.tool(
  "devin_list_sessions",
  "List recent Devin sessions, optionally filtered by status.",
  {
    limit: z
      .number()
      .int()
      .min(1)
      .max(50)
      .optional()
      .default(10)
      .describe("Max sessions to return (default 10)"),
    status_filter: z
      .enum(["running", "suspended", "exit", "error"])
      .optional()
      .describe("Filter by session status"),
  },
  async ({ limit, status_filter }) => {
    let query = `?first=${limit}`;
    if (status_filter) query += `&status=${status_filter}`;

    const result = await api("GET", `/sessions${query}`);
    const items = Array.isArray(result)
      ? result
      : result.items || result.sessions || [];

    const sessions = items.slice(0, limit).map((s) => ({
      session_id: s.session_id,
      status: s.status,
      status_detail: s.status_detail,
      title: s.title,
      url: s.url,
      created_at: s.created_at,
      updated_at: s.updated_at,
    }));

    return {
      content: [
        { type: "text", text: JSON.stringify({ sessions, count: sessions.length }, null, 2) },
      ],
    };
  }
);

// --- devin_get_session_events ------------------------------------------------

server.tool(
  "devin_get_session_events",
  "Get messages / events from a Devin session (cursor-based pagination).",
  {
    session_id: z.string().describe("Devin session ID"),
    limit: z
      .number()
      .int()
      .min(1)
      .max(200)
      .optional()
      .default(20)
      .describe("Max events to return (default 20)"),
    after: z
      .string()
      .optional()
      .describe("Cursor — return events after this cursor for pagination"),
  },
  async ({ session_id, limit, after }) => {
    let query = `?first=${limit}`;
    if (after) query += `&after=${after}`;

    const result = await api(
      "GET",
      `/sessions/${devinId(session_id)}/messages${query}`
    );

    const events = (result.items || []).map((item) => ({
      source: item.source,
      message: item.message,
      timestamp: item.created_at,
      event_id: item.event_id,
    }));

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              session_id,
              events,
              count: events.length,
              has_more: result.has_next_page || false,
              end_cursor: result.end_cursor,
            },
            null,
            2
          ),
        },
      ],
    };
  }
);

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function main() {
  if (!API_KEY || !ORG_ID) {
    console.error(
      "Error: DEVIN_API_KEY and DEVIN_ORG_ID environment variables are required."
    );
    process.exit(1);
  }
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});

#!/usr/bin/env node
/**
 * Jules MCP Bridge — stdio MCP server wrapping the Jules (Google) API.
 *
 * Exposes Jules task management as MCP tools so any MCP-compatible agent
 * can create, monitor, list, and approve Jules coding tasks.
 *
 * Environment variables:
 *   JULES_API_KEY  — Jules API key (Google AI Ultra subscription)
 *
 * Usage:
 *   JULES_API_KEY=... node index.js
 *
 * API reference:
 *   https://developers.google.com/jules (subject to change)
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const API_BASE = process.env.JULES_API_BASE || "https://jules.google.com/api/v1";
const API_KEY = process.env.JULES_API_KEY || "";

// ---------------------------------------------------------------------------
// HTTP helper
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const url = `${API_BASE}${path}`;
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
    throw new Error(`Jules API ${res.status}: ${text.slice(0, 500)}`);
  }
  return text ? JSON.parse(text) : {};
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "jules-mcp",
  version: "1.0.0",
});

// --- jules_create_task -------------------------------------------------------

server.tool(
  "jules_create_task",
  "Create a new Jules coding task. Jules begins working on the prompt immediately against the specified repo.",
  {
    prompt: z.string().describe("Task description or coding instruction"),
    repo: z.string().describe("GitHub repo in owner/repo format"),
    branch: z
      .string()
      .optional()
      .describe("Target branch (defaults to repo default branch)"),
    title: z
      .string()
      .optional()
      .describe("Task title for identification"),
  },
  async ({ prompt, repo, branch, title }) => {
    const body = { prompt, repo };
    if (branch) body.branch = branch;
    if (title) body.title = title;

    const result = await api("POST", "/tasks", body);
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              task_id: result.task_id || result.id,
              status: result.status,
              url: result.url,
            },
            null,
            2
          ),
        },
      ],
    };
  }
);

// --- jules_get_task ----------------------------------------------------------

server.tool(
  "jules_get_task",
  "Get the current status, output, and PR URL of a Jules task.",
  {
    task_id: z.string().describe("Jules task ID"),
  },
  async ({ task_id }) => {
    const result = await api("GET", `/tasks/${task_id}`);
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              task_id,
              status: result.status,
              pr_url: result.pr_url || result.pull_request_url,
              output: result.output || result.summary,
              title: result.title,
              url: result.url,
              updated_at: result.updated_at,
            },
            null,
            2
          ),
        },
      ],
    };
  }
);

// --- jules_list_tasks --------------------------------------------------------

server.tool(
  "jules_list_tasks",
  "List recent Jules tasks, optionally filtered by status.",
  {
    limit: z
      .number()
      .int()
      .min(1)
      .max(50)
      .optional()
      .default(10)
      .describe("Max tasks to return (default 10)"),
    status_filter: z
      .string()
      .optional()
      .describe("Filter by task status (e.g. running, completed, pending_approval)"),
  },
  async ({ limit, status_filter }) => {
    let query = `?limit=${limit}`;
    if (status_filter) query += `&status=${encodeURIComponent(status_filter)}`;

    const result = await api("GET", `/tasks${query}`);
    const items = Array.isArray(result)
      ? result
      : result.tasks || result.items || [];

    const tasks = items.slice(0, limit).map((t) => ({
      task_id: t.task_id || t.id,
      status: t.status,
      title: t.title,
      repo: t.repo,
      pr_url: t.pr_url || t.pull_request_url,
      url: t.url,
      created_at: t.created_at,
      updated_at: t.updated_at,
    }));

    return {
      content: [
        { type: "text", text: JSON.stringify({ tasks, count: tasks.length }, null, 2) },
      ],
    };
  }
);

// --- jules_approve_plan ------------------------------------------------------

server.tool(
  "jules_approve_plan",
  "Approve a Jules task plan so Jules proceeds with implementation. Use after reviewing the plan Jules proposes.",
  {
    task_id: z.string().describe("Jules task ID whose plan to approve"),
  },
  async ({ task_id }) => {
    const result = await api("POST", `/tasks/${task_id}/approve`, {});
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(
            {
              success: true,
              task_id,
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

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function main() {
  if (!API_KEY) {
    console.error(
      "Error: JULES_API_KEY environment variable is required."
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

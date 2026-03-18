---
name: devin
description: Delegate tasks to Devin AI via the Devin REST API. Use for implementation work that needs a full dev environment — installing deps, running servers, browser testing, multi-file PRs. Devin runs on its own VM and works asynchronously.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Coding-Agent, Devin, Delegation, Implementation, PR-Review]
    related_skills: [claude-code, codex, hermes-agent, subagent-driven-development]
---

# Devin AI

Delegate implementation tasks to [Devin](https://devin.ai) via the Devin REST API. Devin is a fully autonomous software engineering agent with its own VM, browser, and shell.

## Prerequisites

- `DEVIN_API_KEY` set (service user key, starts with `cog_`)
- `DEVIN_ORG_ID` set (organization ID from Settings > Service Users)

## When to Use Devin vs Other Agents

- **Devin**: Full dev environment needed (install deps, run servers, browser testing, multi-hour PRs)
- **delegate_task**: Quick subtasks under 5 minutes, no repo access needed
- **Claude Code / Codex**: Fast single-file code generation or refactoring via terminal

## Creating a Session

Include repo, branch, scope, acceptance criteria, and test commands in the prompt. Be explicit — Devin starts cold with no project context unless you've set up knowledge notes.

```
devin_create(
  prompt="## Task: Add rate limiting to the API\n\nRepo: rexterity/viper\nBranch: main\nFiles in scope: src/api/middleware/\n\n## Requirements\n1. Add rate limiting middleware using express-rate-limit\n2. 100 requests per 15 min per IP\n3. Return 429 with retry-after header\n\n## Acceptance Criteria\n- All existing tests pass: npm test\n- New tests for rate limit behavior\n- Create PR into main",
  repos=["rexterity/viper"],
  title="Add API rate limiting",
  tags=["viper", "backend"]
)
```

For recurring task patterns, use a playbook instead of writing prompts from scratch:
```
devin_create(prompt="Add CSV export to the reports page", playbook_id="<id>", repos=["rexterity/app"])
```

## Monitoring

Devin is async — it runs for minutes to hours. Poll status, don't block.

```
# Check what Devin is doing
devin_status()
# → status: "running", status_detail: "working", pull_requests: [...]

# Read messages if Devin asked a question
devin_read()

# Answer if status_detail is "waiting_for_user"
devin_send(message="Yes, use PostgreSQL not SQLite for this project.")
```

## Reviewing Devin's Work

When `devin_status()` shows `pull_requests`, review the PR and iterate:

```
# 1. Check status — look for pull_requests in the response
devin_status()

# 2. Review the PR diff (use terminal or delegate a reviewer subagent)
terminal(command="gh pr diff <number> --repo owner/repo")

# 3. If changes needed, send feedback — Devin will push to the same branch
devin_send(message="Two issues:\n1. Missing error handling in the upload endpoint\n2. Tests don't cover the empty-file case\nPlease fix and push.")

# 4. Re-review after Devin pushes. Approve and merge when satisfied.
terminal(command="gh pr review <number> --approve --repo owner/repo")
terminal(command="gh pr merge <number> --squash --repo owner/repo")
```

## Knowledge Notes

Push project conventions once — Devin gets them in every future session automatically.

```
# Create a knowledge note with a trigger condition
devin_knowledge(
  action="create",
  name="Rexterity Python standards",
  trigger_description="When working on any rexterity/* repository",
  body="Python 3.11+. Type hints required. pytest for testing. Black for formatting. Use existing patterns in the codebase."
)

# List existing notes
devin_knowledge(action="list")

# Update a note
devin_knowledge(action="update", note_id="<id>", body="Updated standards...")

# Delete a note
devin_knowledge(action="delete", note_id="<id>")
```

## Playbooks

Create reusable workflow templates for common task types:

```
# Create a playbook
devin_playbook(
  action="create",
  name="Bug Fix",
  body="1. Reproduce the bug with a failing test\n2. Fix the root cause\n3. Verify all tests pass\n4. Create PR with description explaining the fix"
)

# List playbooks to find IDs
devin_playbook(action="list")

# Use it when creating a session
devin_create(prompt="Fix: CSV export drops unicode characters", playbook_id="<id>", repos=["rexterity/app"])
```

## Multi-Session Work

Track multiple parallel Devin sessions:

```
# List running sessions
devin_list(status="running")

# Switch to a different session to check on it
devin_switch(session_id="<id>")
devin_status()
devin_read()

# Switch back
devin_switch(session_id="<original_id>")
```

## Stopping a Session

```
# Terminate a runaway or unneeded session (irreversible)
devin_terminate()

# Or terminate a specific session
devin_terminate(session_id="<id>")
```

## Rules

1. **Always include repos** — Devin needs repo access to clone and work
2. **Be explicit in prompts** — include branch, file scope, test commands, acceptance criteria
3. **Use knowledge notes** — push project conventions once instead of repeating in every prompt
4. **Tag sessions** — makes it easy to find related work later with `devin_list`
5. **Review PRs before merging** — dispatch a reviewer subagent or review the diff yourself
6. **Don't poll too aggressively** — check every few minutes, not every few seconds
7. **Use `devin_terminate` for stuck sessions** — don't let runaway sessions waste compute
8. **Use playbooks for patterns** — if you delegate the same type of task repeatedly, make a playbook

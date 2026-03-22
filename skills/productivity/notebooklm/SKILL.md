---
name: notebooklm
description: Google NotebookLM CLI for creating notebooks, importing sources (URLs, PDFs, YouTube, Google Drive), generating content (podcasts, videos, slide decks, quizzes, flashcards, reports, mind maps, infographics), chatting with sources, and downloading artifacts. Uses notebooklm-py (unofficial).
version: 1.0.0
author: community
license: MIT
metadata:
  hermes:
    tags: [NotebookLM, Google, Research, Podcast, AI, Productivity, Content Generation]
    homepage: https://github.com/teng-lin/notebooklm-py
prerequisites:
  commands: [notebooklm]
---

# NotebookLM CLI (notebooklm-py)

Unofficial Python CLI for Google NotebookLM. Full programmatic access to NotebookLM features — create notebooks, import sources, generate audio/video/slides/quizzes, chat with sources, and download artifacts.

> **Unofficial library** — uses undocumented Google APIs that can change without notice. Best for prototypes, research, and personal projects.

## Installation

```bash
# Basic install
pip install notebooklm-py

# With browser login support (required for first-time auth)
pip install "notebooklm-py[browser]"
playwright install chromium
```

Or via `uv`:
```bash
uv tool install notebooklm-py
```

Verify: `notebooklm --version`

## Authentication

NotebookLM uses browser-based Google OAuth. No API key — session cookies are stored locally.

```bash
# First-time login (opens browser for Google sign-in)
notebooklm login

# Use Microsoft Edge (for orgs requiring Edge SSO)
notebooklm login --browser msedge

# Check auth status
notebooklm auth check            # Quick local validation
notebooklm auth check --test     # Full validation with network test
notebooklm auth check --json     # Machine-readable output
```

Session cookies expire every few weeks. Re-run `notebooklm login` when you get "Unauthorized" errors.

Storage location: `~/.notebooklm/storage_state.json`

**Environment overrides:**
- `NOTEBOOKLM_AUTH_JSON` — inline JSON auth data
- `NOTEBOOKLM_HOME` — custom config directory

## Quick Start

```bash
# 1. Login
notebooklm login

# 2. Create a notebook and set it active
notebooklm create "My Research"
notebooklm use <notebook_id>

# 3. Add sources
notebooklm source add "https://en.wikipedia.org/wiki/Artificial_intelligence"
notebooklm source add "./paper.pdf"

# 4. Chat with sources
notebooklm ask "What are the key themes?"

# 5. Generate a podcast
notebooklm generate audio "make it engaging" --wait

# 6. Download it
notebooklm download audio ./podcast.mp3
```

## Notebook Management

```bash
# List all notebooks
notebooklm list

# Create a notebook
notebooklm create "Research Notes"

# Set active notebook (supports partial ID matching)
notebooklm use <notebook_id>
notebooklm use abc          # matches 'abc123def456...'

# Show current context
notebooklm status

# Rename a notebook
notebooklm rename <notebook_id> "New Name"

# Delete a notebook
notebooklm delete <notebook_id>

# Get AI-generated notebook summary
notebooklm summary

# Export metadata with sources list
notebooklm metadata --json

# Clear active notebook context
notebooklm clear
```

## Source Management

Add URLs, files (PDF, text, Markdown, Word, audio, video, images), YouTube links, Google Drive docs, or pasted text.

```bash
# Add sources
notebooklm source add "https://example.com/article"
notebooklm source add "./document.pdf"
notebooklm source add "https://youtube.com/watch?v=..."
notebooklm source add "Plain text content to index"

# Add Google Drive document
notebooklm source add-drive <drive_file_id>

# Web/Drive research — search and auto-import sources
notebooklm source add-research "artificial intelligence" --mode deep
notebooklm source add-research "AI" --mode fast --no-wait

# Monitor research progress
notebooklm research status
notebooklm research wait --import-all

# List sources in current notebook
notebooklm source list

# Get source details
notebooklm source get <source_id>

# Get full indexed text content of a source
notebooklm source fulltext <source_id>

# Get AI-generated source summary and keywords
notebooklm source guide <source_id>

# Check if a URL/Drive source needs refresh
notebooklm source stale <source_id>

# Refresh a URL/Drive source
notebooklm source refresh <source_id>

# Wait for source processing to finish
notebooklm source wait <source_id>

# Rename a source
notebooklm source rename <source_id> "New Title"

# Delete a source
notebooklm source delete <source_id>
notebooklm source delete-by-title "Exact Source Title"
```

**Tip:** Source IDs support partial matching — use a prefix instead of the full UUID.

## Chat

```bash
# Ask a question (uses current notebook's sources)
notebooklm ask "What are the key themes?"

# Configure chat persona and response settings
notebooklm configure

# View conversation history
notebooklm history

# Save conversation history as a notebook note
notebooklm history --save
```

## Content Generation

All `generate` commands accept natural-language instructions. Use `--wait` to block until generation completes.

### Audio Overview (Podcast)

```bash
# Generate with default settings
notebooklm generate audio --wait

# With custom instructions
notebooklm generate audio "deep dive focusing on chapter 3" --wait

# Options: --format (deep-dive, brief, critique, debate), --length (short, medium, long)
notebooklm generate audio --format debate --length short --wait

# With retry on rate limits
notebooklm generate audio --wait --retry 3
```

### Video Overview

```bash
notebooklm generate video --wait
notebooklm generate video "a funny explainer for kids" --wait

# Options: --format (explainer, brief, cinematic), --style (9 visual styles)
notebooklm generate video --style whiteboard --wait
```

### Cinematic Video

```bash
notebooklm generate cinematic-video "documentary-style summary" --wait
```

### Slide Deck

```bash
notebooklm generate slide-deck --wait

# Revise an individual slide
notebooklm generate revise-slide <slide_number> "make it more concise"
```

### Quiz

```bash
notebooklm generate quiz --wait
notebooklm generate quiz "focus on vocabulary terms" --difficulty hard --quantity more
```

### Flashcards

```bash
notebooklm generate flashcards --wait
notebooklm generate flashcards --difficulty hard --quantity more
```

### Report

```bash
# Built-in formats: briefing-doc, study-guide, blog-post, custom
notebooklm generate report --format briefing-doc --wait
notebooklm generate report --format blog-post --wait
notebooklm generate report --format custom "compare methodologies" --wait
```

### Infographic

```bash
notebooklm generate infographic --wait
# Options: --orientation (landscape, portrait, square), --detail (low, medium, high)
notebooklm generate infographic --orientation portrait --detail high --wait
```

### Mind Map

```bash
notebooklm generate mind-map --wait
```

### Data Table

```bash
notebooklm generate data-table "compare key concepts" --wait
```

## Downloading Artifacts

```bash
# Audio/Video
notebooklm download audio ./podcast.mp3
notebooklm download video ./overview.mp4
notebooklm download cinematic-video ./documentary.mp4

# Documents
notebooklm download slide-deck ./slides.pdf       # Also supports .pptx
notebooklm download report ./report.md
notebooklm download infographic ./infographic.png

# Structured data
notebooklm download quiz --format markdown ./quiz.md
notebooklm download quiz --format json ./quiz.json
notebooklm download quiz --format html ./quiz.html
notebooklm download flashcards --format json ./cards.json
notebooklm download flashcards --format markdown ./cards.md
notebooklm download mind-map ./mindmap.json
notebooklm download data-table ./data.csv
```

## Artifact Management

```bash
# List all artifacts (or filter by type)
notebooklm artifact list
notebooklm artifact list --type audio

# Get artifact details
notebooklm artifact get <artifact_id>

# Rename an artifact
notebooklm artifact rename <artifact_id> "New Name"

# Delete an artifact
notebooklm artifact delete <artifact_id>

# Export to Google Docs/Sheets
notebooklm artifact export <artifact_id>

# Poll generation status (single check)
notebooklm artifact poll <task_id>

# Wait for generation to complete (blocking)
notebooklm artifact wait <task_id>

# Get AI-suggested report topics
notebooklm artifact suggestions
```

## Notes

```bash
# List notes in notebook
notebooklm note list

# Create a note
notebooklm note create "Note Title" "Note content here"

# Get note content
notebooklm note get <note_id>

# Update note content
notebooklm note save <note_id> "Updated content"

# Rename a note
notebooklm note rename <note_id> "New Title"

# Delete a note
notebooklm note delete <note_id>
```

## Sharing

```bash
# Show sharing status
notebooklm share status

# Enable/disable public link
notebooklm share public --enable
notebooklm share public --disable

# Set viewer access level
notebooklm share view-level full        # Full notebook access
notebooklm share view-level chat-only   # Chat only

# Share with a user
notebooklm share add user@example.com --permission viewer
notebooklm share add user@example.com --permission editor

# Update permission
notebooklm share update user@example.com --permission editor

# Remove access
notebooklm share remove user@example.com
```

## Language Support

```bash
# List supported output languages
notebooklm language list

# Set output language for artifact generation
notebooklm language set <language_code>
```

Supports 50+ languages for audio, video, and text generation.

## Typical Workflows

### Research Pipeline

```bash
notebooklm create "AI Research Survey"
notebooklm use <id>
notebooklm source add "https://arxiv.org/abs/..."
notebooklm source add "./paper1.pdf"
notebooklm source add-research "transformer architectures 2025" --mode deep
notebooklm research wait --import-all
notebooklm ask "Summarize the main contributions"
notebooklm generate report --format briefing-doc --wait
notebooklm download report ./briefing.md
```

### Podcast Creation

```bash
notebooklm create "Podcast Episode"
notebooklm use <id>
notebooklm source add "https://interesting-article.com"
notebooklm source add "https://youtube.com/watch?v=..."
notebooklm generate audio "conversational deep dive, mention specific examples" --format deep-dive --wait
notebooklm download audio ./episode.mp3
```

### Study Materials

```bash
notebooklm create "Exam Prep"
notebooklm use <id>
notebooklm source add "./textbook-chapter.pdf"
notebooklm source add "./lecture-notes.md"
notebooklm generate quiz --difficulty hard --quantity more --wait
notebooklm generate flashcards --wait
notebooklm generate mind-map --wait
notebooklm download quiz --format markdown ./quiz.md
notebooklm download flashcards --format json ./cards.json
notebooklm download mind-map ./mindmap.json
```

## Troubleshooting

- **"Unauthorized" / redirect to login:** Re-run `notebooklm login` (cookies expired).
- **CSRF errors:** Usually auto-refreshed. If persistent, re-run `notebooklm login`.
- **Rate limiting:** Use `--retry N` on generate commands for automatic backoff.
- **Browser login fails:** Delete `~/.notebooklm/browser_profile/` and retry.
- **Text file upload returns None:** Use `notebooklm source add "$(cat ./notes.txt)"` instead.
- **Generation returns None:** Use `--wait` flag; poll with `notebooklm artifact poll <task_id>`.
- **Debug RPC issues:** `NOTEBOOKLM_DEBUG_RPC=1 notebooklm <command>`

## Important Notes

- Uses **unofficial, undocumented Google APIs** — may break without notice
- Session cookies expire every few weeks; re-authenticate with `notebooklm login`
- Rate limits apply — add delays between intensive operations
- Audio/video download URLs are temporary; fetch fresh URLs before downloading
- Partial ID matching works for notebook IDs, source IDs, artifact IDs, and note IDs
- Requires Python >= 3.11
- Playwright (Chromium) is needed only for `notebooklm login`, not for API calls or downloads

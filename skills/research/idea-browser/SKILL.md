---
name: idea-browser
description: Demand intelligence engine — cross-platform trend discovery and idea validation. Queries Google Trends, TikTok Creative Center, Reddit, and Polymarket, then computes a composite demand score (0-100). Zero API keys required.
version: 1.0.0
author: Viper
tags: [demand-intelligence, trends, idea-validation, market-research, tiktok, reddit, polymarket, google-trends]
---

# Idea Browser — Demand Intelligence Engine

Cross-platform demand signal aggregator for idea validation and trend discovery.
Combines data from Google Trends, TikTok, Reddit, and Polymarket into a single
demand score (0-100).

**Zero dependencies. Zero API keys. Python stdlib only.**

## Helper Script

This skill includes `scripts/idea_browser.py` — a complete CLI tool for all demand intelligence operations.

```bash
# Google Trends: interest over time for a keyword
python3 SKILL_DIR/scripts/idea_browser.py trends "AI agents"

# Google Trends: daily trending searches for a region
python3 SKILL_DIR/scripts/idea_browser.py trends --geo US

# Google Trends: rising related queries
python3 SKILL_DIR/scripts/idea_browser.py rising "AI agents"

# TikTok: trending hashtags
python3 SKILL_DIR/scripts/idea_browser.py tiktok --region US --period 7

# TikTok: trending sounds
python3 SKILL_DIR/scripts/idea_browser.py tiktok-sounds --region US

# Reddit: subreddit discovery for a keyword
python3 SKILL_DIR/scripts/idea_browser.py reddit "AI agents"

# Reddit: extract pain points / unmet needs
python3 SKILL_DIR/scripts/idea_browser.py reddit-pain "AI agents"

# Reddit: fast-growing subreddits
python3 SKILL_DIR/scripts/idea_browser.py reddit-growing --limit 20

# Polymarket: prediction market signals
python3 SKILL_DIR/scripts/idea_browser.py polymarket "AI"

# Polymarket: trending markets by volume
python3 SKILL_DIR/scripts/idea_browser.py polymarket-trending

# Demand Score: composite 0-100 across all platforms
python3 SKILL_DIR/scripts/idea_browser.py score "AI agents"

# Full Scan: all platforms + demand score in one call
python3 SKILL_DIR/scripts/idea_browser.py scan "AI agents"
```

`SKILL_DIR` is the directory containing this SKILL.md file. All output is structured JSON.

## When to Use

- User asks "is this idea any good?" or "is there demand for X?"
- User wants to validate a product, niche, or content idea
- User asks about trending topics across platforms
- User wants to find pain points or unmet needs in a market
- User asks about Google Trends, TikTok trends, or Reddit trends
- User wants prediction market data for a topic
- User needs a data-driven demand signal before building something

## Modules

### 1. Google Trends

| Command | What it does | Data source |
|---------|-------------|-------------|
| `trends "keyword"` | Interest over time (0-100 index) | Google Trends Explore API |
| `trends --geo US` | Daily trending searches for a region | Google Trends RSS |
| `rising "keyword"` | Rising + top related queries | Google Trends Explore API |

**Key metrics:** current interest, peak interest, average interest, rising queries with breakout percentages.

### 2. TikTok

| Command | What it does | Data source |
|---------|-------------|-------------|
| `tiktok` | Trending hashtags with view/video counts | TikTok Creative Center API |
| `tiktok-sounds` | Trending sounds/songs with video counts | TikTok Creative Center API |

**Key metrics:** video count, view count, trend direction. Supports `--region` and `--period` filters.

### 3. Reddit

| Command | What it does | Data source |
|---------|-------------|-------------|
| `reddit "keyword"` | Subreddit discovery + subscriber/activity data | Reddit JSON API |
| `reddit-pain "keyword"` | Pain point extraction (frustrations, complaints) | Reddit search API |
| `reddit-growing` | Fast-growing subreddits by activity ratio | Reddit popular API |

**Key metrics:** subscriber count, active users, activity ratio, pain point posts sorted by engagement.

### 4. Polymarket

| Command | What it does | Data source |
|---------|-------------|-------------|
| `polymarket "keyword"` | Search prediction markets | Gamma REST API |
| `polymarket-trending` | Top markets by volume | Gamma REST API |

**Key metrics:** market question, outcome probabilities, USDC volume.

### 5. Demand Scorer

| Command | What it does |
|---------|-------------|
| `score "keyword"` | Compute weighted 0-100 demand score |
| `scan "keyword"` | Full scan: all platforms + demand score |

**Scoring weights:**
- Google Trends: 35% (search interest, recency, average)
- Reddit: 25% (subscribers, activity ratio, community count)
- TikTok: 20% (trending hashtag matches, view/video counts)
- Polymarket: 20% (market volume, market count)

**Score interpretation:**

| Score | Verdict |
|-------|---------|
| 80-100 | VERY HIGH DEMAND |
| 60-79 | HIGH DEMAND |
| 40-59 | MODERATE DEMAND |
| 20-39 | LOW DEMAND |
| 0-19 | MINIMAL DEMAND |

## Typical Workflow

When a user asks to validate an idea or explore demand:

1. **Quick score** — Run `score "keyword"` for an instant 0-100 composite
2. **Deep dive** — If score is promising, run `scan "keyword"` for full platform breakdown
3. **Find pain points** — Run `reddit-pain "keyword"` to discover unmet needs
4. **Check trends** — Run `rising "keyword"` for emerging search queries
5. **Monitor markets** — Run `polymarket "keyword"` for prediction market signals
6. **Present** — Summarize the composite score, key signals, and actionable insights

## Presenting Results

Format demand scores clearly:

- **Score + verdict**: `"AI agents" — Demand Score: 72/100 (HIGH DEMAND)`
- **Platform breakdown**: Show each platform's score and key metrics
- **Rising signals**: Highlight breakout queries and growing communities
- **Pain points**: Quote top Reddit pain points with engagement metrics
- **Actionable insight**: "Strong search interest + active Reddit communities + TikTok trending = validated demand"

## Data Sources

All queries are **read-only** and use **public APIs** — no authentication required:

- **Google Trends** — RSS feed + Explore/Widget API (HTTPS)
- **TikTok Creative Center** — `ads.tiktok.com/creative_radar_api` (HTTPS)
- **Reddit** — Public `.json` endpoints (HTTPS)
- **Polymarket** — Gamma API at `gamma-api.polymarket.com` (HTTPS)

## Rate Limits

- **Google Trends**: May rate-limit aggressive usage; the script uses single requests
- **TikTok**: Creative Center API is generous for normal usage
- **Reddit**: Public JSON API allows ~10 requests/minute without auth
- **Polymarket**: 4,000 requests per 10 seconds

## Limitations

- Google Trends interest values are relative (0-100 index), not absolute search volume
- TikTok hashtag data is region-specific; use `--region` for different markets
- Reddit pain point extraction relies on keyword matching — results may include noise
- Polymarket has limited coverage outside politics, crypto, and major events
- The demand score is a heuristic composite, not a guarantee of market viability
- TikTok Creative Center endpoints may change without notice

## Platform Compatibility

Pure Python stdlib (`urllib`, `json`, `xml.etree`, `concurrent.futures`).
Works identically on Linux, macOS, and Windows with no dependencies.

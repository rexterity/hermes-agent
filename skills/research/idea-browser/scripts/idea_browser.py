#!/usr/bin/env python3
"""Idea Browser — Demand Intelligence Engine.

Cross-platform demand signal aggregator for idea validation and trend discovery.
Queries Google Trends, TikTok, Reddit, and Polymarket, then computes a composite
demand score (0-100).

Usage:
    python3 idea_browser.py trends "keyword"                  # Google Trends: interest over time
    python3 idea_browser.py trends "keyword" --geo US         # Google Trends: interest over time for a region
    python3 idea_browser.py rising "keyword"                  # Google Trends: rising related queries
    python3 idea_browser.py tiktok [--region US] [--period 7] # TikTok: trending hashtags
    python3 idea_browser.py tiktok-sounds [--region US]       # TikTok: trending sounds
    python3 idea_browser.py reddit "keyword"                  # Reddit: subreddit discovery
    python3 idea_browser.py reddit-pain "keyword"              # Reddit: extract pain points / unmet needs
    python3 idea_browser.py reddit-growing [--limit 20]       # Reddit: fast-growing subreddits
    python3 idea_browser.py polymarket "keyword"              # Polymarket: prediction market signals
    python3 idea_browser.py score "keyword"                   # Demand Scorer: composite 0-100 score
    python3 idea_browser.py scan "keyword"                    # Full scan: all platforms + score

All output is structured JSON. Zero external dependencies — Python stdlib only.
"""

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree import ElementTree as ET


# ─── Shared Helpers ───────────────────────────────────────────────────────────

_UA = "hermes-agent/1.0 (idea-browser skill)"


def _get(url: str, headers: dict | None = None, timeout: int = 15) -> bytes:
    """GET request, return raw bytes."""
    hdrs = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code}: {e.reason}"}).encode()
    except urllib.error.URLError as e:
        return json.dumps({"error": f"Connection error: {e.reason}"}).encode()
    except OSError as e:
        return json.dumps({"error": f"Network error: {e}"}).encode()


def _get_json(url: str, headers: dict | None = None, timeout: int = 15):
    """GET request, return parsed JSON."""
    raw = _get(url, headers, timeout)
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"error": "Response is not valid JSON", "raw_snippet": raw[:200].decode("utf-8", errors="replace")}


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _fmt_pct(val) -> str:
    """Format a value as a percentage string, e.g. '65.0%'. Returns '?' on failure."""
    try:
        return f"{float(val) * 100:.1f}%"
    except (ValueError, TypeError):
        return "?"


# ─── Google Trends Module ─────────────────────────────────────────────────────

def google_trends_daily(geo: str = "US") -> dict:
    """Fetch daily trending searches via Google Trends RSS feed."""
    url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={urllib.parse.quote(geo)}"
    raw = _get(url, headers={"Accept": "application/xml"})
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {"error": "Failed to parse Google Trends RSS", "geo": geo}

    ns = {"ht": "https://trends.google.com/trends/trendingsearches/daily"}
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "")
        traffic = item.findtext("ht:approx_traffic", "", ns)
        pub_date = item.findtext("pubDate", "")
        description = item.findtext("description", "")

        news_articles = []
        for news in item.findall("ht:news_item", ns):
            news_articles.append({
                "title": news.findtext("ht:news_item_title", "", ns),
                "url": news.findtext("ht:news_item_url", "", ns),
                "source": news.findtext("ht:news_item_source", "", ns),
            })

        items.append({
            "keyword": title,
            "approx_traffic": traffic,
            "published": pub_date,
            "description": description,
            "news_articles": news_articles[:3],
        })

    return {"geo": geo, "count": len(items), "trending": items}


def google_trends_interest(keyword: str, geo: str = "", timeframe: str = "today 12-m") -> dict:
    """Fetch interest-over-time data for a keyword via Google Trends explore widget.

    Uses the publicly accessible Google Trends embed/explore JSON endpoint.
    Returns relative interest values (0-100) over the requested timeframe.
    """
    # Build the explore request token URL
    params = {
        "hl": "en-US",
        "tz": "360",
        "req": json.dumps({
            "comparisonItem": [{"keyword": keyword, "geo": geo, "time": timeframe}],
            "category": 0,
            "property": "",
        }),
    }
    explore_url = "https://trends.google.com/trends/api/explore?" + urllib.parse.urlencode(params)
    raw = _get(explore_url)
    text = raw.decode("utf-8", errors="replace")

    # Google prefixes response with ")]}',\n" for XSSI protection
    if text.startswith(")]}'"):
        text = text[text.index("\n") + 1:]

    try:
        explore_data = json.loads(text)
    except json.JSONDecodeError:
        return {"keyword": keyword, "geo": geo, "error": "Failed to parse explore response"}

    # Extract the interest-over-time widget token
    widgets = explore_data.get("widgets", [])
    iot_widget = None
    for w in widgets:
        if w.get("id") == "TIMESERIES":
            iot_widget = w
            break

    if not iot_widget:
        return {"keyword": keyword, "geo": geo, "error": "No interest-over-time widget found"}

    token = iot_widget.get("token", "")
    iot_req = json.dumps(iot_widget.get("request", {}))

    # Fetch the multiline timeseries data
    ts_url = (
        "https://trends.google.com/trends/api/widgetdata/multiline?"
        + urllib.parse.urlencode({"hl": "en-US", "tz": "360", "req": iot_req, "token": token})
    )
    ts_raw = _get(ts_url)
    ts_text = ts_raw.decode("utf-8", errors="replace")
    if ts_text.startswith(")]}'"):
        ts_text = ts_text[ts_text.index("\n") + 1:]

    try:
        ts_data = json.loads(ts_text)
    except json.JSONDecodeError:
        return {"keyword": keyword, "geo": geo, "error": "Failed to parse timeseries response"}

    timeline = ts_data.get("default", {}).get("timelineData", [])
    points = []
    for pt in timeline:
        points.append({
            "time": pt.get("formattedTime", ""),
            "value": pt.get("value", [0])[0],
        })

    # Compute summary stats
    values = [p["value"] for p in points]
    current = values[-1] if values else 0
    peak = max(values) if values else 0
    avg = sum(values) / len(values) if values else 0

    return {
        "keyword": keyword,
        "geo": geo or "worldwide",
        "timeframe": timeframe,
        "current_interest": current,
        "peak_interest": peak,
        "average_interest": round(avg, 1),
        "data_points": len(points),
        "timeline": points[-12:],  # last 12 data points for brevity
    }


def google_trends_rising(keyword: str, geo: str = "") -> dict:
    """Fetch rising related queries for a keyword."""
    params = {
        "hl": "en-US",
        "tz": "360",
        "req": json.dumps({
            "comparisonItem": [{"keyword": keyword, "geo": geo, "time": "today 12-m"}],
            "category": 0,
            "property": "",
        }),
    }
    explore_url = "https://trends.google.com/trends/api/explore?" + urllib.parse.urlencode(params)
    raw = _get(explore_url)
    text = raw.decode("utf-8", errors="replace")
    if text.startswith(")]}'"):
        text = text[text.index("\n") + 1:]

    try:
        explore_data = json.loads(text)
    except json.JSONDecodeError:
        return {"keyword": keyword, "error": "Failed to parse explore response"}

    widgets = explore_data.get("widgets", [])
    rq_widget = None
    for w in widgets:
        if w.get("id") == "RELATED_QUERIES":
            rq_widget = w
            break

    if not rq_widget:
        return {"keyword": keyword, "error": "No related queries widget found"}

    token = rq_widget.get("token", "")
    rq_req = json.dumps(rq_widget.get("request", {}))

    rq_url = (
        "https://trends.google.com/trends/api/widgetdata/relatedsearches?"
        + urllib.parse.urlencode({"hl": "en-US", "tz": "360", "req": rq_req, "token": token})
    )
    rq_raw = _get(rq_url)
    rq_text = rq_raw.decode("utf-8", errors="replace")
    if rq_text.startswith(")]}'"):
        rq_text = rq_text[rq_text.index("\n") + 1:]

    try:
        rq_data = json.loads(rq_text)
    except json.JSONDecodeError:
        return {"keyword": keyword, "error": "Failed to parse related queries response"}

    ranked = rq_data.get("default", {}).get("rankedList", [])
    top_queries = []
    rising_queries = []

    for group in ranked:
        for item in group.get("rankedKeyword", []):
            query_text = item.get("query", "")
            value = item.get("value", 0)
            fmt_value = item.get("formattedValue", "")
            link = item.get("link", "")
            entry = {"query": query_text, "value": value, "formatted": fmt_value, "link": link}
            # Rising queries have "Breakout" or percentage values
            if "Breakout" in fmt_value or "%" in fmt_value:
                rising_queries.append(entry)
            else:
                top_queries.append(entry)

    return {
        "keyword": keyword,
        "geo": geo or "worldwide",
        "top_queries": top_queries[:15],
        "rising_queries": rising_queries[:15],
    }


# ─── TikTok Module ───────────────────────────────────────────────────────────

_TIKTOK_CC_BASE = "https://ads.tiktok.com/creative_radar_api/v1/popular"


def tiktok_trending_hashtags(region: str = "US", period: int = 7, limit: int = 20) -> dict:
    """Fetch trending hashtags from TikTok Creative Center."""
    # TikTok Creative Center uses a public-ish API for trending data
    params = {
        "period": str(period),
        "limit": str(min(limit, 50)),
        "country_code": region,
        "page": "1",
        "sort_by": "popular",
    }
    url = f"{_TIKTOK_CC_BASE}/hashtag/list?" + urllib.parse.urlencode(params)
    data = _get_json(url)

    if "error" in data:
        return {"platform": "tiktok", "type": "hashtags", "region": region, **data}

    hashtag_list = (data.get("data") or {}).get("list", [])
    pagination = (data.get("data") or {}).get("pagination", {})

    hashtags = []
    for h in hashtag_list:
        hashtags.append({
            "name": h.get("hashtag_name", ""),
            "video_count": h.get("video_count", 0),
            "view_count": h.get("view_count", 0),
            "trend": h.get("trend", 0),
            "is_promoted": h.get("is_promoted", False),
        })

    return {
        "platform": "tiktok",
        "type": "hashtags",
        "region": region,
        "period_days": period,
        "count": len(hashtags),
        "total": pagination.get("total", len(hashtags)),
        "hashtags": hashtags,
    }


def tiktok_trending_sounds(region: str = "US", period: int = 7, limit: int = 20) -> dict:
    """Fetch trending sounds/songs from TikTok Creative Center."""
    params = {
        "period": str(period),
        "limit": str(min(limit, 50)),
        "country_code": region,
        "page": "1",
        "sort_by": "popular",
    }
    url = f"{_TIKTOK_CC_BASE}/music/list?" + urllib.parse.urlencode(params)
    data = _get_json(url)

    if "error" in data:
        return {"platform": "tiktok", "type": "sounds", "region": region, **data}

    sound_list = (data.get("data") or {}).get("list", [])
    pagination = (data.get("data") or {}).get("pagination", {})

    sounds = []
    for s in sound_list:
        sounds.append({
            "title": s.get("music_name", s.get("title", "")),
            "artist": s.get("artist", s.get("author", "")),
            "video_count": s.get("video_count", 0),
            "trend": s.get("trend", 0),
            "duration": s.get("duration", 0),
        })

    return {
        "platform": "tiktok",
        "type": "sounds",
        "region": region,
        "period_days": period,
        "count": len(sounds),
        "total": pagination.get("total", len(sounds)),
        "sounds": sounds,
    }


# ─── Reddit Module ────────────────────────────────────────────────────────────

_REDDIT_UA = "hermes-agent:idea-browser/1.0 (demand-intel)"


def _reddit_get(url: str):
    """GET from Reddit JSON API with appropriate UA and rate-limit handling."""
    return _get_json(url, headers={"User-Agent": _REDDIT_UA}, timeout=15)


def reddit_search_subreddits(keyword: str, limit: int = 10) -> dict:
    """Search for subreddits related to a keyword, extracting growth signals."""
    q = urllib.parse.quote(keyword)
    url = f"https://www.reddit.com/subreddits/search.json?q={q}&limit={limit}&sort=relevance"
    data = _reddit_get(url)

    if "error" in data:
        return {"platform": "reddit", "keyword": keyword, **data}

    children = (data.get("data") or {}).get("children", [])
    subreddits = []
    for child in children:
        sr = child.get("data", {})
        subreddits.append({
            "name": sr.get("display_name", ""),
            "title": sr.get("title", ""),
            "subscribers": sr.get("subscribers", 0),
            "active_users": sr.get("accounts_active", 0),
            "description": (sr.get("public_description", "") or "")[:200],
            "created_utc": sr.get("created_utc", 0),
            "over18": sr.get("over18", False),
            "url": f"https://reddit.com{sr.get('url', '')}",
        })

    # Sort by subscriber count descending
    subreddits.sort(key=lambda x: x["subscribers"], reverse=True)

    return {
        "platform": "reddit",
        "keyword": keyword,
        "count": len(subreddits),
        "subreddits": subreddits,
    }


def reddit_pain_points(keyword: str, subreddit: str = "", limit: int = 25) -> dict:
    """Extract pain points / complaints from Reddit posts related to a keyword.

    Searches for posts expressing frustration, complaints, or unmet needs.
    """
    pain_queries = [
        f"{keyword} frustrated",
        f"{keyword} problem",
        f"{keyword} wish there was",
        f"{keyword} hate",
        f"{keyword} alternative",
    ]

    all_posts = []
    for pq in pain_queries[:3]:  # limit to 3 queries to avoid rate limits
        q = urllib.parse.quote(pq)
        sr_filter = f"&restrict_sr=on&subreddit={urllib.parse.quote(subreddit)}" if subreddit else ""
        url = f"https://www.reddit.com/search.json?q={q}&sort=relevance&t=year&limit={limit // 3}{sr_filter}"
        data = _reddit_get(url)
        time.sleep(1)  # respect rate limits

        if "error" in data:
            continue

        for child in (data.get("data") or {}).get("children", []):
            post = child.get("data", {})
            all_posts.append({
                "title": post.get("title", ""),
                "subreddit": post.get("subreddit", ""),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "created_utc": post.get("created_utc", 0),
                "selftext_snippet": (post.get("selftext", "") or "")[:150],
            })

    # Deduplicate by URL
    seen = set()
    unique = []
    for p in all_posts:
        if p["url"] not in seen:
            seen.add(p["url"])
            unique.append(p)

    # Sort by engagement (score + comments)
    unique.sort(key=lambda x: x["score"] + x["num_comments"], reverse=True)

    return {
        "platform": "reddit",
        "keyword": keyword,
        "subreddit_filter": subreddit or "all",
        "count": len(unique),
        "pain_points": unique[:limit],
    }


def reddit_growing_subreddits(limit: int = 20) -> dict:
    """Discover fast-growing / popular subreddits from Reddit's own listings."""
    url = f"https://www.reddit.com/subreddits/popular.json?limit={min(limit, 50)}"
    data = _reddit_get(url)

    if "error" in data:
        return {"platform": "reddit", "type": "growing", **data}

    children = (data.get("data") or {}).get("children", [])
    subreddits = []
    for child in children:
        sr = child.get("data", {})
        subscribers = sr.get("subscribers", 0)
        active = sr.get("accounts_active", 0)
        # Active-to-subscriber ratio as a growth proxy
        activity_ratio = round(active / subscribers * 100, 2) if subscribers > 0 else 0

        subreddits.append({
            "name": sr.get("display_name", ""),
            "subscribers": subscribers,
            "active_users": active,
            "activity_ratio_pct": activity_ratio,
            "description": (sr.get("public_description", "") or "")[:150],
            "url": f"https://reddit.com{sr.get('url', '')}",
        })

    # Sort by activity ratio (higher = more engaged / growing)
    subreddits.sort(key=lambda x: x["activity_ratio_pct"], reverse=True)

    return {
        "platform": "reddit",
        "type": "growing",
        "count": len(subreddits),
        "subreddits": subreddits,
    }


# ─── Polymarket Module ───────────────────────────────────────────────────────

_GAMMA = "https://gamma-api.polymarket.com"


def polymarket_search(keyword: str) -> dict:
    """Search Polymarket for prediction markets related to a keyword."""
    q = urllib.parse.quote(keyword)
    data = _get_json(f"{_GAMMA}/public-search?q={q}")

    if "error" in data:
        return {"platform": "polymarket", "keyword": keyword, **data}

    events = data.get("events", [])
    results = []
    for evt in events[:10]:
        markets = evt.get("markets", [])
        market_summaries = []
        for m in markets[:5]:
            prices = m.get("outcomePrices", "[]")
            if isinstance(prices, str):
                try:
                    prices = json.loads(prices)
                except json.JSONDecodeError:
                    prices = []
            outcomes = m.get("outcomes", "[]")
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except json.JSONDecodeError:
                    outcomes = []
            market_summaries.append({
                "question": m.get("question", ""),
                "prices": {
                    outcomes[i] if i < len(outcomes) else f"Outcome {i}":
                    _fmt_pct(prices[i]) if i < len(prices) else "?"
                    for i in range(max(len(prices), len(outcomes)))
                },
                "volume": m.get("volume", 0),
                "closed": m.get("closed", False),
                "slug": m.get("slug", ""),
            })

        volume = evt.get("volume", 0)
        try:
            volume = float(volume)
        except (ValueError, TypeError):
            volume = 0

        results.append({
            "title": evt.get("title", ""),
            "volume": volume,
            "slug": evt.get("slug", ""),
            "markets": market_summaries,
        })

    return {
        "platform": "polymarket",
        "keyword": keyword,
        "count": len(results),
        "events": results,
    }


def polymarket_trending(limit: int = 10) -> dict:
    """Get top trending prediction markets by volume."""
    events = _get_json(
        f"{_GAMMA}/events?limit={limit}&active=true&closed=false&order=volume&ascending=false"
    )

    if isinstance(events, dict) and "error" in events:
        return {"platform": "polymarket", "type": "trending", **events}

    results = []
    for evt in (events if isinstance(events, list) else []):
        volume = evt.get("volume", 0)
        try:
            volume = float(volume)
        except (ValueError, TypeError):
            volume = 0

        results.append({
            "title": evt.get("title", ""),
            "volume": volume,
            "slug": evt.get("slug", ""),
            "market_count": len(evt.get("markets", [])),
        })

    return {
        "platform": "polymarket",
        "type": "trending",
        "count": len(results),
        "events": results,
    }


# ─── Demand Scorer ────────────────────────────────────────────────────────────

def _score_google(keyword: str, geo: str = "", *, prefetched: dict | None = None) -> dict:
    """Score a keyword based on Google Trends data (0-100).

    If *prefetched* is provided it must be the dict returned by
    ``google_trends_interest``; the network call is skipped.
    """
    try:
        interest = prefetched if prefetched is not None else google_trends_interest(keyword, geo)
        if "error" in interest:
            return {"score": 0, "reason": interest["error"], "weight": 0.35}

        current = interest.get("current_interest", 0)
        peak = interest.get("peak_interest", 0)
        avg = interest.get("average_interest", 0)

        # Score: current interest relative to peak, boosted by average
        if peak > 0:
            recency = (current / peak) * 60  # max 60 pts from recency
            strength = min(avg, 100) * 0.4   # max 40 pts from average
            score = _clamp(recency + strength)
        else:
            score = 0

        return {
            "score": round(score, 1),
            "current_interest": current,
            "peak_interest": peak,
            "average_interest": avg,
            "weight": 0.35,
        }
    except Exception as e:
        return {"score": 0, "reason": str(e), "weight": 0.35}


def _score_reddit(keyword: str, *, prefetched: dict | None = None) -> dict:
    """Score a keyword based on Reddit engagement signals (0-100).

    If *prefetched* is provided it must be the dict returned by
    ``reddit_search_subreddits``; the network call is skipped.
    """
    try:
        sr_data = prefetched if prefetched is not None else reddit_search_subreddits(keyword, limit=5)
        if "error" in sr_data:
            return {"score": 0, "reason": sr_data["error"], "weight": 0.25}

        subreddits = sr_data.get("subreddits", [])
        if not subreddits:
            return {"score": 0, "reason": "No related subreddits found", "weight": 0.25}

        # Signals: total subscribers, active users, number of communities
        total_subs = sum(s["subscribers"] for s in subreddits)
        total_active = sum(s["active_users"] for s in subreddits)
        community_count = len(subreddits)

        # Subscriber scale score (log scale — 1M+ is top tier)
        sub_score = min(math.log10(max(total_subs, 1)) / 7 * 50, 50)  # max 50 pts

        # Activity ratio score
        activity_score = min((total_active / max(total_subs, 1)) * 1000, 30)  # max 30 pts

        # Community diversity score
        diversity_score = min(community_count * 4, 20)  # max 20 pts

        score = _clamp(sub_score + activity_score + diversity_score)

        return {
            "score": round(score, 1),
            "total_subscribers": total_subs,
            "total_active_users": total_active,
            "communities_found": community_count,
            "weight": 0.25,
        }
    except Exception as e:
        return {"score": 0, "reason": str(e), "weight": 0.25}


def _score_tiktok(keyword: str, *, prefetched: dict | None = None) -> dict:
    """Score based on TikTok trending hashtags related to the keyword (0-100).

    If *prefetched* is provided it must be the dict returned by
    ``tiktok_trending_hashtags``; the network call is skipped.
    """
    try:
        hashtags = prefetched if prefetched is not None else tiktok_trending_hashtags(region="US", period=7, limit=50)
        if "error" in hashtags:
            return {"score": 0, "reason": hashtags["error"], "weight": 0.20}

        kw_lower = keyword.lower().replace(" ", "")
        matching = []
        for h in hashtags.get("hashtags", []):
            name = h.get("name", "").lower().replace(" ", "")
            if kw_lower in name or (name in kw_lower and len(name) > 3):
                matching.append(h)

        if not matching:
            # Keyword not in trending — low but not zero
            total = hashtags.get("total", 0)
            base_score = min(total * 0.5, 15) if total > 0 else 0
            return {
                "score": round(base_score, 1),
                "reason": "Keyword not in current trending hashtags",
                "trending_hashtag_count": total,
                "weight": 0.20,
            }

        # Keyword matches trending hashtags
        total_views = sum(h.get("view_count", 0) for h in matching)
        total_videos = sum(h.get("video_count", 0) for h in matching)

        view_score = min(math.log10(max(total_views, 1)) / 12 * 60, 60)  # max 60 pts
        video_score = min(math.log10(max(total_videos, 1)) / 8 * 25, 25)  # max 25 pts
        match_score = min(len(matching) * 5, 15)  # max 15 pts

        score = _clamp(view_score + video_score + match_score)

        return {
            "score": round(score, 1),
            "matching_hashtags": len(matching),
            "total_views": total_views,
            "total_videos": total_videos,
            "weight": 0.20,
        }
    except Exception as e:
        return {"score": 0, "reason": str(e), "weight": 0.20}


def _score_polymarket(keyword: str, *, prefetched: dict | None = None) -> dict:
    """Score based on Polymarket prediction market interest (0-100).

    If *prefetched* is provided it must be the dict returned by
    ``polymarket_search``; the network call is skipped.
    """
    try:
        pm_data = prefetched if prefetched is not None else polymarket_search(keyword)
        if "error" in pm_data:
            return {"score": 0, "reason": pm_data["error"], "weight": 0.20}

        events = pm_data.get("events", [])
        if not events:
            return {"score": 0, "reason": "No matching prediction markets", "weight": 0.20}

        total_volume = sum(e.get("volume", 0) for e in events)
        market_count = sum(len(e.get("markets", [])) for e in events)

        # Volume score (log scale — $10M+ is strong signal)
        vol_score = min(math.log10(max(total_volume, 1)) / 8 * 70, 70)  # max 70 pts

        # Market count score
        mkt_score = min(market_count * 5, 30)  # max 30 pts

        score = _clamp(vol_score + mkt_score)

        return {
            "score": round(score, 1),
            "total_volume": total_volume,
            "market_count": market_count,
            "event_count": len(events),
            "weight": 0.20,
        }
    except Exception as e:
        return {"score": 0, "reason": str(e), "weight": 0.20}


def demand_score(
    keyword: str,
    geo: str = "",
    *,
    prefetched: dict | None = None,
) -> dict:
    """Compute a cross-platform demand score (0-100) for a keyword.

    Weighted composite:
      - Google Trends:  35%
      - Reddit:         25%
      - TikTok:         20%
      - Polymarket:     20%

    If *prefetched* is a dict it may contain any of the keys
    ``google_trends``, ``reddit``, ``tiktok``, ``polymarket`` mapping to
    the raw API dicts returned by the corresponding fetch functions.
    When a key is present the scorer will reuse that data instead of
    making a new network call.
    """
    pf = prefetched or {}

    # Run all platform scorers in parallel
    results = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {
            ex.submit(_score_google, keyword, geo, prefetched=pf.get("google_trends")): "google_trends",
            ex.submit(_score_reddit, keyword, prefetched=pf.get("reddit")): "reddit",
            ex.submit(_score_tiktok, keyword, prefetched=pf.get("tiktok")): "tiktok",
            ex.submit(_score_polymarket, keyword, prefetched=pf.get("polymarket")): "polymarket",
        }
        for future in as_completed(futures):
            platform = futures[future]
            try:
                results[platform] = future.result()
            except Exception as e:
                results[platform] = {"score": 0, "reason": str(e), "weight": 0.25}

    # Compute weighted composite
    total_score = 0
    total_weight = 0
    for platform, data in results.items():
        weight = data.get("weight", 0.25)
        total_score += data.get("score", 0) * weight
        total_weight += weight

    composite = round(total_score / total_weight, 1) if total_weight > 0 else 0

    # Interpret the score
    if composite >= 80:
        verdict = "VERY HIGH DEMAND"
    elif composite >= 60:
        verdict = "HIGH DEMAND"
    elif composite >= 40:
        verdict = "MODERATE DEMAND"
    elif composite >= 20:
        verdict = "LOW DEMAND"
    else:
        verdict = "MINIMAL DEMAND"

    return {
        "keyword": keyword,
        "geo": geo or "worldwide",
        "composite_score": composite,
        "verdict": verdict,
        "platform_scores": results,
    }


# ─── Full Scan ────────────────────────────────────────────────────────────────

def full_scan(keyword: str, geo: str = "") -> dict:
    """Run a comprehensive scan across all platforms for a keyword.

    Fetches data once from each platform, then reuses the already-fetched
    results to compute the demand score — avoiding duplicate API calls.
    """
    results = {}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            ex.submit(google_trends_interest, keyword, geo): "google_trends",
            ex.submit(google_trends_rising, keyword, geo): "google_rising",
            ex.submit(reddit_search_subreddits, keyword, 10): "reddit_subreddits",
            ex.submit(reddit_pain_points, keyword, "", 10): "reddit_pain_points",
            ex.submit(polymarket_search, keyword): "polymarket",
            ex.submit(tiktok_trending_hashtags, "US", 7, 50): "tiktok_hashtags",
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = {"error": str(e)}

    # Compute demand score by reusing already-fetched data
    score_result = demand_score(keyword, geo, prefetched={
        "google_trends": results.get("google_trends"),
        "reddit": results.get("reddit_subreddits"),
        "tiktok": results.get("tiktok_hashtags"),
        "polymarket": results.get("polymarket"),
    })
    results["demand_score"] = score_result

    return {
        "keyword": keyword,
        "geo": geo or "worldwide",
        "scan_type": "full",
        "composite_score": score_result["composite_score"],
        "verdict": score_result["verdict"],
        "platforms": results,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _parse_flag(args: list, flag: str, default: str = "") -> str:
    """Extract --flag value from args list."""
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return default


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "trends":
        geo = _parse_flag(args, "--geo", "US")
        if len(args) >= 2 and not args[1].startswith("--"):
            keyword = args[1]
            result = google_trends_interest(keyword, geo)
        else:
            result = google_trends_daily(geo)

    elif cmd == "rising" and len(args) >= 2:
        geo = _parse_flag(args, "--geo", "")
        result = google_trends_rising(args[1], geo)

    elif cmd == "tiktok":
        region = _parse_flag(args, "--region", "US")
        period = int(_parse_flag(args, "--period", "7"))
        limit = int(_parse_flag(args, "--limit", "20"))
        result = tiktok_trending_hashtags(region, period, limit)

    elif cmd == "tiktok-sounds":
        region = _parse_flag(args, "--region", "US")
        period = int(_parse_flag(args, "--period", "7"))
        limit = int(_parse_flag(args, "--limit", "20"))
        result = tiktok_trending_sounds(region, period, limit)

    elif cmd == "reddit" and len(args) >= 2:
        keyword = args[1]
        limit = int(_parse_flag(args, "--limit", "10"))
        result = reddit_search_subreddits(keyword, limit)

    elif cmd == "reddit-pain" and len(args) >= 2:
        keyword = args[1]
        subreddit = _parse_flag(args, "--subreddit", "")
        limit = int(_parse_flag(args, "--limit", "15"))
        result = reddit_pain_points(keyword, subreddit, limit)

    elif cmd == "reddit-growing":
        limit = int(_parse_flag(args, "--limit", "20"))
        result = reddit_growing_subreddits(limit)

    elif cmd == "polymarket" and len(args) >= 2:
        result = polymarket_search(args[1])

    elif cmd == "polymarket-trending":
        limit = int(_parse_flag(args, "--limit", "10"))
        result = polymarket_trending(limit)

    elif cmd == "score" and len(args) >= 2:
        geo = _parse_flag(args, "--geo", "")
        result = demand_score(args[1], geo)

    elif cmd == "scan" and len(args) >= 2:
        geo = _parse_flag(args, "--geo", "")
        result = full_scan(args[1], geo)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

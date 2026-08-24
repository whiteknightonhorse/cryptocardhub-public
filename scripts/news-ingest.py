#!/usr/bin/env python3
"""
news-ingest.py — DefyCard editorial news pipeline.

Per pseo-pages skill §news-ingest. Pulls fresh items from the curated
PRIMARY-source list in `config/news_sources.yaml`, applies the editorial
keyword gate, dedupes against `state/news_seen.db`, asks Claude Haiku to
rewrite each into a 400-600-word news article with explicit *what-this-
means-for-card-holders* analysis, and drops the result into Astro's pSEO
content collection as bucket=news.

This is NOT a paraphrase pump. The Haiku prompt mandates:
  1. Cite the source URL by name in the body.
  2. Include a "What this means for crypto-card users" section.
  3. Quote one specific fact + date from the source.
  4. NEVER invent a fact not in the source text.

Run modes:
  --dry-run           parse + filter; no API call, no file write
  (default)           up to N=10 articles per invocation, where N is total_max_per_run
                      from sources.yaml

Logs to logs/news/<YYYY-MM-DD>.jsonl.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import feedparser
import requests
import urllib.parse
import urllib.request
import yaml
from bs4 import BeautifulSoup

UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "config" / "news_sources.yaml"
STATE_DB = ROOT / "video-generator" / "state" / "news_seen.db"
CONTENT_DIR = ROOT / "seo-site" / "src" / "content" / "pseo"
LOG_DIR = ROOT / "logs" / "news"
ASSETS_DIR = ROOT / "seo-site" / "public" / "assets" / "news"
ASSETS_URL_PREFIX = "/assets/news"

DEFAULT_MODEL = os.environ.get("WRITER_MODEL", "claude-haiku-4-5-20251001")
assert "haiku" in DEFAULT_MODEL.lower(), (
    f"WRITER_MODEL must be Haiku class for cost; got {DEFAULT_MODEL!r}"
)

DEFAULT_FRESHNESS_HOURS = 36   # operator override via --freshness-hours

# =============================================================================
# State (SQLite dedup ledger)
# =============================================================================


def _state() -> sqlite3.Connection:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(STATE_DB))
    db.execute(
        "CREATE TABLE IF NOT EXISTS news_seen ("
        "  url_hash TEXT PRIMARY KEY,"
        "  source_name TEXT NOT NULL,"
        "  source_url TEXT NOT NULL,"
        "  first_seen TIMESTAMP NOT NULL,"
        "  status TEXT NOT NULL"          # 'queued' | 'published' | 'rejected'
        ")"
    )
    db.commit()
    return db


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


# =============================================================================
# Source fetcher
# =============================================================================


@dataclass
class Item:
    source_name: str
    source_url: str         # canonical link to the article
    source_category: str    # regulator-eu | regulator-us | issuer | ...
    title: str
    summary: str            # short blurb from feed
    body: str               # full article text scraped from page (or summary)
    published_at: datetime  # UTC
    priority: int


def _fetch_rss(src: dict, ua: str, timeout: int) -> list[Item]:
    """Pull recent items from an RSS/Atom feed. Returns [] on any failure
    (logged, not raised) — one broken feed must not kill the whole run.

    A failure is written to the JSONL log as `source_failed`, not only to
    stderr: a dead feed and a feed the keyword gate rejects in full both print
    "0 candidates", and a cron log nobody reads cannot tell them apart.
    """
    name = src["name"]
    url = src["url"]
    try:
        # feedparser respects the request headers when given via http_headers
        r = requests.get(
            url,
            headers={"User-Agent": ua, "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,*/*"},
            timeout=timeout,
        )
        if r.status_code != 200:
            print(f"[news-ingest] {name}: HTTP {r.status_code}", file=sys.stderr)
            _log({"event": "source_failed", "source": name, "url": url,
                  "http_status": r.status_code})
            return []
        feed = feedparser.parse(r.content)
    except (requests.RequestException, ValueError) as e:
        print(f"[news-ingest] {name}: fetch error {e}", file=sys.stderr)
        _log({"event": "source_failed", "source": name, "url": url,
              "error_class": type(e).__name__})
        return []

    items: list[Item] = []
    for entry in feed.entries[: src.get("max_items", 10)]:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        title = (entry.get("title") or "").strip()
        summary = re.sub(
            r"<[^>]+>", " ",
            entry.get("summary") or entry.get("description") or "",
        ).strip()
        # Published date — feedparser gives struct_time on `published_parsed`
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub:
            published_at = datetime(*pub[:6], tzinfo=timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)
        items.append(Item(
            source_name=name,
            source_url=link,
            source_category=src.get("category", "unknown"),
            title=title,
            summary=summary[:1200],
            body="",  # filled later from full-page fetch
            published_at=published_at,
            priority=int(src.get("priority", 5)),
        ))
    return items


def _fetch_full_body(item: Item, ua: str, timeout: int) -> str:
    """Grab the article page and extract main text. Falls back to summary
    if the page is JS-rendered or behind a paywall.
    """
    try:
        r = requests.get(
            item.source_url,
            headers={"User-Agent": ua, "Accept": "text/html,*/*"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return item.summary
        soup = BeautifulSoup(r.text, "html.parser")
        # Strip nav / header / footer / script / style nodes
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        # Try common article containers
        article = (
            soup.find("article")
            or soup.find("main")
            or soup.find("div", attrs={"class": re.compile(r"(article|content|post|entry)", re.I)})
            or soup.body
            or soup
        )
        text = article.get_text("\n", strip=True)
        # Collapse blank lines and trim
        text = re.sub(r"\n{2,}", "\n\n", text)
        return text[:8000] or item.summary
    except (requests.RequestException, ValueError):
        return item.summary


# =============================================================================
# Editorial gate
# =============================================================================


# =============================================================================
# Image: Unsplash search + LOCAL download (operator policy 2026-05-07 —
# never reference external CDN URLs in <img src>; download and host on
# defycard.com so visitors don't make any third-party requests).
# =============================================================================


def _unsplash_search(query: str) -> Optional[dict]:
    """Pick one landscape image for `query`. Returns metadata dict or None."""
    if not UNSPLASH_KEY or not query.strip():
        return None
    try:
        params = urllib.parse.urlencode({
            "query": query.strip()[:60],
            "orientation": "landscape",
            "content_filter": "high",
            "per_page": 3,
        })
        req = urllib.request.Request(
            f"https://api.unsplash.com/search/photos?{params}",
            headers={
                "Accept-Version": "v1",
                "Authorization": f"Client-ID {UNSPLASH_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[news-ingest] unsplash search '{query}' failed: {e}", file=sys.stderr)
        return None
    for r in data.get("results", []):
        raw = (r.get("urls") or {}).get("raw") or ""
        if not raw.startswith("https://images.unsplash.com/"):
            continue
        sep = "&" if "?" in raw else "?"
        # Layer-A force 16:9 + face-crop at CDN level so download is already
        # the size we want (1280×720, ~80 KB JPG).
        return {
            "src_remote": f"{raw}{sep}w=1280&h=720&fit=crop&crop=faces,entropy&q=80&fm=jpg",
            "alt": (r.get("alt_description") or query).strip()[:120],
        }
    return None


def _download_to_local(remote_url: str, slug: str) -> Optional[str]:
    """Pull the JPG once and save under public/assets/news/<slug>.jpg.
    Returns the public URL path (e.g. "/assets/news/<slug>.jpg") or None on
    failure. Hashing the filename by slug means re-runs are idempotent.
    """
    if not remote_url:
        return None
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{slug}.jpg"
    out = ASSETS_DIR / fname
    if out.exists() and out.stat().st_size > 1024:
        return f"{ASSETS_URL_PREFIX}/{fname}"
    try:
        r = requests.get(
            remote_url,
            headers={"User-Agent": "DefyCardBot/1.0 (+https://defycard.com; image-fetch)"},
            timeout=15,
            stream=True,
        )
        if r.status_code != 200:
            print(f"[news-ingest] image fetch HTTP {r.status_code}: {remote_url[:80]}", file=sys.stderr)
            return None
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("image/"):
            print(f"[news-ingest] image fetch wrong content-type {ctype!r}", file=sys.stderr)
            return None
        out.write_bytes(r.content)
        return f"{ASSETS_URL_PREFIX}/{fname}"
    except Exception as e:
        print(f"[news-ingest] image download failed: {e}", file=sys.stderr)
        return None


def _resolve_image(query: str, slug: str) -> Optional[dict]:
    """Search Unsplash + download. Returns {src, alt} with LOCAL src path."""
    found = _unsplash_search(query)
    if not found:
        return None
    local = _download_to_local(found["src_remote"], slug)
    if not local:
        return None
    return {"src": local, "alt": found["alt"]}


def _passes_keyword_gate(item: Item, gate: dict) -> bool:
    """Two-list AND gate. An item passes only if it has BOTH a card-
    relevance term AND a context (crypto / regulatory) term. Falls back
    to single-list behavior if `card_relevance_any_of` is missing for
    backward-compat with older config schemas.
    """
    haystack = f"{item.title} {item.summary}".lower()
    if "card_relevance_any_of" in gate:
        card_terms = [k.lower() for k in gate["card_relevance_any_of"]]
        ctx_terms = [k.lower() for k in gate.get("context_any_of", [])]
        has_card = any(n in haystack for n in card_terms)
        has_ctx = (not ctx_terms) or any(n in haystack for n in ctx_terms)
        return has_card and has_ctx
    # legacy single-list mode
    needles = [k.lower() for k in gate.get("must_contain_any_of", [])]
    return any(n in haystack for n in needles)


def _is_fresh(item: Item, hours: int) -> bool:
    age = datetime.now(timezone.utc) - item.published_at
    return age <= timedelta(hours=hours)


# =============================================================================
# Haiku rewrite — editorial-grade
# =============================================================================


SYSTEM_PROMPT = """You are the DefyCard senior editor. You produce 400-600-word
news articles for crypto-card readers, in the same editorial register Bloomberg
or The Information would use for a financial-product brief.

CONTRACT (P0 — never violate):

1. CITE the source: in the first 80 words mention the source name and link
   to it in markdown: e.g. "according to [the FCA](https://...)" or "per
   [ether.fi's blog post](https://...)". rel="nofollow noopener" is added
   automatically downstream — write plain markdown links.
2. NEVER invent a fact not in the source body. If a fact is unclear, omit
   it rather than guess.
3. INCLUDE a section "What this means for crypto-card users" (H2). Two to
   four sentences of analysis specific to card-holders — fees, availability,
   custody, KYC, country gates. THIS is the differentiator vs. paraphrase.
4. INCLUDE one primary-source date or numeric fact verbatim (e.g. "the rule
   takes effect 2026-06-30", "fines reach €4.5 million").
5. DO NOT mention DefyCard, do not promote any card. The news bucket is
   editorial, not affiliate. (The page-level affiliate banner is rendered
   by the layout — do not duplicate it in the body.)

OUTPUT — STRICT JSON, no markdown fences, no preamble:

{
  "title": "60-95 char headline, no clickbait, no emoji",
  "h1": "matches title or close variant",
  "description": "150-160 char meta description that previews the news",
  "slug": "lowercase-hyphenated-slug-30-to-60-chars",
  "intro_paragraph": "first paragraph — sets up the news + cites the source in markdown link form",
  "body_markdown": "1200-2200 chars of GitHub-flavored markdown, body of the article. MUST contain '## What this means for crypto-card users' H2 with 2-4 sentences of card-holder analysis. NO H1. DO NOT include affiliate placeholders or DefyCard self-references.",
  "what_to_watch": ["3-5 short bullets — what to monitor next"],
  "primary_keyword": "the most-search-relevant 3-6 word phrase from this story",
  "image_query": "2-4 word visually concrete query for an Unsplash hero image — favour neutral establishing shots (e.g. 'london financial district', 'eu parliament building', 'gavel courtroom', 'fintech office'); NEVER attempt to depict a specific person or brand logo",
  "schema_news": {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "matches title",
    "datePublished": "the source's publication date in ISO 8601",
    "dateModified": "today's date in ISO 8601 (UTC)",
    "author": {"@type": "Organization", "name": "DefyCard Editorial"},
    "publisher": {"@type": "Organization", "name": "DefyCard", "logo": {"@type": "ImageObject", "url": "https://defycard.com/logo.png"}}
  }
}

WRITING STYLE: third person, active voice, no hedging adjectives (avoid
"significant", "groundbreaking"). Every sentence must add fact or
interpretation — no filler.
"""


def _slugify(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower()).strip()
    s = re.sub(r"[-\s]+", "-", s)
    return f"news-{s[:50]}".rstrip("-")


def _build_user_message(item: Item) -> str:
    return (
        f"# Source\n"
        f"Source name: {item.source_name}\n"
        f"Source URL: {item.source_url}\n"
        f"Source category: {item.source_category}\n"
        f"Source published: {item.published_at.isoformat()}\n\n"
        f"# Title\n{item.title}\n\n"
        f"# Body (verbatim from source)\n{item.body[:7000]}\n"
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _call_claude(item: Item, model: str) -> dict:
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as sp:
        sp.write(SYSTEM_PROMPT)
        sp_path = sp.name
    try:
        proc = subprocess.run(
            [
                "/usr/bin/claude", "--print",
                "--model", model,
                "--append-system-prompt-file", sp_path,
                "--disable-slash-commands",
                "--max-turns", "1",
                "--output-format", "json",
                "--exclude-dynamic-system-prompt-sections",
            ],
            input=_build_user_message(item),
            capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude exit {proc.returncode}: {proc.stderr[:300]!r}")
        cli = json.loads(proc.stdout)
        if cli.get("is_error"):
            raise RuntimeError(f"claude is_error: {str(cli.get('result',''))[:300]}")
        text = (cli.get("result") or "").strip()
        if not text:
            raise RuntimeError(f"empty result; stop_reason={cli.get('stop_reason')!r}")
        return json.loads(_strip_fences(text))
    finally:
        try:
            os.unlink(sp_path)
        except OSError:
            pass


# =============================================================================
# Markdown emitter
# =============================================================================


def _frontmatter(payload: dict, item: Item) -> str:
    """Emit YAML frontmatter compatible with the pseo content schema. We
    add news-specific fields (source_url, source_name, news_published_at,
    schema_news) which are optional in the schema.
    """
    today = datetime.now(timezone.utc)
    fm = {
        "title": payload["title"],
        "description": payload["description"],
        "h1": payload.get("h1") or payload["title"],
        "slug": payload["slug"],
        "bucket": "news",
        "primary_keyword": payload.get("primary_keyword", item.title.lower()[:60]),
        "supporting_keywords": [],
        "intro_paragraph": payload["intro_paragraph"],
        "long_context_paragraph": "",
        "snapshot_bullets": [],
        "stakes_bullets": [],
        "what_to_watch": payload.get("what_to_watch", []),
        "bottom_line": [],
        "faq": [],
        "related_clusters": [],
        "images": [],
        "schema_article": payload.get("schema_news"),
        "generated_at": today.isoformat(),
        "model": DEFAULT_MODEL,
        "pattern": "P-News",
        # News-specific:
        "source_url": item.source_url,
        "source_name": item.source_name,
        "source_category": item.source_category,
        "news_published_at": item.published_at.isoformat(),
    }
    return "---\n" + json.dumps(fm, indent=2, ensure_ascii=False)[1:-1].replace('"', '"') + "\n---\n"


def _write_article(payload: dict, item: Item) -> Path:
    slug = _slugify(payload["slug"])
    path = CONTENT_DIR / f"{slug}.md"
    image_query = (payload.get("image_query") or payload.get("primary_keyword") or "").strip()
    image = _resolve_image(image_query, slug) if image_query else None
    images_list = [{"src": image["src"], "alt": image["alt"], "query": image_query}] if image else []
    fm_dict = {
        "title": payload["title"],
        "description": payload["description"],
        "h1": payload.get("h1") or payload["title"],
        "slug": slug,
        "bucket": "news",
        "primary_keyword": payload.get("primary_keyword", item.title.lower()[:60]),
        "supporting_keywords": [],
        "intro_paragraph": payload["intro_paragraph"],
        "long_context_paragraph": "",
        "snapshot_bullets": [],
        "stakes_bullets": [],
        "what_to_watch": payload.get("what_to_watch", []),
        "bottom_line": [],
        "faq": [],
        "related_clusters": [],
        "images": images_list,
        "schema_article": payload.get("schema_news"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": DEFAULT_MODEL,
        "pattern": "P-News",
        "source_url": item.source_url,
        "source_name": item.source_name,
        "source_category": item.source_category,
        "news_published_at": item.published_at.isoformat(),
    }
    # Emit each top-level key as proper YAML key:value lines
    fm_lines = ["---"]
    for k, v in fm_dict.items():
        fm_lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    fm_lines.append("---\n")
    body = payload.get("body_markdown", "").strip() + "\n"
    path.write_text("\n".join(fm_lines) + "\n" + body, encoding="utf-8")
    return path


# =============================================================================
# Main
# =============================================================================


def _log(rec: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({**rec, "ts": datetime.now(timezone.utc).isoformat()},
                      ensure_ascii=False)
    (LOG_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl").open("a").write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="parse + filter; no claude call, no file write")
    ap.add_argument("--limit", type=int, help="hard override of total_max_per_run")
    ap.add_argument("--freshness-hours", type=int, default=DEFAULT_FRESHNESS_HOURS,
                    help="how far back to consider an item fresh (default 36)")
    args = ap.parse_args()

    cfg = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    sources = cfg["sources"]
    gate = cfg["keyword_gate"]
    http = cfg["http"]

    db = _state()
    candidates: list[Item] = []
    for src in sources:
        items = _fetch_rss(src, http["user_agent"], http["timeout_s"])
        fresh = passed = kept = 0
        for it in items[: http.get("per_source_max_items", 5)]:
            if not _is_fresh(it, args.freshness_hours):
                continue
            fresh += 1
            if not _passes_keyword_gate(it, gate):
                continue
            passed += 1
            h = _url_hash(it.source_url)
            if db.execute("SELECT 1 FROM news_seen WHERE url_hash=?", (h,)).fetchone():
                continue
            candidates.append(it)
            kept += 1
        # One machine-readable record per source per run. "0 candidates" is
        # only diagnosable if it decomposes: dead feed (fetched 0) vs stale
        # feed (fresh 0) vs gate rejection (passed 0) vs already seen.
        _log({"event": "source_result", "source": src["name"],
              "fetched": len(items), "fresh": fresh,
              "passed_gate": passed, "candidates": kept})
        print(f"[news-ingest] {src['name']}: {len(items)} fetched, {fresh} fresh, "
              f"{passed} passed gate, {kept} candidates")

    # Priority sort: lower priority number = higher importance
    candidates.sort(key=lambda x: (x.priority, -x.published_at.timestamp()))
    cap = args.limit or http.get("total_max_per_run", 10)
    candidates = candidates[:cap]

    print(f"[news-ingest] {len(candidates)} candidates after gate + priority "
          f"(cap {cap})")

    if args.dry_run:
        for c in candidates:
            print(f"  - [{c.source_category}] {c.title[:90]}")
        return 0

    # Real run — fetch full bodies + Haiku rewrite
    written = 0
    for it in candidates:
        h = _url_hash(it.source_url)
        try:
            it.body = _fetch_full_body(it, http["user_agent"], http["timeout_s"])
            if len(it.body) < 200:
                _log({"event": "skip_short_body", "url": it.source_url,
                      "len": len(it.body)})
                db.execute(
                    "INSERT OR REPLACE INTO news_seen "
                    "(url_hash, source_name, source_url, first_seen, status) "
                    "VALUES (?,?,?,?,?)",
                    (h, it.source_name, it.source_url,
                     datetime.now(timezone.utc).isoformat(), "rejected"))
                db.commit()
                continue
            payload = _call_claude(it, DEFAULT_MODEL)
            path = _write_article(payload, it)
            db.execute(
                "INSERT OR REPLACE INTO news_seen "
                "(url_hash, source_name, source_url, first_seen, status) "
                "VALUES (?,?,?,?,?)",
                (h, it.source_name, it.source_url,
                 datetime.now(timezone.utc).isoformat(), "published"))
            db.commit()
            _log({"event": "published",
                  "slug": payload["slug"],
                  "source": it.source_name,
                  "url": it.source_url,
                  "title": payload["title"][:80],
                  "path": str(path)})
            written += 1
            print(f"[news-ingest] published: {payload['title'][:80]} "
                  f"({it.source_name}) → {path.name}")
            time.sleep(2)  # be polite to source server between full-page fetches
        except Exception as e:
            _log({"event": "error", "url": it.source_url,
                  "error": f"{type(e).__name__}: {e}"})
            print(f"[news-ingest] error on {it.source_url}: {e}", file=sys.stderr)
            # Mark as rejected so we don't retry forever
            db.execute(
                "INSERT OR REPLACE INTO news_seen "
                "(url_hash, source_name, source_url, first_seen, status) "
                "VALUES (?,?,?,?,?)",
                (h, it.source_name, it.source_url,
                 datetime.now(timezone.utc).isoformat(), "rejected"))
            db.commit()

    print(f"[news-ingest] done: {written} published "
          f"({datetime.now(timezone.utc).isoformat()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Free (unofficial) HTTP API for Google's AI Mode.

This is a reverse-engineered scraper, NOT an official API. It drives the same
endpoint your browser hits for AI Mode (google.com/search?udm=50) using your own
Google session cookies, then parses the AI answer out of the returned HTML.

Caveats (read these):
  * Against Google's ToS to scrape. Use for personal/experimental purposes.
  * Needs your real Google cookies (see README) or you'll get consent / bot pages.
  * Google changes their markup constantly -> parsing WILL break. The parser is
    written defensively and falls back to returning raw text blocks so you can
    see what came back and patch _extract_answer().
  * May trigger CAPTCHAs if you hammer it. Add delays; don't abuse.

Run:
  pip install -r requirements.txt
  export GOOGLE_COOKIE="SID=...; HSID=...; ..."   # see README
  uvicorn server:app --port 8000 --reload
"""

from __future__ import annotations

import os
import re
import json
import html
import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aimode")

app = FastAPI(title="Google AI Mode (unofficial)", version="0.1.0")

# Wide-open CORS so a Chrome extension (or any page) can call it from the browser.
# A packed MV3 extension with host_permissions can bypass CORS anyway, but this
# keeps dev / content-script usage painless.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- config ----------------------------------------------------------------

# Paste your cookie string here or (better) set the GOOGLE_COOKIE env var.
GOOGLE_COOKIE = os.environ.get("GOOGLE_COOKIE", "")

# A recent, real desktop Chrome UA. Keep it reasonably current.
USER_AGENT = os.environ.get(
    "AIMODE_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

# udm=50 is the AI Mode surface on Google Search.
SEARCH_URL = "https://www.google.com/search"


# --- models ----------------------------------------------------------------

class AskRequest(BaseModel):
    query: str
    # optional per-request cookie override (handy from an extension that grabs
    # the user's live google.com cookies itself)
    cookie: str | None = None
    hl: str = "en"
    gl: str = "us"


class Source(BaseModel):
    title: str | None = None
    url: str | None = None


class AskResponse(BaseModel):
    query: str
    answer: str
    sources: list[Source]
    ok: bool
    # raw fallback text so you can debug parsing without re-running curl
    debug_blocks: list[str] | None = None


# --- core ------------------------------------------------------------------

def _looks_like_block_page(text: str) -> str | None:
    """Return a human reason if Google served a consent / captcha / block page."""
    low = text.lower()
    if "our systems have detected unusual traffic" in low or "/sorry/" in low:
        return "Google served a bot/CAPTCHA page (unusual traffic)."
    if "consent.google.com" in low or 'action="https://consent.google' in low:
        return "Google served a cookie-consent page. Provide authenticated cookies."
    if "enablejs" in low and "noscript" in low and len(text) < 20000:
        return "Google served a JS-required stub (likely no/invalid cookies)."
    return None


def _extract_answer(html_text: str) -> tuple[str, list[Source], list[str]]:
    """
    Best-effort extraction of the AI Mode answer + cited sources.

    Google ships the AI answer inside script-tag data blobs and/or rendered
    DOM nodes, and the exact selectors drift. We try several strategies and
    fall back to collecting large visible text blocks so the caller can see
    what arrived and patch this function.
    """
    soup = BeautifulSoup(html_text, "html.parser")

    # Strip noise
    for tag in soup(["style", "noscript", "svg"]):
        tag.decompose()

    answer_parts: list[str] = []
    sources: list[Source] = []
    debug_blocks: list[str] = []

    # Strategy 1: AI Overview / AI Mode containers tend to carry data-attrs or
    # specific roles. These selectors are guesses that have historically matched
    # AI answer wrappers; adjust as Google changes them.
    candidate_selectors = [
        "div[data-subtree='aimc']",
        "div[data-al-attr]",
        "div.WaaZC",            # AI overview text block (historical)
        "div.LT6XE",            # AI mode answer container (historical)
        "div[role='complementary']",
    ]
    for sel in candidate_selectors:
        for node in soup.select(sel):
            txt = _clean(node.get_text(" ", strip=True))
            if len(txt) > 60:
                answer_parts.append(txt)

    # Strategy 2: dig through inline JSON script blobs for the answer text.
    # Google often embeds AI responses in large JSON arrays inside <script>.
    for script in soup.find_all("script"):
        s = script.string or script.get_text() or ""
        if not s or len(s) < 200:
            continue
        # Pull out long human-sentence strings that look like answer prose.
        for m in re.findall(r'"([^"\\]{80,}?[.!?])"', s):
            frag = _clean(_unescape_js(m))
            if _is_prose(frag):
                debug_blocks.append(frag)

    # Collect links that look like citations (external, not google nav).
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        url = _normalize_href(href)
        if not url:
            continue
        title = _clean(a.get_text(" ", strip=True))
        if title and _is_citation(url):
            sources.append(Source(title=title[:200], url=url))

    # De-dup sources by url
    seen = set()
    uniq_sources: list[Source] = []
    for s in sources:
        if s.url and s.url not in seen:
            seen.add(s.url)
            uniq_sources.append(s)
    uniq_sources = uniq_sources[:12]

    # Assemble the answer
    answer = _dedupe_join(answer_parts)
    if not answer and debug_blocks:
        # fall back to the longest prose blocks we scraped from scripts
        ranked = sorted(set(debug_blocks), key=len, reverse=True)
        answer = _dedupe_join(ranked[:5])

    return answer.strip(), uniq_sources, sorted(set(debug_blocks), key=len, reverse=True)[:20]


# --- small helpers ---------------------------------------------------------

def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(t or "")).strip()


def _unescape_js(s: str) -> str:
    # handle \uXXXX and \n etc from JSON-in-script
    try:
        return json.loads(f'"{s}"')
    except Exception:
        return s.encode().decode("unicode_escape", errors="ignore")


def _is_prose(t: str) -> bool:
    if len(t) < 60:
        return False
    if t.startswith(("http", "{", "[", "/", "function", "window.")):
        return False
    letters = sum(c.isalpha() for c in t)
    return letters / max(len(t), 1) > 0.6 and " " in t


def _is_citation(url: str) -> bool:
    bad = ("google.com", "gstatic.com", "googleusercontent.com", "youtube.com/redirect")
    return url.startswith("http") and not any(b in url for b in bad)


def _normalize_href(href: str) -> str | None:
    if href.startswith("/url?"):
        m = re.search(r"[?&](?:q|url)=([^&]+)", href)
        if m:
            from urllib.parse import unquote
            return unquote(m.group(1))
    if href.startswith("http"):
        return href
    return None


def _dedupe_join(parts: list[str]) -> str:
    out: list[str] = []
    seen = set()
    for p in parts:
        key = p[:120]
        if key not in seen:
            seen.add(key)
            out.append(p)
    return "\n\n".join(out)


# --- routes ----------------------------------------------------------------

@app.get("/health")
def health():
    return {"ok": True, "has_cookie": bool(GOOGLE_COOKIE)}


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    cookie = req.cookie or GOOGLE_COOKIE
    if not cookie:
        raise HTTPException(
            status_code=400,
            detail="No Google cookie configured. Set GOOGLE_COOKIE env var or "
                   "pass 'cookie' in the request body. See README.",
        )

    params = {
        "q": req.query,
        "udm": "50",          # AI Mode surface
        "hl": req.hl,
        "gl": req.gl,
    }
    headers = {**BASE_HEADERS, "Cookie": cookie}

    try:
        async with httpx.AsyncClient(
            http2=True, follow_redirects=True, timeout=30.0
        ) as client:
            resp = await client.get(SEARCH_URL, params=params, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {e}")

    text = resp.text

    blocked = _looks_like_block_page(text)
    if blocked:
        raise HTTPException(status_code=502, detail=blocked)

    answer, sources, debug_blocks = _extract_answer(text)

    ok = bool(answer)
    return AskResponse(
        query=req.query,
        answer=answer or "(could not parse an answer — see debug_blocks; "
                         "patch _extract_answer in server.py)",
        sources=sources,
        ok=ok,
        debug_blocks=None if ok else debug_blocks,
    )

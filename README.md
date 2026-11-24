# Free (unofficial) Google AI Mode API

A tiny local HTTP server that wraps Google's **AI Mode** (`google.com/search?udm=50`)
and exposes it as a simple REST endpoint your code — or a Chrome extension — can call.

> ⚠️ **This is a reverse-engineered scraper, not an official API.** It scrapes Google
> using *your own* session cookies. It violates Google's ToS, can trigger CAPTCHAs,
> and **will break** when Google changes their HTML. For personal / experimental use.
> If you need something stable, use the official Gemini API instead.

## What you get

- `POST /ask` → `{ "answer": "...", "sources": [...], "ok": true }`
- `GET /health`
- Wide-open CORS so a browser extension / web page can call it
- A working Chrome extension in `extension/` that grabs your live Google cookies
  and queries AI Mode from a popup

## Run the server

```bash
cd aimode-api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Option A: configure a cookie once (see "Getting cookies" below)
export GOOGLE_COOKIE="SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...; __Secure-1PSID=...; ..."

uvicorn server:app --port 8000 --reload
```

Test it:

```bash
curl -s localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"what is the tallest mountain in japan"}' | python -m json.tool
```

## Getting cookies

AI Mode won't answer an anonymous/bot request — you need a logged-in Google session.

1. Open `google.com` in Chrome while signed in.
2. DevTools → Network → reload → click the `search` document request.
3. Copy the full **`cookie:`** request header value.
4. Put it in `GOOGLE_COOKIE`, **or** let the Chrome extension send it per-request
   (it reads your live cookies automatically — no manual copying).

Cookies expire / rotate, so the extension approach is more convenient.

## Chrome extension

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** → pick the
   `extension/` folder.
2. Make sure the server is running on `localhost:8000`.
3. Click the extension icon, type a question, hit **Ask**.

The extension uses the `cookies` permission + `host_permissions` for `google.com`
to read your live session and passes it as the `cookie` field in the request body,
so the server itself doesn't need `GOOGLE_COOKIE` set.

## When parsing breaks (it will)

Google ships the AI answer in shifting selectors / inline JSON. If `ok` comes back
`false`, the response includes `debug_blocks` — the candidate text the scraper found.
Use those to fix `_extract_answer()` in `server.py` (the selectors at
`candidate_selectors` and the citation logic are the parts most likely to need
updating).

## API

### `POST /ask`

```jsonc
// request
{
  "query": "your question",      // required
  "cookie": "SID=...; ...",      // optional; overrides GOOGLE_COOKIE for this call
  "hl": "en",                    // optional language
  "gl": "us"                     // optional region
}

// response
{
  "query": "your question",
  "answer": "...",
  "sources": [{ "title": "...", "url": "https://..." }],
  "ok": true,
  "debug_blocks": null           // populated only when ok === false
}
```

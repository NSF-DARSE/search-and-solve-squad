import os
import time
import hmac
import hashlib
import urllib.parse
import re
from collections import deque
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import (
    SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, PORT,
    SKIP_SLACK_SIGNATURE_VERIFY, ALLOWED_LINK_PREFIXES
)
from .rag import LocalVectorStore
from .dbx_client import call_llm_direct  # Databricks primary call

ALL_PORTALS_URL = "https://bestegg.atlassian.net/servicedesk/customer/portals"

app = Flask(__name__)
slack = WebClient(token=SLACK_BOT_TOKEN)

# --- Discover bot user id (prevents reply loops) ---
try:
    auth_info = slack.auth_test()
    BOT_USER_ID = auth_info.get("user_id")
    print(f"Bot user id: {BOT_USER_ID}")
except Exception as e:
    BOT_USER_ID = None
    print("WARNING: slack.auth_test failed; own-message filtering may be incomplete:", e)

# --- Data store: Excel-only loader at db/seed/help_docs.xlsx ---
seed_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "db", "seed"))
local_store = LocalVectorStore(seed_dir)  # raises if Excel missing

# --- Normalize allowed link prefixes (string -> list) ---
if isinstance(ALLOWED_LINK_PREFIXES, str):
    ALLOWED_LINK_PREFIXES = [p.strip() for p in ALLOWED_LINK_PREFIXES.split(",") if p.strip()]

# --- Simple de-dupe store to avoid double posts on Slack retries ---
PROCESSED_IDS = deque(maxlen=1024)  # event_ids we've already handled

@app.get("/healthz")
def healthz():
    return "ok", 200

def verify_slack_signature(req) -> bool:
    """Verify Slack signature unless explicitly skipped (dev mode)."""
    if SKIP_SLACK_SIGNATURE_VERIFY:
        return True
    ts = req.headers.get("X-Slack-Request-Timestamp", "")
    sig = req.headers.get("X-Slack-Signature", "")
    if not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > 60 * 5:
            return False
    except Exception:
        return False
    body = req.get_data(as_text=True)
    base = f"v0:{ts}:{body}".encode("utf-8")
    my_sig = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(my_sig, sig)

def _allowed(url: str) -> bool:
    return (not ALLOWED_LINK_PREFIXES) or any(url.startswith(p) for p in ALLOWED_LINK_PREFIXES)

def _mk_alt_bullets(alternates):
    """Alternates as clickable links: • <url|title>"""
    lines = []
    for a in alternates:
        t = (a.get("title") or "").strip()
        u = (a.get("url") or "").strip()
        if t and u and _allowed(u):
            lines.append(f"• <{u}|{t}>")
    return "\n".join(lines)

def _humanize_from_url(portal_url: str) -> str:
    """
    Last-ditch label if the Excel 'Service Name' is blank and no sibling row has it.
    Try to extract a readable token from the URL path, e.g. .../portal/20 -> 'Portal 20'.
    """
    try:
        parts = urllib.parse.urlparse(portal_url).path.strip("/").split("/")
        # Typical JSM: /servicedesk/customer/portal/<id>
        if len(parts) >= 4 and parts[-2].lower() == "portal":
            return f"Portal {parts[-1]}"
        # Fallback: last non-empty path segment
        for seg in reversed(parts):
            if seg:
                return seg.replace("-", " ").title()
    except Exception:
        pass
    return "Portal"

def _choose_best_link(model_out: dict) -> str:
    """Pick the first allowed URL from the model output."""
    links = (model_out or {}).get("links") or []
    for u in links:
        if _allowed(u):
            return u
    # also scan the text for URLs as a fallback
    txt = (model_out or {}).get("text") or ""
    if isinstance(txt, str):
        for u in re.findall(r"https?://[^\s)>\]]+", txt):
            if _allowed(u):
                return u
    return ""

def _find_row_by_link(url: str):
    """Map a URL back to a local Excel row (by exact request_url or portal_url, then prefix match)."""
    if not url:
        return None
    # exact request match
    for r in local_store.rows:
        if (r.get("url") or "").strip() == url:
            return r
    # exact portal match
    for r in local_store.rows:
        if (r.get("portal_url") or "").strip() == url:
            return r
    # request startswith portal (model linked to portal home)
    for r in local_store.rows:
        pu = (r.get("portal_url") or "").strip()
        if pu and url.startswith(pu):
            return r
    return None

def _alternates_same_portal(best_row, limit=3):
    """Alternates (title + url) from the same portal_url."""
    out = []
    if not best_row:
        return out
    portal_url = (best_row.get("portal_url") or "").strip()
    if not portal_url:
        return out
    for r in local_store.rows:
        if r is best_row:
            continue
        if (r.get("portal_url") or "").strip() == portal_url:
            t = (r.get("title") or "").strip()
            u = (r.get("url") or "").strip()
            if t and u and _allowed(u):
                out.append({"title": t, "url": u})
        if len(out) >= limit:
            break
    return out

def _extract_answer_text(model_text: str) -> str:
    """
    Pull just the answer sentence from model_text.
    Accepts either:
      - starts with 'Answer:' and we take the remainder of that line, or
      - the whole text as the answer.
    """
    if not model_text:
        return ""
    m = re.search(r"(?im)^\s*answer\s*:\s*(.+)$", model_text)
    if m:
        return m.group(1).strip()
    return model_text.strip()

def render_blocks_db(answer_text: str, primary_link: str, request_title: str, request_url: str,
                     portal_url: str, alternates):
    """
    Databricks format:
        Ask-BestiE
        Answer: <...>
        Link: <primary_link>, <Open Request> <Portal Home>
        [buttons]
        [alternates]
        [footer]
    """
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Ask-BestiE*"}},
    ]

    # Answer line
    if answer_text:
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"Answer: {answer_text}"}})

    # Link line (primary + inline quick links)
    link_pieces = []
    if primary_link and _allowed(primary_link):
        link_pieces.append(f"<{primary_link}>")
    else:
        link_pieces.append("—")

    if request_url and _allowed(request_url):
        link_pieces.append(f"<{request_url}|Open Request>")
    if portal_url and _allowed(portal_url):
        link_pieces.append(f"<{portal_url}|Portal Home>")

    blocks.append({"type": "section",
                   "text": {"type": "mrkdwn",
                            "text": "Link: " + ", ".join(link_pieces)}})

    # Buttons
    action_elems = []
    if request_url and _allowed(request_url):
        label = request_title or "Open request"
        action_elems.append({"type": "button", "text": {"type": "plain_text", "text": label}, "url": request_url})
    if portal_url and _allowed(portal_url):
        action_elems.append({"type": "button", "text": {"type": "plain_text", "text": "Portal home"}, "url": portal_url})
    if action_elems:
        blocks.append({"type": "actions", "elements": action_elems})

    # Alternates
    alt_bullets = _mk_alt_bullets(alternates)
    if alt_bullets:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*You could also try:*\n{alt_bullets}"}})

    # Footer
    blocks.append({"type": "divider"})
    blocks.append({"type": "section",
                   "text": {"type": "mrkdwn",
                            "text": f"If that’s not what you’re looking for, here are all the portals: <{ALL_PORTALS_URL}|All portals>"}})

    return blocks

def render_blocks_local(request_title: str, request_url: str, portal_url: str, alternates):
    """
    Local format (minimal):
        Ask-BestiE
        [buttons only]
        [alternates]
        [footer]
    """
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Ask-BestiE*"}},
    ]

    # Buttons only
    action_elems = []
    if request_url and _allowed(request_url):
        label = request_title or "Open request"
        action_elems.append({"type": "button", "text": {"type": "plain_text", "text": label}, "url": request_url})
    if portal_url and _allowed(portal_url):
        action_elems.append({"type": "button", "text": {"type": "plain_text", "text": "Portal home"}, "url": portal_url})
    if action_elems:
        blocks.append({"type": "actions", "elements": action_elems})

    # Alternates
    alt_bullets = _mk_alt_bullets(alternates)
    if alt_bullets:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": f"*You could also try:*\n{alt_bullets}"}})

    # Footer
    blocks.append({"type": "divider"})
    blocks.append({"type": "section",
                   "text": {"type": "mrkdwn",
                            "text": f"If that’s not what you’re looking for, here are all the portals: <{ALL_PORTALS_URL}|All portals>"}})

    return blocks

# ---------- Slack events ----------

@app.post("/slack/events")
def slack_events():
    if not verify_slack_signature(request):
        return "Bad signature", 403

    # Ignore Slack automatic retries (prevents duplicates)
    if request.headers.get("X-Slack-Retry-Num"):
        return "", 200

    data = request.get_json(silent=True) or {}
    # Slack URL verification
    if data.get("type") == "url_verification" and "challenge" in data:
        return jsonify({"challenge": data["challenge"]})

    # De-dupe by event_id
    event_id = data.get("event_id")
    if event_id:
        if event_id in PROCESSED_IDS:
            return "", 200
        PROCESSED_IDS.append(event_id)

    if data.get("type") == "event_callback":
        event = data.get("event", {}) or {}

        # Ignore bot / edited / deleted messages and our own posts
        if event.get("bot_id"):
            return "", 200
        if BOT_USER_ID and event.get("user") == BOT_USER_ID:
            return "", 200
        if event.get("type") in ("message",) and (event.get("subtype") in ("message_changed", "message_deleted")):
            return "", 200
        if event.get("subtype") == "bot_message":
            return "", 200

        if event.get("type") in ("app_mention", "message"):
            user = event.get("user")
            text = (event.get("text") or "").strip()
            channel = event.get("channel")

            if user and text and channel:
                try:
                    # ===== 1) PRIMARY: Databricks RAG/LLM =====
                    best_row = None
                    request_title = ""
                    request_url = ""
                    portal_url = ""
                    model_text = ""

                    model_out = call_llm_direct(text)  # {"text": "...", "links": [...] } or {}
                    model_text = (model_out.get("text") or "").strip()
                    best_link = _choose_best_link(model_out)

                    if best_link:
                        # Map the model's link to our local row to extract portal + alternates
                        best_row = _find_row_by_link(best_link)
                        if best_row:
                            request_title = (best_row.get("title") or "").strip()
                            request_url   = (best_row.get("url") or "").strip()
                            portal_url    = (best_row.get("portal_url") or "").strip()
                        else:
                            # If we can't map, still show a useful card using the model link
                            request_title = "Open the recommended link"
                            request_url   = best_link
                            portal_url    = ""

                    # ===== 2) FALLBACK to local-only if no usable model link and no model text =====
                    contexts = []
                    used_db = bool(model_text or best_link)
                    if not (best_row or best_link or model_text):
                        hits = local_store.top_k(text, k=8) or []
                        contexts = [row for _, row in hits]
                        best_row = contexts[0] if contexts else None
                        if best_row:
                            request_title = (best_row.get("title") or "").strip()
                            request_url   = (best_row.get("url") or "").strip()
                            portal_url    = (best_row.get("portal_url") or "").strip()

                    # ===== 3) Alternates from same portal (only if we have a mapped row) =====
                    alternates = _alternates_same_portal(best_row, limit=3) if best_row else []

                    # ===== 4) Build and send the card =====
                    if used_db:
                        answer_text = _extract_answer_text(model_text)
                        primary_link = best_link or request_url or portal_url or ""
                        blocks = render_blocks_db(
                            answer_text=answer_text,
                            primary_link=primary_link,
                            request_title=request_title,
                            request_url=request_url,
                            portal_url=portal_url,
                            alternates=alternates,
                        )
                        fallback_text = f"Answer: {answer_text[:140]}..." if answer_text else (request_title or "Recommended link")
                    else:
                        blocks = render_blocks_local(
                            request_title=request_title,
                            request_url=request_url,
                            portal_url=portal_url,
                            alternates=alternates,
                        )
                        fallback_text = request_title or "Recommended link"

                    slack.chat_postMessage(channel=channel, blocks=blocks, text=fallback_text)

                except SlackApiError as e:
                    print("Slack API error:", e.response.get("error"))
                except Exception:
                    import traceback; print("Handler error:"); traceback.print_exc()

    return "", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
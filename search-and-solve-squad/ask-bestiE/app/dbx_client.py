import os, re, requests

HOST = (os.getenv("DATABRICKS_HOST") or "").rstrip("/")
TOKEN = os.getenv("DATABRICKS_TOKEN") or ""
ENDPOINT = os.getenv("PHI3_ENDPOINT") or ""  # /serving-endpoints/ask_bestie_endpoint/invocations
USE_MODEL = (os.getenv("USE_MODEL","false").lower() == "true")

ALLOWED_PREFIXES = os.getenv("ALLOWED_LINK_PREFIXES","")
ALLOWED_PREFIXES_LIST = [p.strip() for p in ALLOWED_PREFIXES.split(",") if p.strip()]

# simple URL extractor
_URL_RE = re.compile(r"https?://[^\s)>\]]+")

FORMAT_INSTRUCTION = f"""
You are Ask-BestiE, a concise internal assistant. Respond in EXACTLY this format:

Answer: <one short, friendly paragraph that identifies the correct JSM portal/form and what it’s for>
Link: <ONE best URL on an approved domain>

Approved link domains/prefixes: {", ".join(ALLOWED_PREFIXES_LIST) or "[no restriction]"}.
Do not include multiple links. Do not add Markdown around the URL in the Link line.
"""

def _post_json(url, headers, payload, timeout=3):
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _extract_text_and_links(data):
    """
    Handle common Databricks serving shapes.
    Returns {"text": "...", "links": [urls]}.
    """
    txt = ""
    if isinstance(data, dict):
        preds = data.get("predictions") or data.get("prediction")
        if preds and isinstance(preds, list) and isinstance(preds[0], dict):
            txt = preds[0].get("text") or preds[0].get("output_text") or preds[0].get("answer") or ""
        elif "choices" in data and data["choices"]:
            c0 = data["choices"][0]
            if isinstance(c0, dict):
                txt = (c0.get("text")
                       or (c0.get("message") or {}).get("content")
                       or "")
        else:
            txt = (data.get("output")
                   or data.get("result")
                   or data.get("answer")
                   or data.get("text")
                   or "")
    elif isinstance(data, list) and data and isinstance(data[0], str):
        txt = data[0]

    txt = (txt or "").strip()
    links = _URL_RE.findall(txt) if txt else []
    return {"text": txt, "links": links}

def _wrap_user_text(user_text: str) -> str:
    return f"{FORMAT_INSTRUCTION.strip()}\n\nUser: {user_text.strip()}"

def call_llm_direct(user_text: str) -> dict:
    """
    Option 1 path: Databricks endpoint already performs RAG.
    We add a strict formatting instruction so output matches local rendering needs.
    Returns {"text": <answer block>, "links": [<urls>]} or {}.
    """
    if not USE_MODEL or not (HOST and ENDPOINT and TOKEN):
        return {}

    try:
        url = f"{HOST}{ENDPOINT}"
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        }

        composed = _wrap_user_text(user_text)

        # Try a few common payload shapes
        candidates = [
            {"inputs": {"question": composed}, "temperature": 0.2, "max_tokens": 256},
            {"inputs": composed, "temperature": 0.2, "max_tokens": 256},
            {"messages": [{"role":"user","content": composed}], "temperature": 0.2, "max_tokens": 256},
        ]

        for payload in candidates:
            try:
                data = _post_json(url, headers, payload, timeout=4)
                out = _extract_text_and_links(data)
                if out.get("text"):
                    return out
            except Exception:
                continue

        return {}
    except Exception:
        return {}

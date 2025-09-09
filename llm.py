import os
import json
import asyncio
from dotenv import load_dotenv
import httpx
from pathlib import Path

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GGML_MODEL_PATH = os.getenv("GGML_MODEL_PATH")  # path to a ggml .bin model for llama.cpp / llama-cpp-python


LABELS = ["Task", "Bug", "Incident", "Feature Request", "Question"]


def _heuristic_label_from_text(text: str) -> str:
    if not text:
        return "Task"
    t = text.lower()
    # bug-related keywords
    bug_words = [
        "crash", "crashes", "crashed", "exception", "stack trace", "nullpointer", "segfault",
        "error", "fail", "failed", "not working", "doesn't work", "doesnt work", "broken",
        "login failed", "login error", "cannot login", "can't login", "cant login", "unable to login",
        "authentication", "auth", "sign in", "signin", "login page"
    ]
    incident_words = ["outage", "down", "unavailable", "service is down", "cannot", "unable to", "timeout", "incident"]
    feature_words = ["feature", "enhancement", "request", "add", "support", "improve", "improvement"]
    question_words = ["how do", "how to", "why", "what is", "question", "help", "can i"]

    if any(w in t for w in bug_words):
        return "Bug"
    if any(w in t for w in incident_words):
        return "Incident"
    if any(w in t for w in feature_words):
        return "Feature Request"
    if any(w in t for w in question_words):
        return "Question"
    return "Task"


def _normalize_label(reply: str, original_text: str | None = None) -> str:
    """Normalize model reply into one of LABELS.

    If the reply doesn't match any known label, fall back to simple heuristics
    over the original ticket text to choose a label.
    """
    if not reply or not isinstance(reply, str):
        return _heuristic_label_from_text(original_text)

    # take first non-empty line
    lines = [ln.strip() for ln in reply.splitlines() if ln.strip()]
    r = lines[0] if lines else reply.strip()

    # Accept only exact matches or when the reply starts with the full label
    # (handles replies like "Bug" or "Bug: details..."). Do NOT accept partial
    # substring matches because model replies may mention words like "feature"
    # while the ticket is actually a crash.
    for lbl in LABELS:
        lbl_tokens = lbl.lower().split()
        # normalized reply start tokens
        r_tokens = r.lower().split()
        if not r_tokens:
            continue
        # exact match
        if r.strip().lower() == lbl.lower():
            return lbl
        # reply starts with the full label tokens (e.g. "Feature Request: ...")
        if len(r_tokens) >= len(lbl_tokens) and r_tokens[: len(lbl_tokens)] == lbl_tokens:
            return lbl

    # Prefer deterministic heuristics on the original ticket text first.
    # This makes short, explicit user texts like "My app crashes on startup"
    # reliably map to Bug/Incident even when the model reply contains
    # distracting words like "feature".
    if original_text:
        h = _heuristic_label_from_text(original_text)
        if h and h != "Task":
            return h

    # try to detect label from the reply content (still conservative)
    low_r = r.lower()
    if any(k in low_r for k in ("crash", "exception", "error", "fail", "failed", "stack trace")):
        return "Bug"
    if any(k in low_r for k in ("outage", "down", "unavailable", "service is down", "outage")):
        return "Incident"
    if any(k in low_r for k in ("feature request", "feature", "enhancement", "request", "improve")):
        return "Feature Request"
    if any(k in low_r for k in ("how", "why", "what", "help", "question")):
        return "Question"

    # final fallback: use heuristics on original text (this will return Task)
    return _heuristic_label_from_text(original_text)


async def _classify_with_openai(text: str) -> str:
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY not set — skipping OpenAI classification.")
        return "Task"

    system_prompt = (
        "You are a ticket classifier. Choose exactly one label from the list: "
        "Task, Bug, Incident, Feature Request, Question. Respond with ONLY the label."
    )
    user_prompt = f"Classify the following ticket text:\n\n{text.strip()}"

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 12,
        "temperature": 0.0,
        "n": 1
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    except Exception as e:
        print("OpenAI request failed:", str(e))
        return "Task"

    if resp.status_code == 401:
        print("OpenAI API 401: check OPENAI_API_KEY and billing/permissions.")
        return "Task"

    try:
        data = resp.json()
    except Exception:
        print("Failed to parse OpenAI response:", await resp.aread())
        return "Task"

    try:
        reply = data["choices"][0]["message"]["content"].strip()
    except Exception:
        print("Unexpected OpenAI response structure:", json.dumps(data)[:1000])
        return "Task"

    return _normalize_label(reply, original_text=text)


async def _classify_with_ggml(text: str) -> str:
    # Use llama-cpp-python via asyncio.to_thread to avoid blocking the event loop.
    if not GGML_MODEL_PATH:
        return "Task"
    model_file = Path(GGML_MODEL_PATH)
    if not model_file.exists():
        print(f"GGML model file not found at {GGML_MODEL_PATH}")
        return "Task"

    try:
        from llama_cpp import Llama
    except Exception as e:
        print("llama_cpp (llama-cpp-python) not installed or failed to import:", e)
        return "Task"

    prompt = (
        "You are a ticket classifier. Choose exactly one label from: Task, Bug, Incident, Feature Request, Question. "
        "Respond with ONLY the label and nothing else.\n\n"
        f"Ticket:\n{text.strip()}"
    )

    def run_sync():
        llm = Llama(model_path=str(model_file))

        def _extract_text(resp):
            if not resp:
                return None
            # dict-like responses from many llama-cpp-python versions
            if isinstance(resp, dict):
                # common: {'choices': [{'text': '...'}]}
                choices = resp.get("choices") or resp.get("output")
                if choices and isinstance(choices, (list, tuple)) and len(choices) > 0:
                    first = choices[0]
                    if isinstance(first, dict):
                        # look for text fields
                        for key in ("text", "message", "content"):
                            if key in first and isinstance(first[key], str):
                                return first[key]
                        # some shapes: {'message': {'content': '...'}}
                        if "message" in first and isinstance(first["message"], dict):
                            return first["message"].get("content")
                        # some shapes: {'content': [{'type':'output_text','text':'...'}]}
                        if "content" in first and isinstance(first["content"], list) and len(first["content"])>0:
                            c0 = first["content"][0]
                            if isinstance(c0, dict) and "text" in c0:
                                return c0.get("text")
                # some returns have 'generated_text'
                if "generated_text" in resp and isinstance(resp["generated_text"], str):
                    return resp["generated_text"]
                # very small chance the API returns plain {'output': '...'}
                if "output" in resp and isinstance(resp["output"], str):
                    return resp["output"]
                return None

            # list/tuple responses (rare)
            if isinstance(resp, (list, tuple)) and len(resp) > 0:
                first = resp[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict):
                    return _extract_text(first)

            # fallback to string
            if isinstance(resp, str):
                return resp

            return None

        # Try the common llama-cpp-python methods in order.
        last_err = None
        for method_name in ("create", "generate"):
            if hasattr(llm, method_name):
                method = getattr(llm, method_name)
                try:
                    resp = method(prompt=prompt, max_tokens=10, temperature=0.0)
                    text_resp = _extract_text(resp)
                    if text_resp:
                        return text_resp
                except Exception as e:
                    last_err = e

        # Try calling the model object directly (some versions make it callable)
        try:
            try:
                resp = llm(prompt, max_tokens=10, temperature=0.0)
            except TypeError:
                # try keyword form
                resp = llm(prompt=prompt, max_tokens=10, temperature=0.0)
            text_resp = _extract_text(resp)
            if text_resp:
                return text_resp
        except Exception as e:
            last_err = e

        # If we reach here, raise the last error so outer handler can log it
        if last_err:
            raise last_err
        return None

    try:
        reply = await asyncio.to_thread(run_sync)
    except Exception as e:
        print("Error while running ggml model:", e)
        return "Task"

    return _normalize_label(reply, original_text=text)


async def classify_ticket(text: str) -> str:
    """
    Classify the ticket text into one of the LABELS.

    Priority order:
    1. If `GGML_MODEL_PATH` points to a local ggml model and `llama-cpp-python` is available, use it (offline).
    2. Else if `OPENAI_API_KEY` is set, call OpenAI chat completions.
    3. Fallback to default label "Task".
    """
    # Try local ggml model first
    if GGML_MODEL_PATH:
        lbl = await _classify_with_ggml(text)
        if lbl and lbl != "Task":
            return lbl

    # Fallback to OpenAI if available
    if OPENAI_API_KEY:
        return await _classify_with_openai(text)

    # Final fallback
    return "Task"

"""meeting-ingest: persistent webhook on Genos (:8645).

Receives iOS Shortcut audio streams -> saves upload -> fire-and-forget:
Groq Whisper STT (if GROQ_API_KEY present) -> OmniRouter GLM synthesis ->
best-effort forward to christal-gateway bridge (if CHRISTAL_BRIDGE_URL set).

Env (from systemd EnvironmentFile /home/ubuntu/christal-hermes/.env + unit):
  MEETING_INGEST_TOKEN   required; X-Arkham-Token must match (401 otherwise)
  GROQ_API_KEY           optional; enables Whisper large-v3 STT
  OPENROUTER_API_KEY     Bearer key for OmniRouter synthesis
  OPENROUTER_BASE_URL    optional; defaults to http://100.64.0.3:8000
  OPENROUTER_MODEL       optional; defaults to glm-5.3-flash
  CHRISTAL_BRIDGE_URL    optional; inbound webhook on christal-gateway (pending)
"""

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, UploadFile

APP_DIR = Path("/home/ubuntu/meeting-ingest")
UPLOADS_DIR = APP_DIR / "uploads"
OUT_DIR = APP_DIR / "out"

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = "whisper-large-v3"
OMNI_FALLBACK_BASE = "http://100.64.0.3:8000"
DEFAULT_LLM_MODEL = "glm-5.3-flash"
SYS_PROMPT = (
    "Extract agenda items and action items from this meeting transcript, "
    "output as a terse markdown brief"
)

TOKEN = os.environ.get("MEETING_INGEST_TOKEN", "")

EXT_BY_MIME = {
    "audio/m4a": ".m4a", "audio/x-m4a": ".m4a", "audio/mp4": ".m4a",
    "audio/mp4a-latm": ".m4a", "audio/mpeg": ".mp3", "audio/mp3": ".mp3",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
    "audio/vnd.wave": ".wav", "audio/webm": ".webm", "video/webm": ".webm",
    "audio/ogg": ".ogg", "application/ogg": ".ogg", "audio/flac": ".flac",
    "audio/x-flac": ".flac", "audio/aac": ".aac", "audio/aacp": ".aac",
    "audio/opus": ".opus", "audio/amr": ".amr", "audio/3gpp": ".3gp",
    "video/mp4": ".mp4", "video/quicktime": ".mov",
}

app = FastAPI(title="meeting-ingest", version="1.0.0")


def _omni_chat_url() -> str:
    base = (os.environ.get("OPENROUTER_BASE_URL") or OMNI_FALLBACK_BASE).rstrip("/")
    return (base + "/chat/completions") if base.endswith("/v1") else (base + "/v1/chat/completions")


def _pick_ext(content_type: str, original_name: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in EXT_BY_MIME:
        return EXT_BY_MIME[ct]
    name = (original_name or "").strip()
    if "." in name:
        ext = name.rsplit(".", 1)[1].lower()
        if ext and len(ext) <= 5 and ext.isalnum():
            return "." + ext
    return ".bin"


def _pipeline(upload_id: str, audio_bytes: bytes, ext: str, content_type: str) -> None:
    """Fire-and-forget: STT (optional) -> synthesis -> bridge forward."""
    record = {
        "upload_id": upload_id,
        "bytes": len(audio_bytes),
        "received_at_utc": datetime.now(timezone.utc).isoformat(),
        "stt": {"configured": bool(os.environ.get("GROQ_API_KEY", "").strip())},
        "llm": {"configured": bool(os.environ.get("OPENROUTER_API_KEY", "").strip())},
        "bridge": {"configured": bool(os.environ.get("CHRISTAL_BRIDGE_URL", "").strip())},
    }
    transcript = ""

    # Stage 1: Whisper STT via Groq (only when a key exists).
    if record["stt"]["configured"]:
        try:
            r = requests.post(
                GROQ_STT_URL,
                headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"].strip()},
                files={"file": (upload_id + ext, audio_bytes, content_type or "application/octet-stream")},
                data={"model": GROQ_STT_MODEL},
                timeout=(30, 300),
            )
            r.raise_for_status()
            transcript = (r.json().get("text") or "").strip()
            record["stt"].update({"status": "ok", "transcript_chars": len(transcript)})
        except Exception as exc:  # noqa: BLE001 - background task must never raise
            record["stt"].update({"status": "error", "error": str(exc)[:400]})
    else:
        record["stt"].update({"status": "skipped", "note": "GROQ_API_KEY not set; awaiting key"})

    # Stage 2: GLM synthesis via OmniRouter (only when there is a transcript).
    if transcript and record["llm"]["configured"]:
        try:
            r = requests.post(
                _omni_chat_url(),
                headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"].strip()},
                json={
                    "model": os.environ.get("OPENROUTER_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": SYS_PROMPT},
                        {"role": "user", "content": transcript},
                    ],
                },
                timeout=(30, 300),
            )
            r.raise_for_status()
            summary = r.json()["choices"][0]["message"]["content"]
            record["llm"].update({"status": "ok", "model": os.environ.get("OPENROUTER_MODEL", DEFAULT_LLM_MODEL)})
        except Exception as exc:  # noqa: BLE001
            summary = ""
            record["llm"].update({"status": "error", "error": str(exc)[:400]})
    elif not transcript:
        summary = ""
        record["llm"].update({"status": "skipped", "note": "no transcript available (STT awaiting key)"})
    else:
        summary = ""
        record["llm"].update({"status": "skipped", "note": "OPENROUTER_API_KEY not set"})

    # Persist the brief (or pending marker) next to the run record.
    out_path = OUT_DIR / (upload_id + ".md")
    try:
        if summary:
            out_path.write_text(summary + "\n", encoding="utf-8")
        else:
            out_path.write_text(
                "# Meeting brief pending\n\nNo summary generated for `%s`.\n\n"
                "- STT: %s\n- LLM: %s\n" % (
                    upload_id, record["stt"].get("status"), record["llm"].get("status")
                ),
                encoding="utf-8",
            )
        record["output"] = str(out_path)
    except Exception as exc:  # noqa: BLE001
        record["output_error"] = str(exc)[:400]

    # Stage 3: best-effort forward to christal-gateway bridge (/webhooks/generic).
    # Auth: X-Christal-Webhook-Token from WEBHOOK_GENERIC_TOKEN (shared secret,
    # staged on Genos only — never committed).
    if record["bridge"]["configured"] and summary:
        try:
            headers = {}
            webhook_token = (os.environ.get("WEBHOOK_GENERIC_TOKEN") or "").strip()
            if webhook_token:
                headers["X-Christal-Webhook-Token"] = webhook_token
            br = requests.post(
                os.environ["CHRISTAL_BRIDGE_URL"].strip(),
                json={"source": "meeting", "upload_id": upload_id, "summary": summary},
                headers=headers,
                timeout=(15, 60),
            )
            record["bridge"].update({"status": "ok", "http": br.status_code})
        except Exception as exc:  # noqa: BLE001
            record["bridge"].update({"status": "error", "error": str(exc)[:400]})
    else:
        record["bridge"].update({"status": "skipped", "note": "bridge not configured or no summary"})

    try:
        (OUT_DIR / (upload_id + ".json")).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


@app.get("/health")
def health() -> dict:
    try:
        probe = UPLOADS_DIR / ".probe"
        probe.touch()
        probe.unlink()
        writable = True
    except Exception:  # noqa: BLE001
        writable = False
    stt = bool(os.environ.get("GROQ_API_KEY", "").strip())
    llm = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    return {
        "status": "ok" if stt else "awaiting-key",
        "stt_configured": stt,
        "llm_configured": llm,
        "bridge_configured": bool(os.environ.get("CHRISTAL_BRIDGE_URL", "").strip()),
        "uploads_dir_writable": writable,
    }


@app.post("/meeting/ingest")
async def meeting_ingest(
    background: BackgroundTasks,
    audio: UploadFile = File(...),
    x_arkham_token: str | None = Header(default=None, alias="X-Arkham-Token"),
) -> dict:
    if not TOKEN or not x_arkham_token or not secrets.compare_digest(x_arkham_token, TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing X-Arkham-Token")

    payload = await audio.read()
    ext = _pick_ext(audio.content_type, audio.filename or "")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = "meeting-" + ts
    if any(UPLOADS_DIR.glob(stem + ".*")):
        stem += "-" + secrets.token_hex(3)
    dest = UPLOADS_DIR / (stem + ext)
    dest.write_bytes(payload)

    upload_id = stem
    background.add_task(_pipeline, upload_id, payload, ext, audio.content_type)
    return {"received": True, "upload_id": upload_id, "bytes": len(payload)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8645)

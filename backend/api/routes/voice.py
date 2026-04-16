from fastapi import APIRouter, HTTPException
from api.models.request_models import VoiceExecuteRequest
from voice.engine import execute_text_command, listen_once, parse_command

router = APIRouter()


@router.post("/voice/execute")
def voice_execute(req: VoiceExecuteRequest):
    """Execute a voice command from pre-transcribed text (e.g., browser Web Speech API)."""
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Command text is required.")
    return execute_text_command(req.text.strip().lower())


@router.post("/voice/listen")
def voice_listen():
    """
    Capture one utterance from the system microphone and execute it.
    Requires pyaudio installed on the server machine.
    """
    text = listen_once(timeout=10, phrase_time_limit=8)
    if text is None:
        raise HTTPException(status_code=408, detail="No speech detected or microphone unavailable.")
    result = execute_text_command(text)
    result["recognised_text"] = text
    return result


@router.get("/voice/commands")
def voice_commands():
    """Return the list of supported voice commands."""
    import json, os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "voice_commands.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)

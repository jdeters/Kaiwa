"""Speech-to-text with faster-whisper (CTranslate2, int8 on CPU).

The model (medium by default: on a laptop CPU, small is ~2x faster but drifts
badly on noisy or accented speech, and large-v3-turbo is ~1.7x slower without
being more accurate here; KAIWA_WHISPER_MODEL overrides) is
downloaded into models/whisper by setup.sh and loaded once, on first use or
by warm() at server start. Input is a 16-bit PCM WAV (the browser sends 16 kHz mono).
"""
import glob
import importlib.util
import io
import os
import re
import threading
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "models", "whisper")
MODEL_NAME = os.environ.get("KAIWA_WHISPER_MODEL", "medium")

_model = None
_lock = threading.Lock()


def available() -> bool:
    """faster-whisper installed and the model already downloaded."""
    if importlib.util.find_spec("faster_whisper") is None:
        return False
    return bool(glob.glob(os.path.join(MODEL_DIR, "**", "model.bin"), recursive=True))


def load():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8",
                                  download_root=MODEL_DIR)
        return _model


def warm():
    """Load the model ahead of the first utterance (call from a background thread)."""
    if available():
        load()


def _pcm16k(wav_bytes: bytes) -> np.ndarray:
    """16-bit PCM WAV → mono float32 at 16 kHz. Decoded here rather than by
    faster-whisper's PyAV path, which breaks across PyAV versions."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate, channels = w.getframerate(), w.getnchannels()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio)
    return audio.astype(np.float32)


def speech_end(wav_bytes: bytes) -> dict:
    """Where speech stops in a recent stretch of audio, by Silero VAD (bundled
    with faster-whisper). A volume threshold can't tell a pause from background
    noise that the browser's auto-gain has lifted; this can. ~1 ms per second of audio."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    audio = _pcm16k(wav_bytes)
    ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=0))
    return {"duration": len(audio) / 16000,
            "last_speech_end": ts[-1]["end"] / 16000 if ts else None}


def transcribe(wav_bytes: bytes, language: str = "ja", context: str = "") -> str:
    """context: the conversation so far (the partner's last line). Passed as
    Whisper's prompt, it steers ambiguous sounds toward words that fit."""
    segments, _ = load().transcribe(
        _pcm16k(wav_bytes), language=language, beam_size=5,
        vad_filter=True,                   # skip silence: whisper hallucinates on it
        condition_on_previous_text=False,
        initial_prompt=context or None,
    )
    text = "".join(s.text for s in segments).strip()
    # strip bracketed non-speech artifacts like [音楽], (笑い)
    return re.sub(r"[\[(（【][^\])）】]*[\])）】]", "", text).strip()

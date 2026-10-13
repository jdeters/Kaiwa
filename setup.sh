#!/bin/bash
# One-time setup: Python env, dependencies, speech model, dictionary.
#   ./setup.sh                   everything except the voice engine
#   ./setup.sh --with-voicevox   also download VOICEVOX (Linux x64, ~1.8 GB)
set -e
cd "$(dirname "$0")"

echo "🗣  Kaiwa setup"

command -v python3 >/dev/null || { echo "✗ python3 is required — https://www.python.org"; exit 1; }

if [ ! -d .venv ]; then
  echo "▶ creating Python environment…"
  python3 -m venv .venv
fi
echo "▶ installing dependencies…"
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

mkdir -p data models

# speech-recognition model for faster-whisper (medium, ~1.5 GB; KAIWA_WHISPER_MODEL overrides)
if [ -z "$(find models/whisper -name model.bin 2>/dev/null)" ]; then
  echo "▶ downloading speech-recognition model…"
  .venv/bin/python -c "from server import stt; stt.load()"
fi

# VOICEVOX voice engine (opt-in: large download)
VOICEVOX_VERSION=0.25.2
if [ "$1" = "--with-voicevox" ] && [ -z "$(ls vendor/linux-*/run 2>/dev/null)" ]; then
  if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
    echo "⚠ --with-voicevox downloads the Linux x64 build; get yours from"
    echo "  https://github.com/VOICEVOX/voicevox_engine/releases and unzip it into vendor/"
  else
    command -v 7z >/dev/null || { echo "✗ VOICEVOX ships as .7z — install p7zip (7z) first"; exit 1; }
    ARCHIVE="vendor/download/voicevox_engine-linux-cpu-x64-$VOICEVOX_VERSION.7z.001"
    mkdir -p vendor/download
    if [ ! -f "$ARCHIVE" ]; then
      echo "▶ downloading VOICEVOX engine (~1.8 GB)…"
      curl -L --fail --progress-bar -o "$ARCHIVE.part" \
        "https://github.com/VOICEVOX/voicevox_engine/releases/download/$VOICEVOX_VERSION/$(basename "$ARCHIVE")"
      mv "$ARCHIVE.part" "$ARCHIVE"
    fi
    echo "▶ unpacking VOICEVOX…"
    7z x -y -ovendor "$ARCHIVE" >/dev/null
    rm -rf vendor/download
  fi
fi

# JMdict dictionary (latest jmdict-simplified English release)
if ! ls models/jmdict-eng-*.json >/dev/null 2>&1; then
  echo "▶ downloading JMdict dictionary…"
  JM_URL=$(curl -s https://api.github.com/repos/scriptin/jmdict-simplified/releases/latest \
    | grep -o '"browser_download_url": *"[^"]*jmdict-eng-[0-9][^"]*\.json\.zip"' \
    | head -1 | cut -d'"' -f4)
  if [ -n "$JM_URL" ]; then
    curl -L --progress-bar -o /tmp/kaiwa-jmdict.zip "$JM_URL"
    unzip -oq /tmp/kaiwa-jmdict.zip -d models/
    rm -f /tmp/kaiwa-jmdict.zip
  else
    echo "⚠ couldn't reach the JMdict release — download a jmdict-eng-*.json from"
    echo "  https://github.com/scriptin/jmdict-simplified/releases and put it in models/"
  fi
fi

echo "✓ setup complete. Next:"
echo "  1. Put ANTHROPIC_API_KEY=… in .env (or paste the key in Settings later)"
echo "  2. With Anki + AnkiConnect running: .venv/bin/python -m server.learner build"
echo "     (edit data/learner.yaml for your decks and textbook lesson, then build again)"
echo "  3. ./run.sh"

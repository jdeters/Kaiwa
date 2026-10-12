#!/bin/bash
# Kaiwa launcher — starts the voice engines (if installed) and the app server.
set -e
cd "$(dirname "$0")"

echo "🗣️  Kaiwa — Japanese conversation practice"
PORT="${KAIWA_PORT:-8130}"

# first run without setup? be helpful
[ -d .venv ] || { echo "✗ run ./setup.sh first"; exit 1; }

# Learner profile: refresh from Anki when it's open, otherwise keep the last one
if curl -s --max-time 2 http://127.0.0.1:8765 >/dev/null; then
  echo "▶ updating learner profile from Anki…"
  .venv/bin/python -m server.learner build | head -1 || true
elif [ -f data/learner_profile.json ]; then
  echo "• Anki isn't open — using your last learner profile (Settings → Update from Anki later)"
else
  echo "⚠ no learner profile yet — open Anki (with AnkiConnect), then Settings → Update from Anki"
fi

# 1. VOICEVOX (the tutor's voice; ./setup.sh --with-voicevox installs it on Linux)
VV=$(ls vendor/linux-*/run vendor/macos-*/run 2>/dev/null | head -1)
if [ -n "$VV" ] && [ -x "$VV" ]; then
  if ! curl -s --max-time 2 http://127.0.0.1:50021/version >/dev/null; then
    echo "▶ starting VOICEVOX engine (takes ~20s)…"
    (cd "$(dirname "$VV")" && ./run --host 127.0.0.1 --port 50021 >/tmp/voicevox.log 2>&1 &)
  fi
else
  echo "⚠ no VOICEVOX engine in vendor/ — the tutor can't speak (./setup.sh --with-voicevox)"
fi

# 1b. AivisSpeech (optional — emotional / anime-style voices, VOICEVOX-compatible API)
if ! curl -s --max-time 2 http://127.0.0.1:10101/version >/dev/null; then
  AIVIS="vendor/aivisspeech-engine/run"
  [ -x "$AIVIS" ] || AIVIS=$(find /Applications/AivisSpeech.app -maxdepth 5 -name run -type f 2>/dev/null | head -1)
  if [ -x "$AIVIS" ]; then
    echo "▶ starting AivisSpeech engine…"
    (cd "$(dirname "$AIVIS")" && ./run --host 127.0.0.1 --port 10101 >/tmp/aivisspeech.log 2>&1 &)
  fi
fi

# 2. Tailscale HTTPS link (optional — lets your phone reach Kaiwa from anywhere)
TS=$(command -v tailscale || echo "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
if [ -x "$TS" ] && "$TS" status >/dev/null 2>&1; then
  "$TS" serve --bg "$PORT" >/dev/null 2>&1 || true
  TSHOST=$("$TS" status --json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null)
  [ -n "$TSHOST" ] && echo "📱 phone link: https://$TSHOST"
fi

# 3. App server (0.0.0.0 so phones on the network can connect)
echo "▶ starting Kaiwa at http://localhost:$PORT"
OPEN=$(command -v xdg-open || command -v open || true)
[ -n "$OPEN" ] && (sleep 2 && "$OPEN" "http://localhost:$PORT" >/dev/null 2>&1) &
exec .venv/bin/python -m uvicorn server.main:app --host 0.0.0.0 --port "$PORT"

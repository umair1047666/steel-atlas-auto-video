#!/usr/bin/env bash
# Local end-to-end test: 45-second Steel Atlas pilot video.
# Proves: real-clip download, voice (Edge TTS), render, music mix + ducking,
# highlight subtitles burn-in, auto-QC, upload dry-run.
# Anything that cannot be tested here is reported as UNTESTED, never claimed.
set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv-test/bin/python"
[ -x "$PY" ] || PY="python3"

echo "=== 1. unit tests (no network) ==="
"$PY" -m pytest tests/ -q || { echo "UNIT TESTS FAILED"; exit 1; }

echo "=== 2. stand-in music track (royalty-free track comes from Drive in production) ==="
mkdir -p music
if [ ! -f music/track.mp3 ]; then
  ffmpeg -y -v error -f lavfi -i "sine=frequency=220:duration=90" \
    -f lavfi -i "sine=frequency=330:duration=90" \
    -filter_complex "[0:a][1:a]amix=inputs=2:duration=first,volume=0.4" \
    -c:a libmp3lame -b:a 128k music/track.mp3
fi

echo "=== 3. test job ==="
mkdir -p jobs/sa-test-001
cp queue/sample-job.json jobs/sa-test-001/job.json

echo "=== 4. run the pipeline (all stages, upload dry-run) ==="
"$PY" -m pipeline.run_job --stage all --job sa-test-001
RC=$?
if [ $RC -ne 0 ]; then
  echo "PIPELINE FAILED (exit $RC) — see log above; state in jobs/sa-test-001/state.json"
  exit $RC
fi

echo "=== 5. verify outputs ==="
"$PY" scripts/verify_e2e.py jobs/sa-test-001

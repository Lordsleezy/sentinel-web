#!/usr/bin/env sh
set -eu

OLLAMA_HOST="${AI_HELPER_HOST:-http://127.0.0.1:11434}"
AI_MODEL="${AI_HELPER_MODEL:-llama3.2:1b}"
PORT="${PORT:-8766}"

echo "Starting Ollama helper..."
ollama serve &
OLLAMA_PID="$!"

echo "Waiting for Ollama on ${OLLAMA_HOST}..."
i=0
until curl -fsS "${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "Ollama did not become ready in time" >&2
    exit 1
  fi
  sleep 1
done

if ! ollama list | awk '{print $1}' | grep -Fx "${AI_MODEL}" >/dev/null 2>&1; then
  echo "Pulling tiny AI helper model ${AI_MODEL}. First boot may take several minutes..."
  ollama pull "${AI_MODEL}"
fi

echo "Verifying tiny AI helper model..."
if ! printf '{"model":"%s","prompt":"Return JSON: {\"ok\": true}","stream":false,"options":{"num_predict":16}}' "${AI_MODEL}" \
  | curl -fsS "${OLLAMA_HOST}/api/generate" -H "Content-Type: application/json" -d @- >/dev/null; then
  echo "AI helper model did not respond" >&2
  kill "${OLLAMA_PID}" >/dev/null 2>&1 || true
  exit 1
fi

echo "Starting SentinelWeb on port ${PORT}..."
exec uvicorn api:app --host 0.0.0.0 --port "${PORT}"

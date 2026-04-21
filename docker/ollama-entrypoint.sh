#!/bin/bash
set -e

MODEL="${OLLAMA_MODEL:-lfm2.5-thinking:latest}"

/bin/ollama serve &
OLLAMA_PID=$!

echo "[ollama] waiting for server to start..."
until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 1
done
echo "[ollama] server ready"

if ! ollama list | grep -q "${MODEL%%:*}"; then
    echo "[ollama] pulling ${MODEL}..."
    ollama pull "$MODEL"
    echo "[ollama] model ready"
else
    echo "[ollama] model ${MODEL} already present"
fi

wait "$OLLAMA_PID"

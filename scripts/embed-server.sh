#!/usr/bin/env bash
# Local llama.cpp embedding server for airec.
#
#   scripts/embed-server.sh             download the GGUF if needed, run in the foreground
#   scripts/embed-server.sh stop        stop a server started by the pipeline
#   scripts/embed-server.sh install     run it at login as a launchd agent (macOS)
#   scripts/embed-server.sh uninstall   remove that agent
#
# The pipeline starts this automatically (EMBED_AUTOSTART=1, the default) when
# nothing is listening on the embedding URL in config/models.yaml; `install`
# makes it start at login instead. Either way the server sleeps
# after EMBED_SLEEP_IDLE seconds (weights unloaded, ~0.1 GB) and wakes on the
# next request in under a second. Uses the official Qwen GGUF, whose header sets
# tokenizer.ggml.add_eos_token=true, so every input ends in <|endoftext|> and
# last-token pooling reads the token the model was trained to pool.
set -euo pipefail

REPO="${EMBED_HF_REPO:-Qwen/Qwen3-Embedding-0.6B-GGUF}"
FILE="${EMBED_HF_FILE:-Qwen3-Embedding-0.6B-Q8_0.gguf}"
ALIAS="${EMBED_MODEL:-qwen3-embedding-0.6b}"
PORT="${EMBED_PORT:-8081}"
# One slot, and a batch as large as the context: an embedding input must fit in
# a single ubatch. The client truncates inputs to 8000 chars (~2-3k tokens).
CTX="${EMBED_CTX:-8192}"
SLEEP_IDLE="${EMBED_SLEEP_IDLE:-300}"
PID_FILE="${EMBED_PID_FILE:-${DATA_DIR:-./data}/embed-server/server.pid}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT="$HOME/Library/LaunchAgents/com.airec.embed-server.plist"

if [[ "${1:-}" == "install" ]]; then
    mkdir -p "$ROOT/data/embed-server" "$(dirname "$AGENT")"
    cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.airec.embed-server</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/embed-server.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin</string>
    <key>EMBED_PORT</key><string>$PORT</string>
    <key>EMBED_SLEEP_IDLE</key><string>$SLEEP_IDLE</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$ROOT/data/embed-server/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/embed-server/server.log</string>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$AGENT"
    echo "installed $AGENT (port $PORT, sleeps after ${SLEEP_IDLE}s idle)"
    exit 0
fi

if [[ "${1:-}" == "uninstall" ]]; then
    launchctl bootout "gui/$(id -u)" "$AGENT" 2>/dev/null || true
    rm -f "$AGENT"
    echo "removed $AGENT"
    exit 0
fi

if [[ "${1:-}" == "stop" ]]; then
    if [[ -f "$PID_FILE" ]] && kill "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "stopped embed server (pid $(cat "$PID_FILE"))"
    else
        echo "no embed server running from $PID_FILE"
    fi
    rm -f "$PID_FILE"
    exit 0
fi

command -v llama-server >/dev/null || { echo "llama-server not found (brew install llama.cpp)" >&2; exit 1; }

# Use the Hugging Face cache when the file is already there (no network, no hf
# CLI needed); otherwise download it with hf, or with uvx when hf is missing.
HF_CACHE="${HF_HUB_CACHE:-${HF_HOME:-$HOME/.cache/huggingface}/hub}"
MODEL_PATH="$(ls "$HF_CACHE/models--${REPO//\//--}"/snapshots/*/"$FILE" 2>/dev/null | head -n 1 || true)"
if [[ ! -f "$MODEL_PATH" ]]; then
    if command -v hf >/dev/null; then
        HF=(hf)
    elif command -v uvx >/dev/null; then
        HF=(uvx --from huggingface_hub hf)
    else
        echo "model not cached and neither hf nor uvx found (pip install -U huggingface_hub)" >&2
        exit 1
    fi
    # Prints the local path on the last line.
    MODEL_PATH="$("${HF[@]}" download "$REPO" "$FILE" --quiet | tail -n 1)"
fi
[[ -f "$MODEL_PATH" ]] || { echo "download failed: $REPO/$FILE" >&2; exit 1; }

mkdir -p "$(dirname "$PID_FILE")"
echo $$ > "$PID_FILE"

exec llama-server \
    --model "$MODEL_PATH" \
    --alias "$ALIAS" \
    --embeddings \
    --pooling last \
    --host 127.0.0.1 \
    --port "$PORT" \
    --sleep-idle-seconds "$SLEEP_IDLE" \
    --ctx-size "$CTX" \
    --batch-size "$CTX" \
    --ubatch-size "$CTX" \
    --parallel 1

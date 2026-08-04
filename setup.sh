#!/usr/bin/env bash
# Rivet v2 setup — Ollama + model + memory DB.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_NAME="rivet"
BASE_MODEL="qwen2.5-coder:32b"
MODELFILE="$SCRIPT_DIR/Modelfile"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
MEMORY_DB="$SCRIPT_DIR/data/rivet_memory.db"

echo "== Rivet setup =="

# 1. Ollama
if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama not found — installing..."
    if [[ "$(uname -s)" == "Darwin" ]]; then
        echo "No unattended installer for macOS. Install from https://ollama.com/download and re-run this script." >&2
        exit 1
    fi
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "Ollama found: $(command -v ollama)"
fi

if ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Starting ollama serve..."
    nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    for _ in $(seq 1 30); do
        curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
        sleep 1
    done
fi

# 2. Pull base model
echo "Pulling $BASE_MODEL (this can take a while)..."
ollama pull "$BASE_MODEL"

# 3. Create the Rivet model from the Modelfile
if [[ ! -f "$MODELFILE" ]]; then
    echo "Modelfile not found at $MODELFILE" >&2
    exit 1
fi
echo "Creating '$MODEL_NAME' model from $MODELFILE..."
ollama create "$MODEL_NAME" -f "$MODELFILE"

# 4. Python deps (memory.py itself is stdlib-only; this covers anything
#    added later, e.g. the HTTP server's `requests` dependency)
if [[ -f "$REQUIREMENTS" ]] && grep -qv '^\s*#\|^\s*$' "$REQUIREMENTS"; then
    echo "Installing Python dependencies from $REQUIREMENTS..."
    python3 -m pip install --user -r "$REQUIREMENTS"
else
    echo "No Python dependencies to install."
fi

# 5. Initialize the memory database
echo "Initializing memory database..."
python3 "$SCRIPT_DIR/engine/memory.py" init --db "$MEMORY_DB"

# 6. Instructions
cat <<EOF

== Rivet is ready ==

Model:   $MODEL_NAME (base: $BASE_MODEL)
Memory:  $MEMORY_DB

Start Rivet directly:
  ollama run $MODEL_NAME

Or run it through the discipline-gated HTTP endpoint:
  python3 "$SCRIPT_DIR/rivet.py"

In Python, load a user's memory before a session:
  from engine.memory import MemoryStore
  store = MemoryStore("$MEMORY_DB")
  system_block = store.to_system_block(user_id, first_message)

EOF

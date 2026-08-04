# Rivet Deployment Guide

## Prerequisites

- **Ollama** (setup.sh installs it)
- **Python 3.11+** (for the scaffold)
- **~20GB disk** (for the qwen2.5-coder:32b model)
- **~20GB RAM** (for inference — the model runs in your system memory)
- Your local checkout of the `multiversecampus` repo

## Step 1: Install

```bash
git clone https://github.com/Liberation-Labs-THCoalition/Project-Rivet.git
cd Project-Rivet
chmod +x setup.sh
./setup.sh
```

This pulls the model (~19GB, one-time), creates the Rivet configuration, and initializes the memory database.

## Step 2: Point at Your Repo

Edit `kintsugi_config.yaml`:

```yaml
paths:
  campus_repo: /path/to/your/multiversecampus
```

Then build the context:

```bash
python pharos/build_campus_pack.py --context-dir context
```

This generates the architecture snapshot and loads it into Rivet's context.

## Step 3: Start

```bash
python rivet.py                    # HTTP server on port 8100
python rivet.py --port 8200        # Custom port
python rivet.py --ask "question"   # One-shot CLI mode
```

## IDE Integration

### IMPORTANT: Use Rivet's endpoint, not Ollama directly

Rivet's value is the discipline gate. If your IDE talks directly to Ollama (`localhost:11434`), the gate is bypassed — no migration safety, no audit pattern matching, no confidence levels. Always point your IDE at Rivet's endpoint.

### Continue.dev (VS Code)

In `.continue/config.json`:

```json
{
  "models": [{
    "title": "Rivet",
    "provider": "openai",
    "model": "rivet",
    "apiBase": "http://localhost:8100/v1"
  }]
}
```

### Cursor

Settings → Models → Add custom model:
- API Base: `http://localhost:8100/v1`
- Model: `rivet`

### Any OpenAI-compatible tool

Rivet serves an OpenAI-compatible API at `http://localhost:8100/v1/chat/completions`. Any tool that speaks the OpenAI protocol can use it. The system prompt and discipline gate are applied server-side — the IDE doesn't need to know about them.

### CLI (no IDE)

```bash
# One-shot question
python rivet.py --ask "How does the gem transfer work?" --user neek

# Interactive session
curl -X POST http://localhost:8100/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Is this migration safe?", "user": "neek"}'
```

## Docker (Alternative)

```bash
docker compose up -d
```

This starts Ollama + Rivet in containers. The model is stored in a Docker volume (persists across restarts). Mount your repo as a volume:

```yaml
# docker-compose.yml
services:
  rivet:
    build: .
    ports:
      - "8100:8100"
    volumes:
      - /path/to/multiversecampus:/repo:ro
      - rivet-data:/app/data
    environment:
      - REPO_PATH=/repo
```

## Updating

When the campus codebase changes significantly (new tables, new routes, major refactor):

```bash
python pharos/build_campus_pack.py --context-dir context
```

This rebuilds the architecture context. Rivet picks it up on next session start.

When the audit findings are updated:

```bash
cp /path/to/new/audit_findings.md context/audit_findings.md
```

## Troubleshooting

### "Ollama not responding"
```bash
ollama serve  # Start Ollama if not running
```

### "Model not found"
```bash
ollama list          # Check if rivet model exists
./setup.sh           # Re-run setup to create it
```

### "Slow responses"
The 32B model needs ~20GB RAM. If your machine is swapping, try the 14B fallback:
```bash
ollama pull qwen2.5-coder:14b
# Edit Modelfile: change FROM line to qwen2.5-coder:14b
ollama create rivet -f Modelfile
```

### "Rivet approved a destructive migration"
This means the CLAUDE.md system prompt isn't loaded correctly. Verify:
```bash
ollama show rivet --system | grep "Never suggest a destructive migration"
```
If not found, rebuild: `ollama create rivet -f Modelfile`

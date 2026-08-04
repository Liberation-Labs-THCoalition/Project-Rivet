"""Model access for Rivet.

Three backends behind one interface:

  OllamaClient        — HTTP to an Ollama server via /api/generate.
                        Knowledge injected as system-context block.
  TransformersClient  — local HF transformers. Knowledge injected as
                        precomputed KV cache blocks via pharos.kv_injector
                        — true zero-prompt-token injection.
  OpenAIClientBackend — HTTP to any /v1/chat/completions endpoint:
                        vLLM, Ollama /v1, llama-server, LM Studio,
                        OpenRouter, Cerebras, Groq, or any OpenAI-
                        compatible routing proxy. Knowledge injected as
                        system-context (same honest limit as Ollama).

Note: rivet.py *serves* /v1/chat/completions inbound (so Continue.dev
and Cursor can talk TO Rivet). OpenAIClientBackend is outbound — Rivet
talking to an upstream model server. Two directions, one protocol.

Which backend runs is a config decision (`model.backend`), not a code
change. Chips only ever see `ModelClient.generate()`.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class ModelReply:
    text: str
    ok: bool
    backend: str
    knowledge_injected: str = "none"  # none | system_prompt | kv_cache
    error: str = ""


class ModelClient:
    """Interface. Use OllamaClient, OpenAIClientBackend, or TransformersClient."""

    def generate(self, prompt: str, system: str = "", knowledge: str = "",
                 max_tokens: int = 1024) -> ModelReply:
        raise NotImplementedError


class OllamaClient(ModelClient):
    def __init__(self, base_url: str = "http://localhost:11434",
                 model: str = "qwen2.5-coder:32b",
                 temperature: float = 0.3, num_ctx: int = 32768,
                 timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.timeout = timeout

    def generate(self, prompt: str, system: str = "", knowledge: str = "",
                 max_tokens: int = 1024) -> ModelReply:
        full_system = system
        injected = "none"
        if knowledge:
            full_system = (
                f"{system}\n\n# INJECTED KNOWLEDGE (Pharos)\n"
                f"You have access to the following knowledge:\n{knowledge}"
            ).strip()
            injected = "system_prompt"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": full_system,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": max_tokens,
            },
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
            return ModelReply(
                text=data.get("response", ""), ok=True,
                backend=f"ollama:{self.model}", knowledge_injected=injected,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ModelReply(
                text="", ok=False, backend=f"ollama:{self.model}",
                error=f"Ollama unreachable at {self.base_url}: {exc}",
            )


class OpenAIClientBackend(ModelClient):
    """Outbound client for any OpenAI-compatible /v1/chat/completions endpoint.

    Supports hosted APIs (OpenRouter, Together, Cerebras, Groq) and local
    servers (vLLM, Ollama /v1, llama-server, LM Studio). Knowledge is
    injected as system-context — same approach as OllamaClient.
    """

    def __init__(self, base_url: str = "http://localhost:8000/v1",
                 model: str = "", api_key: str = "",
                 temperature: float = 0.3, max_ctx: int = 32768,
                 timeout: int = 180):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_ctx = max_ctx
        self.timeout = timeout

    def generate(self, prompt: str, system: str = "", knowledge: str = "",
                 max_tokens: int = 1024) -> ModelReply:
        full_system = system
        injected = "none"
        if knowledge:
            full_system = (
                f"{system}\n\n# INJECTED KNOWLEDGE (Pharos)\n"
                f"You have access to the following knowledge:\n{knowledge}"
            ).strip()
            injected = "system_prompt"

        messages = []
        if full_system:
            messages.append({"role": "system", "content": full_system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())

            choices = data.get("choices", [])
            text = ""
            if choices:
                text = choices[0].get("message", {}).get("content", "") or ""

            return ModelReply(
                text=text, ok=True,
                backend=f"openai_client:{self.model}",
                knowledge_injected=injected,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return ModelReply(
                text="", ok=False, backend=f"openai_client:{self.model}",
                error=f"OpenAI-compatible endpoint unreachable at {self.base_url}: {exc}",
            )


class TransformersClient(ModelClient):
    """Local transformers backend with real KV-cache pack injection.

    Lazy-loads torch/transformers on first generate() so importing this
    module never requires them. Pack KV blocks come from
    pharos.kv_injector.KVPackEncoder (precomputed once per pack).
    """

    def __init__(self, model_path: str, device: str = "auto",
                 pack_cache_dir: str = ""):
        self.model_path = model_path
        self.device = device
        self.pack_cache_dir = pack_cache_dir
        self._injector = None

    def _ensure_loaded(self):
        if self._injector is None:
            from pharos.kv_injector import KVInjector
            self._injector = KVInjector(
                self.model_path, device=self.device,
                cache_dir=self.pack_cache_dir,
            )
        return self._injector

    def generate(self, prompt: str, system: str = "", knowledge: str = "",
                 max_tokens: int = 1024) -> ModelReply:
        try:
            injector = self._ensure_loaded()
        except ImportError as exc:
            return ModelReply(
                text="", ok=False, backend="transformers",
                error=f"torch/transformers not available: {exc}",
            )
        text = injector.generate(
            prompt, system=system, knowledge=knowledge, max_tokens=max_tokens,
        )
        return ModelReply(
            text=text, ok=True,
            backend=f"transformers:{self.model_path}",
            knowledge_injected="kv_cache" if knowledge else "none",
        )


def build_client(cfg: dict) -> ModelClient:
    """Construct the configured backend from the `model:` config section.

    backend values: "ollama" (default), "transformers", "openai_client"
    """
    backend = cfg.get("backend", "ollama")
    if backend == "transformers":
        return TransformersClient(
            model_path=cfg.get("model_path", cfg.get("name", "")),
            device=cfg.get("device", "auto"),
            pack_cache_dir=cfg.get("pack_cache_dir", ""),
        )
    if backend == "openai_client":
        return OpenAIClientBackend(
            base_url=cfg.get("base_url", "http://localhost:8000/v1"),
            model=cfg.get("name", ""),
            api_key=cfg.get("api_key", ""),
            temperature=cfg.get("temperature", 0.3),
            max_ctx=cfg.get("max_ctx", 32768),
            timeout=cfg.get("timeout_seconds", 180),
        )
    return OllamaClient(
        base_url=cfg.get("base_url", "http://localhost:11434"),
        model=cfg.get("name", "qwen2.5-coder:32b"),
        temperature=cfg.get("temperature", 0.3),
        num_ctx=cfg.get("num_ctx", 32768),
        timeout=cfg.get("timeout_seconds", 180),
    )

"""Model access for Rivet.

Two backends behind one interface:

  OllamaClient       — HTTP to an Ollama server. Knowledge packs are
                       injected as a system-context block (Ollama's API
                       exposes no KV-cache handle, so this is the honest
                       limit of that transport).
  TransformersClient — local HF transformers. Knowledge packs are
                       injected as precomputed KV cache blocks via
                       pharos.kv_injector — true zero-prompt-token
                       injection, the real Pharos pipeline.

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
    """Interface. Use OllamaClient or TransformersClient."""

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
    """Construct the configured backend from the `model:` config section."""
    backend = cfg.get("backend", "ollama")
    if backend == "transformers":
        return TransformersClient(
            model_path=cfg.get("model_path", cfg.get("name", "")),
            device=cfg.get("device", "auto"),
            pack_cache_dir=cfg.get("pack_cache_dir", ""),
        )
    return OllamaClient(
        base_url=cfg.get("base_url", "http://localhost:11434"),
        model=cfg.get("name", "qwen2.5-coder:32b"),
        temperature=cfg.get("temperature", 0.3),
        num_ctx=cfg.get("num_ctx", 32768),
        timeout=cfg.get("timeout_seconds", 180),
    )

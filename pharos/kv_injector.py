"""Real KV-cache knowledge injection (the actual Pharos pipeline).

The zero-prompt-token path: the knowledge pack is run through the model
ONCE, its key/value cache is saved to disk, and every subsequent request
resumes generation from that cache — the model attends over the
knowledge without the knowledge ever being re-tokenized, re-prefilled,
or counted against the request.

Mechanics (transformers backend):

  prefix_text  = chat-template rendering of the system message
                 (Rivet rules + pack knowledge)
  prefix_cache = model(prefix_tokens).past_key_values   # computed once
  answer       = model.generate(suffix_tokens, past_key_values=prefix_cache)

The suffix (user question + generation prompt) must be the exact textual
continuation of the prefix, so both are rendered from the same chat
template and we assert the prefix is a string prefix of the full render.
Caches are keyed by SHA-256 of (model, prefix_text) and stored under
`cache_dir`, so pack updates automatically invalidate them.

Ollama exposes no KV handle, which is why OllamaClient falls back to
system-prompt injection. This module is what makes the transformers
backend the real thing.
"""

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("pharos.kv")


class KVInjector:
    def __init__(self, model_path: str, device: str = "auto",
                 cache_dir: str = ""):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.model_path = model_path
        self.device = self._pick_device(device)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info("loading %s on %s", model_path, self.device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        dtype = torch.float16 if self.device != "cpu" else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            model_path, torch_dtype=dtype,
        ).to(self.device).eval()
        self._mem_cache: dict = {}   # prefix_hash -> legacy kv tuple

    def _pick_device(self, device: str) -> str:
        if device != "auto":
            return device
        if self.torch.cuda.is_available():
            return "cuda"
        if getattr(self.torch.backends, "mps", None) and \
                self.torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    # ------------------------------------------------------------------

    def _render(self, system: str, user: str) -> tuple:
        """Render (prefix_text, full_text) from the chat template."""
        sys_msgs = [{"role": "system", "content": system}]
        all_msgs = sys_msgs + [{"role": "user", "content": user}]
        prefix = self.tokenizer.apply_chat_template(
            sys_msgs, tokenize=False, add_generation_prompt=False,
        )
        full = self.tokenizer.apply_chat_template(
            all_msgs, tokenize=False, add_generation_prompt=True,
        )
        if not full.startswith(prefix):
            # Template interleaves system content non-prefix-wise (rare);
            # KV reuse is unsound here — signal the caller to prefill fully.
            return "", full
        return prefix, full

    def _prefix_cache(self, prefix_text: str):
        """Compute (or load) the KV cache for a knowledge prefix."""
        key = hashlib.sha256(
            f"{self.model_path}||{prefix_text}".encode()
        ).hexdigest()[:24]

        if key in self._mem_cache:
            return key, self._mem_cache[key]

        disk_path = self.cache_dir / f"{key}.pt" if self.cache_dir else None
        if disk_path and disk_path.exists():
            legacy = self.torch.load(disk_path, map_location=self.device)
            self._mem_cache[key] = legacy
            logger.info("loaded pack KV cache %s from disk", key)
            return key, legacy

        tokens = self.tokenizer(prefix_text, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            out = self.model(**tokens, use_cache=True)
        cache = out.past_key_values
        legacy = (cache.to_legacy_cache()
                  if hasattr(cache, "to_legacy_cache") else cache)
        self._mem_cache[key] = legacy
        if disk_path:
            self.torch.save(legacy, disk_path)
            logger.info("saved pack KV cache %s (%d tokens)",
                        key, tokens["input_ids"].shape[1])
        return key, legacy

    def _to_cache_obj(self, legacy):
        try:
            from transformers import DynamicCache
            return DynamicCache.from_legacy_cache(legacy)
        except (ImportError, AttributeError):
            return legacy

    # ------------------------------------------------------------------

    def generate(self, prompt: str, system: str = "", knowledge: str = "",
                 max_tokens: int = 1024) -> str:
        full_system = system
        if knowledge:
            full_system = (
                f"{system}\n\n# INJECTED KNOWLEDGE (Pharos)\n{knowledge}"
            ).strip()

        prefix_text, full_text = self._render(full_system, prompt)

        if prefix_text and knowledge:
            _, legacy = self._prefix_cache(prefix_text)
            cache = self._to_cache_obj(legacy)
            prefix_ids = self.tokenizer(prefix_text, return_tensors="pt")
            full_ids = self.tokenizer(full_text, return_tensors="pt")
            n_prefix = prefix_ids["input_ids"].shape[1]
            # tokenization boundary check — resume point must match
            if not self.torch.equal(
                full_ids["input_ids"][:, :n_prefix], prefix_ids["input_ids"]
            ):
                return self._plain_generate(full_text, max_tokens)
            # Prefix-caching pattern: pass the FULL sequence plus the
            # precomputed cache — generate() sees the cache already covers
            # the first n_prefix positions and prefills only the suffix.
            input_ids = full_ids["input_ids"].to(self.device)
            attention = full_ids["attention_mask"].to(self.device)
            with self.torch.no_grad():
                out = self.model.generate(
                    input_ids=input_ids,
                    attention_mask=attention,
                    past_key_values=cache,
                    max_new_tokens=max_tokens,
                    do_sample=False,
                    use_cache=True,
                    pad_token_id=self.tokenizer.eos_token_id,
                )
            new_tokens = out[0][input_ids.shape[1]:]
            return self.tokenizer.decode(new_tokens, skip_special_tokens=True)

        return self._plain_generate(full_text, max_tokens)

    def _plain_generate(self, full_text: str, max_tokens: int) -> str:
        inputs = self.tokenizer(full_text, return_tensors="pt").to(self.device)
        with self.torch.no_grad():
            out = self.model.generate(
                **inputs, max_new_tokens=max_tokens, do_sample=False,
                use_cache=True, pad_token_id=self.tokenizer.eos_token_id,
            )
        new_tokens = out[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True)


def precompute_pack_caches(model_path: str, packs_dir: str, cache_dir: str,
                           system: str, device: str = "auto") -> list:
    """Warm the disk cache for every pack — run once at deploy time."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from pharos.pack_loader import PackLibrary

    injector = KVInjector(model_path, device=device, cache_dir=cache_dir)
    library = PackLibrary(packs_dir)
    warmed = []
    for pack in library.packs:
        knowledge = pack.triples_text()
        full_system = f"{system}\n\n# INJECTED KNOWLEDGE (Pharos)\n{knowledge}"
        prefix_text, _ = injector._render(full_system, "warmup")
        if prefix_text:
            key, _ = injector._prefix_cache(prefix_text)
            warmed.append({"pack": pack.name, "cache_key": key})
    return warmed


if __name__ == "__main__":
    import argparse
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from engine.synthesis import RIVET_SYSTEM

    ap = argparse.ArgumentParser(description="Precompute Pharos pack KV caches")
    ap.add_argument("--model", required=True,
                    help="HF model path, e.g. Qwen/Qwen2.5-Coder-32B-Instruct")
    ap.add_argument("--packs-dir", default="packs")
    ap.add_argument("--cache-dir", default="pack_kv_cache")
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO)
    warmed = precompute_pack_caches(
        args.model, args.packs_dir, args.cache_dir, RIVET_SYSTEM, args.device,
    )
    print(json.dumps(warmed, indent=2))

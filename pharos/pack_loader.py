"""Pharos pack loading + confidence-gated routing.

Ported from the Pharos router on MTH
(~/lab/projects/pharos/router.py): embedding similarity when
sentence-transformers is installed, with a deterministic keyword-overlap
fallback so routing still works on a bare deployment.

Encoding policy follows the Pharos encoding-comparison results:
triples-only is the safe default; richer encodings only above confidence
thresholds; walk-only never (it hurt reasoning in the benchmark).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PACKS_DIR = Path(__file__).parent.parent.parent / "packs"


@dataclass
class Pack:
    name: str
    description: str
    triples: list
    path: Path

    def triples_text(self, max_triples: int = 200) -> str:
        lines = [f"Knowledge domain: {self.description}\n", "Key relationships:"]
        for t in self.triples[:max_triples]:
            s = t.get("subject", t.get("s", ""))
            p = t.get("predicate", t.get("p", ""))
            o = t.get("object", t.get("o", ""))
            lines.append(f"- {s} [{p}] {o}")
        return "\n".join(lines)

    def descriptor(self) -> str:
        from collections import Counter
        counter: Counter = Counter()
        for t in self.triples[:120]:
            for k in ("subject", "s", "predicate", "p", "object", "o"):
                counter.update(re.findall(
                    r"[a-z]{3,}",
                    str(t.get(k, "")).lower().replace("_", " "),
                ))
        common = [w for w, _ in counter.most_common(100)]
        return (f"{self.name.replace('_', ' ')}. {self.description}. "
                + " ".join(common))


@dataclass
class RoutedKnowledge:
    knowledge_text: str = ""
    pack_names: list = field(default_factory=list)
    confidence: float = 0.0
    encoding_policy: str = "none"
    explanation: str = ""


class PackLibrary:
    def __init__(self, packs_dir: Path | str = DEFAULT_PACKS_DIR):
        self.packs_dir = Path(packs_dir)
        self.packs: list[Pack] = []
        self._load()

    def _load(self) -> None:
        if not self.packs_dir.exists():
            return
        # Flat .json packs (Multiverse demo format) ...
        for f in sorted(self.packs_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and "triples" in data:
                self.packs.append(Pack(
                    name=data.get("pack_name", f.stem),
                    description=data.get("description", f.stem),
                    triples=data["triples"], path=f,
                ))
        # ... and directory packs (triples.json inside, Pharos pack format)
        for d in sorted(p for p in self.packs_dir.iterdir() if p.is_dir()):
            tf = d / "triples.json"
            if not tf.exists():
                continue
            try:
                data = json.loads(tf.read_text())
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                triples, desc = data, d.name
            else:
                triples = data.get("triples", [])
                desc = data.get("description", d.name)
            self.packs.append(Pack(name=d.name, description=desc,
                                   triples=triples, path=tf))

    def get(self, name: str) -> Pack | None:
        for p in self.packs:
            if p.name == name:
                return p
        return None


class PharosRouter:
    """Confidence-gated pack selection (see module docstring)."""

    def __init__(self, library: PackLibrary, match_threshold: float = 0.30,
                 source_threshold: float = 0.55, max_packs: int = 2,
                 embedding_model: str = "all-MiniLM-L6-v2"):
        self.library = library
        self.match_threshold = match_threshold
        self.source_threshold = source_threshold
        self.max_packs = max_packs
        self._st_model = None
        self._embeddings = None
        self._try_embeddings(embedding_model)

    def _try_embeddings(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return
        if not self.library.packs:
            return
        self._st_model = SentenceTransformer(model_name)
        descriptors = [p.descriptor() for p in self.library.packs]
        self._embeddings = self._st_model.encode(
            descriptors, convert_to_numpy=True,
        )

    def route(self, query: str) -> RoutedKnowledge:
        if not self.library.packs:
            return RoutedKnowledge(explanation="no packs loaded")
        scored = (self._route_embedding(query) if self._st_model is not None
                  else self._route_keyword(query))

        matches = [(p, s) for p, s in scored if s >= self.match_threshold]
        matches = matches[: self.max_packs]
        if not matches:
            return RoutedKnowledge(
                explanation=f"no pack above threshold {self.match_threshold}",
            )

        top_conf = matches[0][1]
        policy = "triples_source" if top_conf >= self.source_threshold else "triples"
        knowledge = "\n\n".join(p.triples_text() for p, _ in matches)
        return RoutedKnowledge(
            knowledge_text=knowledge,
            pack_names=[p.name for p, _ in matches],
            confidence=round(top_conf, 3),
            encoding_policy=policy,
            explanation=(f"top={matches[0][0].name} ({top_conf:.3f}), "
                         f"method={'embedding' if self._st_model else 'keyword'}"),
        )

    def _route_embedding(self, query: str) -> list:
        import numpy as np
        q = self._st_model.encode(query, convert_to_numpy=True)
        sims = self._embeddings @ q / (
            np.linalg.norm(self._embeddings, axis=1) * (np.linalg.norm(q) or 1)
        )
        ranked = sorted(zip(self.library.packs, sims.tolist()),
                        key=lambda x: -x[1])
        return ranked

    def _route_keyword(self, query: str) -> list:
        """Fraction of (content-bearing) query words found in the pack
        descriptor, with 4-char prefix matching so migration/migrations
        and share/shared/shares count as hits."""
        stop = {"the", "and", "for", "with", "how", "what", "why", "does",
                "should", "would", "can", "you", "this", "that", "when"}
        q_words = [w for w in re.findall(r"[a-z]{3,}", query.lower())
                   if w not in stop]
        scored = []
        for pack in self.library.packs:
            d_words = set(re.findall(r"[a-z]{3,}", pack.descriptor().lower()))
            if not q_words or not d_words:
                scored.append((pack, 0.0))
                continue

            def matched(qw: str) -> bool:
                if qw in d_words:
                    return True
                if len(qw) >= 4:
                    prefix = qw[:4]
                    return any(dw.startswith(prefix) or qw.startswith(dw[:4])
                               for dw in d_words if len(dw) >= 4)
                return False

            hits = sum(1 for qw in q_words if matched(qw))
            scored.append((pack, hits / len(q_words)))
        return sorted(scored, key=lambda x: -x[1])


def load_router(packs_dir: Path | str = DEFAULT_PACKS_DIR,
                **kwargs) -> PharosRouter:
    return PharosRouter(PackLibrary(packs_dir), **kwargs)


if __name__ == "__main__":
    import sys
    router = load_router(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PACKS_DIR)
    print(f"packs: {[p.name for p in router.library.packs]}")
    for q in ("How do I add a migration for a new table?",
              "Why does the webhook skip signature verification?"):
        r = router.route(q)
        print(f"\nQ: {q}\n  -> {r.pack_names} conf={r.confidence} "
              f"policy={r.encoding_policy} ({r.explanation})")

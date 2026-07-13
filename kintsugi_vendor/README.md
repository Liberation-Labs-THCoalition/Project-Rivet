# Vendored Kintsugi Core

Verbatim copies of the stdlib-only cognition modules from
[Liberation-Labs-THCoalition/Project-Kintsugi](https://github.com/Liberation-Labs-THCoalition/Project-Kintsugi)
at commit `48f8bd406aafbafcc10b64bf54942ac9808acdde` (2026-07-11).

| File | Origin | Modified? |
|---|---|---|
| `kintsugi/bdi/models.py` | `kintsugi/bdi/models.py` | verbatim |
| `kintsugi/bdi/store.py` | `kintsugi/bdi/store.py` | verbatim |
| `kintsugi/bdi/coherence.py` | `kintsugi/bdi/coherence.py` | verbatim |
| `kintsugi/bdi/drift_classifier.py` | `kintsugi/bdi/drift_classifier.py` | verbatim |
| `kintsugi/bdi/__init__.py` | `kintsugi/bdi/__init__.py` | verbatim |
| `kintsugi/skills/base.py` | `kintsugi/skills/base.py` | verbatim |
| `kintsugi/skills/dag.py` | `kintsugi/skills/dag.py` | verbatim |
| `kintsugi/skills/registry.py` | `kintsugi/skills/registry.py` | verbatim |
| `kintsugi/skills/__init__.py` | reduced | re-exports only the vendored modules (original also imports `router`, which Rivet does not use) |
| `kintsugi/__init__.py` | reduced | version string only |

## Why vendored

Rivet deploys to machines (Starship) that do not have the Kintsugi repo
installed. These modules are pure stdlib, so a verbatim copy is safe and
keeps Rivet a *real* Kintsugi deployment — same dataclasses, same DAG
executor, same registry — not a reimplementation.

`v2/kintsugi_core.py` prefers a real Kintsugi checkout when one is
available (`KINTSUGI_PATH` env var or installed package) and falls back
to this vendor copy. Upgrading: re-copy the files and bump the commit
hash above.

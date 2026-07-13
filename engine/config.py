"""Config loader for kintsugi_config.yaml.

Uses PyYAML when available. Falls back to a strict-subset parser so the
deployment target needs nothing beyond the stdlib. The subset covers
exactly what kintsugi_config.yaml uses: nested mappings by indentation,
`- item` lists, scalars (str/int/float/bool/null), quoted strings, and
`#` comments. No anchors, no multi-line strings, no flow collections.
"""

from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "kintsugi_config.yaml"


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    text = Path(path).read_text()
    try:
        import yaml
        return yaml.safe_load(text)
    except ImportError:
        return _parse_subset(text)


def _scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1]
    low = raw.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _strip_comment(line: str) -> str:
    """Remove a trailing comment, respecting quoted strings."""
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            return line[:i]
    return line


def _parse_subset(text: str) -> dict:
    # Tokenize into (indent, content) lines, skipping blanks/comments.
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))

    root, _ = _parse_block(lines, 0, 0)
    return root


def _parse_block(lines: list, pos: int, indent: int) -> tuple[Any, int]:
    """Parse a block starting at lines[pos] with the given indent level."""
    if pos >= len(lines):
        return {}, pos

    if lines[pos][1].startswith("- "):
        result_list: list = []
        while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
            item = lines[pos][1][2:].strip()
            if ":" in item and not item.startswith(("'", '"')):
                # list of single-key mappings: "- key: value"
                key, _, val = item.partition(":")
                entry = {key.strip(): _scalar(val)}
                pos += 1
                # absorb continuation keys indented under the dash
                while pos < len(lines) and lines[pos][0] > indent and not lines[pos][1].startswith("- "):
                    k, _, v = lines[pos][1].partition(":")
                    entry[k.strip()] = _scalar(v)
                    pos += 1
                result_list.append(entry)
            else:
                result_list.append(_scalar(item))
                pos += 1
        return result_list, pos

    result: dict = {}
    while pos < len(lines):
        line_indent, content = lines[pos]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ValueError(f"Unexpected indent in config near: {content!r}")
        key, sep, val = content.partition(":")
        if not sep:
            raise ValueError(f"Expected 'key:' in config near: {content!r}")
        key = key.strip()
        val = val.strip()
        if val:
            result[key] = _scalar(val)
            pos += 1
        else:
            pos += 1
            if pos < len(lines) and lines[pos][0] > indent:
                child, pos = _parse_block(lines, pos, lines[pos][0])
                result[key] = child
            else:
                result[key] = None
    return result, pos


if __name__ == "__main__":
    import json
    cfg = _parse_subset(Path(DEFAULT_CONFIG_PATH).read_text())
    print(json.dumps(cfg, indent=2))

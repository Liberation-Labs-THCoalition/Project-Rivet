"""The builder loop: one phase is one role (an Agent Army SKILL.md) working one task with the workspace's tools until
it answers without a tool call, or runs out of turns. The model proposes; the workspace decides what happens.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field

from builder.workspace import MAX_TOOL_OUTPUT, TOOL_SCHEMAS, Workspace

DONE_WORD = "PHASE COMPLETE"


class OllamaChat:
    """One model behind Ollama's /api/chat with tool calling. One request at a time; the caller sets the port."""

    def __init__(self, url: str, model: str, num_ctx: int = 32768, timeout: int = 900, think=None):
        self.url, self.model, self.num_ctx, self.timeout, self.think = url.rstrip("/"), model, num_ctx, timeout, think

    def __call__(self, messages: list, tools: list) -> dict:
        body = {"model": self.model, "messages": messages, "tools": tools, "stream": False,
                "options": {"temperature": 0.2, "num_ctx": self.num_ctx}}
        if self.think is not None:
            body["think"] = self.think
        req = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())["message"]


@dataclass
class PhaseResult:
    role: str
    turns: int
    finished: bool                       # the model said it was done (DONE_WORD), not just ran out of turns
    final_text: str = ""
    tool_calls: list = field(default_factory=list)
    error: str = ""


def _dispatch(ws: Workspace, name: str, args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return "ERROR: tool arguments must be a JSON object"
    if not isinstance(args, dict):
        return "ERROR: tool arguments must be a JSON object"
    fn = {"list_files": ws.list_files, "read_file": ws.read_file, "write_file": ws.write_file,
          "replace_in_file": ws.replace_in_file, "run_tests": ws.run_tests, "git_diff": ws.git_diff}.get(name)
    if fn is None:
        return f"ERROR: no tool named {name!r}"
    try:
        return str(fn(**args))[:MAX_TOOL_OUTPUT]
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"


def run_phase(ws: Workspace, model, role_name: str, role_text: str, task: str, max_turns: int = 40,
              log=None) -> PhaseResult:
    system = (f"You are the {role_name} on a small build team. Your role's guide follows; then the rules of this "
              f"workspace.\n\n<role_guide>\n{role_text}\n</role_guide>\n\n"
              "Workspace rules: you work only through the tools. You may write only these files: "
              f"{ws.writable}. Every path is relative to the checkout. You can't run commands other than the tests. "
              f"When your task is finished and the tests say what the task needs, reply with the words {DONE_WORD} "
              "and a short summary, and call no tool in that reply.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    result = PhaseResult(role=role_name, turns=0, finished=False)
    for turn in range(1, max_turns + 1):
        result.turns = turn
        try:
            msg = model(messages, TOOL_SCHEMAS)
        except Exception as e:                       # a dead model ends the phase, unfinished, and says why
            result.error = f"{type(e).__name__}: {e}"
            return result
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls = msg.get("tool_calls") or []
        if not calls:
            result.final_text = msg.get("content") or ""
            result.finished = DONE_WORD in result.final_text
            if result.finished:
                return result
            messages.append({"role": "user", "content": f"Continue with the tools, or reply {DONE_WORD} if the "
                                                        "task is finished."})
            continue
        for c in calls:
            fn = c.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments", {})
            out = _dispatch(ws, name, args)
            result.tool_calls.append({"turn": turn, "tool": name, "args": _short(args), "result": out[:300]})
            if log:
                log(f"  [{role_name} t{turn}] {name} {_short(args)[:120]} -> {out[:100]!r}")
            messages.append({"role": "tool", "content": out, "tool_name": name})
    return result


def _short(args) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False)
    except TypeError:
        s = str(args)
    return s if len(s) < 400 else s[:400] + "..."


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")

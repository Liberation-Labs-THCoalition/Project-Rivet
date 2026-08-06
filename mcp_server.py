"""Rivet MCP Server — discipline-gated code review inside any editor.

Exposes Rivet's capabilities as MCP tools so Claude Code, Cursor,
Continue.dev, or any MCP client can use the discipline gate without
running a standalone server.

Every question goes through the full pipeline:
  Intent classification → SkillDAG → Evidence gathering → Discipline gate

The gate sits last. Nothing reaches the user without passing it.

Tools:
  rivet_ask           — Full pipeline: ask a code question, get a gated answer
  rivet_review        — Review a code change for migration safety + security
  rivet_health        — Agent status, model backend, loaded packs
  rivet_bdi           — BDI snapshot (beliefs, desires, intentions)

Usage:
  python mcp_server.py --config kintsugi_config.yaml

  # In Claude Code .claude/mcp.json:
  {
    "mcpServers": {
      "rivet": {
        "command": "python",
        "args": ["mcp_server.py", "--config", "kintsugi_config.yaml"]
      }
    }
  }
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("rivet.mcp")

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, Resource
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

V2_ROOT = Path(__file__).parent
sys.path.insert(0, str(V2_ROOT))


def create_server(config_path: str = "") -> "Server":
    if not HAS_MCP:
        raise ImportError("MCP SDK not installed. Install with: pip install mcp")

    server = Server("rivet")
    _agent = None

    def _get_agent():
        nonlocal _agent
        if _agent is None:
            from rivet import RivetAgent
            _agent = RivetAgent(config_path=config_path or None)
            logger.info("RivetAgent loaded: %s", _agent.health().get("agent", "Rivet"))
        return _agent

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="rivet_ask",
                description=(
                    "Ask a code question through Rivet's discipline-gated "
                    "pipeline. The question is classified by intent (migration, "
                    "auth, codegen, review, question), routed through relevant "
                    "skill chips (code analysis, migration safety, security "
                    "review), and validated by the discipline gate before "
                    "the answer reaches you. Includes confidence grading "
                    "(HIGH/MEDIUM/LOW) and flags for anything that needs "
                    "human review."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The code question or request",
                        },
                        "user": {
                            "type": "string",
                            "description": "User identifier",
                            "default": "mcp-user",
                        },
                    },
                    "required": ["question"],
                },
            ),
            Tool(
                name="rivet_review",
                description=(
                    "Review a code change, migration, or SQL statement for "
                    "safety. Checks for destructive operations, auth flow "
                    "changes, and patterns from past audit findings. Returns "
                    "gate verdict (pass/block) with specific flags."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "The code, SQL, or migration to review",
                        },
                        "context": {
                            "type": "string",
                            "description": "What this change is for",
                            "default": "",
                        },
                    },
                    "required": ["code"],
                },
            ),
            Tool(
                name="rivet_health",
                description=(
                    "Agent health: model backend, loaded knowledge packs, "
                    "skill chips, belief count, repo attachment status."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="rivet_bdi",
                description=(
                    "BDI snapshot: active beliefs (constraints + learned), "
                    "desires (mission goals), and current intentions."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "rivet_ask":
                return _handle_ask(arguments)
            elif name == "rivet_review":
                return _handle_review(arguments)
            elif name == "rivet_health":
                return _handle_health()
            elif name == "rivet_bdi":
                return _handle_bdi()
            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    def _handle_ask(args: dict) -> list[TextContent]:
        agent = _get_agent()
        question = args.get("question", "")
        user = args.get("user", "mcp-user")
        result = agent.ask(question, user)

        output = {
            "response": result.get("response", result.get("error", "")),
            "gate_passed": result.get("gate_passed"),
            "confidence": result.get("confidence"),
            "intent": result.get("intent"),
            "flags": result.get("flags", []),
            "requires_review": result.get("requires_review", []),
            "packs_used": result.get("packs_used", []),
            "elapsed": result.get("elapsed_seconds"),
        }

        if result.get("error"):
            output["error"] = result["error"]

        return [TextContent(type="text", text=json.dumps(output, indent=2, default=str))]

    def _handle_review(args: dict) -> list[TextContent]:
        agent = _get_agent()
        code = args.get("code", "")
        context = args.get("context", "")
        question = f"Review this code for safety:\n\n{code}"
        if context:
            question = f"{context}\n\n{question}"

        result = agent.ask(question, "mcp-reviewer")

        output = {
            "response": result.get("response", result.get("error", "")),
            "gate_passed": result.get("gate_passed"),
            "confidence": result.get("confidence"),
            "flags": result.get("flags", []),
            "requires_review": result.get("requires_review", []),
        }

        return [TextContent(type="text", text=json.dumps(output, indent=2, default=str))]

    def _handle_health() -> list[TextContent]:
        agent = _get_agent()
        return [TextContent(type="text", text=json.dumps(agent.health(), indent=2, default=str))]

    def _handle_bdi() -> list[TextContent]:
        agent = _get_agent()
        return [TextContent(type="text", text=json.dumps(agent.bdi_snapshot(), indent=2, default=str))]

    @server.list_resources()
    async def list_resources():
        return [
            Resource(
                uri="rivet://config",
                name="Rivet Configuration",
                description="Current model backend, paths, and constraints",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str):
        if uri == "rivet://config":
            agent = _get_agent()
            safe_config = {
                "org": agent.config.get("org", {}),
                "model": {
                    "backend": agent.config.get("model", {}).get("backend"),
                    "name": agent.config.get("model", {}).get("name"),
                },
                "pharos": agent.config.get("pharos", {}),
                "tools": agent.config.get("tools", {}),
                "beliefs_count": len(agent.bdi.list_beliefs()),
                "desires_count": len(agent.bdi.list_desires()),
            }
            return json.dumps(safe_config, indent=2)
        return json.dumps({"error": f"Unknown resource: {uri}"})

    return server


async def main(config_path: str):
    server = create_server(config_path)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="Rivet MCP Server")
    parser.add_argument("--config", default="kintsugi_config.yaml",
                        help="Path to kintsugi_config.yaml")
    args = parser.parse_args()

    asyncio.run(main(args.config))

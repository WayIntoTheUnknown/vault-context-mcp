from __future__ import annotations

import asyncio

from vault_ctx.server import mcp


def test_server_registers_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"ctx_sync", "ctx_digest", "ctx_status", "ctx_append_decision"} <= names


def test_server_registers_resources() -> None:
    resources = asyncio.run(mcp.list_resources())
    uris = {str(resource.uri) for resource in resources}
    assert {"ctx://context", "ctx://decisions", "ctx://status"} <= uris


def test_server_registers_prompt() -> None:
    prompts = asyncio.run(mcp.list_prompts())
    assert "handoff" in {prompt.name for prompt in prompts}

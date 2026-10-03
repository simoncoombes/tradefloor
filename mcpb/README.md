# The tradefloor MCP bundle

An [MCP bundle](https://github.com/modelcontextprotocol/mcpb) (`.mcpb`) of
the local MCP server, for Claude Desktop and Smithery. It vendors nothing:
`pyproject.toml` depends on `tradefloor[mcp]` at this release, and the host
runs `server.py` with uv, which installs it.

Pack and check it:

```
npx -y @anthropic-ai/mcpb validate mcpb/manifest.json
npx -y @anthropic-ai/mcpb pack mcpb tradefloor.mcpb
```

Smithery (`tradefloor/tradefloor`) also wants each tool's `inputSchema` in
the manifest, which the MCPB spec does not allow. For Smithery, copy the
manifest, add each tool's `inputSchema` from the server's `tools/list`, zip
the four files, and publish:

```
npx -y @smithery/cli mcp publish ./tradefloor-smithery.mcpb -n tradefloor/tradefloor
```

with `SMITHERY_API_KEY` set.

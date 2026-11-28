# MCP server

How an MCP client starts tradefloor's server and what a session holds. The
server's tools are counted in the contents table of
[docs/API.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/API.md),
and a Python client that drives it is in
[`examples/mcp-client/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/mcp-client).

## Install and start

```
pip install "tradefloor[mcp]"
claude mcp add tradefloor -- tradefloor-mcp
```

`tradefloor-mcp` speaks MCP over stdio, and `tradefloor mcp` starts the same
server. Strategies, universes and scenarios are data, so a tool argument
cannot reach code. Each result carries its own caveats. A session keeps one
market in the server between calls, so an agent can place orders a step at a
time, fork the market to try two actions, and rewind. See
[the MCP page](https://docs.tradefloor.dev/mcp-local.html).

## Other MCP clients

Each client below starts the server from a JSON entry under `mcpServers`. Use the
full path that `which tradefloor-mcp` prints, because an application started from the
desktop does not always see your shell's `PATH`.

| client | file | from |
|---|---|---|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows), `~/.config/Claude/claude_desktop_config.json` (Linux) | [Connect to local MCP servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers) |
| Cursor | `.cursor/mcp.json` in a project, or `~/.cursor/mcp.json` for every project | [Cursor: Model Context Protocol](https://cursor.com/docs/context/mcp) |
| VS Code (Copilot) | `.mcp.json` at the workspace root, with `"type": "stdio"` in the entry | [VS Code: MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration) |

```json
{
  "mcpServers": {
    "tradefloor": {
      "command": "/full/path/to/tradefloor-mcp",
      "args": []
    }
  }
}
```

VS Code's older `.vscode/mcp.json` uses a top-level `servers` key instead of
`mcpServers`. Restart the client after editing the file.

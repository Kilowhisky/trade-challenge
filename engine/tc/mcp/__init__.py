"""The engine's MCP surface: who may call what, and over which mount.

`registry.py` is the table (data: names and roles, readable without standing a
server up). `server.py` builds the two FastMCP instances from it, guards them
with one bearer middleware, and exposes the `register()` hook the tool modules
fill.
"""

"""Print the schwab-mcp tool registry for a given allow_write setting.

Used by scripts/test-broker-readonly.sh to assert, against the real built image,
exactly which tools the read-only broker exposes. Needs no credentials and makes
no network call: register_tools only touches a client object, never uses it.
"""
import asyncio
import sys
from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer
from schwab_mcp.tools import register_tools


def names(allow_write: bool) -> set[str]:
    srv = MCPServer(name="probe")
    register_tools(srv, MagicMock(), allow_write=allow_write,
                   enable_technical=True, result_transform=lambda x: x)
    return {t.name for t in asyncio.run(srv.list_tools())}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "diff"
    ro, rw = names(False), names(True)
    if mode == "readonly":
        print("\n".join(sorted(ro)))
    elif mode == "write":
        print("\n".join(sorted(rw)))
    else:
        print(f"readonly_count={len(ro)}")
        print(f"write_count={len(rw)}")
        for t in sorted(rw - ro):
            print(f"write_only={t}")

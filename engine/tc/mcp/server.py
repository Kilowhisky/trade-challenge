"""The engine's MCP surface: two roles, two mounts, one middleware.

Shape verified by execution (0c-sdk-facts.md §3.2-§3.3):

* Two FastMCP instances with DISJOINT tool registries, not one server filtering
  per token. FastMCP's registry is process-global, so a per-request filter would
  be a gate inside the thing being gated; disjoint registries make a decide tool
  unreachable with a research token rather than merely unchosen.
* `streamable_http_path="/"` plus `Mount("/mcp/<role>", ...)` puts the endpoint at
  `/mcp/<role>/`. Clients get the trailing slash; without it every message pays a
  307.
* Both `session_manager.run()` contexts MUST be entered in OUR lifespan.
  `streamable_http_app()` builds the manager without starting it, and mounting
  into our app replaces FastMCP's own lifespan. Forgetting this is a 500 on the
  first request, every time.

Nothing here can place an order. `McpDeps.broker` is the read-only Phase 0a
protocol, no order module is imported, and `build_servers` refuses outright to
finish building a server that carries a name the registry did not declare --
so a tool added in a later task cannot become reachable without also appearing
in the table that `rules/consistency.py` and the contract test both read.
"""

from __future__ import annotations

import contextlib
import hmac
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

# The router chooses a mount with `get_route_path`, which strips `root_path`.
# The gate MUST match on exactly that string or it can be walked around by
# mounting this app under a prefix, so it uses the router's own function
# rather than a re-derivation that could drift from it. It is private; if a
# starlette upgrade moves it this fails loudly at import, which is the right
# failure for an auth check -- a silently divergent copy is not.
from starlette._utils import get_route_path
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount
from starlette.types import ASGIApp

from tc.broker.client import Broker
from tc.config import Settings
from tc.mcp.registry import FORBIDDEN, ROLE_TOOLS, Role
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import Store

SERVER_NAME = "engine"


@dataclass
class McpDeps:
    """Everything a tool may reach.

    Assembled once by the engine and handed to every registrar, so a tool
    module never reaches for a global: what it can touch is exactly what is
    on this object, and that list is reviewable in one place.
    """

    store: Store
    broker: Broker
    docs: DocStore
    rules: Rules
    settings: Settings
    clock: Callable[[], datetime]
    account_hash: Callable[[], str | None]


class Pong(BaseModel):
    """A BaseModel return type, not a dict: FastMCP derives an output schema
    from the annotation, and only then does the client get
    `structuredContent` rather than a bare JSON text block
    (0c-sdk-facts.md §3.4)."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    role: Role
    server: str
    at: datetime


# `(server, deps, role)`, which is the signature the tool modules already
# expose: several of them register a slightly different set per role (the read
# module gives `book` to decide and not to research), so the role has to reach
# the registrar rather than only the dispatcher.
ToolRegistrar = Callable[[FastMCP, McpDeps, Role], None]

# Filled by the wiring (`register("research", tools_read.register)`).
# A list per role rather than one function per role so the read tools and the
# research tools can be separate modules that know nothing about each other.
_REGISTRARS: dict[Role, list[ToolRegistrar]] = {role: [] for role in ROLE_TOOLS}


def register(role: Role, fn: ToolRegistrar) -> None:
    """Hook a tool module into a role.

    Every name the registrar adds must already appear in
    `registry.ROLE_TOOLS[role]`; `build_servers` enforces that rather than
    trusting it, because the registry table is what the allowlists and the
    §10 contract check are computed from, and a tool outside it would be
    reachable without ever having been declared.
    """
    _REGISTRARS[role].append(fn)


def registrars(role: Role) -> tuple[ToolRegistrar, ...]:
    return tuple(_REGISTRARS[role])


def _names(server: FastMCP) -> set[str]:
    """The tools registered so far. `FastMCP.list_tools` is a coroutine and
    `build_servers` is not; the manager's own listing is the same data."""
    return {t.name for t in server._tool_manager.list_tools()}


def _register_ping(server: FastMCP, role: Role, deps: McpDeps) -> None:
    def ping() -> Pong:
        return Pong(ok=True, role=role, server=SERVER_NAME, at=deps.clock())

    server.add_tool(
        ping,
        name="ping",
        description=(
            "Liveness check. Returns the engine's role and its clock. "
            "Reaches nothing and changes nothing."
        ),
    )


def _register_pending(server: FastMCP, missing: list[str]) -> None:
    """Declare the names no registrar has supplied yet. Opt-in only.

    Plan 0c builds the roles before their tool modules exist (tasks 8 and 9),
    and a declared-but-absent name would otherwise make the list-tools and
    contract tests pass over an empty server. But stubbing by default makes
    `live == declared` unfalsifiable -- with no registrars at all, every
    declared name becomes a stub and every check goes green over a server that
    does nothing. So stubbing is reachable only through
    `build_servers(..., allow_stubs=True)`, which the tests of the mounting
    itself pass and production never does.

    `ToolError`, not a bare exception: FastMCP returns it to the caller as an
    ordinary `isError` result with the message intact and logs it at INFO,
    where any other exception is treated as a crash and the caller sees only
    "Error executing tool <name>" (0c-sdk-facts.md §3.4).
    """
    for name in missing:
        server.add_tool(
            _stub(name), name=name, description=f"NOT IMPLEMENTED YET: {name}"
        )


def _stub(name: str) -> Callable[[], str]:
    """A no-argument stub closing over its own name.

    A default argument (`def stub(name=name)`) would be simpler and is wrong
    twice over: FastMCP derives the tool's input schema from the signature, so
    the stub would advertise an argument the real tool does not take, and it
    rejects underscore-prefixed parameters outright.
    """

    def stub() -> str:
        raise ToolError(f"{name} is declared but not implemented yet")

    return stub


def _refuse_order_shaped(role: Role, names: Iterable[str], where: str) -> None:
    """Spec §10, enforced at boot rather than only in CI.

    The contract test and `rules/consistency.check_tool_registry` both read the
    declared table; neither runs in the deployed process. This does, on every
    build, over both the table and what the registrars actually put on the
    server -- so an order-shaped tool cannot become reachable on a machine
    where nobody ran the gate.
    """
    offenders = sorted(n for n in names if FORBIDDEN.search(n))
    if offenders:
        raise ValueError(
            f"role {role!r} {where} order-shaped tools {offenders}: spec §10 forbids "
            f"any tool name matching {FORBIDDEN.pattern!r}"
        )


def build_servers(deps: McpDeps, *, allow_stubs: bool = False) -> dict[Role, FastMCP]:
    """One FastMCP per role, carrying exactly the names the registry declares.

    `allow_stubs` is the difference between a server and a promise of one. The
    default refuses to build a role whose declared tools nobody registered,
    because a stub-filled server satisfies every "live matches declared" check
    while answering nothing -- so an unimplemented tool must fail boot, not
    ship. Only the tests of the mounting itself (which need a live server
    before the tool modules exist) pass `allow_stubs=True`, and they pass it
    explicitly.
    """
    servers: dict[Role, FastMCP] = {}
    for role, declared in ROLE_TOOLS.items():
        _refuse_order_shaped(role, declared, "declares")
        server = FastMCP(name=SERVER_NAME, streamable_http_path="/", stateless_http=False)
        _register_ping(server, role, deps)
        for fn in _REGISTRARS[role]:
            fn(server, deps, role)
        registered = _names(server)
        _refuse_order_shaped(role, registered, "registered")
        undeclared = sorted(registered - set(declared))
        if undeclared:
            # Fail the build, not the review. A tool reachable by a role that
            # never declared it is invisible to both the §10 consistency check
            # and to the job allowlists computed from the same table.
            raise ValueError(
                f"role {role!r} registered undeclared tools {undeclared}: "
                "add them to tc/mcp/registry.py or do not register them"
            )
        missing = sorted(set(declared) - registered)
        if missing and not allow_stubs:
            raise RuntimeError(
                f"role {role!r} declares tools no registrar supplies: {missing}. "
                "Register them, remove them from tc/mcp/registry.py, or pass "
                "allow_stubs=True if you are testing the mounting itself."
            )
        _register_pending(server, missing)
        servers[role] = server
    return servers


class RoleAuthMiddleware(BaseHTTPMiddleware):
    """Bearer -> role, then role vs mount prefix.

    Everything outside `/mcp/` is untouched: `/health` is read by an
    unattended probe that carries no credential, and a middleware that 401'd
    it would blind the only external check on the engine.

    A missing or non-bearer Authorization is 401 (you did not present a
    credential); an unknown token and a role/prefix mismatch are both 403 (you
    presented one and it does not open this door). There is no default role:
    a default role is a way to reach tools without a credential.
    """

    def __init__(self, app: ASGIApp, tokens: dict[str, str]) -> None:
        super().__init__(app)
        self._tokens = tokens

    def _role_for(self, presented: str) -> str | None:
        """Constant-time over the (at most two) configured bearers.

        `compare_digest` raises on non-ASCII, and an attacker chooses the
        header, so a non-ASCII bearer is answered rather than allowed to become
        a 500 -- it cannot match a configured token in any case.
        """
        if not presented.isascii():
            return None
        for token, role in self._tokens.items():
            if hmac.compare_digest(token, presented):
                return role
        return None

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # NOT `request.url.path`: that keeps `root_path`, while the router
        # matches the stripped path. Mount this app under any prefix and the
        # two diverge -- `/engine/mcp/decide/` does not start with `/mcp/`, so
        # a raw-path gate would wave it through unauthenticated to a mount the
        # router still resolves.
        path = get_route_path(request.scope)
        if not path.startswith("/mcp/"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer"}, status_code=401)
        role = self._role_for(auth.split(" ", 1)[1].strip())
        if role is None:
            return JSONResponse({"error": "bad token"}, status_code=403)
        parts = path.strip("/").split("/")
        wanted = parts[1] if len(parts) > 1 else ""
        if wanted != role:
            return JSONResponse(
                {"error": f"token role {role} may not use /{wanted}"}, status_code=403
            )
        request.scope["mcp_role"] = role
        return await call_next(request)


def mcp_routes(servers: dict[Role, FastMCP]) -> list[Mount]:
    return [
        Mount(f"/mcp/{role}", app=s.streamable_http_app()) for role, s in servers.items()
    ]


@contextlib.asynccontextmanager
async def mcp_lifespan(servers: dict[Role, FastMCP]) -> AsyncIterator[None]:
    """Enter every session manager on one stack.

    `streamable_http_app()` builds the `StreamableHTTPSessionManager` but does
    not start it, and mounting into our Starlette app replaces FastMCP's own
    lifespan with ours. This is the supported pattern for several FastMCP
    instances in one application, and skipping it is a 500 on the first
    request to whichever server was missed.
    """
    async with contextlib.AsyncExitStack() as stack:
        for server in servers.values():
            await stack.enter_async_context(server.session_manager.run())
        yield

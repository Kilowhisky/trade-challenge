"""Who may call what, stated once.

This table is the single source for three consumers: `server.py` registers
exactly these names and refuses to build a server that carries any other,
`jobs/spec.py` builds each job's allowlist out of them, and
`rules/consistency.py` fails the build if any of them is order-shaped. Keeping
it as data rather than as a property of the FastMCP objects means the
consistency checker can read it without standing up a server, and means the
allowlists and the servers cannot drift apart silently.

The names Claude actually sees are `mcp__<client config key>__<tool>` — the
prefix comes from the runner's `mcp_servers` key, not from the server's own
name (0c-sdk-facts.md §1.4, proven with `get_mcp_status()`). The runner fixes
that key at `engine`, so every allowlist and every prompt says
`mcp__engine__*`; this file only decides reachability.
"""

from __future__ import annotations

import re
from typing import Literal, get_args

Role = Literal["research", "decide"]
ROLES: tuple[Role, ...] = get_args(Role)

# Available to every role. `ping` is the engine's own liveness answer: it
# reaches nothing, it is the tool this module's tests call to prove the mount,
# the bearer gate and the session-manager lifespan all work end to end, and it
# is the one tool a runner can call to tell "the MCP endpoint is up" from
# "the job's first real tool failed".
COMMON_TOOLS: tuple[str, ...] = ("ping",)

READ_TOOLS: tuple[str, ...] = (
    "get_datetime", "market_hours", "quotes", "price_history", "option_chain",
    "expiration_chain", "instruments", "movers", "status_latest", "rules",
)
# `book` is the held-position view. The research roles have never had account
# tools (0c-writers-contract.md §4.3) and do not get them here.
DECIDE_ONLY_READ_TOOLS: tuple[str, ...] = ("book",)

RESEARCH_TOOLS: tuple[str, ...] = (
    # writers. `doc_write` is the whole-document write (v2's
    # research-replace.sh). It is NOT named `doc_replace`: FORBIDDEN below
    # matches `replace` as a substring, and §10 is a rule about the model's
    # vocabulary, not about which tool happens to be dangerous -- a name the
    # contract test has to be taught to forgive is a contract test with an
    # exception list, which is the thing this check exists to not have.
    "evidence_append", "escalation_raise", "escalation_score", "sector_write",
    "ledger_append", "tombstone", "doc_write",
    # readers the prompts cannot run without
    "doc_read", "evidence_read", "escalations_read", "ledger_read", "sectors_read",
    "cohort", "universe_symbols", "universe_names_page", "alert_read",
)

# Empty on purpose: Plan 1 adds propose_entry / propose_exit /
# propose_option_close / get_proposal here and nowhere else.
DECIDE_TOOLS: tuple[str, ...] = ()

ROLE_TOOLS: dict[Role, tuple[str, ...]] = {
    "research": COMMON_TOOLS + READ_TOOLS + RESEARCH_TOOLS,
    "decide": COMMON_TOOLS + READ_TOOLS + DECIDE_ONLY_READ_TOOLS + DECIDE_TOOLS,
}

# Spec §10. Substring, not a whole-name match: `get_orders`, `order_status`
# and `cancel_stop` are all order-shaped, and the point of the rule is that the
# model has no vocabulary for the order path at all. The order path is
# deterministic engine code; the model's only write-shaped tools are the
# Plan 1 `propose_*` family, which the engine then decides on.
FORBIDDEN = re.compile(r"place|cancel|replace|order")


def forbidden_tools() -> list[tuple[str, str]]:
    """`(role, tool)` for every declared name that is order-shaped.

    Returns pairs rather than raising so both callers can say what they need
    to: the consistency checker turns each into a Finding, the contract test
    asserts the list is empty.
    """
    return [
        (role, name)
        for role, names in ROLE_TOOLS.items()
        for name in names
        if FORBIDDEN.search(name)
    ]

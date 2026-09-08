"""The research role's tools, driven the way the model drives them.

Every call goes through `ToolManager.call_tool`, not through `tool.fn`. That is
the whole point of this file: the model's arguments arrive as JSON and are
validated against the tool's own schema before a body runs, so a test that
called `fn` directly would skip exactly the layer these tools add and would
happily pass a dict where a `SectorRow` is declared.

What is being asserted, over and over, is one property: a REFUSAL REACHES THE
MODEL AS TEXT IT CAN ACT ON. `tc.research` raises `LedgerError`,
`DocValidationError` and `DocCasMismatch` with reasons written for a reader;
this layer's only real job is to keep those reasons intact instead of turning
them into a traceback the model never sees.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from tc.broker.fake import FakeBroker
from tc.config import Settings, load_settings
from tc.mcp import tools_research
from tc.mcp.registry import RESEARCH_TOOLS
from tc.mcp.server import McpDeps
from tc.research.docs import DocStore
from tc.rules.model import Rules
from tc.store.db import Store

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "tests" / "engine" / "fixtures" / "broker"
NOW = datetime(2026, 9, 7, 17, 31, tzinfo=UTC)

CONFIG = """
engine:
  data_dir: {p}
  repo_dir: {repo}
  research_dir: {p}/research
token:
  reauth_after_days: 5
  hard_expiry_days: 7
  callback_url: https://pi.example.ts.net/oauth/callback
runner:
  url: http://127.0.0.1:8090
"""
ENV = "TC_SCHWAB_APP_KEY=k\nTC_SCHWAB_APP_SECRET=s\n"

# Named constants rather than inlined literals: ruff's S105/S106 read any
# name holding "pass" as a credential, and `expect_last_pass="..."` trips it.
# These stamps are the opposite of a secret -- they are written into the
# document on purpose, so the next writer can prove which version it read.
STAMP = "Last pass: 2026-09-07 13:05 ET"
STAMP_NEXT = "Last pass: 2026-09-07 14:05 ET"
STALE = "Last pass: stale"

CANDIDATES = f"""# Research candidates

{STAMP}

This document is context, never a source for order parameters.

## Tier 1
- CSX — waiting on the print

## Tier 2
- MPC — nothing yet

## Tier 3
- none
"""


def _settings(tmp_path: Path) -> Settings:
    cfg = tmp_path / "config.yml"
    cfg.write_text(CONFIG.format(p=tmp_path, repo=REPO))
    env = tmp_path / ".env"
    env.write_text(ENV)
    return load_settings(cfg, env)


def _universe_row(symbol: str, description: str) -> dict[str, Any]:
    return {
        "symbol": symbol, "price": Decimal("50"), "adv10": Decimal("1000000"),
        "dollar_vol": Decimal("50000000"), "pct_from_52wk_high": Decimal("1.0"),
        "optionable": True, "leverage": Decimal("0"), "last_earnings": "2026-08-04",
        "is_etf": False, "session_range_pct": Decimal("1.2"), "description": description,
        "qualified": True,
    }


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    # ":memory:" and not a tmp file: nothing here is asserted about durability,
    # and an in-memory database cannot leave a WAL behind for the next test.
    s = Store(Path(":memory:"))
    await s.open()
    yield s
    await s.close()


@pytest.fixture
async def docs(tmp_path: Path, store: Store) -> DocStore:
    return DocStore(tmp_path / "research", store)


@pytest.fixture
async def research_server(tmp_path: Path, store: Store, docs: DocStore) -> FastMCP:
    deps = McpDeps(
        store=store,
        broker=FakeBroker(FIXTURES, NOW),
        docs=docs,
        rules=Rules.load(REPO / "rules.yml"),
        settings=_settings(tmp_path),
        clock=lambda: NOW,
    )
    server = FastMCP(name="engine", streamable_http_path="/", stateless_http=False)
    tools_research.register(server, deps, "research")
    return server


async def call(server: FastMCP, tool: str, /, **arguments: Any) -> Any:
    """The model's path in: arguments validated against the tool's schema,
    then the body. `tool.fn` would skip the validation half.

    Positional-only up to the tool name, because several tools take an
    argument called `name` and a keyword helper would swallow it.
    """
    return await server._tool_manager.call_tool(tool, arguments)


@pytest.fixture
async def seeded_candidates(docs: DocStore) -> str:
    await docs.replace("candidates", CANDIDATES)
    return CANDIDATES


@pytest.fixture
async def two_iv_days(store: Store) -> None:
    for d in ("2026-09-02", "2026-09-03"):
        await store.append_ledger(
            "iv", date.fromisoformat(d), "CSX",
            {"symbol": "CSX", "t": "16:00:00", "atm_iv": "0.41"},
        )


@pytest.fixture
async def swept_universe(store: Store) -> None:
    await store.replace_universe(
        date(2026, 9, 5),
        [_universe_row("CSX", "CSX Corp"), _universe_row("MPC", "Marathon Petroleum Corp")],
    )


@pytest.fixture
async def open_alert(store: Store) -> None:
    await store.open_alert("stop_missing", "CSX has no resting stop")


# --- the surface -------------------------------------------------------


async def test_every_declared_research_tool_is_registered(research_server: FastMCP) -> None:
    for name in RESEARCH_TOOLS:
        assert research_server._tool_manager.get_tool(name) is not None, name


async def test_the_module_registers_nothing_the_registry_did_not_declare(
    research_server: FastMCP,
) -> None:
    """`build_servers` raises on an undeclared name, which is a boot failure
    in production and a silent widening of the surface if it ever passed."""
    names = {t.name for t in research_server._tool_manager.list_tools()}
    assert names - set(RESEARCH_TOOLS) == set()


# --- refusals the model must be able to read ---------------------------


async def test_evidence_append_refusal_reaches_the_model_as_a_tool_error(
    research_server: FastMCP,
) -> None:
    with pytest.raises(ToolError) as e:
        await call(
            research_server, "evidence_append", symbol="CSX", date="2026-09-07",
            record={"claim": "x", "url": "u", "source_type": "blog",
                    "observed": "2026-09-07", "independence": "i"},
        )
    assert "source_type" in str(e.value)          # the reason, not just "failed"


async def test_evidence_append_records_a_good_observation(research_server: FastMCP,
                                                          store: Store) -> None:
    out = await call(
        research_server, "evidence_append", symbol="CSX", date="2026-09-07",
        record={"claim": "yard idle", "url": "https://x/1", "source_type": "end-user",
                "observed": "2026-09-07", "independence": "unrelated"},
    )
    assert out.appended is True and out.detail == "CSX"
    read = await call(research_server, "evidence_read", symbol="CSX")
    assert [r["claim"] for r in read.rows] == ["yard idle"]


async def test_escalation_below_the_bar_is_refused_with_the_bar_named(
    research_server: FastMCP,
) -> None:
    with pytest.raises(ToolError) as e:
        await call(
            research_server, "escalation_raise", symbol="CSX", date="2026-09-07",
            record={"claim": "c", "direction": "up", "event_date": "2026-10-10",
                    "source_types": ["end-user", "mainstream"]},
        )
    assert "2 distinct source types" in str(e.value)


async def test_a_raise_is_scored_by_a_second_row_and_the_last_one_wins(
    research_server: FastMCP,
) -> None:
    raised = await call(
        research_server, "escalation_raise", symbol="CSX", date="2026-09-07",
        record={"claim": "c", "direction": "up", "event_date": "2026-10-10",
                "source_types": ["end-user", "employee"]},
    )
    for outcome in ("wrong", "right"):
        scored = await call(
            research_server, "escalation_score", escalation_id=raised.id,
            outcome=outcome, date="2026-10-11",
        )
        assert scored.id == raised.id
    read = await call(research_server, "escalations_read", symbol="CSX")
    assert len(read.rows) == 1 and read.rows[0]["latest_outcome"] == "right"


async def test_scoring_an_id_that_was_never_raised_is_a_refusal(
    research_server: FastMCP,
) -> None:
    with pytest.raises(ToolError) as e:
        await call(
            research_server, "escalation_score", escalation_id="CSX-2026-09-07-1",
            outcome="right", date="2026-09-07",
        )
    assert "no such raise" in str(e.value)


# --- documents ---------------------------------------------------------


async def test_doc_write_cas_mismatch_says_what_to_do(
    research_server: FastMCP, seeded_candidates: str
) -> None:
    with pytest.raises(ToolError) as e:
        await call(
            research_server, "doc_write", kind="candidates", body=seeded_candidates,
            expect_last_pass=STALE,
        )
    assert "retry once" in str(e.value)
    assert STAMP in str(e.value)                  # the fresh stamp, named


async def test_doc_write_refuses_a_truncated_body_with_the_line_count(
    research_server: FastMCP,
) -> None:
    with pytest.raises(ToolError) as e:
        await call(research_server, "doc_write", kind="candidates", body="# Research candidates\n")
    assert "truncated heredoc" in str(e.value)


async def test_doc_write_then_read_round_trips_the_last_pass_stamp(
    research_server: FastMCP, seeded_candidates: str
) -> None:
    body = seeded_candidates.replace("13:05", "14:05")
    out = await call(
        research_server, "doc_write", kind="candidates", body=body,
        expect_last_pass=STAMP,
    )
    assert out.kind == "candidates"
    view = await call(research_server, "doc_read", kind="candidates")
    assert view.last_pass == STAMP_NEXT


async def test_doc_read_of_a_missing_document_is_not_an_error(research_server: FastMCP) -> None:
    out = await call(research_server, "doc_read", kind="scorecard")
    assert out.exists is False and out.body == ""


# --- ledgers -----------------------------------------------------------


async def test_ledger_read_latest_before_returns_the_prior_day(
    research_server: FastMCP, two_iv_days: None
) -> None:
    out = await call(research_server, "ledger_read", name="iv", latest_before="2026-09-07")
    assert [r["date"] for r in out.rows] == ["2026-09-03"]


async def test_oi_already_snapshotted_is_a_result_not_an_error(research_server: FastMCP) -> None:
    rec = {"symbol": "CSX", "t": "16:26:00"}
    first = await call(research_server, "ledger_append", name="oi", date="2026-09-07", record=rec)
    assert first.appended is True
    again = await call(research_server, "ledger_append", name="oi", date="2026-09-07", record=rec)
    assert again.appended is False
    assert again.reason is not None and "already snapshotted" in again.reason


async def test_a_missing_required_ledger_field_is_refused_by_name(
    research_server: FastMCP,
) -> None:
    with pytest.raises(ToolError) as e:
        await call(
            research_server, "ledger_append", name="screen", date="2026-09-07",
            record={"symbol": "CSX", "t": "09:40:00"},
        )
    assert "src" in str(e.value)


async def test_tombstone_is_the_typed_front_door_onto_the_same_validator(
    research_server: FastMCP,
) -> None:
    out = await call(
        research_server, "tombstone", symbol="CSX", date="2026-09-07", gate="liquidity",
        reason="adv below the floor", ref_price="34.55", hypo_qty=10, hypo_stop="31.10",
    )
    assert out.appended is True
    rows = await call(research_server, "ledger_read", name="tombstones", date="2026-09-07")
    assert rows.rows[0]["record"]["ref_price"] == "34.55"   # a string, never a float
    assert rows.rows[0]["record"]["hypo_stop"] == "31.10"


async def test_a_tombstone_price_that_is_not_a_number_is_refused(
    research_server: FastMCP,
) -> None:
    with pytest.raises(ToolError) as e:
        await call(
            research_server, "tombstone", symbol="CSX", date="2026-09-07", gate="liquidity",
            reason="r", ref_price="thirty",
        )
    assert "ref_price" in str(e.value)


# --- sectors -----------------------------------------------------------


async def test_sector_write_takes_only_the_batch_form(research_server: FastMCP) -> None:
    out = await call(
        research_server, "sector_write",
        rows=[{"symbol": "CSX", "sector": "airlines-transport"}], date="2026-09-07",
    )
    assert out.written == 1 and out.new == 1
    read = await call(research_server, "sectors_read")
    assert [(r["symbol"], r["sector"]) for r in read.rows] == [("CSX", "airlines-transport")]


async def test_sector_write_is_all_or_nothing(research_server: FastMCP) -> None:
    """The second row is bad, so the FIRST must not land: a half-applied batch
    leaves the cohort join reading a universe nobody chose."""
    with pytest.raises(ToolError) as e:
        await call(
            research_server, "sector_write",
            rows=[{"symbol": "CSX", "sector": "airlines-transport"},
                  {"symbol": "MPC", "sector": "energy"}],
            date="2026-09-07",
        )
    assert "row 2" in str(e.value)
    read = await call(research_server, "sectors_read")
    assert read.rows == []


# --- universe, cohort, alert -------------------------------------------


async def test_universe_symbols_and_names_page(
    research_server: FastMCP, swept_universe: None
) -> None:
    syms = await call(research_server, "universe_symbols")
    assert syms.symbols[:2] == ["CSX", "MPC"]
    page = await call(research_server, "universe_names_page", offset=1, limit=1)
    assert [r.symbol for r in page.rows] == ["MPC"]
    assert page.rows[0].description == "Marathon Petroleum Corp"
    assert page.total == 2


async def test_universe_names_page_past_the_end_is_empty_with_the_total_intact(
    research_server: FastMCP, swept_universe: None
) -> None:
    page = await call(research_server, "universe_names_page", offset=9, limit=50)
    assert page.rows == [] and page.total == 2


async def test_cohort_is_empty_and_that_is_a_result(research_server: FastMCP) -> None:
    out = await call(research_server, "cohort", date="2026-09-07")
    assert out.rows == [] and out.date == "2026-09-07"


async def test_alert_read_reports_an_unacked_alert(
    research_server: FastMCP, open_alert: None
) -> None:
    out = await call(research_server, "alert_read")
    assert out.exists is True and out.acknowledged is False
    assert "CSX has no resting stop" in out.body


async def test_alert_read_with_nothing_open_is_not_closing_only(
    research_server: FastMCP,
) -> None:
    out = await call(research_server, "alert_read")
    assert out.exists is False and out.acknowledged is True and out.body == ""

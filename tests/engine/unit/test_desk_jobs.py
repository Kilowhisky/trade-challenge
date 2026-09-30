"""Task 17: the desk's job table."""

from __future__ import annotations

from pathlib import Path

import yaml

from tc.jobs.spec import CHAINED_JOBS, DESK_CHAINS, JOB_SPECS, AnalystVerdict, output_schema

REPO = Path(__file__).resolve().parents[3]


def test_every_chained_job_has_a_spec_and_none_is_scheduled_on_its_own() -> None:
    sched = yaml.safe_load((REPO / "config.yml").read_text())["schedule"]
    assert {"desk_evening", "desk_preopen", "pm", "pm_midday", "desk_watch"} <= set(sched)
    for chain, jobs in DESK_CHAINS.items():
        assert chain in sched
        for job in jobs:
            assert job in JOB_SPECS and job not in sched


def test_the_evening_chain_order_is_technical_earnings_news_macro() -> None:
    assert DESK_CHAINS["desk_evening"] == (
        "analyst_technical", "analyst_earnings", "analyst_news", "analyst_macro",
    )
    assert CHAINED_JOBS >= set(DESK_CHAINS["desk_preopen"])


def test_analysts_run_as_research_and_the_pm_as_decide() -> None:
    for job in CHAINED_JOBS:
        assert JOB_SPECS[job].role == "research"
    assert JOB_SPECS["pm"].role == JOB_SPECS["pm_midday"].role == "decide"


def test_jobs_sharing_an_agent_share_one_allowlist() -> None:
    for a, b in (("analyst_news", "preopen_news"), ("analyst_earnings", "preopen_earnings"),
                 ("pm", "pm_midday")):
        assert set(JOB_SPECS[a].allowed_tools) == set(JOB_SPECS[b].allowed_tools)


def test_only_the_pm_retries_a_failure() -> None:
    assert JOB_SPECS["pm"].retry_failed_after_s == 900
    assert all(s.retry_failed_after_s is None for n, s in JOB_SPECS.items() if n != "pm")


def test_the_analyst_verdict_forbids_extra_keys() -> None:
    schema = output_schema(AnalystVerdict)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"pitched", "withdrawn", "summary"}

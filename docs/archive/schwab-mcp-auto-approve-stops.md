# Local patch: auto-approve protective equity stops

**Applied 2026-08-14** to
`~/.local/share/uv/tools/schwab-mcp/lib/python3.12/site-packages/schwab_mcp/tools/orders.py`,
inside `place_previewed_order`, immediately after
`entry = ctx.previews.pop(preview_id, account_hash)`.

**Authorized by Chris, day-one close review (verbatim):** "If there is a
way for you to work off stops without my input that would be approved."

**Effect:** an order whose cached preview spec is `orderType` STOP or
STOP_LIMIT with every leg an EQUITY SELL is placed without the Discord ✅
gate (a `ctx.warning` is emitted instead, so the placement is still
visible in the session). All buys, options, market/limit sells, and
cancels still require approval. The account is CASH, so the broker
rejects any SELL stop that would open a short — the bypass cannot create
a position, only protect one.

**Why:** day one's USB entry sat naked 3m32s waiting on the stop's ✅
(approval latency 195s). The §3.4/§4.3 machinery exists to close exactly
that window.

**Fragile:** any `uv tool upgrade/install schwab-mcp` wipes the patch.
Reapply the block below and re-verify with an AST parse. Takes effect on
the next MCP server start (next Claude session).

```python
    # LOCAL PATCH 2026-08-14 (Chris, day-one close review): "If there is a
    # way for you to work off stops without my input that would be
    # approved." Protective equity stops — orderType STOP/STOP_LIMIT with
    # every leg an equity SELL — skip the Discord gate so a filled entry is
    # never left naked waiting on a reaction. Everything else (all buys,
    # options, market/limit sells, cancels) still requires approval. A cash
    # account cannot go short, so an unmatched SELL stop is rejected by the
    # broker, not opened. NOTE: this patch lives in the installed uv tool
    # and is wiped by any schwab-mcp reinstall/upgrade — reapply from
    # docs/patches/ in the trade-challenge repo.
    _spec = entry.order_spec if isinstance(entry.order_spec, dict) else {}
    _legs = _spec.get("orderLegCollection") or []
    _is_protective_stop = (
        _spec.get("orderType") in ("STOP", "STOP_LIMIT")
        and bool(_legs)
        and all(
            leg.get("instruction") == "SELL"
            and (leg.get("instrument") or {}).get("assetType") == "EQUITY"
            for leg in _legs
        )
    )
    if _is_protective_stop:
        await ctx.warning(
            f"Auto-approved protective stop (Discord gate bypassed by local patch): {entry.summary}"
        )
        decision = ApprovalDecision.APPROVED
    else:
        # original ApprovalRequest construction + run_approval, indented
        # one level under this else
```

**Validation plan (Monday 8/17, pre-entry):** after the session-open
protocol, run a stop-replace drill on a held name — cancel one resting
stop (normal ✅ path), immediately re-place the identical protective stop
and confirm it goes WORKING with **no Discord embed**. Counts as one
replace against the §4.10 3-per-stop ceiling. Until this passes, treat
the patch as unverified and keep watching for the embed on every stop.

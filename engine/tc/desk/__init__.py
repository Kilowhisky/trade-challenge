"""The trading desk (docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md).

Analysts pitch, a portfolio manager calls and funds, the engine validates,
fills the paper book, exits and scores. Everything here is engine code: the
model reaches it only through the typed tools in tc/mcp/tools_desk.py.
"""

-- v3 engine schema, Phase 0. Idempotent; applied by Store.open().
-- Money columns are TEXT holding Decimal strings: SQLite REAL would silently
-- turn 0.1+0.2 into a float, which is the rounding class the spec forbids.
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS account_snapshots (
  id INTEGER PRIMARY KEY,
  account_hash TEXT NOT NULL,
  read_at TEXT NOT NULL,
  liquidation_value TEXT NOT NULL,
  cash_available_for_trading TEXT NOT NULL,
  unsettled_cash TEXT NOT NULL,
  cash_balance TEXT NOT NULL,
  cash_call TEXT NOT NULL,
  is_closing_only_restricted INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS position_snapshots (
  snapshot_id INTEGER NOT NULL REFERENCES account_snapshots(id),
  symbol TEXT NOT NULL, asset_type TEXT NOT NULL, quantity INTEGER NOT NULL,
  average_price TEXT NOT NULL, market_value TEXT NOT NULL, day_pl TEXT NOT NULL,
  settled_quantity INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS order_snapshots (
  id INTEGER PRIMARY KEY, account_hash TEXT NOT NULL, read_at TEXT NOT NULL,
  order_id INTEGER NOT NULL, status TEXT NOT NULL, order_type TEXT NOT NULL, duration TEXT NOT NULL,
  entered_at TEXT NOT NULL, symbol TEXT NOT NULL, quantity INTEGER NOT NULL, filled_quantity INTEGER NOT NULL,
  price TEXT, stop_price TEXT, legs_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticks (
  id INTEGER PRIMARY KEY, at_et TEXT NOT NULL, state TEXT NOT NULL,
  account_value TEXT NOT NULL, comp_capital TEXT NOT NULL, hwm TEXT NOT NULL, drawdown_pct TEXT NOT NULL,
  level TEXT NOT NULL, positions INTEGER NOT NULL, stops INTEGER NOT NULL, orders INTEGER NOT NULL,
  settled TEXT NOT NULL, unsettled TEXT NOT NULL, reserve TEXT NOT NULL, flags TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_status (
  date TEXT PRIMARY KEY, close_value TEXT NOT NULL, hwm TEXT NOT NULL, halt TEXT NOT NULL,
  drawdown_pct TEXT NOT NULL, level TEXT NOT NULL, prior_hwm TEXT NOT NULL, ratcheted INTEGER NOT NULL,
  intraday_high TEXT, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('done','noop','content_failed','failed','timeout','missed')),
  detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS token_events (id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, opened_at TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, acked_at TEXT
);
CREATE TABLE IF NOT EXISTS rules_versions (sha256 TEXT PRIMARY KEY, path TEXT NOT NULL, seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expectations_log (id INTEGER PRIMARY KEY, at TEXT NOT NULL, name TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trade_log (
  id INTEGER PRIMARY KEY, at_et TEXT NOT NULL, symbol TEXT NOT NULL, action TEXT NOT NULL,
  quantity INTEGER NOT NULL, price TEXT NOT NULL, order_id INTEGER, pretrade_json TEXT NOT NULL, note TEXT NOT NULL
);

-- Append-only ledgers (CLAUDE.md §7.1: never edited once written; a correction is a new row).
CREATE TRIGGER IF NOT EXISTS ticks_no_update BEFORE UPDATE ON ticks BEGIN SELECT RAISE(ABORT, 'ticks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ticks_no_delete BEFORE DELETE ON ticks BEGIN SELECT RAISE(ABORT, 'ticks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS job_runs_no_update BEFORE UPDATE ON job_runs BEGIN SELECT RAISE(ABORT, 'job_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS job_runs_no_delete BEFORE DELETE ON job_runs BEGIN SELECT RAISE(ABORT, 'job_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_snapshots_no_update BEFORE UPDATE ON order_snapshots BEGIN SELECT RAISE(ABORT, 'order_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS order_snapshots_no_delete BEFORE DELETE ON order_snapshots BEGIN SELECT RAISE(ABORT, 'order_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS token_events_no_update BEFORE UPDATE ON token_events BEGIN SELECT RAISE(ABORT, 'token_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS token_events_no_delete BEFORE DELETE ON token_events BEGIN SELECT RAISE(ABORT, 'token_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_log_no_update BEFORE UPDATE ON trade_log BEGIN SELECT RAISE(ABORT, 'trade_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trade_log_no_delete BEFORE DELETE ON trade_log BEGIN SELECT RAISE(ABORT, 'trade_log is append-only'); END;

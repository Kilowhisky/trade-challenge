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
  -- average_price is nullable: Schwab reports no cost basis for some
  -- positions, and NULL is the honest record of that (see Position.lifetime_pl).
  average_price TEXT, market_value TEXT NOT NULL, day_pl TEXT NOT NULL,
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

-- Research ledgers (spec §6). Everything the bash writers validated as JSON or
-- TSV becomes a row here; the documents Claude reads whole stay as files and
-- are indexed in `artifacts`.
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, date TEXT NOT NULL,
  claim TEXT NOT NULL, url TEXT NOT NULL, source_type TEXT NOT NULL,
  observed TEXT NOT NULL, independence TEXT NOT NULL, extra_json TEXT NOT NULL,
  written_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS evidence_symbol ON evidence(symbol, id);

-- One table for raises AND scores, because scoring is append-only and an id may
-- carry many score rows: the LAST score per id wins, and a reader that counts
-- rows instead of reducing to the latest gets the hit rate wrong
-- (0c-writers-contract.md §1.5, reader contract).
CREATE TABLE IF NOT EXISTS escalations (
  row_id INTEGER PRIMARY KEY, id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('raise','score')),
  symbol TEXT NOT NULL, at TEXT NOT NULL, record_json TEXT NOT NULL,
  written_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS escalations_id ON escalations(id, row_id);

CREATE TABLE IF NOT EXISTS sectors (
  symbol TEXT PRIMARY KEY, sector TEXT NOT NULL, date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe (
  asof TEXT NOT NULL, symbol TEXT NOT NULL,
  price TEXT NOT NULL, adv10 TEXT NOT NULL, dollar_vol TEXT NOT NULL,
  pct_from_52wk_high TEXT NOT NULL, optionable INTEGER NOT NULL, leverage TEXT NOT NULL,
  last_earnings TEXT NOT NULL, is_etf INTEGER NOT NULL, session_range_pct TEXT,
  description TEXT NOT NULL, qualified INTEGER NOT NULL,
  PRIMARY KEY (asof, symbol)
);

CREATE TABLE IF NOT EXISTS tombstones (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screen_rows (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iv_series (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
-- UNIQUE(date,symbol) is the §1.7 idempotency guard: one snapshot per
-- underlying per day, and a second attempt is a SKIP, never an error.
CREATE TABLE IF NOT EXISTS oi_snapshots (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT NOT NULL,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL,
  UNIQUE (date, symbol)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, symbol TEXT,
  record_json TEXT NOT NULL, written_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, date TEXT,
  path TEXT NOT NULL, sha256 TEXT NOT NULL, lines INTEGER NOT NULL,
  written_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS escalations_no_update BEFORE UPDATE ON escalations BEGIN SELECT RAISE(ABORT, 'escalations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS escalations_no_delete BEFORE DELETE ON escalations BEGIN SELECT RAISE(ABORT, 'escalations is append-only'); END;

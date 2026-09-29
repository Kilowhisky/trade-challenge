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

-- === The trading desk (docs/superpowers/specs/2026-09-27-claude-trading-desk-design.md §13) ===
-- Daily bars are a cache of the broker's own history, not a ledger: a
-- re-fetch may correct a bar, so a row is replaced on conflict.
CREATE TABLE IF NOT EXISTS bars (
  symbol TEXT NOT NULL, date TEXT NOT NULL,
  open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
  volume INTEGER NOT NULL,
  PRIMARY KEY (symbol, date)
);
-- A pitch is an analyst's prediction (§5), filed once and never edited.
CREATE TABLE IF NOT EXISTS pitches (
  id INTEGER PRIMARY KEY,
  analyst TEXT NOT NULL CHECK (analyst IN ('technical','earnings','news','macro')),
  filed_at TEXT NOT NULL,
  session TEXT NOT NULL,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('up','down')),
  thesis TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  target TEXT NOT NULL,
  invalidation TEXT NOT NULL,
  horizon_days INTEGER NOT NULL,
  conviction INTEGER NOT NULL CHECK (conviction BETWEEN 1 AND 5),
  benchmark TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pitch_withdrawals (
  pitch_id INTEGER PRIMARY KEY REFERENCES pitches(id), at TEXT NOT NULL
);
-- A call is the PM's prediction (§6). ref/spy_ref/bench_ref are stamped by
-- the engine from live quotes; the model never types a price.
CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY,
  made_at TEXT NOT NULL,
  session TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('pitch','pm','legacy')),
  pitch_id INTEGER REFERENCES pitches(id),
  extends_call_id INTEGER REFERENCES calls(id),
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('up','down')),
  thesis TEXT NOT NULL,
  target TEXT NOT NULL,
  invalidation TEXT NOT NULL,
  horizon_days INTEGER NOT NULL,
  conviction INTEGER NOT NULL CHECK (conviction BETWEEN 1 AND 5),
  benchmark TEXT NOT NULL,
  ref_price TEXT NOT NULL,
  spy_ref TEXT NOT NULL,
  bench_ref TEXT NOT NULL,
  funding TEXT NOT NULL CHECK (funding IN ('none','shares','call','put'))
);
CREATE TABLE IF NOT EXISTS call_tightenings (
  id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL REFERENCES calls(id),
  at TEXT NOT NULL, invalidation TEXT NOT NULL
);
-- One resolution per item, written once (§7). UNIQUE is the idempotency
-- guard: a second scoring pass over the same day skips, never rewrites.
CREATE TABLE IF NOT EXISTS resolutions (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('pitch','call')),
  item_id INTEGER NOT NULL,
  ref_date TEXT NOT NULL, ref_price TEXT NOT NULL,
  resolved_on TEXT NOT NULL,
  how TEXT NOT NULL CHECK (how IN ('target','invalidation','horizon','gap_target','gap_invalidation','open_through')),
  exit_price TEXT NOT NULL,
  ret_pct TEXT NOT NULL, spy_ret_pct TEXT NOT NULL, bench_ret_pct TEXT NOT NULL,
  written_at TEXT NOT NULL,
  UNIQUE (kind, item_id)
);
-- The paper book (§10). One row: when it started and with what.
CREATE TABLE IF NOT EXISTS paper_book (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  start_date TEXT NOT NULL, start_equity TEXT NOT NULL, created_at TEXT NOT NULL
);
-- Operational rows, not ledgers: posting stamps posted_at/veto_deadline/
-- message_id onto a proposal after it is created.
CREATE TABLE IF NOT EXISTS proposals (
  id INTEGER PRIMARY KEY,
  call_id INTEGER NOT NULL REFERENCES calls(id),
  created_at TEXT NOT NULL,
  instrument TEXT NOT NULL CHECK (instrument IN ('shares','call','put')),
  symbol TEXT NOT NULL,
  underlying TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  max_entry_price TEXT NOT NULL,
  atr_pct TEXT,
  posted_at TEXT, veto_deadline TEXT, message_id TEXT
);
CREATE TABLE IF NOT EXISTS proposal_outcomes (
  proposal_id INTEGER PRIMARY KEY REFERENCES proposals(id),
  at TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('filled','skipped_price','skipped_invalid','skipped_blind','expired')),
  vetoed INTEGER NOT NULL, approved INTEGER NOT NULL, detail_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fills (
  id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(id),
  at TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  price TEXT NOT NULL,
  reason TEXT NOT NULL,
  stop_trigger TEXT, stop_limit TEXT
);
CREATE TABLE IF NOT EXISTS paper_marks (
  symbol TEXT PRIMARY KEY, price TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exit_requests (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, symbol TEXT NOT NULL,
  proposal_id INTEGER REFERENCES proposals(id), reason TEXT NOT NULL, done_at TEXT
);
CREATE TRIGGER IF NOT EXISTS pitches_no_update BEFORE UPDATE ON pitches BEGIN SELECT RAISE(ABORT, 'pitches is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pitches_no_delete BEFORE DELETE ON pitches BEGIN SELECT RAISE(ABORT, 'pitches is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pitch_withdrawals_no_update BEFORE UPDATE ON pitch_withdrawals BEGIN SELECT RAISE(ABORT, 'pitch_withdrawals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS pitch_withdrawals_no_delete BEFORE DELETE ON pitch_withdrawals BEGIN SELECT RAISE(ABORT, 'pitch_withdrawals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS calls_no_update BEFORE UPDATE ON calls BEGIN SELECT RAISE(ABORT, 'calls is append-only'); END;
CREATE TRIGGER IF NOT EXISTS calls_no_delete BEFORE DELETE ON calls BEGIN SELECT RAISE(ABORT, 'calls is append-only'); END;
CREATE TRIGGER IF NOT EXISTS call_tightenings_no_update BEFORE UPDATE ON call_tightenings BEGIN SELECT RAISE(ABORT, 'call_tightenings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS call_tightenings_no_delete BEFORE DELETE ON call_tightenings BEGIN SELECT RAISE(ABORT, 'call_tightenings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS resolutions_no_update BEFORE UPDATE ON resolutions BEGIN SELECT RAISE(ABORT, 'resolutions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS resolutions_no_delete BEFORE DELETE ON resolutions BEGIN SELECT RAISE(ABORT, 'resolutions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS proposal_outcomes_no_update BEFORE UPDATE ON proposal_outcomes BEGIN SELECT RAISE(ABORT, 'proposal_outcomes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS proposal_outcomes_no_delete BEFORE DELETE ON proposal_outcomes BEGIN SELECT RAISE(ABORT, 'proposal_outcomes is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_fills_no_update BEFORE UPDATE ON paper_fills BEGIN SELECT RAISE(ABORT, 'paper_fills is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_fills_no_delete BEFORE DELETE ON paper_fills BEGIN SELECT RAISE(ABORT, 'paper_fills is append-only'); END;

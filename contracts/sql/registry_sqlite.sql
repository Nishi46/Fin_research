-- Append-only registry schema, SQLite flavour (local dev and tests).
-- UUIDs are TEXT, timestamps are ISO-8601 UTC TEXT, JSON is TEXT.
-- Nothing here is ever updated or deleted, so there is no soft-delete column.
--
-- INSERT OR REPLACE deletes the old row without firing delete triggers, so each table also
-- has a BEFORE INSERT trigger that refuses a key that already exists.
-- Not blocked: DROP TRIGGER / DROP TABLE by whoever holds the database file.

CREATE TABLE trials (
  id TEXT PRIMARY KEY NOT NULL,
  created_at TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  params TEXT NOT NULL,
  capital_amount TEXT NOT NULL,
  capital_currency TEXT NOT NULL,
  data_snapshot_id TEXT NOT NULL,
  survivorship_biased INTEGER NOT NULL CHECK (survivorship_biased IN (0, 1)),
  stage_reached TEXT NOT NULL CHECK (stage_reached IN (
    'lookahead', 'walk_forward', 'trial_adjusted', 'cost_sensitivity',
    'stability', 'floors', 'paper', 'owner_gate')),
  outcome TEXT NOT NULL CHECK (outcome IN ('rejected', 'passed', 'error')),
  metrics TEXT NOT NULL
);
CREATE INDEX idx_trials_strategy_id ON trials (strategy_id);
CREATE INDEX idx_trials_created_at ON trials (created_at);

CREATE TABLE runs (
  event_id TEXT PRIMARY KEY NOT NULL,
  run_id TEXT NOT NULL,
  at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'failed')),
  host TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX idx_runs_run_id ON runs (run_id);
CREATE INDEX idx_runs_at ON runs (at);

CREATE TABLE commands (
  id TEXT PRIMARY KEY NOT NULL,
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('set_capital', 'promote', 'reject', 'set_threshold')),
  payload TEXT NOT NULL,
  issued_by TEXT NOT NULL
);

CREATE TABLE command_status_history (
  event_id TEXT PRIMARY KEY NOT NULL,
  command_id TEXT NOT NULL,
  at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected')),
  reason TEXT
);
CREATE INDEX idx_command_status_history_command_id ON command_status_history (command_id);

CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

CREATE TRIGGER runs_no_update BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

CREATE TRIGGER commands_no_update BEFORE UPDATE ON commands
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER commands_no_delete BEFORE DELETE ON commands
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

CREATE TRIGGER command_status_history_no_update BEFORE UPDATE ON command_status_history
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER command_status_history_no_delete BEFORE DELETE ON command_status_history
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

CREATE TRIGGER trials_no_replace BEFORE INSERT ON trials
WHEN EXISTS (SELECT 1 FROM trials WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

CREATE TRIGGER runs_no_replace BEFORE INSERT ON runs
WHEN EXISTS (SELECT 1 FROM runs WHERE event_id = NEW.event_id)
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

CREATE TRIGGER commands_no_replace BEFORE INSERT ON commands
WHEN EXISTS (SELECT 1 FROM commands WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

CREATE TRIGGER command_status_history_no_replace BEFORE INSERT ON command_status_history
WHEN EXISTS (SELECT 1 FROM command_status_history WHERE event_id = NEW.event_id)
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;

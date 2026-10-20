-- Append-only registry schema, Postgres flavour (production).
-- Phase 0 only pattern-checks this file (tests/unit/test_registry_ddl.py); it has never been
-- parsed or run against a real Postgres. A live test is a Phase 1 task.
-- Requires the uuid-ossp extension to be available (check your host, for example Neon).
-- Nothing here is ever updated or deleted, so there is no soft-delete column.
-- No foreign keys: relationships are validated by the application.
--
-- The primary control is privileges: the engine's database role should be granted INSERT and
-- SELECT only. The triggers below are a backstop for roles that do have UPDATE/DELETE; a table
-- owner or superuser can still DISABLE or DROP them.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'append-only table: % is not allowed on %', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE trials (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  strategy_id TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  params JSONB NOT NULL,
  capital_amount NUMERIC NOT NULL CHECK (capital_amount > 0),
  capital_currency TEXT NOT NULL,
  data_snapshot_id TEXT NOT NULL,
  survivorship_biased BOOLEAN NOT NULL,
  stage_reached TEXT NOT NULL CHECK (stage_reached IN (
    'lookahead', 'walk_forward', 'trial_adjusted', 'cost_sensitivity',
    'stability', 'floors', 'paper', 'owner_gate')),
  outcome TEXT NOT NULL CHECK (outcome IN ('rejected', 'passed', 'error')),
  metrics JSONB NOT NULL
);
CREATE INDEX idx_trials_strategy_id ON trials (strategy_id);
CREATE INDEX idx_trials_created_at ON trials (created_at);

-- One row per trial that produced a return series: zlib of little-endian int64 deltas of epoch
-- microseconds followed by float32 returns. Written once, next to (not inside) the trial row.
CREATE TABLE trial_returns (
  trial_id UUID PRIMARY KEY,
  group_key TEXT NOT NULL,
  n INTEGER NOT NULL CHECK (n > 0),
  payload BYTEA NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_trial_returns_group ON trial_returns (group_key, created_at);

CREATE TABLE runs (
  event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  run_id UUID NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL CHECK (status IN ('started', 'succeeded', 'failed')),
  host TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX idx_runs_run_id ON runs (run_id);
CREATE INDEX idx_runs_at ON runs (at);

CREATE TABLE commands (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  kind TEXT NOT NULL CHECK (kind IN ('set_capital', 'promote', 'reject', 'set_threshold')),
  payload JSONB NOT NULL,
  issued_by TEXT NOT NULL
);

CREATE TABLE command_status_history (
  event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  command_id UUID NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected')),
  reason TEXT
);
CREATE INDEX idx_command_status_history_command_id ON command_status_history (command_id);

CREATE TRIGGER trials_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON trials
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER runs_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON runs
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER commands_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON commands
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER command_status_history_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON command_status_history
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER trial_returns_append_only
  BEFORE UPDATE OR DELETE OR TRUNCATE ON trial_returns
  FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

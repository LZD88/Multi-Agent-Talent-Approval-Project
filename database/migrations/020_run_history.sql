CREATE TABLE IF NOT EXISTS talent_runs (
    run_id VARCHAR(64) PRIMARY KEY,
    message TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    report TEXT,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    trace_id VARCHAR(128),
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_talent_runs_status ON talent_runs (status);
CREATE INDEX IF NOT EXISTS idx_talent_runs_started_at ON talent_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_talent_runs_trace_id ON talent_runs (trace_id);

CREATE TABLE IF NOT EXISTS talent_run_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id VARCHAR(64) NOT NULL REFERENCES talent_runs(run_id) ON DELETE CASCADE,
    step_id VARCHAR(64) NOT NULL,
    position INTEGER NOT NULL CHECK (position > 0),
    title VARCHAR(255) NOT NULL,
    kind VARCHAR(32),
    status VARCHAR(32) NOT NULL,
    completed INTEGER,
    total INTEGER,
    summary TEXT,
    output JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ,
    active_started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms BIGINT,
    CONSTRAINT uq_talent_run_step UNIQUE (run_id, step_id)
);

ALTER TABLE talent_run_steps
ADD COLUMN IF NOT EXISTS active_started_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_talent_run_steps_run_position
ON talent_run_steps (run_id, position);

COMMENT ON TABLE talent_runs IS '人才评估 Run 的最新状态、报告与引用快照';
COMMENT ON TABLE talent_run_steps IS 'Run 内各业务步骤的最新可见结果';

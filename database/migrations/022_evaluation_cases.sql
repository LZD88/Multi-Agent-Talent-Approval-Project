CREATE TABLE IF NOT EXISTS evaluation_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_run_id VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64) NOT NULL,
    turn_no INTEGER NOT NULL CHECK (turn_no > 0),
    module_key VARCHAR(64) NOT NULL,
    invocation_no INTEGER NOT NULL CHECK (invocation_no > 0),
    input_payload JSONB NOT NULL,
    output_payload JSONB NOT NULL,
    replay_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    version_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'succeeded',
    error_code VARCHAR(64),
    duration_ms BIGINT,
    annotation_status VARCHAR(32) NOT NULL DEFAULT 'unlabeled',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_evaluation_case_invocation
        UNIQUE (source_run_id, module_key, invocation_no)
);

CREATE INDEX IF NOT EXISTS idx_evaluation_cases_run
ON evaluation_cases (source_run_id, created_at);

CREATE INDEX IF NOT EXISTS idx_evaluation_cases_module
ON evaluation_cases (module_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_evaluation_cases_conversation_turn
ON evaluation_cases (conversation_id, turn_no);

COMMENT ON TABLE evaluation_cases IS '从真实 Graph 模块调用边界自动采集的待评测样例';

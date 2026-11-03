CREATE TABLE IF NOT EXISTS evaluation_feedback (
    id UUID PRIMARY KEY,
    source_run_id VARCHAR(64) NOT NULL REFERENCES talent_runs(run_id),
    case_id UUID REFERENCES evaluation_cases(id),
    feedback_scope VARCHAR(16) NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_feedback_scope CHECK (
        (feedback_scope = 'module' AND case_id IS NOT NULL) OR
        (feedback_scope = 'run' AND case_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS ix_evaluation_feedback_run ON evaluation_feedback(source_run_id);
CREATE INDEX IF NOT EXISTS ix_evaluation_feedback_case ON evaluation_feedback(case_id);

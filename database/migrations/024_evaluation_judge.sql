CREATE TABLE IF NOT EXISTS evaluation_annotations (
    case_id UUID PRIMARY KEY REFERENCES evaluation_cases(id),
    annotator_type VARCHAR(16) NOT NULL,
    judge_version VARCHAR(64) NOT NULL,
    judge_model VARCHAR(128),
    score INTEGER CHECK (score BETWEEN 0 AND 4),
    verdict VARCHAR(32) NOT NULL,
    error_types JSON NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL,
    golden_candidate JSON,
    confidence DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
    review_status VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending', 'confirmed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_evaluation_annotations_score ON evaluation_annotations(score);
CREATE TABLE IF NOT EXISTS evaluation_dataset_items (
    id UUID PRIMARY KEY,
    source_case_id UUID NOT NULL UNIQUE REFERENCES evaluation_cases(id),
    module_key VARCHAR(64) NOT NULL,
    input_payload JSON NOT NULL,
    golden_output JSON NOT NULL,
    replay_context JSON NOT NULL,
    dataset_split VARCHAR(16) NOT NULL DEFAULT 'test' CHECK (dataset_split IN ('dev', 'test')),
    tags JSON NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

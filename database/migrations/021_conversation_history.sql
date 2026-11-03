ALTER TABLE talent_runs
ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(64);

ALTER TABLE talent_runs
ADD COLUMN IF NOT EXISTS turn_no INTEGER;

UPDATE talent_runs SET conversation_id = run_id WHERE conversation_id IS NULL;
UPDATE talent_runs SET turn_no = 1 WHERE turn_no IS NULL;

ALTER TABLE talent_runs ALTER COLUMN conversation_id SET NOT NULL;
ALTER TABLE talent_runs ALTER COLUMN turn_no SET NOT NULL;

ALTER TABLE talent_runs DROP COLUMN IF EXISTS resolved_request_text;
ALTER TABLE talent_runs DROP COLUMN IF EXISTS revision_action;
ALTER TABLE talent_runs DROP COLUMN IF EXISTS revision_summary;

CREATE INDEX IF NOT EXISTS idx_talent_runs_conversation_turn
ON talent_runs (conversation_id, turn_no);

CREATE TABLE IF NOT EXISTS talent_conversations (
    conversation_id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL,
    latest_run_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_talent_conversations_status_updated
ON talent_conversations (status, updated_at DESC);

COMMENT ON TABLE talent_conversations IS '前端会话列表的业务投影，不用于恢复 LangGraph State';

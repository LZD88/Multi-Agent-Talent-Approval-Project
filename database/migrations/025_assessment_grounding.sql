-- Additive and idempotent. Keep this column when rolling back application code.
ALTER TABLE talent_runs ADD COLUMN IF NOT EXISTS assessment_grounding JSON;

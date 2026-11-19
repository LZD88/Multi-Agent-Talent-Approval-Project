"""Verify additive migration twice against an isolated PostgreSQL schema, rollback."""
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text
from app.database import SessionLocal

migration = Path(__file__).resolve().parents[1] / 'database' / 'migrations' / '025_assessment_grounding.sql'
sql = migration.read_text()
with SessionLocal() as db:
    schema = 'grounding_check_'+uuid4().hex
    try:
        db.execute(text(f'CREATE SCHEMA {schema}'))
        db.execute(text(f'SET LOCAL search_path TO {schema}'))
        db.execute(text('CREATE TABLE talent_runs (run_id text primary key, report text)'))
        db.execute(text("INSERT INTO talent_runs VALUES ('legacy', '旧报告')"))
        db.execute(text(sql))
        db.execute(text(sql))
        row = db.execute(text('SELECT report, assessment_grounding FROM talent_runs')).one()
        assert row.report == '旧报告' and row.assessment_grounding is None
        db.execute(text("UPDATE talent_runs SET assessment_grounding = '{\"schema_version\":\"1.0\"}'"))
        assert db.execute(text('SELECT assessment_grounding FROM talent_runs')).scalar_one()['schema_version'] == '1.0'
        print('PostgreSQL: migration twice, legacy NULL, JSON roundtrip passed; isolated schema rolled back')
    finally:
        db.rollback()

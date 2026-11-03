"""Raw Cases and problem signals in the existing course-demo application."""
from typing import Literal
from datetime import datetime
from types import SimpleNamespace
from copy import deepcopy
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from app.config import get_settings
from app.evaluation_judge import judge_case, replay_output, output_schema, JUDGE_VERSION
from app.models import EvaluationAnnotationRecord as Annotation, EvaluationDatasetItemRecord as DatasetItem
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, exists, func
from app.database import SessionLocal
from app.evaluation_case import EvaluationModule
from app.models import EvaluationCaseRecord as Case, EvaluationFeedbackRecord as Feedback, TalentRunRecord, TalentConversationRecord

router = APIRouter(prefix='/api/evaluation', tags=['evaluation'])


class FeedbackInput(BaseModel):
    source_run_id: str = Field(min_length=1, max_length=64)
    case_id: UUID | None = None
    feedback_scope: Literal['module', 'run']
    note: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def check_scope(self):
        if (self.feedback_scope == 'module') != (self.case_id is not None):
            raise ValueError('模块反馈必须引用 Case，报告反馈仅关联 Run')
        return self


def feedback_json(row):
    return dict(feedback_id=str(row.id), source_run_id=row.source_run_id,
                case_id=str(row.case_id) if row.case_id else None,
                feedback_scope=row.feedback_scope, note=row.note, created_at=row.created_at.isoformat())


@router.post('/feedback', status_code=201)
def create_feedback(payload: FeedbackInput):
    with SessionLocal() as db:
        if db.get(TalentRunRecord, payload.source_run_id) is None:
            raise HTTPException(404, 'Run 不存在')
        if payload.case_id:
            case = db.get(Case, payload.case_id)
            if case is None or case.source_run_id != payload.source_run_id:
                raise HTTPException(404, '该 Run 下的 Case 不存在')
        row = Feedback(**payload.model_dump())
        db.add(row)
        db.commit()
        db.refresh(row)
        return feedback_json(row)


@router.get('/cases')
def list_cases(group_by: Literal['run'] | None = None, source_run_id: str | None = None, module_key: EvaluationModule | None = None,
               status: Literal['succeeded', 'failed'] | None = None,
               has_feedback: bool | None = None, unannotated: bool = False,
               max_score: int | None = Query(None, ge=0, le=4),
               created_after: datetime | None = None, created_before: datetime | None = None, offset: int = Query(0, ge=0),
               limit: int = Query(100, ge=1, le=200)):
    with SessionLocal() as db:
        direct = exists(select(Feedback.id).where(Feedback.case_id == Case.id))
        run_signal = exists(select(Feedback.id).where(Feedback.source_run_id == Case.source_run_id,
                                                      Feedback.feedback_scope == 'run'))
        stmt = select(Case, direct.label('has_feedback'), run_signal.label('run_has_feedback'), Annotation).outerjoin(Annotation, Annotation.case_id == Case.id)
        if unannotated:
            stmt = stmt.where(Annotation.case_id.is_(None))
        if max_score is not None:
            stmt = stmt.where(Annotation.score <= max_score)
        if created_after:
            stmt = stmt.where(Case.created_at >= created_after)
        if created_before:
            stmt = stmt.where(Case.created_at <= created_before)
        if source_run_id:
            stmt = stmt.where(Case.source_run_id == source_run_id)
        if module_key:
            stmt = stmt.where(Case.module_key == module_key.value)
        if status:
            stmt = stmt.where(Case.status == status)
        if has_feedback is not None:
            stmt = stmt.where(direct == has_feedback)
        if group_by == 'run':
            # Group the filtered calls before pagination so a Run is never split.
            matched = stmt.with_only_columns(Case.source_run_id, maintain_column_froms=True).subquery()
            counts = select(matched.c.source_run_id, func.count().label('case_count')).group_by(matched.c.source_run_id).subquery()
            groups = select(counts.c.source_run_id, counts.c.case_count,
                TalentRunRecord.completed_at, TalentConversationRecord.title.label('conversation_title')
            ).outerjoin(TalentRunRecord, TalentRunRecord.run_id == counts.c.source_run_id
            ).outerjoin(TalentConversationRecord, TalentConversationRecord.conversation_id == TalentRunRecord.conversation_id
            ).order_by(TalentRunRecord.completed_at.desc().nulls_last(), counts.c.source_run_id
            ).offset(offset).limit(limit + 1)
            rows = db.execute(groups).mappings().all()
            return {'has_more': len(rows) > limit, 'items': [dict(row) for row in rows[:limit]]}
        rows = db.execute(stmt.order_by(Case.created_at.desc(), Case.id).offset(offset).limit(limit + 1)).all()
        return {'has_more': len(rows) > limit, 'items': [dict(
            case_id=str(row.id), source_run_id=row.source_run_id, conversation_id=row.conversation_id,
            turn_no=row.turn_no, module_key=row.module_key, invocation_no=row.invocation_no,
            status=row.status, error_code=row.error_code, created_at=row.created_at.isoformat(),
            identity={k: row.input_payload[k] for k in ('candidate_id', 'dimension_number', 'requirement_id', 'attempt') if k in row.input_payload},
            has_feedback=signal, run_has_feedback=run_signal_value,
            annotation=row_json(annotation) if annotation else None,
        ) for row, signal, run_signal_value, annotation in rows[:limit]]}


@router.get('/cases/{case_id}')
def get_case(case_id: UUID):
    with SessionLocal() as db:
        row = db.get(Case, case_id)
        if row is None:
            raise HTTPException(404, 'Case 不存在')
        feedback = db.scalars(select(Feedback).where(Feedback.source_run_id == row.source_run_id).order_by(Feedback.created_at)).all()
        return {**{column.name: getattr(row, column.name) for column in Case.__table__.columns},
                'dataset_item': row_json(item) if (item := db.scalar(select(DatasetItem).where(DatasetItem.source_case_id == case_id))) else None,
                'annotation': row_json(db.get(Annotation, case_id)) if db.get(Annotation, case_id) else None,
                'feedback': [feedback_json(item) for item in feedback if item.case_id == row.id or item.feedback_scope == 'run']}


def row_json(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class JudgeBatch(BaseModel):
    case_ids: list[UUID] = Field(min_length=1, max_length=20)


@router.post('/judge')
def batch_judge(payload: JudgeBatch):
    results = []
    for case_id in dict.fromkeys(payload.case_ids):
        try:
            with SessionLocal() as db:
                case = db.get(Case, case_id)
                if case is None:
                    raise ValueError('Case 不存在')
                if db.get(Annotation, case_id): # 已标注的case跳过
                    results.append(dict(case_id=str(case_id), status='skipped'))
                    continue
                result = judge_case(case)
                db.add(Annotation(case_id=case_id, annotator_type='rule' if case.status == 'failed' else 'llm',
                    judge_version=JUDGE_VERSION, judge_model=get_settings().judge_model if case.status != 'failed' else None,
                    **result.model_dump()))
                db.commit()
                results.append(dict(case_id=str(case_id), status='annotated', score=result.score))
        except IntegrityError:
            results.append(dict(case_id=str(case_id), status='skipped'))
        except ValidationError:
            results.append(dict(case_id=str(case_id), status='error',
                error_type='ValidationError', error_stage='judge_response'))
        except Exception as exc:
            results.append(dict(case_id=str(case_id), status='error', error_type=type(exc).__name__))
    return {'items': results}


class ConfirmGolden(BaseModel):
    golden_output: dict
    dataset_split: Literal['dev', 'test'] = 'test'
    tags: list[str] = Field(default_factory=list, max_length=20)


@router.post('/cases/{case_id}/confirm')
def confirm_golden(case_id: UUID, payload: ConfirmGolden):
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if case is None:
            raise HTTPException(404, 'Case 不存在')
        if db.scalar(select(DatasetItem).where(DatasetItem.source_case_id == case_id)):
            raise HTTPException(409, '已确认的数据集条目不可覆盖')
        try:
            golden = output_schema(case.module_key).model_validate(payload.golden_output).model_dump(mode='json')
        except (ValidationError, KeyError):
            raise HTTPException(422, 'Golden 不符合模块输出 Schema')
        annotation = db.get(Annotation, case_id)
        if annotation is None:
            annotation = Annotation(case_id=case_id, annotator_type='human', judge_version='human-v1',
                verdict='unscored', reason='人工编写并确认参考答案', golden_candidate=None)
            db.add(annotation)
        annotation.review_status = 'confirmed'
        item = DatasetItem(source_case_id=case_id, module_key=case.module_key,
            input_payload=deepcopy(case.input_payload), golden_output=golden,
            replay_context=deepcopy(case.replay_context), dataset_split=payload.dataset_split, tags=payload.tags)
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, '该 Case 已被其他请求确认，请刷新')
        db.refresh(item)
        return row_json(item)


@router.get('/dataset')
def list_dataset(module_key: EvaluationModule | None = None, dataset_split: Literal['dev', 'test'] | None = None,
                 offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    with SessionLocal() as db:
        stmt = select(DatasetItem)
        if module_key:
            stmt = stmt.where(DatasetItem.module_key == module_key.value)
        if dataset_split:
            stmt = stmt.where(DatasetItem.dataset_split == dataset_split)
        rows = db.scalars(stmt.order_by(DatasetItem.created_at.desc(), DatasetItem.id).offset(offset).limit(limit + 1)).all()
        return {'items': [row_json(row) for row in rows[:limit]], 'has_more': len(rows) > limit}


class ReplayBatch(BaseModel):
    dataset_item_ids: list[UUID] = Field(min_length=1, max_length=20)


@router.post('/replay')
def batch_replay(payload: ReplayBatch):
    results = []
    for item_id in dict.fromkeys(payload.dataset_item_ids):
        with SessionLocal() as db:
            item = db.get(DatasetItem, item_id)
            if item is None:
                results.append(dict(dataset_item_id=str(item_id), status='error', error_type='MissingItem'))
                continue
            try:
                output = replay_output(item)
            except Exception as exc:
                results.append(dict(dataset_item_id=str(item_id), status='module_failed', score=0, error_type=type(exc).__name__))
                continue
            try:
                result = judge_case(SimpleNamespace(module_key=item.module_key, input_payload=item.input_payload,
                    output_payload=output, status='succeeded'), golden_output=item.golden_output)
                results.append(dict(dataset_item_id=str(item_id), status='scored', output=output, **result.model_dump(exclude={'golden_candidate'})))
            except Exception as exc:
                results.append(dict(dataset_item_id=str(item_id), status='judge_error', output=output, error_type=type(exc).__name__))
    scores = [row['score'] for row in results if 'score' in row]
    return dict(items=results, requested_count=len(results), scored_count=len(scores),
        complete=len(scores) == len(results), average_score=sum(scores)/len(scores) if scores else None,
        minimum_score=min(scores) if scores else None, judge_version=JUDGE_VERSION,
        judge_model=get_settings().judge_model, chat_model=get_settings().chat_model)

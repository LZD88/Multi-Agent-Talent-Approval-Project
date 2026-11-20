"""Frozen assessment fixtures; offline by default, model calls require --live."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import sys
from types import SimpleNamespace
from uuid import UUID

from app.assessment_grounding import (AssessmentClaim, ClaimReviewer, EvidenceAnchor,
    GroundingReview, POLICY_VERSION, PROMPT_VERSION, review_with)
from app.assessment_metrics import quality_metrics
from app.evaluation_judge import output_schema, replay_output, validate_golden_input

DATA = Path(__file__).resolve().parents[1] / 'samples' / 'assessment-grounding-v1'


def hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def load_cases(directory=DATA):
    raw = (directory / 'cases.jsonl').read_bytes()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(raw).hexdigest() == manifest['cases_sha256'], 'dataset_hash_mismatch'
    cases = [json.loads(line) for line in raw.decode().splitlines()]
    assert len(cases) >= 60 and len({row['case_id'] for row in cases}) == len(cases)
    assert Counter(row['split'] for row in cases) == {'dev': 40, 'test': 20}
    assert not ({r['scenario_id'] for r in cases if r['split'] == 'dev'} & {r['scenario_id'] for r in cases if r['split'] == 'test'})
    labels = Counter()
    for row, entry in zip(cases, manifest['cases'], strict=True):
        assert row['synthetic'] is True and row['input_payload']['schema_version'] == '1.0'
        assert entry['case_id'] == row['case_id'] and entry['input_hash'] == hash_json(row['input_payload'])
        output_schema(row['module_key']).model_validate(row['proposed_output'])
        validate_golden_input(row['module_key'], row['input_payload'], row['proposed_output'])
        if row['module_key'] == 'dimension_scoring':
            from app.talent_evaluation_dispatch import EvaluationDimension, BranchEvidenceDraft
            from app.talent_evaluation_report import EvidenceItem
            EvaluationDimension.model_validate(row['input_payload']['dimension'])
            BranchEvidenceDraft.model_validate(row['input_payload']['branch'])
            for item in row['input_payload']['evidence_items']:
                EvidenceItem.model_validate(item)
        if row['module_key'] == 'report_grounding':
            for anchor in row['input_payload']['anchors']:
                EvidenceAnchor.model_validate(anchor)
            if row['split'] == 'test':
                labels.update('supported' if r['verdict'] == 'supported' else 'unsupported' for r in row['proposed_output']['reviews'])
    assert sum(labels.values()) >= 60 and min(labels['supported'], labels['unsupported']) >= 20
    return manifest, cases


def import_pending(cases, manifest):
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import EvaluationCaseRecord as Case, EvaluationAnnotationRecord as Annotation
    imported = 0
    with SessionLocal() as db:
        for index, row in enumerate(cases, 1):
            run_id = 'synthetic-assessment-v1-'+row['split']
            existing = db.scalar(select(Case).where(Case.source_run_id == run_id, Case.invocation_no == index))
            if existing:
                annotation = db.get(Annotation, existing.id)
                if annotation and annotation.review_status != 'confirmed':
                    existing.input_payload = row['input_payload']
                    existing.version_snapshot = {'dataset': manifest['dataset_version'], 'dataset_hash': manifest['cases_sha256']}
                    annotation.golden_candidate = row['proposed_output']
                continue
            case = Case(source_run_id=run_id, conversation_id=run_id, turn_no=1,
                module_key=row['module_key'], invocation_no=index, input_payload=row['input_payload'],
                output_payload={}, status='failed', error_code='synthetic_fixture_not_executed',
                annotation_status='pending', replay_context={'synthetic': True, 'case_id': row['case_id'], 'split': row['split']},
                version_snapshot={'dataset': manifest['dataset_version'], 'dataset_hash': manifest['cases_sha256']})
            db.add(case)
            db.flush()
            db.add(Annotation(case_id=case.id, annotator_type='synthetic', judge_version='proposed-label-v1',
                verdict='needs_review', reason='虚构回归输入与候选参考答案，尚未执行模块且未经人工确认。',
                golden_candidate=row['proposed_output'], review_status='pending'))
            imported += 1
        db.commit()
    return imported


def export_confirmed(cases, manifest, path):
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import EvaluationCaseRecord as Case, EvaluationAnnotationRecord as Annotation, EvaluationDatasetItemRecord as Item
    by_id = {row['case_id']: row for row in cases}
    labels = {}
    with SessionLocal() as db:
        rows = db.execute(select(Case, Annotation, Item).join(Annotation, Annotation.case_id == Case.id)
                          .join(Item, Item.source_case_id == Case.id).where(Annotation.review_status == 'confirmed')).all()
        for case, annotation, item in rows:
            key = (case.replay_context or {}).get('case_id')
            fixture = by_id.get(key)
            if not fixture or annotation.annotator_type != 'human' or not (case.replay_context or {}).get('synthetic'):
                continue
            if (item.input_payload != fixture['input_payload'] or item.module_key != fixture['module_key']
                    or item.dataset_split != fixture['split']):
                continue  # An older confirmation stays in history and cannot label this manifest.
            labels[key] = dict(output=item.golden_output, input_hash=hash_json(item.input_payload),
                confirmation={'dataset_item_id': str(item.id), 'source_case_id': str(case.id), 'confirmed_at': item.created_at.isoformat()})
    payload = {'dataset_version': manifest['dataset_version'], 'cases_sha256': manifest['cases_sha256'],
               'labels': labels, 'labels_hash': hash_json(labels)}
    validate_confirmed(payload, manifest, cases)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return len(labels)


def validate_confirmed(payload, manifest, cases):
    if any(payload.get(key) != manifest[key] for key in ('dataset_version', 'cases_sha256')):
        raise ValueError('confirmed_dataset_mismatch')
    labels = payload.get('labels')
    if not isinstance(labels, dict) or hash_json(labels) != payload.get('labels_hash'):
        raise ValueError('confirmed_labels_hash_mismatch')
    fixtures = {row['case_id']: row for row in cases}
    for case_id, label in labels.items():
        fixture = fixtures.get(case_id)
        if fixture is None or label.get('input_hash') != hash_json(fixture['input_payload']):
            raise ValueError(f'confirmed_input_mismatch: {case_id}')
        confirmation = label.get('confirmation') or {}
        try:
            UUID(confirmation['dataset_item_id'])
            UUID(confirmation['source_case_id'])
            datetime.fromisoformat(confirmation['confirmed_at'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f'confirmed_metadata_invalid: {case_id}') from exc
        golden = output_schema(fixture['module_key']).model_validate(label['output']).model_dump(mode='json')
        validate_golden_input(fixture['module_key'], fixture['input_payload'], golden)
        if any(row['verdict'] == 'error' for row in golden.get('reviews', [])):
            raise ValueError(f'confirmed_label_is_technical_error: {case_id}')
    return labels


def execute_case(row, *, live=False, baseline=False, fault=False):
    started = time.monotonic()
    status, error, output = 'completed', None, None
    try:
        if row['input_payload'].get('schema_version') != '1.0':
            raise ValueError('incompatible_schema')
        if row['module_key'] == 'report_grounding':
            data = row['input_payload']
            claims = [AssessmentClaim.model_validate(r) for r in data['claims']]
            anchors = [EvidenceAnchor.model_validate(r) for r in data['anchors']]
            if live:
                from app.model_provider import get_grounding_model
                provider = get_grounding_model
                if baseline:
                    class BaselineModel:
                        def with_structured_output(self, *args, **kwargs):
                            model = get_grounding_model()
                            self.root_async_client = model.root_async_client
                            self.structured = model.with_structured_output(*args, **kwargs)
                            return self
                        async def ainvoke(self, messages):
                            return await self.structured.ainvoke([('system', '逐条判断命题是否被输入材料支持，返回GroundingReview。'), messages[1]])
                    provider = BaselineModel
                from app.config import get_settings
                settings = get_settings()
                reviewer = ClaimReviewer(provider, attempts=settings.grounding_max_attempts,
                    request_timeout=settings.grounding_request_timeout, batch_timeout=settings.grounding_batch_timeout,
                    concurrency=settings.grounding_concurrency)
            else:
                def reviewer(claims, anchors):
                    if fault:
                        raise TimeoutError('injected_outage')
                    by_id = {r['claim_id']: r for r in row['proposed_output']['reviews']}
                    return GroundingReview(reviews=[by_id[r.claim_id] for r in claims])
            output = asyncio.run(review_with(reviewer, claims, anchors, phase=data['phase'])).model_dump(mode='json')
        elif live:
            output = replay_output(SimpleNamespace(module_key=row['module_key'], input_payload=row['input_payload']))
        else:
            output = output_schema(row['module_key']).model_validate(row['proposed_output']).model_dump(mode='json')
            validate_golden_input(row['module_key'], row['input_payload'], output)
    except Exception as exc:
        status, error = ('incompatible' if 'incompatible' in str(exc) else 'execution_failed'), type(exc).__name__
    return dict(case_id=row['case_id'], input_hash=hash_json(row['input_payload']), module_key=row['module_key'],
        status=status, error_type=error, output=output, duration_ms=round((time.monotonic()-started)*1000))


def compare(previous, current):
    old = {r['case_id']: r for r in previous['items']}
    new = {r['case_id']: r for r in current['items']}
    common = sorted(key for key in old.keys() & new.keys() if old[key]['input_hash'] == new[key]['input_hash'])
    incompatible = sorted((old.keys() | new.keys()) - set(common))
    result = dict(common_case_ids=common, incompatible_case_ids=incompatible,
        changed_case_ids=[key for key in common if old[key]['output'] != new[key]['output']],
        execution_delta={
            'failed': sum(case_failed(new[key]) for key in common) - sum(case_failed(old[key]) for key in common),
            'duration_ms': sum(new[key]['duration_ms'] - old[key]['duration_ms'] for key in common),
        })
    compatible = (bool(common) and not incompatible and previous.get('mode') == current.get('mode')
                  and previous.get('confirmed_labels_hash') is not None
                  and previous.get('confirmed_labels_hash') == current.get('confirmed_labels_hash')
                  and previous.get('dataset', {}).get('cases_sha256') == current.get('dataset', {}).get('cases_sha256'))
    result['formal_metrics_compatible'] = compatible
    if compatible:
        old_metrics = previous['quality_metrics']['metrics']
        result['formal_metric_deltas'] = {
            key: None if old_metrics[key]['value'] is None or metric['value'] is None
            else metric['value'] - old_metrics[key]['value']
            for key, metric in current['quality_metrics']['metrics'].items()
        }
    old_retrieval, new_retrieval = previous.get('retrieval'), current.get('retrieval')
    if (old_retrieval and new_retrieval and old_retrieval.get('cases_hash') == new_retrieval.get('cases_hash')
            and old_retrieval['k'] == new_retrieval['k']):
        old_recall, new_recall = old_retrieval['recall_at_k']['value'], new_retrieval['recall_at_k']['value']
        result['retrieval_recall_delta'] = (new_recall - old_recall
                                            if old_recall is not None and new_recall is not None else None)
    return result


def case_failed(item):
    return item['status'] != 'completed' or any(
        row['verdict'] == 'error' for row in (item.get('output') or {}).get('reviews', []))


def offline_passed(report):
    diagnostics = report['provisional_diagnostics']
    metrics = diagnostics['metrics']
    retrieval = report['retrieval']
    return (diagnostics['complete'] and not report['execution']['failed_cases']
            and metrics['score_eligibility_agreement']['value'] == 1
            and metrics['published_claim_support']['value'] == 1
            and metrics['unsupported_claim_blocking']['value'] == 1
            and metrics['supported_claim_false_blocking']['value'] == 0
            and diagnostics['counts'].get('illegal_reference_published', 0) == 0
            and retrieval['recall_at_k']['value'] == 1
            and retrieval['missing_evidence_with_hits'] == 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='显式允许发送选中的虚构样本给配置模型')
    parser.add_argument('--baseline', action='store_true', help='仅真实模型：使用精简提示词对照，评分器保持相同')
    parser.add_argument('--fault', action='store_true', help='离线注入审查故障')
    parser.add_argument('--split', choices=['dev', 'test', 'all'], default='all')
    parser.add_argument('--case-id', action='append')
    parser.add_argument('--output', type=Path, default=Path('assessment-benchmark.json'))
    parser.add_argument('--confirmed', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--import-pending', action='store_true')
    parser.add_argument('--export-confirmed', type=Path)
    parser.add_argument('--check-offline', action='store_true', help='离线回归失败时返回非零状态')
    parser.add_argument('--require-passing', action='store_true', help='真实测试集正式验收未通过时返回非零状态')
    parser.add_argument('--chunk-size', type=int, default=800)
    parser.add_argument('--chunk-overlap', type=int, default=100)
    parser.add_argument('--retrieval-k', type=int, default=5)
    args = parser.parse_args()
    manifest, cases = load_cases()
    if args.import_pending:
        print(json.dumps({'imported_pending': import_pending(cases, manifest), 'confirmed': 0}))
        return
    if args.export_confirmed:
        print(json.dumps({'exported_confirmed': export_confirmed(cases, manifest, args.export_confirmed)}))
        return
    if args.check_offline and (args.live or args.fault or args.split != 'all' or args.case_id):
        parser.error('--check-offline requires the complete offline baseline')
    if args.require_passing and (not args.live or args.split != 'test' or args.case_id):
        parser.error('--require-passing requires the complete live test split')
    if args.retrieval_k <= 0 or args.chunk_size <= 0 or not 0 <= args.chunk_overlap < args.chunk_size:
        parser.error('invalid chunk size, overlap or retrieval k')
    selected = [row for row in cases if (args.split == 'all' or row['split'] == args.split) and (not args.case_id or row['case_id'] in args.case_id)]
    assert selected, 'no_selected_cases'
    confirmed_payload = json.loads(args.confirmed.read_text(encoding='utf-8')) if args.confirmed else None
    labels = validate_confirmed(confirmed_payload, manifest, cases) if confirmed_payload is not None else {}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as executor:
        items = list(executor.map(lambda row: execute_case(row, live=args.live, baseline=args.baseline, fault=args.fault), selected))
    rows, diagnostic_rows = [], []
    for fixture, result in zip(selected, items, strict=True):
        confirmed = labels.get(fixture['case_id'])
        base = dict(module_key=fixture['module_key'], actual=result['output'], status=result['status'], input_payload=fixture['input_payload'])
        valid_confirmation = confirmed is not None
        rows.append(dict(base, confirmed=valid_confirmation, golden=confirmed['output'] if valid_confirmation else fixture['proposed_output']))
        # Explicitly provisional diagnostics, never exported as formal Golden.
        diagnostic_rows.append(dict(base, confirmed=True, golden=fixture['proposed_output']))
    from scripts.benchmark_retrieval import run_retrieval_cases
    retrieval_fixture = json.loads((DATA / 'retrieval.json').read_text(encoding='utf-8'))
    retrieval = run_retrieval_cases(retrieval_fixture, chunk_size=args.chunk_size,
                                    chunk_overlap=args.chunk_overlap, limit=args.retrieval_k)
    retrieval['cases_hash'] = hash_json(retrieval_fixture)
    report = dict(dataset=manifest, mode='live' if args.live else 'offline_injected',
        policy_version=POLICY_VERSION, prompt_version='minimal-baseline-v0' if args.baseline else PROMPT_VERSION,
        model='', scope='仅选中的虚构样本；候选标签诊断不属于人工Golden验收。',
        confirmed_labels_hash=confirmed_payload['labels_hash'] if labels else None,
        execution={'requested_cases': len(items), 'failed_cases': sum(case_failed(item) for item in items)},
        retrieval=retrieval, duration_ms=round((time.monotonic()-started)*1000), items=items,
        quality_metrics=quality_metrics(rows), provisional_diagnostics=quality_metrics(diagnostic_rows))
    if args.live:
        from app.config import get_settings
        settings = get_settings()
        report['model'] = settings.grounding_model or settings.chat_model
        report['scoring_model'] = settings.chat_model
    if args.compare:
        report['comparison'] = compare(json.loads(args.compare.read_text(encoding='utf-8')), report)
    required = report['quality_metrics']['metrics']
    report['acceptance'] = 'incomplete'
    if (args.live and args.split == 'test' and not args.case_id and report['quality_metrics']['complete']
            and all(required[k]['value'] is not None for k in required)):
        passed = all(required[k]['value'] >= .95 for k in ('score_eligibility_agreement', 'published_claim_support', 'unsupported_claim_blocking'))
        passed &= required['supported_claim_false_blocking']['value'] <= .10
        passed &= report['quality_metrics']['counts'].get('illegal_reference_published', 0) == 0
        report['acceptance'] = 'passed' if passed else 'failed'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(args.output), cases=len(items), mode=report['mode'], acceptance=report['acceptance'],
        duration_ms=report['duration_ms'], formal=report['quality_metrics'], provisional=report['provisional_diagnostics']), ensure_ascii=False))
    if args.check_offline and not offline_passed(report):
        raise SystemExit(1)
    if args.require_passing and report['acceptance'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()

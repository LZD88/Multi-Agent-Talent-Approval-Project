from copy import deepcopy
from uuid import uuid4

import pytest

from scripts.benchmark_assessment import DATA, load_cases, execute_case, compare, hash_json, validate_confirmed


def test_manifest_split_and_offline_run_without_model_factory(monkeypatch):
    from app import model_provider
    monkeypatch.setattr(model_provider, 'get_grounding_model', lambda: (_ for _ in ()).throw(AssertionError('network')))
    _, cases = load_cases()
    assert len(cases) == 60
    assert all(execute_case(row)['status'] == 'completed' for row in cases)


def test_windows_and_linux_checkouts_keep_the_same_dataset_hash(tmp_path):
    manifest, cases = load_cases()
    fixture = tmp_path / 'cases.jsonl'
    (tmp_path / 'manifest.json').write_bytes((DATA / 'manifest.json').read_bytes())
    lf = (DATA / 'cases.jsonl').read_bytes().replace(b'\r\n', b'\n')
    for newline in (b'\n', b'\r\n'):
        fixture.write_bytes(lf.replace(b'\n', newline))
        assert load_cases(tmp_path) == (manifest, cases)
    fixture.write_bytes(lf.replace(b'synthetic', b'tampered', 1))
    with pytest.raises(AssertionError, match='dataset_hash_mismatch'):
        load_cases(tmp_path)


def test_fault_incompatible_and_common_sample_comparison():
    _, cases = load_cases()
    good = execute_case(cases[0])
    failed = execute_case(cases[0], fault=True)
    assert all(r['verdict'] == 'error' for r in failed['output']['reviews'])
    incompatible = deepcopy(cases[0])
    incompatible['input_payload']['schema_version'] = '99'
    assert execute_case(incompatible)['status'] == 'incompatible'
    comparison = compare({'items': [good]}, {'items': [failed, execute_case(cases[1])]})
    assert comparison['common_case_ids'] == [cases[0]['case_id']]
    assert comparison['changed_case_ids'] == [cases[0]['case_id']]
    assert comparison['incompatible_case_ids'] == [cases[1]['case_id']]


def test_frozen_label_export_rejects_tampering_and_wrong_dataset():
    manifest, cases = load_cases()
    row = cases[0]
    labels = {row['case_id']: {
        'output': row['proposed_output'], 'input_hash': hash_json(row['input_payload']),
        'confirmation': {'dataset_item_id': str(uuid4()), 'source_case_id': str(uuid4()),
                         'confirmed_at': '2026-11-20T00:00:00+00:00'},
    }}
    payload = {'dataset_version': manifest['dataset_version'], 'cases_sha256': manifest['cases_sha256'],
               'labels': labels, 'labels_hash': hash_json(labels)}
    assert validate_confirmed(payload, manifest, cases) == labels
    with pytest.raises(ValueError, match='confirmed_dataset_mismatch'):
        validate_confirmed({**payload, 'cases_sha256': 'stale'}, manifest, cases)
    modified = deepcopy(payload)
    modified['labels'][row['case_id']]['input_hash'] = 'other'
    with pytest.raises(ValueError, match='confirmed_labels_hash_mismatch'):
        validate_confirmed(modified, manifest, cases)
    modified['labels_hash'] = hash_json(modified['labels'])
    with pytest.raises(ValueError, match='confirmed_input_mismatch'):
        validate_confirmed(modified, manifest, cases)


def test_comparison_requires_same_confirmed_labels():
    manifest, cases = load_cases()
    item = execute_case(cases[0])
    def report(label_hash, value):
        return {'items': [item], 'mode': 'live', 'dataset': manifest, 'confirmed_labels_hash': label_hash,
                'quality_metrics': {'metrics': {'published_claim_support': {'value': value}}}}

    assert 'formal_metric_deltas' not in compare(report('old', .5), report('new', 1))
    delta = compare(report('same', .5), report('same', 1))
    assert delta['formal_metric_deltas']['published_claim_support'] == .5

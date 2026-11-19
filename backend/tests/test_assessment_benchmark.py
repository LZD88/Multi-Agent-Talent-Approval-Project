from copy import deepcopy

from scripts.benchmark_assessment import load_cases, execute_case, compare


def test_manifest_split_and_offline_run_without_model_factory(monkeypatch):
    from app import model_provider
    monkeypatch.setattr(model_provider, 'get_grounding_model', lambda: (_ for _ in ()).throw(AssertionError('network')))
    _, cases = load_cases()
    assert len(cases) == 60
    assert all(execute_case(row)['status'] == 'completed' for row in cases)


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

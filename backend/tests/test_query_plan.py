from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.query_plan import (
    FilterCondition,
    QueryPlan,
    SemanticRequirement,
    build_candidate_statement,
    choose_optimization_strategy,
    compile_query_plan,
    execute_composite_search,
    filter_dsl_catalog,
    merge_query_results,
    optimize_semantic_query,
    search_with_optimization,
)


def _plan(*filters, clarifications=None):
    return QueryPlan(
        task_type="find_talent",
        filters=list(filters),
        semantic_requirements=[SemanticRequirement(requirement_id="S1", query="AI 项目工作经历")],
        clarifications=clarifications or [],
    )


def test_builds_parameterized_candidate_query_with_system_tenant():
    plan = _plan(
        FilterCondition(field="age", operator="lt", value=35),
        FilterCondition(field="region", operator="eq", value="深圳"),
    )
    statement = build_candidate_statement(plan, tenant_id="course-demo", today=date(2026, 9, 6))
    compiled = statement.compile(dialect=postgresql.dialect())

    assert "tenant_id = %(tenant_id_1)s" in str(compiled)
    assert "birth_date > %(birth_date_1)s" in str(compiled)
    assert "region = %(region_1)s" in str(compiled)
    assert compiled.params["birth_date_1"] == date(1991, 9, 6)
    assert compiled.params["region_1"] == "深圳"


def test_rejects_unknown_filter_field():
    with pytest.raises(ValidationError):
        FilterCondition(field="salary", operator="gt", value=10000)


def test_refuses_to_execute_plan_with_clarification():
    plan = _plan(clarifications=[{"expression": "比较资深", "reason": "缺少年限或职级阈值"}])
    with pytest.raises(ValueError, match="待澄清"):
        build_candidate_statement(plan, tenant_id="course-demo")


def test_empty_candidate_set_stops_before_semantic_search():
    calls = []
    result = execute_composite_search(
        _plan(),
        candidate_ids=[],
        search_requirement=lambda **kwargs: calls.append(kwargs),
    )

    assert result["candidate_ids"] == []
    assert result["evidence_by_requirement"] == {}
    assert calls == []


def test_each_semantic_requirement_keeps_its_own_evidence():
    plan = QueryPlan(
        task_type="find_talent",
        semantic_requirements=[
            {"requirement_id": "S1", "query": "AI 项目经历"},
            {"requirement_id": "S2", "query": "团队管理经历"},
        ],
    )
    result = execute_composite_search(
        plan,
        candidate_ids=["C001"],
        search_requirement=lambda query, candidate_ids: [{"query": query, "candidate_ids": candidate_ids}],
    )

    assert set(result["evidence_by_requirement"]) == {"S1", "S2"}
    assert result["evidence_by_requirement"]["S2"][0]["candidate_ids"] == ["C001"]


def test_filter_registry_is_also_sent_to_query_compiler():
    seen = {}

    class StructuredModel:
        def invoke(self, messages):
            seen["messages"] = messages
            return _plan()

    class Model:
        def with_structured_output(self, schema, **kwargs):
            seen["schema"] = schema
            return StructuredModel()

    compile_query_plan("35 岁以下", Model())
    filter_dsl = filter_dsl_catalog()
    print(filter_dsl)
    assert seen["schema"] is QueryPlan
    assert "age: 年龄" in seen["messages"][0][1]
    assert "region: 工作地区" in filter_dsl


def test_query_optimization_is_triggered_by_failure_type():
    assert choose_optimization_strategy(hit_count=2, requirement_count=1) is None
    assert choose_optimization_strategy(hit_count=0, requirement_count=1, expression_is_vague=True) == "rewrite"
    assert choose_optimization_strategy(hit_count=0, requirement_count=1) == "multi_query"
    assert choose_optimization_strategy(hit_count=0, requirement_count=2) == "decompose"


def test_optimizer_uses_structured_output_and_keeps_strategy():
    class StructuredModel:
        def invoke(self, messages):
            return {"strategy": "multi_query", "queries": ["AI 项目经历", "大模型项目经验"], "reason": "无召回"}

    class Model:
        def with_structured_output(self, schema, **kwargs):
            class Adapter(StructuredModel):
                def invoke(self, messages):
                    return schema.model_validate(super().invoke(messages))
            return Adapter()

    result = optimize_semantic_query(
        SemanticRequirement(requirement_id="S1", query="AI 相关工作"),
        strategy="multi_query",
        model=Model(),
    )
    assert result.queries == ["AI 项目经历", "大模型项目经验"]


def test_optimized_query_results_are_deduplicated_by_chunk():
    merged = merge_query_results(
        [
            [{"chunk_id": "A", "score": 0.9}, {"chunk_id": "B", "score": 0.8}],
            [{"chunk_id": "A", "score": 0.7}, {"chunk_id": "C", "score": 0.6}],
        ]
    )
    assert [item["chunk_id"] for item in merged] == ["A", "B", "C"]


def test_failed_first_pass_runs_generated_queries_and_merges_results(monkeypatch):
    monkeypatch.setattr(
        "app.query_plan.optimize_semantic_query",
        lambda requirement, strategy, model: type(
            "Optimization", (), {"strategy": strategy, "queries": ["机器学习项目", "大模型项目"]}
        )(),
    )
    responses = {
        "AI 相关工作": [],
        "机器学习项目": [{"chunk_id": "A"}],
        "大模型项目": [{"chunk_id": "A"}, {"chunk_id": "B"}],
    }

    result = search_with_optimization(
        SemanticRequirement(requirement_id="S1", query="AI 相关工作"),
        search_query=lambda query: responses[query],
        model=object(),
    )

    assert result["strategy"] == "multi_query"
    assert result["queries"] == ["AI 相关工作", "机器学习项目", "大模型项目"]
    assert [item["chunk_id"] for item in result["results"]] == ["A", "B"]

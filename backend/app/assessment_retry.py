"""Retry a selected assessment failure against the original evidence."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

from app.assessment_grounding import AssessmentClaim, stable_id
from app.talent_decision_graph import DecisionContext


class RetryUnavailable(ValueError):
    """The request cannot be applied to the current report."""


def retry_items(state):
    options = []
    dimensions = state["dimensions"]
    assessments = {row["task_id"]: row for row in state["dimension_assessments"]}
    review_errors = {row["claim_id"]: row["reason_code"] for row in state["grounding_reviews"]
                     if row["verdict"] == "error"}

    def option(kind, target, candidate, numbers, codes):
        options.append({
            "item_id": stable_id(kind, target),
            "kind": kind,
            "target": target,
            "candidate_id": candidate,
            "dimension_names": [dimensions[number - 1]["name"] for number in numbers],
            "reason_codes": sorted(set(codes)),
        })

    scorable = {row["task_id"] for row in state.get("branch_results", [])
                if row["execution_status"] != "failed"}
    dimension_review_errors = {}
    for claim in state["grounding_claims"]:
        claim_id = claim["claim_id"]
        if claim_id not in review_errors:
            continue
        candidate = claim["candidate_ids"][0]
        numbers = claim["dimension_numbers"]
        task_id = f"{candidate}:{numbers[0]}" if len(numbers) == 1 else ""
        if task_id in assessments and claim_id.startswith(task_id + ":"):
            dimension_review_errors.setdefault(task_id, []).append(review_errors[claim_id])
        else:
            option("report_review", claim_id, candidate, numbers, [review_errors[claim_id]])

    for task_id, row in assessments.items():
        if row["evidence_status"] != "failed" or task_id not in scorable:
            continue
        candidate, number = row["candidate_id"], row["dimension_number"]
        if task_id in dimension_review_errors:
            option("dimension_review", task_id, candidate, [number], dimension_review_errors[task_id])
        elif row.get("reason_code") != "score_grounding_rejected":
            option("scoring", task_id, candidate, [number], [row.get("reason_code") or "model_unavailable"])
    if state.get("report_error"):
        option("report_writer", "report", None, [], ["report_writer_unavailable"])
    else:
        for candidate in state.get("candidate_report_errors", {}):
            option("report_writer", candidate, candidate, [], ["report_writer_unavailable"])
    return options


def freeze_retry_state(state):
    keys = (
        "dimensions", "branch_results", "evidence_items", "dimension_assessments",
        "original_assessments", "consistency_issues", "candidate_assessments", "ranking",
        "report_draft", "report_error", "grounding_claims", "grounding_reviews",
        "assessment_context", "retry_revision", "candidate_report_errors",
    )
    frozen = {key: state[key] for key in keys if key in state}
    return {"version": 1, "state": frozen}


def _replace(rows, replacements, key):
    return [replacements.get(row[key], row) for row in rows]


def _merge_reviews(state, updated, removed):
    for key in ("grounding_claims", "grounding_reviews"):
        state[key] = [row for row in state[key] if row["claim_id"] not in removed] + updated[key]


class AssessmentRetryRunner:
    def __init__(self, *, dimension_scorer, consistency_reviewer, report_writer, claim_reviewer):
        from app.assessment_graph_nodes import make_grounding_nodes
        from app.talent_evaluation_report import _compose_report, _review_consistency, _score_dimensions

        self.score = _score_dimensions(dimension_scorer)
        self.consistency = _review_consistency(consistency_reviewer)
        self.compose = _compose_report(report_writer)
        self.ground_dimensions, self.ground_report = make_grounding_nodes(claim_reviewer)
        self.reviewer = claim_reviewer

    async def retry(self, frozen, item_id: str, revision: int):
        from app.assessment_graph_nodes import (
            render_grounded_report, review_with_reuse, verification_node,
        )

        if frozen.get("version") != 1:
            raise RetryUnavailable("此报告的重试数据版本不兼容")
        state = deepcopy(frozen["state"])
        option = next((item for item in retry_items(state) if item["item_id"] == item_id), None)
        if option is None:
            raise RetryUnavailable("该事项已处理或无法局部重试")
        claims = {row["claim_id"]: row for row in state["grounding_claims"]}
        state["reusable_reviews"] = {
            row["claim_id"]: (claims[row["claim_id"]], row)
            for row in state["grounding_reviews"] if row["verdict"] != "error"
        }
        target = option["target"]
        if option["kind"] == "report_review":
            claim = AssessmentClaim.model_validate(claims[target])
            review, = await review_with_reuse(self.reviewer, [claim], state, phase="report")
            state["grounding_reviews"] = _replace(state["grounding_reviews"], {target: review}, "claim_id")
        elif option["kind"] == "report_writer":
            await self._refresh_draft(state, option["candidate_id"])
        else:
            await self._retry_dimension(state, option)
        state["retry_revision"] = revision + 1
        state.update(verification_node(state))
        return render_grounded_report(state)

    async def _retry_dimension(self, state, option):
        from app.talent_evaluation_report import _aggregate_candidates

        candidate, task_id = option["candidate_id"], option["target"]
        previous = next(row for row in state["dimension_assessments"] if row["task_id"] == task_id)
        original = next(row for row in state["original_assessments"] if row["task_id"] == task_id)
        if option["kind"] == "scoring":
            branch = next(row for row in state["branch_results"] if row["task_id"] == task_id)
            context = DecisionContext(**state["assessment_context"]) if state.get("assessment_context") else None
            scored = await asyncio.to_thread(self.score, {**state, "branch_results": [branch]},
                                             SimpleNamespace(context=context))
            original, = scored["dimension_assessments"]
        grounded = await self.ground_dimensions.ainvoke({**state, "dimension_assessments": [original]})
        assessment, = grounded["dimension_assessments"]
        state["original_assessments"] = _replace(state["original_assessments"], {task_id: original}, "task_id")
        state["dimension_assessments"] = _replace(state["dimension_assessments"], {task_id: assessment}, "task_id")
        removed = {row["claim_id"] for row in state["grounding_claims"]
                   if row["claim_id"].startswith(task_id + ":")}
        _merge_reviews(state, grounded, removed)
        if previous["score"] != assessment["score"]:
            result = await asyncio.to_thread(self.consistency, {
                **state, "dimension_assessments": [row for row in state["dimension_assessments"]
                                                   if row["candidate_id"] == candidate],
            })
            state["consistency_issues"] = [
                row for row in state["consistency_issues"] if row["candidate_id"] != candidate
            ] + result["consistency_issues"]
        state.update(_aggregate_candidates(state))
        if assessment["score"] is not None:
            await self._refresh_draft(state, candidate)

    async def _refresh_draft(self, state, candidate=None):
        candidates = [row for row in state["candidate_assessments"]
                      if candidate is None or row["candidate_id"] == candidate]
        candidate_ids = [row["candidate_id"] for row in candidates]
        result = await asyncio.to_thread(self.compose, {
            **state, "candidate_assessments": candidates, "ranking": candidate_ids,
            "evidence_items": [row for row in state["evidence_items"] if row["candidate_id"] in candidate_ids],
        })
        draft = result["report_draft"]
        errors = state.setdefault("candidate_report_errors", {})
        if result["report_error"]:
            if candidate is None:
                state["report_error"] = result["report_error"]
            else:
                errors[candidate] = result["report_error"]
            return  # The previous draft still uses valid, unchanged evidence.
        if candidate is None:
            errors.clear()
        else:
            errors.pop(candidate, None)
        original = {row["candidate_id"]: row for row in state["report_draft"]["candidates"]}
        for row in draft["candidates"]:
            prior = original[row["candidate_id"]]
            # Keep existing claim positions. Only add novel points up to the report's section limit.
            for section in ("highlights", "risk_points"):
                points = list(prior.get(section, []))
                for point in row.get(section, []):
                    if len(points) < 8 and point not in points:
                        points.append(point)
                row[section] = points
        original.update({row["candidate_id"]: row for row in draft["candidates"]})
        state["report_draft"] = {**state["report_draft"], "candidates": [original[cid] for cid in state["ranking"]]}
        if candidate is None:
            state["report_error"] = ""
        removed = {row["claim_id"] for row in state["grounding_claims"]
                   if (":highlights:" in row["claim_id"] or ":risk_points:" in row["claim_id"])
                   and (candidate is None or row["candidate_ids"] == [candidate])}
        grounded = await self.ground_report.ainvoke({
            **state, "report_draft": draft, "grounding_claims": [], "grounding_reviews": [],
        })
        _merge_reviews(state, grounded, removed)

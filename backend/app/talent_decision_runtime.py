from __future__ import annotations

from app.talent_decision_engine import build_talent_decision_engine
from app.talent_evaluation_runtime import graph as evaluation_graph
from app.talent_evaluation_runtime import report_graph
from app.talent_request_graph import graph as request_graph


def build_runtime_decision_graph(*, checkpointer=None):
    """Build the integrated graph for Agent Server or the FastAPI run service."""

    return build_talent_decision_engine(
        request_graph=request_graph,
        evaluation_graph=evaluation_graph,
        report_graph=report_graph,
        checkpointer=checkpointer,
    )


# Agent Server provides the parent checkpointer. The FastAPI run service calls
# build_runtime_decision_graph with its own checkpointer so interrupt/resume uses
# the same graph thread instead of an API-layer simulation.
graph = build_runtime_decision_graph()

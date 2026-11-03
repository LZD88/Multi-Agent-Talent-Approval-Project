"""Demonstrate LangChain PIIMiddleware without calling an external model."""
from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import PIIDetectionError, PIIMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult


SAMPLE_TEXT = "候选人邮箱 zhangwei@example.com，有 5 年 Python 经验"


class EchoUserModel(BaseChatModel):
    """Return the last user message so the middleware result stays visible."""

    @property
    def _llm_type(self) -> str:
        return "course-demo-echo-model"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        user_content = next(
            message.content
            for message in reversed(messages)
            if isinstance(message, HumanMessage)
        )
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=f"模型收到：{user_content}"))
            ]
        )


def _run_strategy(strategy: str) -> str:
    agent = create_agent(
        EchoUserModel(),
        tools=[],
        middleware=[PIIMiddleware("email", strategy=strategy)],
    )
    result = agent.invoke({"messages": [{"role": "user", "content": SAMPLE_TEXT}]})
    return result["messages"][-1].content


def run_demo() -> dict[str, str]:
    results = {"original": SAMPLE_TEXT}
    for strategy in ("redact", "mask", "hash", "block"):
        try:
            results[strategy] = _run_strategy(strategy)
        except PIIDetectionError as exc:
            results[strategy] = f"{type(exc).__name__}: {exc}"
    return results


def main() -> None:
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

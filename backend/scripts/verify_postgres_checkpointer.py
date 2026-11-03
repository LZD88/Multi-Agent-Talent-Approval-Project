from __future__ import annotations

from uuid import uuid4

import psycopg
from langchain.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import START, MessagesState, StateGraph

from app.config import get_settings
from app.talent_checkpoint import checkpoint_dsn


CHECKPOINT_TABLES = (
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)


def reply(state: MessagesState) -> dict:
    latest = str(state["messages"][-1].content)
    print(f"reply节点收到消息：{latest}")
    return {"messages": [AIMessage(content=f"已记住：{latest}")]}


def main() -> None:
    dsn = checkpoint_dsn(get_settings().database_url)
    thread_id = f"lesson21-{uuid4()}"
    builder = StateGraph(MessagesState)
    builder.add_node("reply", reply)
    builder.add_edge(START, "reply")

    with PostgresSaver.from_conn_string(dsn) as checkpointer:
        checkpointer.setup()
        graph = builder.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        graph.invoke(
            {"messages": [HumanMessage(content="请帮我筛选上海的 AI 工程师")]},
            config,
        )
        graph.invoke(
            {"messages": [HumanMessage(content="北京的也行")]},
            config,
        )
        messages = graph.get_state(config).values["messages"]

    print(f"thread_id={thread_id}")
    print("messages=" + " | ".join(str(message.content) for message in messages))
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            for table in CHECKPOINT_TABLES:
                cursor.execute(f"SELECT count(*) FROM {table}")
                print(f"{table}={cursor.fetchone()[0]}")


if __name__ == "__main__":
    main()

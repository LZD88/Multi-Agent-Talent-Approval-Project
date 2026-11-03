from types import SimpleNamespace
from uuid import uuid4

from app import worker


class FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_failed_queue_item_does_not_escape_worker(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(worker, "ObjectStore", lambda: object())

    def parse_version(*_args):
        calls.append("parse")
        raise ValueError("document version was deleted")

    monkeypatch.setattr(worker, "parse_version", parse_version)

    worker.process_item(
        redis=object(),
        settings=SimpleNamespace(),
        queue_name="talent:parse:queue",
        payload=f"{uuid4()}:{uuid4()}",
    )

    assert calls == ["parse"]

from app.talent_checkpoint import checkpoint_dsn


def test_checkpoint_dsn_removes_sqlalchemy_driver_name():
    assert checkpoint_dsn(
        "postgresql+psycopg://talent:secret@localhost:15432/talent_docs"
    ) == "postgresql://talent:secret@localhost:15432/talent_docs"

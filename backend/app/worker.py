import logging
from uuid import UUID

from redis import Redis
from redis.exceptions import TimeoutError

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.document_pipeline import continue_document_pipeline
from app.evidence_index_service import run_index_job
from app.logging_config import configure_logging
from app.object_store import ObjectStore
from app.services import parse_version


logger = logging.getLogger(__name__)


def process_item(redis: Redis, settings: Settings, queue_name: str, payload: str) -> None:
    """Process one queue item without allowing it to terminate the worker."""
    try:
        job_id, version_id = payload.split(":", 1)
        logger.info("worker_job_received queue=%s job_id=%s version_id=%s", queue_name, job_id, version_id)
        with SessionLocal() as db:
            if queue_name == "talent:index:queue":
                job = run_index_job(db, UUID(job_id), UUID(version_id))
                logger.info("worker_index_job_completed job_id=%s status=%s indexed_count=%s", job.id, job.status, job.indexed_count)
            else:
                job = parse_version(db, ObjectStore(), UUID(version_id), UUID(job_id))
                logger.info("worker_parse_job_completed job_id=%s status=%s parser=%s", job.id, job.status, job.parser_name)
                if job.status.value == "succeeded":
                    index_job = continue_document_pipeline(
                        db,
                        store=ObjectStore(),
                        redis_client=redis,
                        version_id=UUID(version_id),
                        settings=settings,
                    )
                    logger.info(
                        "worker_pipeline_continued version_id=%s index_job_id=%s status=%s",
                        version_id,
                        index_job.id,
                        index_job.status,
                    )
    except Exception:
        logger.exception("worker_job_failed queue=%s payload=%s", queue_name, payload)


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_dir, "worker")
    redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=15)
    queues = ["talent:parse:queue", "talent:index:queue"]
    logger.info("worker_started queues=%s", ",".join(queues))
    while True:
        try:
            item = redis.blpop(queues, timeout=5)
        except TimeoutError:
            continue
        except Exception:
            logger.exception("worker_queue_read_failed queue=talent:parse:queue")
            raise
        if not item:
            continue
        queue_name, payload = item
        process_item(redis, settings, queue_name, payload)


if __name__ == "__main__":
    run()

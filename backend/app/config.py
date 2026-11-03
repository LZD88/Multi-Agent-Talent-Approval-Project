from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://talent:talent_dev_password@127.0.0.1:15432/talent_docs"
    redis_url: str = "redis://127.0.0.1:16379/0"
    s3_endpoint_url: str = "http://127.0.0.1:19000"
    s3_public_endpoint_url: str = "http://127.0.0.1:19000"
    s3_access_key: str = "talent_minio"
    s3_secret_key: str = "talent_minio_password"
    s3_bucket: str = "talent-documents"
    cors_origins: str = "http://localhost:15173,http://127.0.0.1:15173"
    mineru_url: str = "http://127.0.0.1:18001"
    dashscope_api_key: str = ""
    chat_model: str = "qwen-plus"
    judge_model: str = ""
    judge_api_key: str = ""
    judge_base_url: str = ""
    embedding_model: str = "text-embedding-v3"
    embedding_dimension: int = 1024
    rerank_model: str = "gte-rerank-v2"
    rerank_top_n: int = 20
    milvus_uri: str = "http://127.0.0.1:19531"
    milvus_token: str = "root:Milvus"
    milvus_collection: str = "talent_evidence_v2"
    log_dir: str = "logs"
    service_name: str = "backend"


@lru_cache
def get_settings() -> Settings:
    return Settings()

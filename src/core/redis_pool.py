import redis.asyncio as redis
from src.core.config import settings

# 全局异步 Redis 客户端实例
redis_client = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50  # 结合高并发设计要求，设置合理的最大连接数
)

async def get_redis() -> redis.Redis:
    """提供给 FastAPI 依赖注入或作为异步任务中的 Redis 获取工具"""
    return redis_client

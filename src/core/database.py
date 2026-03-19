from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.config import settings

# 针对PgBouncer配置了相应的连接池策略，例如pool_size和max_overflow
# 如果测试环境是sqlite，就不配置pool_size
engine_args = {}
if settings.DATABASE_URL.startswith("postgresql"):
    engine_args["pool_size"] = 20
    engine_args["max_overflow"] = 50

engine = create_engine(
    settings.DATABASE_URL,
    **engine_args
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

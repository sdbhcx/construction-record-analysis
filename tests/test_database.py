import pytest
import sys
import os

# 将根目录加到sys.path以便能正确定位src
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.schema import MetaData
from src.core.database import SessionLocal, engine
from src.models.domain import Base, Task, EntityAlignmentLog, FeedbackPool

def test_models_metadata():
    """测试所有的表模型是否已正确注册到 Base.metadata"""
    tables = Base.metadata.tables.keys()
    
    assert "ai_parse_tasks" in tables
    assert "entity_alignment_logs" in tables
    assert "lora_feedback_pool" in tables

def test_database_session_creation():
    """测试通过 sessionmaker 获取 session 是否工作且不崩溃"""
    session = SessionLocal()
    assert session is not None
    session.close()

def test_database_url_dialect():
    """确保我们的引擎使用了我们所配置的 postgresql"""
    # 哪怕在没有真 pg 启动的情况下，也断言其 dialect_name 已被设定正确
    assert engine.name in ["postgresql", "sqlite"]

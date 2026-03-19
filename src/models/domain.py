import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Integer, Numeric, Text, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

Base = declarative_base()

class Task(Base):
    __tablename__ = "ai_parse_tasks"

    task_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    biz_flow_id = Column(String(128), nullable=False)
    source_type = Column(String(20), nullable=False)
    file_url = Column(String(512), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    extracted_json = Column(JSONB, nullable=True)
    aligned_json = Column(JSONB, nullable=True)
    confidence = Column(Numeric(5, 4), nullable=True)
    need_human_review = Column(Boolean, default=False)
    target_legacy_id = Column(String(64), nullable=True)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    alignment_logs = relationship("EntityAlignmentLog", back_populates="task", cascade="all, delete")
    feedback_pool = relationship("FeedbackPool", back_populates="task", cascade="all, delete")


class EntityAlignmentLog(Base):
    __tablename__ = "entity_alignment_logs"

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("ai_parse_tasks.task_id"), nullable=False)
    field_name = Column(String(64), nullable=False)
    original_text = Column(String(255), nullable=False)
    aligned_id = Column(String(64), nullable=True)
    aligned_name = Column(String(255), nullable=True)
    match_level = Column(Integer, nullable=False)
    score = Column(Numeric(6, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    task = relationship("Task", back_populates="alignment_logs")


class FeedbackPool(Base):
    __tablename__ = "lora_feedback_pool"

    feedback_id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(UUID(as_uuid=True), ForeignKey("ai_parse_tasks.task_id"), nullable=False)
    ai_predicted_json = Column(JSONB, nullable=False)
    human_corrected_json = Column(JSONB, nullable=False)
    diff_score = Column(Numeric(6, 4), nullable=True)
    pii_cleaned = Column(Boolean, default=False)
    is_processed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    task = relationship("Task", back_populates="feedback_pool")

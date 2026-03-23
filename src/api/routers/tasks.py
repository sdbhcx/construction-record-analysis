import uuid
from fastapi import APIRouter, status, HTTPException
from pydantic import BaseModel, AnyHttpUrl
from typing import Optional

router = APIRouter(prefix="/tasks", tags=["Tasks"])

class CreateTaskRequest(BaseModel):
    biz_flow_id: str
    source_type: str
    file_url: str 
    callback_url: Optional[str] = None

class CreateTaskResponse(BaseModel):
    task_id: uuid.UUID
    status: str

class TaskStatusResponse(BaseModel):
    task_id: uuid.UUID
    status: str
    # 将来可扩展 confidence, aligned_json 等字段

@router.post("", response_model=CreateTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(req: CreateTaskRequest):
    """
    接收多模态文件解析任务，生成处于 PENDING 状态的任务记录。
    """
    valid_sources = ["audio", "image", "pdf", "excel"]
    if req.source_type not in valid_sources:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid source_type. Must be one of {valid_sources}"
        )
        
    task_id = uuid.uuid4()
    # TODO: 1.落库到 postgres (状态为 PENDING) 
    # TODO: 2.使用 Celery send_task 派发异步任务
    
    return CreateTaskResponse(task_id=task_id, status="PENDING")

@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: uuid.UUID):
    """
    查询任务的执行流转状态与解析结果。
    """
    # TODO: 实际应从数据库查询对应的任务实体
    return TaskStatusResponse(task_id=task_id, status="PENDING")

import uuid
from fastapi.testclient import TestClient
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import app
from src.core.config import settings

client = TestClient(app)
API_V1_STR = settings.API_V1_STR

def test_create_task_success():
    payload = {
        "biz_flow_id": "test_flow_123",
        "source_type": "image",
        "file_url": "http://minio/1.jpg"
    }
    response = client.post(f"{API_V1_STR}/tasks", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "PENDING"

def test_create_task_invalid_type():
    payload = {
        "biz_flow_id": "test_flow_123",
        "source_type": "video",  # invalid
        "file_url": "http://minio/1.mp4"
    }
    response = client.post(f"{API_V1_STR}/tasks", json=payload)
    assert response.status_code == 400

def test_get_task_status():
    task_id = str(uuid.uuid4())
    response = client.get(f"{API_V1_STR}/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["status"] == "PENDING"

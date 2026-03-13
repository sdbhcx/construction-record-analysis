import sys
import os
import pytest
from fastapi.testclient import TestClient

# 将根目录加到sys.path以便能正确定位src
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import app
from src.core.config import settings

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "project": settings.PROJECT_NAME}

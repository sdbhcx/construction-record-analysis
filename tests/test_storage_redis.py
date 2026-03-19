import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# 将根目录加到sys.path以便能正确定位src
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.redis_pool import get_redis
from src.core.storage import storage_client, MinioAdapter

@pytest.mark.asyncio
async def test_redis_client_init():
    """测试 Redis 客户端初始化与URL配置获取"""
    client = await get_redis()
    assert client is not None
    # 验证是否正确解析了我们设定在 config 里面的 redis://localhost 参数
    assert client.connection_pool.connection_kwargs.get("host") in ["localhost", "127.0.0.1"]

@patch("src.core.storage.Minio")
def test_minio_client_init(mock_minio):
    """测试 MinIO Adapter 初始化与方法签名可用性"""
    assert storage_client is not None
    assert hasattr(storage_client, "upload_file_bytes")
    assert hasattr(storage_client, "get_presigned_url")

def test_minio_upload_method_call():
    """测试单例中 MinIO put_object 的组装"""
    # 模拟客户端以防连接报错
    adapter = MinioAdapter()
    adapter.client = MagicMock()
    adapter.client.bucket_exists.return_value = True

    # 执行虚拟发包
    test_bytes = b"test_image_data"
    result = adapter.upload_file_bytes("test_bucket", "test_obj.jpg", test_bytes)

    assert result == "test_obj.jpg"
    adapter.client.put_object.assert_called_once()

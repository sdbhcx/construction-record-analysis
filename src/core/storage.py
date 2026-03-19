import io
from minio import Minio
from datetime import timedelta
from src.core.config import settings

class MinioAdapter:
    def __init__(self):
        # 初始化 MinIO 客户端
        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE
        )

    def ensure_bucket(self, bucket_name: str):
        """确保指定的存储桶存在"""
        if not self.client.bucket_exists(bucket_name):
            self.client.make_bucket(bucket_name)

    def upload_file_bytes(self, bucket_name: str, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """从内存态直接流式上传字节对象到 MinIO（满足设计中零拷贝的需求）"""
        self.ensure_bucket(bucket_name)
        data_stream = io.BytesIO(data)
        
        # 使用部分流直传机制降低写入消耗
        self.client.put_object(
            bucket_name=bucket_name,
            object_name=object_name,
            data=data_stream,
            length=len(data),
            content_type=content_type,
            # 设置合适的分片传输大小（如部分大图或切分后的多页）
            part_size=10 * 1024 * 1024
        )
        return object_name

    def get_presigned_url(self, bucket_name: str, object_name: str, expires_in_hours: int = 1) -> str:
        """生成预签名 URL 供外部安全访问文件"""
        url = self.client.presigned_get_object(
            bucket_name,
            object_name,
            expires=timedelta(hours=expires_in_hours)
        )
        return url

# 全局的 Minio 适配器单例
storage_client = MinioAdapter()

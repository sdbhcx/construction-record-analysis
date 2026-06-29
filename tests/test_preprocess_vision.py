import pytest
import sys
import os
import cv2
import numpy as np

# 将根目录加到sys.path以便能正确定位src
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.preprocess.vision import vision_processor, VisionPreprocessor

def create_dummy_image_bytes(width=800, height=600):
    """创建一张虚拟画面的图片数据(白底带一些随机噪点)，用于测试"""
    img = np.ones((height, width, 3), dtype=np.uint8) * 255
    # 增加一点高斯噪声模拟污渍
    noise = np.random.randint(0, 50, (height, width, 3), dtype=np.uint8)
    img = cv2.subtract(img, noise)
    
    success, encoded_img = cv2.imencode('.jpg', img)
    return encoded_img.tobytes() if success else b""

def test_resize_logic():
    """测试自适应缩放机制 (长边不超 2048)"""
    processor = VisionPreprocessor(max_threads=1)
    
    # 构建一张超大测试矩阵 (3000 x 2000)
    large_img = np.zeros((2000, 3000, 3), dtype=np.uint8)
    resized_img = processor._resize_if_needed(large_img, max_dim=2048)
    
    # 预期长边变为 2048, 检查形状
    h, w = resized_img.shape[:2]
    assert max(h, w) <= 2048
    assert w == 2048
    # 同比例缩放 2000 * (2048 / 3000) ≈ 1365
    assert h == int(2000 * (2048.0 / 3000.0))

def test_process_single_image():
    """测试核心图像流水线 (缩放 -> CLAHE灰度增强 -> 滤噪)"""
    test_bytes = create_dummy_image_bytes()
    processed_bytes = vision_processor._process_single_image(test_bytes)
    
    # 应返回处理后的合规 JPG 字节流
    assert processed_bytes is not None
    assert isinstance(processed_bytes, bytes)
    
    # 解码看是否还是一张合规图像，由于处理流水线输出灰度图（被视为单通道图，但在imencode时.jpg若没有指定，解码后依然是3通道灰度图，或者原样）
    np_arr = np.frombuffer(processed_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_GRAYSCALE)
    assert img is not None

def test_batch_process():
    """测试多张图片的并发批处理"""
    img_list = [create_dummy_image_bytes(200, 200) for _ in range(3)]
    
    results = vision_processor.batch_process(img_list)
    
    assert len(results) == 3
    for res in results:
        assert res is not None
        assert isinstance(res, bytes)

def test_process_invalid_image():
    """测试针对损坏数据的防御"""
    bad_bytes = b"not_an_image_at_all_12345"
    result = vision_processor._process_single_image(bad_bytes)
    assert result is None

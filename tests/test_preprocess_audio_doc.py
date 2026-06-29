import pytest
import sys
import os
import fitz # PyMuPDF

# 将根目录加到sys.path以便能正确定位src
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.preprocess.audio import audio_processor
from src.preprocess.document import document_processor

def create_mock_pdf_bytes():
    """在内存中生成一个 2 页的最小规格 PDF"""
    doc = fitz.open()
    
    # page 1
    page1 = doc.new_page()
    page1.insert_text((50, 50), "Test Page 1")
    
    # page 2
    page2 = doc.new_page()
    page2.insert_text((50, 50), "Test Page 2")
    
    return doc.write()

def test_document_processor_split():
    mock_pdf = create_mock_pdf_bytes()
    images = document_processor.split_pdf_to_images(mock_pdf)
    
    # 我们知道生成的是 两页的PDF
    assert len(images) == 2
    for img_bytes in images:
        assert isinstance(img_bytes, bytes)
        assert len(img_bytes) > 0
        
def test_document_processor_invalid_pdf():
    # 破坏性输入测试
    invalid_bytes = b"not_a_pdf"
    images = document_processor.split_pdf_to_images(invalid_bytes)
    assert len(images) == 0

def test_audio_processor_transcribe_mock():
    # 测试音频识别管道（Mock 模式）是否有返回值
    res = audio_processor.transcribe(b"fake_audio_stream")
    
    assert res is not None
    assert "混凝土" in res
    assert "120方" in res

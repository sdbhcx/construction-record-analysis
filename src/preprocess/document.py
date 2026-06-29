import io
import fitz  # PyMuPDF
from typing import List, Tuple

class DocumentPreprocessor:
    def __init__(self):
        # 默认使用 2倍缩放（DPI提升）以保证转换图片后的 OCR 清晰度
        self.zoom_matrix = fitz.Matrix(2, 2)

    def split_pdf_to_images(self, pdf_bytes: bytes) -> List[bytes]:
        """
        接收长文档 PDF 的字节流，拆分为按页排序的清晰 Jpeg 图片字节流集合，
        从而方便输入给后续的轻量级 OCR 流水线。
        """
        images = []
        try:
            # 内存态读取 PDF 降低 IO 消耗
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            for page_index in range(len(doc)):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(matrix=self.zoom_matrix, alpha=False)
                # 转换出 jpeg 字节形式直接存入内存数组
                img_bytes = pix.tobytes("jpeg")
                images.append(img_bytes)
            doc.close()
            return images
        except Exception as e:
            # TODO: 接入系统监控与告警
            print(f"Document preprocessing error: {e}")
            return []

    def extract_excel_basic_text(self, excel_bytes: bytes) -> str:
        """
        Excel 初步提取桩代码。对于历史遗留 Excel 的纯结构读取（可选）。
        待正式引入 openpyxl / pandas 解析。
        """
        # TODO: 使用 openpyxl 读取基础文字或转化为 JSON
        return "【Excel 表格提取内容】"

document_processor = DocumentPreprocessor()

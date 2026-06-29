import cv2
import numpy as np
import concurrent.futures
from typing import List, Optional

class VisionPreprocessor:
    def __init__(self, max_threads: int = 4):
        # 初始化线程池，限制最大并发数防止 CPU 调度雪崩
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_threads)

    def _resize_if_needed(self, img: np.ndarray, max_dim: int = 2048) -> np.ndarray:
        """根据长边限制等比例缩放图片，防止过大图片耗尽显存/内存"""
        h, w = img.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / float(max(h, w))
            new_w, new_h = int(w * scale), int(h * scale)
            img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
        return img

    def _perspective_transform(self, img: np.ndarray) -> np.ndarray:
        """
        透视变换：寻找页面轮廓并拉直（此处为桩方法，后续根据实际业务调整锚点算法）
        目前直接返回原图，仅保留扩展桩口
        """
        # TODO: 实际可接入轮廓检测(findContours), 算出最大四边形后用 getPerspectiveTransform 和 warpPerspective 处理
        return img

    def _process_single_image(self, image_bytes: bytes) -> Optional[bytes]:
        """单张图像的 CPU 密集型处理流"""
        try:
            # 1. 内存态零拷贝解码 (Zero-Copy Decode)
            np_arr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if img is None:
                return None

            # 2. 图像自适应缩放（防止大体量图片拖垮后续显存）
            img = self._resize_if_needed(img, max_dim=2048)

            # 3. 增强与滤波 (开销最大的步骤，由于释放了 GIL，此处可受惠于多处理器的并行)
            # 转灰度 -> CLAHE 增强 -> 双边滤波拉伸对比同时去除白噪声
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            
            # 双边滤波：能够在保持边缘清晰的同时去除噪声（如泥点、噪点）
            filtered = cv2.bilateralFilter(enhanced, d=9, sigmaColor=75, sigmaSpace=75)

            # 4. 透视变换 (基于边缘检测寻找最大四边形轮廓并拉直)
            aligned_img = self._perspective_transform(filtered)

            # 5. 内存编码直接输出字节流
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
            success, encoded_img = cv2.imencode('.jpg', aligned_img, encode_param)
            
            return encoded_img.tobytes() if success else None

        except Exception as e:
            # TODO: 接入系统监控埋点记录异常
            print(f"Vision preprocessor error: {e}")
            return None

    def batch_process(self, image_bytes_list: List[bytes]) -> List[Optional[bytes]]:
        """
        利用线程池并发执行，处理批量的同流水线需求。
        由于 cv2 的底层运算自动释放了 Python GIL，该 map 操作能在多核机器上进行高效并发。
        """
        results = []
        # map保证了输入和输出顺序的一致性
        for res in self.executor.map(self._process_single_image, image_bytes_list):
            results.append(res)
        return results

# 提供全局单例
vision_processor = VisionPreprocessor()

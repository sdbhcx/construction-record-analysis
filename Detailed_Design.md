# 施工记录智能分析系统 - 详细设计说明书 (Detailed Design)

## 1. 设计目标与范围

本文档基于系统架构与概要设计，对以下五大功能模块进行可落地的详细设计：

1. 多模态数据接入与预处理模块
2. 高并发批处理调度模块
3. AI 解析与大小模型级联引擎
4. 现有系统适配器与强制业务对齐模块
5. 人机协同与持续学习模块

每个模块均包含：实现算法、核心数据结构、接口定义、可选技术及优点。

---

## 2. 全局设计约束

### 2.1 非功能目标

- 吞吐目标：批处理模式支持日处理 ≥ 10,000 页文档。
- 可用性目标：核心链路可恢复，单点故障可旁路降级。
- 可审计目标：关键决策（路由、对齐、推送、人工修订）全量留痕。
- 数据安全目标：私有化部署、传输加密、最小权限访问。

### 2.2 全局状态机

任务状态：

`PENDING -> PREPROCESSING -> EXTRACTING -> VALIDATING -> ALIGNING -> ADAPTING -> SUCCESS`

异常分支：

- `* -> DLQ`（可人工重放）
- `* -> NEED_HUMAN_REVIEW`（快速失败触发）
- `ADAPTING -> RETRYING -> ADAPTING`（网络级可重试异常）

---

## 3. 工程源码结构设计

根据四大业务模块和基础支撑模块，在 `Architecture_Design.md` 的指引下，项目后端核心应用及周边脚本的工程源码目录分配如下（遵循 FastAPI + Celery 标准单体/服务分离架构）：

```text
construction-record-analysis/
├── docker-compose.yml              # 容器编排入口
├── .env.example                    # 环境变量管理
├── src/                            # 核心业务源码 (Backend Service)
│   ├── main.py                     # API网关与FastAPI ASGI主入口
│   ├── api/                        # 【多模态数据接入模块】
│   │   ├── dependencies.py         # 用户鉴权、CORS等依赖
│   │   └── routers/
│   │       ├── tasks.py            # POST /tasks 接收多模态文件
│   │       └── feedback.py         # PUT /correction 人工纠偏回写
│   ├── core/                       # 核心基础设施隔离层
│   │   ├── config.py               # 环境变量Pydantic配置解析
│   │   ├── database.py             # SQLAlchemy 引擎/PG数据库连接池
│   │   ├── redis_pool.py           # Redis Broker与限流池
│   │   └── storage.py              # MinIO/S3 适配器实现
│   ├── preprocess/                 # 【预处理模块】
│   │   ├── vision.py               # OpenCV防伪去噪与透视变换
│   │   ├── audio.py                # Whisper ASR与降噪包
│   │   └── document.py             # PDF拆页与Excel初步提取
│   ├── worker/                     # 【批处理调度模块与引擎入口】
│   │   ├── celery_app.py           # Celery配置与队列规则路由(q_realtime等)
│   │   └── tasks/
│   │       ├── consumer.py         # 多级队列消费者拉取分配与限流
│   │       ├── entry.py            # 从API承接的异步任务入口 (分装路由)
│   │       ├── dlq.py              # 死信重试执行器
│   │       └── schedule.py         # Celery Beat定时清理等批处理操作
│   ├── engine/                     # 【AI 解析与大小模型级联引擎】
│   │   ├── orchestrator.py         # 级联调度器 (复杂度阈值判定, Fallback分流)
│   │   ├── light_model.py          # PaddleOCR等轻量模型调用客户端
│   │   ├── vllm_client.py          # 与GPU侧vLLM通信、Token Bucket流流控
│   │   ├── extractor.py            # AI结构化解析与提示词工程管道
│   │   └── agent/                  # 【Plan-Execute-Reflect】机制落地
│   │       ├── validator.py        # Reflect反思：基建业务逻辑硬规则校验
│   │       └── tools.py            # Execute工具：图片裁剪放大、矢量外键查询
│   ├── adapter/                    # 【现有系统适配器与强制对齐模块】
│   │   ├── aligner.py              # 四级强制逻辑映射处理 (Redis/TF-IDF/Qdrant)
│   │   ├── qdrant_client.py        # 向量数据库读写工具
    │   └── legacy_rest/            # REST API封装层
    │       └── client.py           # 指数退避重试 (Exponential Backoff) 执行槽与接口调用
│   └── models/                     # 【持久层模型映射】
│       └── domain.py               # ORM表结构定义 (包含：Task/Log/Feedback)
└── scripts/                        # 【持续学习模块与离线任务】
    ├── auto_eval.py                # “黄金集”评测门禁系统
    ├── clean_feedback.py           # 定时抽取 Feedback 过滤噪音的清洗脚本
    └── run_lora_tuning.sh          # 定期触发大模型挂载微调指令
```

---

## 4. 模块详细设计

## 4.1 多模态数据接入与预处理模块

### 3.1.1 目标

统一接收音频、图像、PDF/Excel，完成安全校验、基础质量增强、结构切分，并将原始文件写入对象存储。

### 3.1.2 实现算法

1. 文件类型识别与路由算法

   - 输入：`content-type`、文件头魔数、后缀。
   - 输出：`source_type ∈ {audio,image,pdf,excel}`。
   - 规则：三因子交叉校验，不一致直接拒绝。
2. 图像增强算法

   - 透视校正：OpenCV 四点变换。
   - 去噪：双边滤波 + 自适应阈值。
   - 防伪检测：GPS/时间戳/水印区域检测与 OCR 核验。
3. 音频预处理算法

   - 降噪：RNNoise 或 WebRTC NS。
   - 语音活动检测：VAD 分段。
   - ASR：Whisper/FunASR（工程词典热词增强）。
4. 文档切分算法

   - PDF：逐页转图并保留页号映射。
   - Excel：工作表遍历，结构化抽取为中间 JSON 表格块。
   - 音频：FFmpeg 分段转码为 WAV。

### 3.1.3 核心数据结构

```text
MediaObject {
  object_id: string
  source_type: enum(audio,image,pdf,excel)
  uri: string
  checksum_sha256: string
  page_count: int?
  meta: map<string,string>
}

PreprocessTask {
  task_id: uuid
  biz_flow_id: string
  source_type: enum
  object_id: string
  preprocess_profile: string
  status: enum
  created_at: timestamp
}

PreprocessResult {
  task_id: uuid
  normalized_uris: list<string>
  asr_text: string?
  quality_score: float
  anti_forgery_passed: bool
  warnings: list<string>
}
```

### 3.1.4 接口定义

1. 创建任务

   - `POST /api/v1/tasks`
   - 请求：`biz_flow_id`, `source_type`, `file_url`, `callback_url`
   - 响应：`task_id`, `status=PENDING`
2. 文件直传回调（可选）

   - `POST /api/v1/uploads/complete`
   - 请求：`object_id`, `checksum`, `meta`
   - 响应：`ok`
3. 任务状态查询

   - `GET /api/v1/tasks/{task_id}`

### 3.1.5 技术推荐与优点

- FastAPI：异步能力强、开发效率高、自动生成 OpenAPI。
- MinIO：S3 兼容、私有化友好、生命周期管理完善。
- OpenCV + PaddleOCR：图文预处理成熟，性价比高。
- Whisper/FunASR：中文识别效果好，支持热词增强。

### 3.1.6 设计要点与关键实现要素

**设计要点：**
1. **基于 FastAPI 的异步非阻塞网关**：使用 `UploadFile` 结合 `SpooledTemporaryFile`，当文件低于 `1MB` 时放置内存，超过则自动落盘。使用 `async/await` 解耦业务逻辑，接收到文件后立即使用 `celery_app.send_task` 派发到处理队列并立刻返回 HTTP 202 及 `task_id`，避免长连接耗尽 ASGI Worker。
2. **零拷贝 (Zero-Copy) 与 MinIO 分块直传**：预处理后的文件对象需推送至 MinIO。摒弃本地读取文件再构建请求的方式，利用 MinIO Python SDK 的 `put_object(..., part_size=10*1024*1024)` 结合流式生成器 `StreamingResponse` 直接将解压/处理后的字节流推送至对象存储，减少两倍的磁盘 I/O 开销与内存暂用。
3. **基于文件魔数 (Magic Number) 的安全穿透防御**：针对非法文件伪装，引入 `python-magic` 库进行底层判断。仅读取首部 `2048 Bytes` 构建 `magic.Magic(mime=True)` 检测，一旦发现与请求 MIME 及后缀特征不符，抛出 `HTTPException(415 Unsupported Media Type)` 拒绝执行。

**关键实现要素：**
1. **OpenCV 多线程图像预处理流水线**：由于 OpenCV (cv2) 主要是基于 C++ 编译且释放 GIL（全局解释器锁），为防止在此密集型 CPU 操作时产生雪崩，在 Celery Task 内通过 `concurrent.futures.ThreadPoolExecutor` 或使用 NumPy 矩阵并行矢量化操作实现“透视校正 - 双边滤波 - 二值化”。对于多发请求利用 `cv2.imdecode` 流式加载缓冲直接进行内存态解码转换，避免产生临时图片文件。
缓冲解码 (Decode)：从内存读字节流转为 NumPy 矩阵。（取代 cv2.imread，切断磁盘 IO）
尺寸对齐 (Resize/Pad)：根据轻量级 OCR 的输入特征尺度（比如长边限制在 2048px 内），成比例缩放图像。
图像增强 (Enhancement)：使用自适应直方图均衡化（CLAHE）解决工地现场照片常见的“逆光、局部阴影”问题。
形态学与校正 (Perspective & Filter)：双边滤波去除泥点/噪点（保留边缘特征），以及边缘检测计算四点透视变换，把歪斜的纸单“拍正”。
内存编码 (Encode)：将处理完的矩阵用 cv2.imencode 根据质量阈值重新压缩为 .jpg 字节流，以供后续上传 MinIO 或喂给大模型。
```
import cv2
import numpy as np
import concurrent.futures
from typing import List, Optional

class VisionPreprocessor:
    def __init__(self, max_threads: int = 4):
        # 初始化线程池，限制最大并发数防止 CPU 调度雪崩
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_threads)

    def _process_single_image(self, image_bytes: bytes) -> Optional[bytes]:
        """单张图像的 CPU 密集型处理流"""
        try:
            # 1. 内存态零拷贝解码 (Zero-Copy Decode)
            np_arr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if img is None: return None

            # 2. 图像自适应缩放（防止大体量图片拖垮后续显存）
            img = self._resize_if_needed(img, max_dim=2048)

            # 3. 增强与滤波 (开销最大的步骤，由于释放了 GIL，此处可受惠于多处理器的并行)
            # 转灰度 -> CLAHE 增强 -> 双边滤波拉伸对比同时去除白噪声
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
            enhanced = clahe.apply(gray)
            filtered = cv2.bilateralFilter(enhanced, d=9, sigmaColor=75, sigmaSpace=75)

            # 4. 透视变换 (伪代码，基于边缘检测寻找最大四边形轮廓并拉直)
            # aligned_img = self._perspective_transform(filtered)

            # 5. 内存编码直接输出字节流
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
            success, encoded_img = cv2.imencode('.jpg', filtered, encode_param)
            
            return encoded_img.tobytes() if success else None

        except Exception as e:
            # TODO: 接入系统监控埋点
            return None

    def batch_process(self, image_bytes_list: List[bytes]) -> List[bytes]:
        """
        利用线程池并发执行，处理批量的同流水线需求
        由于 cv2 的底层运算自动释放了 Python GIL，
        这个 map 操作能在 Celery 单个进程节点内吃满所在宿主机的多核。
        """
        results = []
        # map保证了输入和输出顺序的一致性
        for res in self.executor.map(self._process_single_image, image_bytes_list):
            results.append(res)
        return results
```
2. **PDF/音频资源的 Chunked 并发切片**：音频使用 `ffmpeg-python` 切分时配合 `subprocess` 的管道挂起（Piped output），并将 PCM 数据块直接喂给 Whisper 的解码流；PDF 拆页使用 `PyMuPDF` (fitz)，设定每 `10` 页为一个 Task Sub-Job 横向散列回 RabbitMQ/Redis，避免因处理百页大图纸导致单次作业超时产生 Celery `SoftTimeLimitExceeded` 异常。
3. **防重复计算的 SHA-256 指纹缓存墙**：为文件流接入 `hashlib.sha256(file.file.read(8192)).hexdigest()` 滚动哈希并拼接。在入库/推理前建立 Redis 锁：`SETNX task:hash:{sha256} {task_id}`。若已被其他实例计算，则轮询原 `task_id` 状态实现“旁路复用”，从根源拦截同一份施工录音/图纸在高并发提交下的冗余 GPU 计算消耗。

---

## 3.2 高并发批处理调度模块

### 3.2.1 目标

保障高峰期稳定吞吐，避免 GPU 服务拥塞，支持优先级、重试、降级与死信处理。

### 3.2.2 实现算法

1. 多队列优先级调度

   - 队列：`q_realtime`, `q_batch`, `q_dlq_retry`。
   - 策略：实时任务优先，批处理按权重配额执行。
2. 限流与拥塞控制

   - 令牌桶（Token Bucket）控制 vLLM 请求速率。
   - Redis 全局计数控制并发上限：
     $$
     concurrency\_inflight < threshold\_{gpu}
     $$
3. 动态降级

   - 当 GPU 显存水位 > 阈值，路由到轻量 OCR；复杂文档进入待处理缓冲队列。
4. 重试与快速失败

   - 网络/超时异常：指数退避 `1s,2s,4s,8s`。
   - 业务不可逆异常（全白页、文件破损、连续同错）触发 Fast-Fail，直接转 `NEED_HUMAN_REVIEW`。

### 3.2.3 核心数据结构

```text
DispatchPolicy {
  max_inflight_llm: int
  max_retry: int
  backoff_seconds: list<int>
  queue_weights: map<string,int>
  gpu_mem_high_watermark: float
}

TaskEnvelope {
  task_id: uuid
  priority: int
  queue: string
  attempt: int
  payload_ref: string
  trace_id: string
}
```

Redis 关键键设计：

- `task:state:{task_id}`
- `task:retry:{task_id}`
- `llm:inflight:global`
- `queue:quota:{queue_name}`

### 3.2.4 接口定义

- 内部事件：`task.created`, `task.preprocessed`, `task.failed`, `task.need_review`
- 运维接口：
  - `GET /api/v1/admin/queues/stats`
  - `POST /api/v1/admin/tasks/{task_id}/requeue`

### 3.2.5 技术推荐与优点

- Celery + Redis：生态成熟、任务模型清晰、支持重试路由。
- Flower/Prometheus：队列可视化和告警方便。
- PgBouncer：削峰填谷，保护 PostgreSQL 连接池。

### 3.2.6 设计要点与关键实现要素

**设计要点：**
1. **基于 QoS (服务质量) 的多队列隔离机制**：为避免海量批处理历史图纸阻塞现场施工人员上传的急办任务，采用 Celery 的 `task_routes`进行硬隔离。配置三个核心队列：`q_realtime` (高优先级，由专门的 Worker 组监听)、`q_batch` (大批量长耗时任务队列) 与 `q_dlq_retry` (死信与退避重试专用队列)。
2. **GPU 显存防击穿与全局令牌桶限流**：AI 模型（如 vLLM）对并发极其敏感，单纯靠 Worker 数限制是不够的。必须在调度层引入基于 Redis 的 Token Bucket 算法或原子计数器 (`INCR`/`DECR`)，强行遏制打入 GPU API 侧的并发请求上限，保障服务不 OOM。
3. **分级异常与动态降级重试**：将异常严格分类。如果是网络超时或者 `vLLM rate_limit` 异常，抛出 `bind=True` 的重试异常等待再次调度；如果是文档破损此类不可逆异常，则立刻触发 Fallback 短路，将任务直接抛向轻量级 PaddleOCR 或标注为人工干预 (`NEED_HUMAN_REVIEW`)。

**关键实现要素：**
1. **Celery 指数退避 (Exponential Backoff) 实现**：在 Task 装饰器中显式配置 `@celery_app.task(autoretry_for=(ConnectionError, RateLimitExceeded), retry_kwargs={'max_retries': 4}, retry_backoff=True, retry_backoff_max=60)`。这意味着一旦调用远端发生拥塞，任务会以 `1s, 2s, 4s, 8s` 的非线性延迟退避重置自身，防止产生重试风暴（Retry Storm）。
2. **基于 Redis Lua 脚本的全局并发锁**：由于 Celery 存在分布式多 Worker 节点抢占，简单的 `get` 和 `set` 无法保证并发安全。实现限流时，必须使用 Redis Lua 脚本原子核验：`return redis.call('INCR', KEYS[1]) <= tonumber(ARGV[1])`。在调用大模型前执行该脚本获取锁，如果超出 GPU 安全水位水位阈值 (如 `max_inflight_llm=10`)，则使当前 Task `raise self.retry()` 回列队等待。
3. **基于 SQLAlchemy 连接池与 PgBouncer 的削峰管控**：当上万个 Task 从队列并发被拉起，并在数据库内更新自己的运行状态为 `EXTRACTING` 时，极易将 PostgreSQL 原生的连接池写挂。必须在应用层使用 SQLAlchemy `create_engine(..., pool_size=20, max_overflow=50)`，并在基础设施层加装 PgBouncer 作为代理缓冲层，将并行的长连接请求合并为对短平快的 DB 连接复用。

---

## 3.3 AI 解析与大小模型级联引擎

### 3.3.1 目标

以最低成本获得可审计的高质量结构化结果，兼顾常规文档吞吐与复杂文档准确率。

### 3.3.2 实现算法

1. 复杂度判定与级联路由

   - 计算复杂度分：
     $$
     S = 0.35\cdot(1-conf) + 0.25\cdot layout\_entropy + 0.20\cdot handwriting\_ratio + 0.20\cdot damage\_score
     $$
   - 当 $S \ge \tau$ 时进入大模型 Fallback。
2. Plan-Execute-Reflect 循环

   - Plan：根据校验失败原因生成纠偏策略。
   - Execute：执行 `Crop_Image`、`Table_Split`、`Retrieve_Vector` 等工具。
   - Reflect：规则校验（金额平衡、必填项、时间逻辑）。
   - 终止：`retry >= 3` 或命中 Fast-Fail 条件。
3. 柔性实体对齐（引擎内预对齐）

   - Top-K 向量召回 + 阈值判定。
   - `sim >= 0.85` 自动映射；`0.70~0.85` 标记待核；`<0.70` 原词保留并告警。
4. 结果置信度融合

   - OCR 置信度、LLM 自评置信度、规则通过率加权融合：
     $$
     confidence = 0.4\cdot conf_{ocr} + 0.3\cdot conf_{llm} + 0.3\cdot pass_{rule}
     $$

### 3.3.3 核心数据结构

```text
ExtractionContext {
  task_id: uuid
  media_refs: list<string>
  schema_version: string
  retry_count: int
  tool_history: list<ToolCallLog>
  rule_violations: list<string>
}

ExtractedField {
  key: string
  value: any
  confidence: float
  bbox: list<int>?
  source_page: int?
  trace: string
}

ExtractionResult {
  task_id: uuid
  fields: list<ExtractedField>
  raw_json: json
  confidence: float
  need_human_review: bool
}
```

### 3.3.4 接口定义

- 轻量模型接口：`POST /internal/ocr/parse`
- 大模型接口：`POST /internal/vllm/chat/completions`
- 校验接口：`POST /internal/validator/check`
- 工具调用接口：`POST /internal/agent/tools/{tool_name}`

### 3.3.5 技术推荐与优点

- vLLM（Qwen2-VL）：吞吐高、支持连续批处理与 LoRA 动态加载。
- PaddleOCR/LayoutLMv3：常规场景成本低、速度快。
- Qdrant：向量检索延迟低，运维复杂度低于重型方案。

### 3.3.6 设计要点与关键实现要素

**设计要点：**
1. **基于 OpenAI 兼容格式的防腐层 (Anti-corruption Layer)**：不要将业务逻辑与特定模型（如 Qwen 或 GPT-4）的特定 API 绑定。使用 Python 的 `openai` 客户端库，通过修改 `base_url` 指向内部部署的 vLLM OpenAI-Compatible Server。这样无论是切换闭源 API 还是本地不同架构的大模型，抽取流水线代码 `extractor.py` 都无需改动。
2. **基于 Pydantic 的结构化输出强约束 (Structured Outputs)**：为了避免大模型生成随意格式的 JSON 导致解析崩溃，必须使用 `Pydantic` 定义业务抽取模型，并在调用大模型时利用如 `instructor` 或原生的 `response_format` 参数进行强制约束。不仅能提干 JSON 的合格率，还能直接获得结构化 Python 对象用于反思校验。
3. **分层分治的 Plan-Execute-Reflect (反思代理)**：当 AI 抽取结果违反业务常识（如“含钢量＞0但在材料清单未提取到钢材”），不要直接用原图再问一遍。应该在 `validator.py` 中记录报错（Rule Violation），并将报错信息组装成新的指导 Prompt（Plan层），带着原图和错因请求模型通过调用外部查询函数修复问题（Execute层）。

**关键实现要素：**
1. **vLLM 异步客户端连接池**：在 `vllm_client.py` 中避免使用同步的 `requests`。由于大模型生成首字时间较长（TTFT），必须结合 `AsyncOpenAI` 客户端并在 FastAPI/Celery worker 生命周期内维持单例长连接，搭配 `asyncio.Semaphore` 控制每个应用实例打到显卡接口的最大并发协程数。
2. **图片多分辨率切分馈入 (Image Token Optimization)**：对于长条形凭证或大图纸，直接塞入 `Qwen2-VL` 会因 Token 爆炸导致显存 OOM 或生成截断。在代码侧需依据比例对长图进行切片，并组装为 OpenAI 多模态消息数组格式 `[{"type": "image_url", "image_url": {"url": "slice1"}}, {"type": "image_url",...}]` 联合喂入。
3. **基于反射的校验器注入 (Reflection Validator Injector)**：在 `agent/validator.py` 中，不应硬编码所有业务形态。应采用注册表模式：
```python
from pydantic import BaseModel, model_validator

class ConcretePouringSchema(BaseModel):
    volume: float
    material: str
    
    @model_validator(mode='after')
    def check_logic(self):
        if self.volume > 200 and "大体积" not in self.material:
            raise ValueError("单次浇筑超200方需明确大体积标识！")
        return self
```
通过捕获 Pydantic 原生的 `ValidationError`，提取 `e.errors()` 中的描述，自动构造成 `Retry` 提示词喂回给大模型进行下一次修正迭代，直至达到最大重试次数 (`retry_count > 3`) 才抛入人工队列。

---

## 3.4 现有系统适配器与强制业务对齐模块

### 3.4.1 目标

将 AI 结构化数据强制转换为现有系统可落库的外键化数据，保证幂等、可重试、可回溯。

### 3.4.2 实现算法

三重漏斗对齐流水线：

1. Level 1：本地内存级高速近似匹配（完全匹配 + 文本相似度）
   - 基于预加载字典的字面与编辑距离匹配，利用高速 C 底层库快速拦截（得分=100为精确匹配；100>得分>85为容错匹配）。
2. Level 2：向量语义召回兜底（Top-3）
   - 当浅层相似度无法判断（如行业黑话、形变严重或短映射），通过 Qdrant 模型向量匹配拉取最近似实体。
3. Level 3：人工挂起（`Unmapped`）
   - 两级策略均未达到置信度阈值，进入人工队列并为后续学习提供回灌样本。

幂等策略：

- 使用 `task_id` 作为业务幂等键。
- 适配器直接调用现有系统的 RESTful `/api/v1/drafts` 接口创建数据，通过 `X-Idempotency-Key` 传递 `task_id` 确保不重复。

### 3.4.3 核心数据结构

```text
AlignmentCandidate {
  original_text: string
  candidate_id: string
  candidate_name: string
  level: int
  score: float
}

AlignedPayload {
  task_id: uuid
  biz_flow_id: string
  foreign_keys: map<string,string>
  business_json: json
  evidence_links: list<string>
}
```

### 3.4.4 接口定义

1. 内部适配接口

   - `POST /internal/adapter/push-draft`
2. 下游 RESTful 接口

   - `POST https://legacy-system.local/api/v1/drafts`
   - Headers: `X-Idempotency-Key: {task_id}`
   - Body: `AlignedPayload`
3. 人工映射接口

   - `POST /api/v1/alignment/manual`
   - 请求：`task_id`, `field`, `original_text`, `mapped_id`

### 3.4.5 技术推荐与优点

- RESTful API + HTTP Client (`httpx` / `requests`)：开发简单，现有系统原生支持，无需额外部署 RPC 通信环境。
- rapidfuzz：基于 C++ 底层实现，利用本地内存进行数十万级字符串集合的高速相似度搜索（Edit Distance/Jaro-Winkler），远快于引入外部网络缓存组件。
- HTTP 级重试：结合 tenacity / retrying 库可直接在 HTTP 客户端层面实现。

### 3.4.6 设计要点与关键实现要素

**设计要点：**
1. **彻底的数据防腐层 (DTO 隔离)**：AI 引擎输出的 `ExtractedField` 不应直接传入旧系统。必须经由适配器转换为 `AlignedPayload`，并在代码层面对两者进行严格区分（例如分别定义 `AIResponseSchema` 和 `LegacyDraftDTO`）。这样即使旧系统接口表结构巨变，只需修改适配器映射逻辑，AI 核心逻辑完全免于波及。
2. **基于内存与向量库的计算卸载 (Shedding Load)**：不要对所有的字符串都无脑调用向量数据库或大模型求相似度。依靠“三重漏斗”阻挡流量：在服务启动或定时轮询阶段从主库拉取业务字典至本地内存，85% 的“精确匹配词”和“带有轻微错字（如多字少字）”被本地 CPU 运行的 `rapidfuzz` 高速计算拦截并直接拿到基建系统 ID；仅剩 15% 的“生僻黑话”或跨词态映射向下穿透，交由 Qdrant 向量搜索兜底；彻底拦截失败的转人工并为日后建立映射对。
3. **基于 Header 的防重放设计 (Idempotency)**：旧系统（Legacy System）的接口可能没有处理并发重试的防腐能力。因此在推送草稿时，必须在 HTTP 协议的 Header 中强行塞入 `X-Idempotency-Key: {task_id}`。若遇到 502/504 网络网关报错，Celery 会发起重试，此时旧系统网关根据这个唯一 ID 直接返回上次成功的结果，不会在库里产生脏数据（双重记录）。
4. **基于 WBS 树形依赖的“级联对齐与作用域限定” (Cascading Alignment)**：基建项目特有的“工点 -> 单位工程 -> 分部分项 -> 部位 -> 工序”构成了严格的 WBS (Work Breakdown Structure) 树。**绝对不能进行全局扁平化对齐（例如整个库去搜“主体结构”或“钢筋绑扎”，必定引发 ID 碰撞灾难）**。在设计上，必须采取“自顶向下”的级联推导顺序：获取上一级 ID 后，作为严格的过滤条件（Scope Filter），圈定下一级的搜索范围。

**关键实现要素：**
1. **多层级命名空间的 Python 内存搜索**：在针对 WBS 数据建立内存树时，要摒弃扁平字典 `Dict[str, str]`，改为包含层级依赖的 `Dict[parent_id, Dict[str, child_id]]` 形式。当利用 `rapidfuzz` 搜索“四层面部位”时，其输入的 `choices` 列表必须且只能是刚刚对齐成功的“分部分项 ID”下属的实体名称，从根源上消除跨楼栋或跨标段的重名错误。
2. **Qdrant 向量库的 Payload 属性过滤 (Metadata Filtering)**：将标准词汇入库 Qdrant 时，不仅存 Embedding 和目标主键 ID，必须存入其全链路祖先节点的 ID 作为 `Payload`（例如 `{"unit_id": "xxx", "sub_part_id": "yyy"}`）。当深度查询兜底进行到“工序”提取寻找对齐时，必须在 API 请求中构造带有严格限制的过滤查询 `Filter: {must: [{key: "sub_part_id", match: {value: "刚才获取的分部分项ID"}}]} `，确保 Qdrant 搜出的生僻词外键绝对属于这一特定 WBS 分支。
3. **利用 Tenacity 实现指数退避补偿的 HTTP 客户端**：在进行下游 RESTful 调用时，禁止使用单纯的 `try-except` 包裹。使用专业的容错控制库 `tenacity` 装饰内部封装的异步 HTTPX 请求类，专门捕获网络层与连接池的异常，平滑处理网络抖动。
```python
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from pydantic import BaseModel

class LegacyAdapterClient:
    # 发生超时、断连等异常时，分别以 2s, 4s, 8s, 16s 指数等待，最多尝试5次
    @retry(
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadTimeout))
    )
    async def push_draft_to_legacy(self, task_id: str, payload_dict: dict):
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 强行注入业务幂等键，防止下游系统生成多份重复草稿单据
            headers = {
                "X-Idempotency-Key": str(task_id),
                "Content-Type": "application/json"
            }
            res = await client.post(
                "http://legacy-system.local/api/v1/drafts", 
                json=payload_dict, 
                headers=headers
            )
            # 捕获状态码并针对性抛出（触发重试或 Fast-fail）
            res.raise_for_status() 
            return res.json()
```

---

## 3.5 人机协同与持续学习模块

### 3.5.1 目标

提供可解释审核界面、快速人工纠偏、闭环数据回流与模型持续迭代。

### 3.5.2 实现算法

1. 三分屏审核联动

   - 字段点击定位原图 BBox。
   - 支持 Re-Crop 触发局部重识别。
2. 反馈样本筛选

   - 仅保留结构性差异样本（忽略空白/标点噪声）。
   - PII 清洗后进入训练池。
3. 训练触发策略

   - 时间阈值（每日低谷）或样本阈值（如 10,000 条）。
4. 质量门禁

   - 新 LoRA 在黄金集上回归评估，分数下降自动回滚。

### 3.5.3 核心数据结构

```text
ReviewRecord {
  review_id: bigint
  task_id: uuid
  corrected_json: json
  reviewer: string
  review_time: timestamp
}

FeedbackSample {
  sample_id: bigint
  task_id: uuid
  ai_json: json
  human_json: json
  diff_score: float
  pii_cleaned: bool
  used_for_training: bool
}

ModelVersion {
  model_name: string
  base_version: string
  lora_version: string
  eval_score: float
  status: enum(CANARY,ACTIVE,ROLLED_BACK)
}
```

### 3.5.4 接口定义

- 审核提交：`PUT /api/v1/tasks/{task_id}/correction`
- 回流查询：`GET /api/v1/feedback/pending`
- 训练触发：`POST /api/v1/admin/training/trigger`
- 模型切换：`POST /api/v1/admin/model/promote`

### 3.5.5 技术推荐与优点

- React + Ant Design：中后台三分屏交互开发效率高。
- Kafka（可选）：反馈流异步解耦，避免抢占推理资源。
- PEFT/Unsloth：LoRA 训练成本低、速度快。

### 3.5.6 设计要点与关键实现要素

**设计要点：**
1. **构建高价值的 DPO / SFT 排队水池 (Data Flywheel)**：当人工坐席在前端修改了 AI 的错误抓取后，系统绝对不能仅仅“丢给旧业务系统就了事”。必须在写入链路上进行异步分流拦截，抽取 `[原本构建的Prompt, AI的错误输出JSON, 人类纠正后的最终正确JSON]` 作为一个三元组，自动落入专用的表 `lora_feedback_pool`，物理沉淀“人工偏好数据”。
2. **防灾难遗忘的黄金集回归测试门禁 (Golden Dataset Gatekeeper)**：训练产生的每一个新 LoRA 适配器（Adapter）都严禁直接推上物理生产线，否则极易发生“修了提取漏水报错的问题，却导致连C35混凝土都不认识了”的大模型灾难性遗忘。因此必须依托设计篇末尾规划的【黄金测试集】，用脚本 `auto_eval.py` 在沙盒环境全量测一次，只有其字段提取综合 F1 Score 大于目前运行体系的分数，才能自动晋升它为 `ACTIVE`。
3. **依托 vLLM 的多 LoRA 热加载无感切换 (Hot-Swapping)**：为了保证生产环境不断服，绝对不能在切模型时重启驻留在显卡上的百亿参数主干引擎（Base Model启动动辄几十秒会导致整个系统断流崩塌）。必须利用 vLLM 原生的 `enable_lora` 参数，加载主模型的同时预留 Adapter 显存缓冲，通过在具体每一次 OpenAI API 请求中切换 `"model": "qwen2-vl-base-lora-v2"` 这一参数，在请求分发层实现权重的毫秒级动态路由切转。

**关键实现要素：**
1. **基于 `deepdiff` 的脏反馈判定与清洗**：在处理前端发回的 `PUT /correction` 人工审核反馈时，应当引入 Python 的 `deepdiff` 工具库在后台进行 JSON 两个维度的对比校验。如果人工审核只不过是把一个 `，` (半角) 改成了 `,` (全角) 这种纯格式噪音，计算出的结构化差异分量 (`diff_score`) 极低，程序应直接丢弃，不污染训练池。只收集真正纠正了字段、改变了判定的高价值对抗样本。
2. **自动化的视觉语言多模态协议转换 (ChatML Formatting)**：在每天夜间调起清理脚本 `clean_feedback.py` 后，必须把收集到的表数据洗为严格的 JSONL 格式来喂给框架。对于 Qwen2-VL 这类模型，绝不能只存文本，代码实现必须负责组装其独特的视觉 Token 占位符（例如：拼合 `<|im_start|>user\nPicture 1: <image>\n请你提取...` 的规范模式），确保符合 Unsloth 微调数据结构的严苛校验。
3. **独立的 Subprocess 训练工作槽 (Training Worker Isolation)**：触发训练任务（通过 Celery 调用）时，绝不能跟提供 Web 服务的节点或者做推理的 Worker 放在一起。由于梯度计算和优化器参数更新极巨消耗硬件资源（哪怕是 LoRA），必须把它丢入一个特定路由队列下的物理节点隔离开来跑，通过挂载 `subprocess.Popen` 去调集底层的 `run_lora_tuning.sh`，仅收集进程返回的 Loss 下降记录和保存至磁盘的模型权重文件哈希，再上报成功状态机。

---

## 4. 数据库物理模型（建议版）

```sql
CREATE TABLE ai_parse_tasks (
    task_id UUID PRIMARY KEY,
    biz_flow_id VARCHAR(128) NOT NULL,
    source_type VARCHAR(20) NOT NULL,
    file_url VARCHAR(512) NOT NULL,
    status VARCHAR(32) NOT NULL,
    extracted_json JSONB,
    aligned_json JSONB,
    confidence NUMERIC(5,4),
    need_human_review BOOLEAN DEFAULT FALSE,
    target_legacy_id VARCHAR(64),
    retry_count INT DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ai_parse_tasks_status ON ai_parse_tasks(status);

CREATE TABLE entity_alignment_logs (
    log_id BIGSERIAL PRIMARY KEY,
    task_id UUID REFERENCES ai_parse_tasks(task_id),
    field_name VARCHAR(64) NOT NULL,
    original_text VARCHAR(255) NOT NULL,
    aligned_id VARCHAR(64),
    aligned_name VARCHAR(255),
    match_level INT NOT NULL,
    score NUMERIC(6,4),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE lora_feedback_pool (
    feedback_id BIGSERIAL PRIMARY KEY,
    task_id UUID REFERENCES ai_parse_tasks(task_id),
    ai_predicted_json JSONB NOT NULL,
    human_corrected_json JSONB NOT NULL,
    diff_score NUMERIC(6,4),
    pii_cleaned BOOLEAN DEFAULT FALSE,
    is_processed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. 关键接口契约（摘要）

### 5.1 任务提交

- `POST /api/v1/tasks`
- 请求字段：
  - `biz_flow_id: string`
  - `source_type: string`
  - `file_url: string`
  - `callback_url?: string`
- 响应字段：
  - `task_id: uuid`
  - `status: PENDING`

### 5.2 任务详情

- `GET /api/v1/tasks/{task_id}`
- 响应字段：
  - `status`
  - `confidence`
  - `aligned_json`
  - `target_legacy_id`

### 5.3 人工纠偏

- `PUT /api/v1/tasks/{task_id}/correction`
- 请求字段：
  - `corrected_json: object`
  - `review_comment?: string`

### 5.4 适配器推送结果回写

- `POST /internal/adapter/ack`
- 请求字段：
  - `task_id`
  - `legacy_draft_id`
  - `rest_status`

---

## 6. 可观测性与安全设计

### 6.1 可观测性

- 指标：任务吞吐、成功率、平均时延、DLQ 比例、人工介入率。
- 日志：按 `task_id` + `trace_id` 全链路串联。
- 链路追踪：OpenTelemetry + Prometheus + Grafana。


**. 指标定义设计 (Metrics Definition)：**

* **任务吞吐量与处理成功率** ：使用 `Counter`。分别记录 `job_total{status="success"}` 和 `job_total{status="failed"}`。
* **任务平均时延** ：使用 `Histogram`。观测 `job_duration_seconds`，Grafana 中使用 `histogram_quantile(0.95, ...)` 计算 P95 延迟。
* **人工介入率/死信队列(DLQ)** ：使用 `Gauge` 记录当前 DLQ 深度。通过计算 `人工修改次数 / 总处理数` 得出介入率。

```
from prometheus_client import Counter, Histogram

FORM_PROCESS_TOTAL = Counter('form_process_total', 'Total processed forms', ['status', 'form_type'])
LLM_LATENCY = Histogram('llm_processing_latency', 'Latency of LLM calls', ['model_name'])

def handle_form_record(record):
    with LLM_LATENCY.labels(model_name="gpt-4o").time():
        result = process_record(record)
  
    if result.requires_human_intervention:
        FORM_PROCESS_TOTAL.labels(status='human_fallback', form_type=record.type).inc()
        send_to_dlq(record) # 发送至死信队列
    else:
        FORM_PROCESS_TOTAL.labels(status='success', form_type=record.type).inc()
```

### 6.2 安全

- 传输：TLS/mTLS（内部服务）。
- 存储：对象存储服务端加密，数据库透明加密（可选）。
- 权限：RBAC + 最小权限 + 操作审计。

---

## 7. 技术选型总表（推荐）

| 模块             | 推荐技术                             | 主要优点                           |
| :--------------- | :----------------------------------- | :--------------------------------- |
| API 网关与服务层 | FastAPI + Uvicorn                    | 高性能异步、接口定义清晰、易于扩展 |
| 对象存储         | MinIO                                | S3 兼容、私有化部署成熟、成本可控  |
| 调度与缓存       | Celery + Redis                       | 分布式任务成熟、重试和限流能力强   |
| 关系数据库       | PostgreSQL + PgBouncer               | JSONB 强、事务可靠、连接治理好     |
| 向量检索         | Qdrant                               | 查询延迟低、部署轻量、运维简单     |
| 大模型推理       | vLLM + Qwen2-VL                      | 高吞吐、显存友好、支持 LoRA        |
| 训练微调         | PEFT / Unsloth                       | 微调成本低、迭代速度快             |
| 可观测性         | OpenTelemetry + Prometheus + Grafana | 全链路追踪与告警体系完善           |

---

## 8. 里程碑建议

1. M1（2 周）：打通“上传-预处理-提取-状态查询”主链路。
2. M2（2 周）：完成对齐适配与下游草稿推送，接入 DLQ。
3. M3（2 周）：上线三分屏审核与反馈回流。
4. M4（持续）：LoRA 周期迭代与黄金集回归门禁。

---

## 9. 项目排期

> **排期原则（敏捷迭代与端到端交付）**
>
> - **Sprint 0先行**：以第3节目录树为“交付清单”，先完成快速失败与可流转的工程骨架（TDD增量导向）。
> - **效果与基建并重**：为大模型Prompt调优、前端联调、指标看板与硬体部署预留专属工时包。
> - **闭环验收**：每个节点均需给出“验收标准 + 测试方法”，联调节点必须包含下游服务方介入。

### 阶段总览（Sprint 规划 → 目的）

#### Sprint 0: 工程骨架与基础支撑层联调（MVP 跑通）
1. **阶段 A：基础支撑层** (DB、Redis、MinIO配置接入)
2. **阶段 B：多模态预处理** (图像音道防伪去噪流管)
3. **阶段 C：高并发调度** (Celery优先级调度与限流重试)
4. **阶段 D：AI 解析骨架** (路由分发与VLLM接口挂载，使用Mock或Baseline Prompt)
5. **阶段 E：遗留系统适配** (多重匹配规则与推库幂等)
6. **阶段 F：持续学习管道** (反馈入口与评测脚本)

#### Sprint 1: 业务准确率调优与体验打通（集成与精度）
7. **阶段 G：AI效果与提示词调优** 
   - 目的：适配多模态（语音+图纸）的提示词迭代、Few-shot模板微调、处理大模型幻觉，完成行业术语黑话的字典沉淀，使得整体字段提取准确率达到验收基线（如85%）。
8. **阶段 I：前端与基础设施集成**
   - 目的：完成 React + Ant Design 的三分屏UI研发；部署 GPU 算力集群池及依赖服务的 K8s/Docker 组网配置。

#### Sprint 2: 生产就绪与高可用演练（非功能性保障）
9. **阶段 H：核心链路压测与可观测性打通**
   - 目的：完成全链路超大文件并发压力测试，验证 Celery 降级与限流阈值；接入 Prometheus 与 Grafana 实现业务指标与系统指标的看板化监控。

---

### 📊 进度跟踪表示例 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成
> **更新机制**：基于每一项实现（实现类/接口）完成后更新看板状态。

| 任务编号 | 任务名称                      | 状态    | 估算工时 | 前置依赖 | 下游依赖 / 前提条件 | 交付产物（主文件）                    |
| :------- | :---------------------------- | :------ | :------- | :------- | :------------------ | :------------------------------------ |
| A1       | 环境与参数注入初始化          | `[ ]` | 1h       | 无       | 无                  | `src/main.py`                       |
| A2       | 物理表模型与存取隔离层建立    | `[ ]` | 1h       | A1       | 无                  | `src/core/database.py`              |
| A3       | Redis 缓存池及存储底层配通    | `[ ]` | 1h       | A1       | 无                  | `src/core/redis_pool.py`            |
| B1       | 核心请求门面 API 支持         | `[ ]` | 1h       | A2, A3   | 无                  | `src/api/routers/tasks.py`          |
| B2       | 结构视觉信息透视去噪防伪算子  | `[ ]` | 1h       | B1       | 无                  | `src/preprocess/vision.py`          |
| B3       | 音频清洗转录与多模拆页管道    | `[ ]` | 1h       | B1       | 无                  | `src/preprocess/audio.py`           |
| C1       | 调度工厂与重试路由分配        | `[ ]` | 1.5h     | B1, B3   | 无                  | `src/worker/celery_app.py`          |
| C2       | 多级队列消费者与调度分配      | `[ ]` | 1.5h     | C1       | 无                  | `src/worker/tasks/consumer.py`      |
| C3       | Celery 分派消费者降级接引槽   | `[ ]` | 1.5h     | C2       | 无                  | `src/worker/tasks/entry.py`         |
| D1       | 大小视觉模型通信对接枢纽      | `[ ]` | 1.5h     | A3       | 需具备推理端沙盒    | `src/engine/vllm_client.py`         |
| D2       | Agent反思架构与引擎任务分发器 | `[ ]` | 1.5h     | D1       | 无                  | `src/engine/orchestrator.py`        |
| D3       | 核心 AI 结构化解析与管道搭建  | `[ ]` | 1.5h     | D2       | 无                  | `src/engine/extractor.py`           |
| E1       | 四层面外键对齐缓存调度计算    | `[ ]` | 2h       | D3       | 行业术语本/向量库环境 | `src/adapter/aligner.py`            |
| E2       | 离线遗留系统回填触发推库      | `[ ]` | 1h       | E1       | **外部基建团队联调沙盒** | `src/adapter/legacy_rest/client.py` |
| F1       | 自回流修订评测与清洗闭环管道  | `[ ]` | 1h       | E2       | **前端三分屏UI就绪** | `scripts/clean_feedback.py`         |
| F2       | 本地微调与门禁控制策略评测    | `[ ]` | 1h       | F1       | 无                  | `scripts/auto_eval.py`              |
| **G1**   | **OCR防伪与透视算法业务调优**| `[ ]` | **3d**   | B2       | 真实场景带噪图集集齐 | 业务配置参数及算法阈值修订            |
| **G2**   | **多模态Prompt与Few-Shot调优**| `[ ]` | **5d**   | D3       | 黄金基准集构建完成  | `prompt_templates.py`及策略库修订   |
| **H1**   | **全链路并发压测与容量防崩溃测试**| `[ ]` | **2d** | C3, E2 | 运维提供压测环境   | 压测报告与并发限流阈值修正文件        |
| **H2**   | **Prometheus与Grafana监控接引**| `[ ]` | **2d**  | 全链路完成 | DevOps提供监控基座 | Helm/Docker-Compose指标注入更新      |
| **I1**   | **前端三分屏人工验证UI研发**  | `[ ]` | **5d**   | F1设计   | UX/UI 设计落图      | React前端仓库及组件代码                  |
| **I2**   | **异构资源组网与应用集群部署**| `[ ]` | **3d**   | 架构敲定 | VLLM机器与网络策略打通 | `docker-compose.yml` 及 K8s Yamls   |

---

### Sprint 0: 工程骨架对接详细排期 (阶段A ~ 阶段F)
*(注：此阶段首要目标为打通全链路状态机流转，不追求最终的AI数据结构化精度)*

**A1：环境与参数注入初始化**
- **目标**：搭建 API 入口基座与 FastAPI 运行实例，集成 CORS、全局流控规则和环境变量检测能力。
- **测试方法**：利用 pytest 断言非法加载直接抛出预警机制。

**A2：物理表模型与存取隔离层建立**
- **目标**：以 SQLAlchemy 完成实体映射，建立基于 PgBouncer 的连接池策略；初始化如 `Task`、`EntityAlignmentLog` 模型。
- **测试方法**：利用隔离数据库写入测试记录，验证序列化读取。

**A3：Redis 缓存池及存储底层配通**
- **目标**：挂接全局对象源并连接池；构建 MinIO 预验证签名的存放工具以解耦存储和上传逻辑。
- **测试方法**：进行 set/get 请求比对一致化缓存键。

**B1：核心请求门面 API 支持**
- **目标**：产出标准的 PENDING 数据载荷记录并校验 `UUID`。
- **测试方法**：使用 `TestClient` 发送不同类型文件声明验证流式响应。

*(备注：B2~F2 任务细节同架构分层设计，在此省略描述，核心标准为各层桩代码流转通过即可。)*

---

### Sprint 1: 业务准确率调优与体验打通详细排期 (阶段G, 阶段I)

**G1：OCR防伪与透视算法业务调优**
- **目标**：接入真实工地的“泥污、强反光、严重扭曲”样本集，反复微调双边滤波参数与透视变换锚点抓取准确率。
- **验收标准**：在 500 张高保真测试集上，识别阻断或解析空白的失败率低于 5%。

**G2：多模态Prompt与Few-Shot调优**
- **目标**：通过业务领域专家的反馈，针对复杂术语映射（如 C35/C40 混凝土，隐蔽工程黑话）进行 Prompt 改写与边界约束。
- **验收标准**：通过 `auto_eval.py` 在“黄金基准集”上的精确匹配测试，字段提取 F1-Score 达到 85% 以上。

**I1：前端三分屏人工验证UI研发**
- **目标**：研发供人工坐席使用的高低保真图片放大、表单高亮及重切边（Re-Crop）交互前端模块。
- **验收标准**：与后端 `POST /correction` 接口顺畅联调并成功投递修正历史入库，单据人工审核交互延迟体验小于 1s，单据纠错时长控制在 15s。

**I2：异构资源组网与应用集群部署**
- **目标**：准备底层运维组件（MinIO, PG, Redis），以及在 GPU 节点完成基于 vLLM 的大模型推理基座承载配置。
- **验收标准**：通过 `docker-compose up` 后各容器服务日志探针均反馈 `Healthy` 状态，并完成内网段互相寻址连通。

---

### Sprint 2: 生产就绪与高可用演练详细排期 (阶段H)

**H1：全链路并发压测与削峰熔断测试**
- **目标**：模拟日均 10,000 单的并发量峰值及网络劣化大体量投递场景，检验 Celery 队列限流策略、Redis 计数器与 vLLM 集群的并发瓶颈。
- **验收标准**：并发数触及显存天花板时系统自动路由降级轻量模型处理或挂起重试；系统 OOM 和假死次数为0；异步超时或失败任务全部落入死信队列。

**H2：Prometheus与Grafana监控接引**
- **目标**：在核心业务流节点注入 Metric 抓取逻辑，建立如 `task_throughput`、`dlq_depth`、`llm_latency` 的数据表盘。
- **验收标准**：在 Grafana 控制台可直接呈现任务处理延迟 P95 曲线，并通过 Webhook 成功触发一次业务死锁（死信队列拥塞）告警钉钉/邮件通知。

## 10其他

**黄金集核心字段设计**
```json
{
  "sample_id": "G_2026_0311_001",           // 样本唯一ID
  "domain_category": "concrete_pouring",    // 施工业务类别（如：混凝土浇筑单、钢筋隐蔽工程等）
  "difficulty_level": "hard",               // 难度级别（easy, medium, hard）
  "source_type": "image_and_audio",         // 源数据模态组合

  // 1. 输入部分 (Inputs)
  "inputs": {
    "media_refs": [
      "s3://bucket/test_data/images/concrete_form_noisy_01.jpg"
    ],
    "audio_transcript_raw": "这个...啊C35混凝土已经打完了，大概用了120方吧，上面有点渗水。", // 原始录音转写文本（可能带语气词或噪声）
    "ocr_raw_text": "【原始OCR如果可用，也作为辅助输入提供】",
    "prompt_version": "v2" // 生成标注时基于的提示词基座大版本
  },

  // 2. 环境与业务上下文 (Context)
  "context": {
    "project_id": "PROJ_XY_001",
    "current_date": "2026-03-10",
    "schema_version": "v1.2" // 需要提取校验的表单结构Schema版本
  },

  // 3. 预期标准答案 / Ground Truth (Expected Outputs)
  "ground_truth": {
    // 关键提取字段精确匹配依据
    "extracted_fields": {
      "material_type": "C35混凝土",
      "volume_m3": 120,
      "issue_reported": "表面渗水",
      "completion_status": true
    },
    // 对齐测试 (Entity Alignment) 的预期ID映射
    "aligned_foreign_keys": {
      "material_code": "MAT-CONC-C35", 
      "vendor_id": "V-NULL"
    },
    // 预期业务规则是否应该报错/人工介入？
    "expect_need_human_review": false 
  },

  // 4. 评估控制与权重 (Evaluation Criteria)
  "eval_criteria": {
    // 允许不同字段采用不同的评估方式
    "exact_match_fields": ["volume_m3", "completion_status"], 
    "semantic_match_fields": ["issue_reported"], // 允许“渗水”和“轻微漏水”有语义相似度容忍
    // 在 LLM-as-a-judge 评估时，针对该实体的特定评判指令
    "judge_instruction": "判断大模型是否准确提取出方量120，以及是否注意到渗水问题。材质必须明确为C35。"
  }
}
```

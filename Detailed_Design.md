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
│   │       ├── entry.py            # 从API承接的异步任务入口 (分装路由)
│   │       ├── dlq.py              # 死信重试执行器
│   │       └── schedule.py         # Celery Beat定时清理等批处理操作
│   ├── engine/                     # 【AI 解析与大小模型级联引擎】
│   │   ├── orchestrator.py         # 级联调度器 (复杂度阈值判定, Fallback分流)
│   │   ├── light_model.py          # PaddleOCR等轻量模型调用客户端
│   │   ├── vllm_client.py          # 与GPU侧vLLM通信、Token Bucket流流控
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
     $$concurrency\_inflight < threshold\_{gpu}$$

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

---

## 3.3 AI 解析与大小模型级联引擎

### 3.3.1 目标

以最低成本获得可审计的高质量结构化结果，兼顾常规文档吞吐与复杂文档准确率。

### 3.3.2 实现算法

1. 复杂度判定与级联路由
   - 计算复杂度分：
     $$S = 0.35\cdot(1-conf) + 0.25\cdot layout\_entropy + 0.20\cdot handwriting\_ratio + 0.20\cdot damage\_score$$
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
     $$confidence = 0.4\cdot conf_{ocr} + 0.3\cdot conf_{llm} + 0.3\cdot pass_{rule}$$

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

---

## 3.4 现有系统适配器与强制业务对齐模块

### 3.4.1 目标

将 AI 结构化数据强制转换为现有系统可落库的外键化数据，保证幂等、可重试、可回溯。

### 3.4.2 实现算法

四级对齐流水线：

1. Level 1：Redis 精确匹配（别名词典，$O(1)$）
2. Level 2：文本相似度融合（Jaro-Winkler + TF-IDF）
   $$score = 0.6\cdot JW + 0.4\cdot CosSim_{tfidf}$$
3. Level 3：向量检索兜底（Top-3）
4. Level 4：人工挂起（`Unmapped`）并回灌别名词典

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
- Redis 词典缓存：高频别名秒级命中，显著降低向量检索压力。
- HTTP 级重试：结合 tenacity / retrying 库可直接在 HTTP 客户端层面实现。

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

### 6.2 安全

- 传输：TLS/mTLS（内部服务）。
- 存储：对象存储服务端加密，数据库透明加密（可选）。
- 权限：RBAC + 最小权限 + 操作审计。

---

## 7. 技术选型总表（推荐）

| 模块 | 推荐技术 | 主要优点 |
| :-- | :-- | :-- |
| API 网关与服务层 | FastAPI + Uvicorn | 高性能异步、接口定义清晰、易于扩展 |
| 对象存储 | MinIO | S3 兼容、私有化部署成熟、成本可控 |
| 调度与缓存 | Celery + Redis | 分布式任务成熟、重试和限流能力强 |
| 关系数据库 | PostgreSQL + PgBouncer | JSONB 强、事务可靠、连接治理好 |
| 向量检索 | Qdrant | 查询延迟低、部署轻量、运维简单 |
| 大模型推理 | vLLM + Qwen2-VL | 高吞吐、显存友好、支持 LoRA |
| 训练微调 | PEFT / Unsloth | 微调成本低、迭代速度快 |
| 可观测性 | OpenTelemetry + Prometheus + Grafana | 全链路追踪与告警体系完善 |

---

## 8. 里程碑建议

1. M1（2 周）：打通“上传-预处理-提取-状态查询”主链路。
2. M2（2 周）：完成对齐适配与下游草稿推送，接入 DLQ。
3. M3（2 周）：上线三分屏审核与反馈回流。
4. M4（持续）：LoRA 周期迭代与黄金集回归门禁。

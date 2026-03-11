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

---

## 3.4 现有系统适配器与强制业务对齐模块

### 3.4.1 目标

将 AI 结构化数据强制转换为现有系统可落库的外键化数据，保证幂等、可重试、可回溯。

### 3.4.2 实现算法

四级对齐流水线：

1. Level 1：Redis 精确匹配（别名词典，$O(1)$）
2. Level 2：文本相似度融合（Jaro-Winkler + TF-IDF）
   $$
   score = 0.6\cdot JW + 0.4\cdot CosSim_{tfidf}
   $$
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

> **排期原则（严格对齐本项目的架构分层与目录结构）**
>
> - **只按本文档设计落地**：以第3节目录树为“交付清单”，每一步都要在文件系统上产生可见变化。
> - **1 小时一个可验收增量**：每个小阶段（≈1h）都必须同时给出“验收标准 + 测试方法”，尽量做到 TDD。
> - **先打通主闭环，再补齐默认实现**：优先做“可跑通的端到端路径”，避免出现“只有接口没有实现”的空转。

### 阶段总览（大阶段 → 目的）

1. **阶段 A：工程骨架与基础支撑层**
   - 目的：建立可配置与可测试的 FastAPI 与 Celery 工程骨架，跑通数据库与对象存储的联通，这是后续所有子系统的前置条件。
2. **阶段 B：多模态数据接入与预处理模块**
   - 目的：构建核心数据输入口，支持图片、音频、文档接收，完成防伪、去噪及碎片拆分（对应图像防伪增强、音频降噪、文档拆分拆解）。
3. **阶段 C：高并发批处理调度模块**
   - 目的：使用消息队列削峰填谷，打通任务优先级编排及多级异常退避重试流转链（建立 `q_realtime`、`q_batch` 和 `q_dlq_retry`），实现基于令牌桶的保护机制。
4. **阶段 D：AI 解析与大小模型级联引擎**
   - 目的：完成轻量模型 OCR 和大模型 vLLM 的协同。实现复杂度判决路由、`Plan-Execute-Reflect` 校验抽象及柔性置信反馈网体系。
5. **阶段 E：遗留系统适配器与强制对齐模块**
   - 目的：建立基于 Redis、TF-IDF 与 Qdrant 的四级映射对齐管道框架。确保经过 AI 解析的非结构化结构能正确投递回原有基建系统（基于幂等保护推库）。
6. **阶段 F：人机协同与闭环持续学习模块**
   - 目的：提供数据修正 API，落地数据噪音清理清洗离线处理脚本以及 LoRA 微调触发评测自动门禁脚手架。

---

### 📊 进度跟踪表示例 (Progress Tracking)

> **状态说明**：`[ ]` 未开始 | `[~]` 进行中 | `[x]` 已完成
> **更新机制**：基于每一项实现（实现类/接口）完成后更新看板状态。

| 任务编号 | 任务名称                      | 状态    | 估算工时 | 前置依赖 | 交付产物（主文件）                    |
| :------- | :---------------------------- | :------ | :------- | :------- | :------------------------------------ |
| A1       | 环境与参数注入初始化          | `[ ]` | 1h       | 无       | `src/main.py`                       |
| A2       | 物理表模型与存取隔离层建立    | `[ ]` | 1h       | A1       | `src/core/database.py`              |
| A3       | Redis 缓存池及存储底层配通    | `[ ]` | 1h       | A1       | `src/core/redis_pool.py`            |
| B1       | 核心请求门面 API 支持         | `[ ]` | 1h       | A2, A3   | `src/api/routers/tasks.py`          |
| B2       | 结构视觉信息透视去噪防伪算子  | `[ ]` | 1h       | B1       | `src/preprocess/vision.py`          |
| B3       | 音频清洗转录与多模拆页管道    | `[ ]` | 1h       | B1       | `src/preprocess/audio.py`           |
| C1       | 调度工厂与重试路由分配        | `[ ]` | 1.5h     | B1, B3   | `src/worker/celery_app.py`          |
| C2       | Celery 分派消费者降级接引槽   | `[ ]` | 1.5h     | C1       | `src/worker/tasks/entry.py`         |
| D1       | 大小视觉模型通信对接枢纽      | `[ ]` | 1.5h     | A3       | `src/engine/vllm_client.py`         |
| D2       | Agent反思架构与引擎任务分发器 | `[ ]` | 1.5h     | D1       | `src/engine/orchestrator.py`        |
| E1       | 四层面外键对齐缓存调度计算    | `[ ]` | 2h       | D2       | `src/adapter/aligner.py`            |
| E2       | 离线遗留系统回填触发推库      | `[ ]` | 1h       | E1       | `src/adapter/legacy_rest/client.py` |
| F1       | 自回流修订评测与清洗闭环管道  | `[ ]` | 1h       | E2       | `scripts/clean_feedback.py`         |
| F2       | 本地微调与门禁控制策略评测    | `[ ]` | 1h       | F1       | `scripts/auto_eval.py`              |

---

### 详细排期与分步验收标准（TDD 增量导向）

#### 阶段 A：工程骨架与基础支撑层

**A1：环境与参数注入初始化**

- **目标**：搭建 API 入口基座与 FastAPI 运行实例，集成 CORS、全局流控规则和环境变量检测能力。
- **修改文件**：`src/main.py`、`src/core/config.py`
- **实现类/函数**：`Config` 设置类结构、实例化的 `app` 并承接依赖钩子。
- **测试方法**：利用 pytest 在本地无网络下读取 `Settings` 断言非法加载直接抛出加载预警机制。

**A2：物理表模型与存取隔离层建立**

- **目标**：以 SQLAlchemy 完成实体映射，建立基于 PgBouncer 的连接池策略；初始化如 `Task`、`EntityAlignmentLog` 模型。
- **修改文件**：`src/core/database.py`、`src/models/domain.py`
- **实现类/函数**：`get_db` 异步钩子、`ai_parse_tasks` 类建立。
- **测试方法**：依靠隔离测试基集向库中写入 1 笔假 Task并校验 `UUID` 解析持久是否自洽完备。

**A3：Redis 缓存池及存储底层配通**

- **目标**：挂接全局对象源并连接池；构建 MinIO 预验证签名的存放工具以解耦存储和上传逻辑。
- **修改文件**：`src/core/redis_pool.py`、`src/core/storage.py`
- **实现类/函数**：`get_redis` 连接、`StorageManager.upload_buffer`。
- **测试方法**：建立与假 Mock 池客户端实例进行 set/get 请求比对一致化缓存键。

#### 阶段 B：多模态数据接入与预处理模块

**B1：核心请求门面 API 支持**

- **目标**：设计支持挂载 `POST /tasks` 请求与查询详情 API 逻辑层路由口，产出标准的 PENDING 数据载荷记录。
- **修改文件**：`src/api/routers/tasks.py`、`src/api/dependencies.py`
- **实现类/函数**：`create_multimodal_task` 函数、`check_access` 中间件层。
- **测试方法**：使用 `TestClient` 发送不同类型文件声明；校验返回是否有序遵循生成 32 位 UUID 的机制流。

**B2：结构视觉信息透视去噪防伪算子**

- **目标**：利用 OpenCV 和传统算法组装双边滤波纠偏模块及水印截取逻辑。
- **修改文件**：`src/preprocess/vision.py`
- **实现类/函数**：`DeskewEnhancer.apply`、`SpoofingDetector.check_watermark`。
- **测试方法**：准备手工倾斜及附加不合规图样的标准图件驱动单元测试观察还原纠正打分准确性。

**B3：音频清洗转录与多模拆页管道**

- **目标**：封包针对音频的 Whisper 预调用管道以及对 PDF 及 Excel 切分 JSON 快照能力器加载。
- **修改文件**：`src/preprocess/audio.py`、`src/preprocess/document.py`
- **实现类/函数**：`AudioPreprocess.vad_and_transcribe`、`DocumentShredder.split_pages`。
- **测试方法**：注入带有高噪声的声音片段比对回返字符结构或使用超多页的 PDF 流测试大文件的页码对应字典。

#### 阶段 C：高并发批处理调度模块

**C1：调度工厂与重试路由分配**

- **目标**：将 Celery 嵌入至 FastAPI 环境，分配多级消费队列 (`q_realtime`, `q_batch`, `q_dlq_retry`)，设置 Token Bucket。
- **修改文件**：`src/worker/celery_app.py`
- **实现类/函数**：`app_celery`、路由判定键设定。
- **测试方法**：利用模拟 worker 抛空测试消息查阅不同优先级设定队列投递结果落点及限流情况。

**C2：Celery 分派消费者降级接引槽与死信处理**

- **目标**：实现网络故障的指数重试及彻底失效异常转交至人工审核挂起管道，即任务路由主状态机执行入口。
- **修改文件**：`src/worker/tasks/entry.py`、`src/worker/tasks/dlq.py`
- **实现类/函数**：`run_extract_pipeline` 消费端方法、`handle_dead_letter`。
- **测试方法**：针对业务函数模拟故意掷出三连超时抛压 `RetryError`，判定死信队中记录有堆栈错误抛出。

#### 阶段 D：AI 解析与大小模型级联引擎

**D1：大小视觉模型通信对接枢纽**

- **目标**：完成对大模型推理服务 (vLLM) 以及基础轻体模型 API 调用的高吞吐支持载体设定。
- **修改文件**：`src/engine/vllm_client.py`、`src/engine/light_model.py`
- **实现类/函数**：`VLLMEngineClient.infer_structured_format`、`PaddleEdge.invoke`。
- **测试方法**：截胡网口挂起网络返回验证熔断限流报错。

**D2：Agent 反思架构与级联任务分发器**

- **目标**：依据置信分实现复杂任务转发。若模型打分下降走 Agent 计划执行反思结构。
- **修改文件**：`src/engine/orchestrator.py`、`src/engine/agent/validator.py`、`src/engine/agent/tools.py`
- **实现类/函数**：`CascadeRouter.determine_complexity`、`RuleValidator.reflect_output`。
- **测试方法**：强行设置一个包含“时间逻辑倒置冲突”规则的验证案例，观察系统是否能够自发重入截切工具进行二验。

#### 阶段 E：遗留系统适配器与强制对齐模块

**E1：四层面外键对齐缓存调度与融合对齐**

- **目标**：实现多层遗留别名降级配对——从 Redis 超高速层比对到 TF-IDF 甚至召出向量库。
- **修改文件**：`src/adapter/aligner.py`、`src/adapter/qdrant_client.py`
- **实现类/函数**：`FourLevelAligner.match` 以及 `QdrantEngine.search_similar_key`。
- **测试方法**：编写含偏僻拼写与混杂的文字项输入验证字典比对不命中下的向量库容错查找精准度。

**E2：离线遗留系统回填触发推库**

- **目标**：完成将结果经过 ID 幂等锁定请求结构打包推送到外部系统。
- **修改文件**：`src/adapter/legacy_rest/client.py`
- **实现类/函数**：`LegacyPlatformAdapter.publish_draft`。
- **测试方法**：对假接收接口调用并发投递 10 例并利用 `task_id` 观察避免冗余建立问题。

#### 阶段 F：人机协同与闭环持续学习模块

**F1：自回流修订纠偏系统 API**

- **目标**：建设中后台调用的三分屏人工验证通道、及回滚纠偏逻辑并落库到回馈资源池进行训练标注储备。
- **修改文件**：`src/api/routers/feedback.py`、`scripts/clean_feedback.py`
- **实现类/函数**：`update_task_correction`，清洗脚本的 `remove_pii_noise`。
- **测试方法**：模拟发送带有不合法修订键体结构的补救表单查询合法校验及落后表持久记录追踪状态是否切更为人工修补。

**F2：微调脚本钩子及黄金集闭门门禁**

- **目标**：编写调用 PEFT 组件开启 LoRA 特征训练合并以及调用离线黄金基准集验证结果卡点的执行点。
- **修改文件**：`scripts/auto_eval.py`、`scripts/run_lora_tuning.sh`
- **实现类/函数**：以 bash 调度 `train`，`Evaluator.ensure_no_regression`。
- **测试方法**：投递一份被故意劣化修改结果的验证集观察脚本是否拦截当前模型应用晋升抛异常下发告警。


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

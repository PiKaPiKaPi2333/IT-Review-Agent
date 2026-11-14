# 审计智能问答系统架构设计文档

> 版本：v0.1（草案）
> 日期：2026-10-28

---

## 1. 概述

### 1.1 项目目标

构建一个面向**审计场景**的智能问答系统：审计方提出问题后，系统自动完成问题分类、知识库检索、充分性判断、带引用的回答生成与引用核验，最终返回一份**可溯源、可核验**的答案。

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **可溯源** | 每条回答必须附带知识库片段级引用（文档 ID + 页码/段落） |
| **可核验** | 独立的验证 Agent 对引用做二次校验，防止模型幻觉 |
| **结构化** | LLM 所有输出经 Pydantic 校验，非法输出自动重试/降级 |
| **可评估** | LangSmith 全链路追踪 + RAGAS 离线评估，效果可量化 |
| **有记忆** | 区分短时（会话内）记忆与长期（跨会话）记忆，PostgreSQL 持久化 |
| **可编排** | 基于 LangGraph 状态机驱动，流程显式、可观测、可恢复 |

### 1.3 核心业务流程

```
审计方提问
   │
   ▼
问题分类 Agent ──► 判断问题类型（事实型 / 分析型 / 澄清型 / 超范围 …）
   │
   ▼
检索 Agent ──► 从 Milvus 知识库召回相关片段
   │
   ▼
充分性判断 ──┬── 不充分 ──► 补充检索 / 反问澄清 / 返回"证据不足"
   │         │
   │ 充分    ▼
   ▼      生成模块（LLM 生成带引用的回答，Pydantic 校验）
   │
   ▼
验证 Agent ──► 检查引用是否真实支持回答
   │
   ├── 验证通过 ──► 返回审计方
   └── 验证失败 ──► 修正重生成 / 降级回答并标注置信度
```

---

## 2. 总体架构

### 2.1 分层架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        接入层 (Interface)                         │
│   REST API (FastAPI)  │  CLI  │  （后续可扩展 Web UI）              │
├─────────────────────────────────────────────────────────────────┤
│                      编排层 (Orchestration)                       │
│  LangGraph StateGraph：流转控制 / 条件路由 / 重试 / 人机协同         │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────────────┐   │
│  │ 问题分类 Agent │ │   检索 Agent   │      验证 Agent        │   │
│  └───────────────┘ └───────────────┘ └───────────────────────┘   │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │        充分性判断 / 回答生成（Graph 内共享 LLM 调用节点）      │   │
│  └───────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────┤
│                       能力层 (Capabilities)                       │
│  Pydantic 输出校验 │ Prompt 管理 │ Structured Output │ 重试策略     │
├─────────────────────────────────────────────────────────────────┤
│                     记忆层 (Memory & State)                       │
│  短时记忆：LangGraph Checkpointer（会话状态）                      │
│  长期记忆：对话历史/用户偏好/审计上下文（PostgreSQL 持久化）          │
├─────────────────────────────────────────────────────────────────┤
│                      知识库层 (Knowledge Base)                    │
│  MinerU 文档解析 │ 切分 │ Embedding │ Milvus 向量检索 │ 混合检索     │
├─────────────────────────────────────────────────────────────────┤
│                      基础设施层 (Infrastructure)                   │
│  PostgreSQL │ Milvus │ LLM API │ LangSmith │ Docker Compose      │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 技术选型汇总

| 组件 | 技术 | 用途 |
|------|------|------|
| Agent 编排 | **LangGraph** | 状态机式多 Agent 编排、条件路由、Checkpointer |
| LLM | Claude / 兼容 OpenAI 协议的模型（可配置） | 分类、生成、验证 |
| 结构化输出 | **Pydantic v2** + LLM Structured Output | LLM 输出格式校验 |
| 文档解析 | **MinerU** | PDF/Word 等审计资料 → 结构化 Markdown/JSON |
| 向量数据库 | **Milvus** | 知识片段向量存储与检索 |
| 持久化 | **PostgreSQL** | 长期记忆、会话元数据、审计日志 |
| 可观测 | **LangSmith** | 全链路 Trace、Prompt 版本、在线调试 |
| 离线评估 | **RAGAS** | 检索质量 / 忠实度 / 引用准确性 评估 |
| API 服务 | FastAPI | 对外 REST 接口 |
| 部署 | Docker Compose | 一键拉起全栈 |

---

## 3. 多 Agent 设计（编排层）

### 3.1 Agent 划分

按照 plan 要求实现三个显式 Agent + 一个隐含编排层：

| Agent | 职责 | 输入 | 输出（Pydantic 模型） |
|-------|------|------|----------------------|
| **问题分类 Agent** (ClassifierAgent) | 判断问题类型、是否在知识库范围内、是否需澄清 | 用户问题 + 会话上下文 | `ClassificationResult` |
| **检索 Agent** (RetrievalAgent) | 生成查询变体、多路召回、重排序、充分性初判 | 问题 + 类型 | `RetrievalResult` |
| **验证 Agent** (VerifierAgent) | 逐条核对引用是否真实支持答案断言 | 答案 + 引用片段 | `VerificationResult` |
| **编排层** (LangGraph Graph) | 节点路由、状态流转、失败重试、循环控制 | 全局 State | — |

> 设计取舍：充分性判断与回答生成不单列为 Agent，而是作为 Graph 中的**判断节点（conditional edge）**与**共享 LLM 节点**，避免过度拆分导致的状态传递开销。它们与检索 Agent 形成闭环（不充分 → 重检索）。

### 3.2 LangGraph 状态定义

全局状态（`TypedDict` / Pydantic Model，随 Checkpointer 持久化）：

```python
class AgentState(BaseModel):
    # —— 会话标识 ——
    thread_id: str                  # LangGraph thread，映射 PostgreSQL 会话
    user_id: str

    # —— 输入 ——
    question: str                   # 审计方原始问题
    attachments: list[str] = []     # 可选：随问题附带的材料 ID

    # —— 分类阶段 ——
    classification: ClassificationResult | None

    # —— 检索阶段 ——
    retrieval_round: int = 0        # 当前检索轮次（防死循环）
    retrieved_chunks: list[RetrievedChunk] = []
    sufficiency: SufficiencyResult | None

    # —— 生成阶段 ——
    draft_answer: AnswerWithCitations | None

    # —— 验证阶段 ——
    verification: VerificationResult | None

    # —— 输出 ——
    final_answer: FinalAnswer | None
    error: str | None = None
```

### 3.3 Graph 拓扑

```python
graph = StateGraph(AgentState)

graph.add_node("classify",     classifier_agent)      # 问题分类 Agent
graph.add_node("retrieve",     retrieval_agent)       # 检索 Agent
graph.add_node("judge",        sufficiency_judge)     # 充分性判断（含 LLM 判断 + 规则）
graph.add_node("generate",     answer_generator)      # 带引用生成（Pydantic 强校验）
graph.add_node("verify",       verifier_agent)        # 验证 Agent
graph.add_node("regenerate",   answer_regenerator)    # 验证失败后的修正生成
graph.add_node("clarify",      clarification_node)    # 澄清反问 / 超范围应答
graph.add_node("finalize",     finalizer)             # 汇总输出 + 写记忆

graph.set_entry_point("classify")

graph.add_conditional_edges("classify", route_by_type, {
    "in_scope":  "retrieve",
    "clarify":   "clarify",
    "out_of_scope": "finalize",
})

graph.add_conditional_edges("judge", route_by_sufficiency, {
    "sufficient":     "generate",
    "retry_retrieve": "retrieve",     # 改写查询，二次检索（≤ MAX_ROUNDS）
    "insufficient":   "finalize",     # 返回"证据不足"结论
})

graph.add_conditional_edges("verify", route_by_verification, {
    "pass":        "finalize",
    "fixable":     "regenerate",      # 局部引用错误 → 修正重生成（≤ 2 次）
    "unfixable":   "finalize",        # 降级输出 + 低置信度标注
})

graph.add_edge("generate",    "verify")
graph.add_edge("regenerate",  "verify")
graph.add_edge("clarify",     END)
graph.add_edge("finalize",    END)
```

**防失控约束**：
- `MAX_RETRIEVAL_ROUNDS = 2`（检索重试上限）
- `MAX_REGENERATION = 2`（修正重生成上限）
- 超出上限走降级路径：明确告知审计方"现有证据不足/引用未能完全核验"，而非硬编造。

### 3.4 各 Agent 详细设计

#### 3.4.1 问题分类 Agent

- **输入**：问题 + 短时记忆（当前会话历史）+ 长期记忆摘要（该审计项目的上下文偏好）
- **输出模型**：

```python
class QuestionType(str, Enum):
    FACTUAL     = "factual"      # 事实型：单点取证（制度条款、金额、日期）
    ANALYTICAL  = "analytical"   # 分析型：需多片段综合（合规性判断、风险分析）
    CLARIFY     = "clarify"      # 澄清型：问题模糊，需反问
    OUT_OF_SCOPE= "out_of_scope" # 超出知识库范围

class ClassificationResult(BaseModel):
    question_type: QuestionType
    rewritten_question: str          # 归一化后的问题（供检索使用）
    search_queries: list[str]        # 生成的检索查询变体（2~4 个）
    needs_clarification: bool
    clarification_question: str | None
    confidence: float
```

- **要点**：分类与查询改写合并为一次 LLM 调用（structured output），减少链路延迟。

#### 3.4.2 检索 Agent

- **流程**：
  1. 接收 `search_queries`（多查询变体）
  2. Milvus **向量检索** + **关键词/BM25 过滤**（混合检索，Milvus 2.4+ 内置稀疏向量支持）
  3. Rerank（可选：bge-reranker / Cohere Rerank）
  4. 上下文压缩：按 token 预算裁剪片段
- **输出模型**：

```python
class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    page: int | None
    text: str
    score: float
    metadata: dict            # 文档类型、审计年度、部门等过滤字段

class SufficiencyResult(BaseModel):
    is_sufficient: bool
    reason: str
    missing_aspects: list[str]   # 缺失的证据维度，指导二次检索改写
    next_queries: list[str]      # 充分性判断节点为 retry 生成的补查查询
```

#### 3.4.3 验证 Agent

- **任务**：对 `draft_answer.citations` 逐条做 **NLI（自然语言推理）式校验**——
  该引用片段是否蕴含（entail）答案中对应的断言？
- **输出模型**：

```python
class CitationCheck(BaseModel):
    citation_id: str
    citation_index: int        # 对应答案中的 [n] 角标
    verdict: Literal["supported", "unsupported", "partially_supported"]
    reason: str

class VerificationResult(BaseModel):
    overall: Literal["pass", "fixable", "unfixable"]
    checks: list[CitationCheck]
    fix_instruction: str | None   # fixable 时给出修正指令（供 regenerate）
```

- **要点**：
  - 每条引用独立校验，避免长上下文互相干扰（可用并行子调用）；
  - 验证 Agent 使用**与生成不同的 prompt 视角**（判官视角），必要时可换用不同模型以降低同源偏差。

#### 3.4.4 回答生成节点

```python
class Citation(BaseModel):
    index: int
    chunk_id: str
    document_id: str
    page: int | None
    quote: str              # 被引用的原文关键句（供验证与前端高亮）

class AnswerWithCitations(BaseModel):
    answer_markdown: str    # 正文中用 [1] [2] 标注引用
    citations: list[Citation]
    confidence: Literal["high", "medium", "low"]
    caveats: list[str]      # 证据局限性说明
```

---

## 4. 记忆系统设计

### 4.1 双层记忆模型

| 维度 | 短时记忆 | 长期记忆 |
|------|----------|----------|
| 生命周期 | 单次会话（thread）内 | 跨会话永久 |
| 载体 | LangGraph **Checkpointer**（PostgreSQL backend） | PostgreSQL 业务表 |
| 内容 | 当前 State、多轮问答历史、检索中间结果 | 历史问答归档、审计项目上下文、用户偏好、澄清记录 |
| 访问方式 | Graph 自动读写（`thread_id`） | 显式读写（Agent 节点内查询/写入） |

### 4.2 短时记忆（会话内）

- 使用 **langgraph-checkpoint-postgres**，每个 `thread_id` 对应一次审计咨询会话；
- 多轮追问时：
  - 分类 Agent 可读到前轮的 `classification`，处理"接着上面第 2 条再问"这类指代；
  - 检索 Agent 可复用前轮的 `retrieved_chunks`（如新问题与上轮高度相关，可先做增量检索）。

### 4.3 长期记忆（跨会话）

PostgreSQL 中三张核心表（详见 §6）：

| 表 | 存什么 | 谁写入 |
|----|--------|--------|
| `conversations` / `messages` | 会话与消息归档（问答对、引用、验证结论） | finalize 节点 |
| `memory_facts` | 从对话中提炼的稳定事实：用户关注领域、常用口径、已澄清的术语 | finalize 节点（LLM 提炼 + Pydantic 校验后写入） |
| `audit_contexts` | 审计项目级上下文（被审计单位、年度、政策文件清单） | 管理端/导入流程 |

**注入策略**：`classify` 与 `generate` 节点开始时，按 `user_id + 审计项目` 拉取长期记忆摘要（限量 token），注入 system prompt 的上下文区块。

**淘汰策略**：`memory_facts` 设 `importance` 与 `last_accessed_at`，低分且久未访问的记忆定期归档，防止 prompt 无限膨胀。

---

## 5. 知识库管线（MinerU + Milvus）

### 5.1 离线入库流程

```
审计资料 (PDF / Word / 扫描件)
   │
   ▼
MinerU 解析 ──► 结构化输出（Markdown + 版面/坐标/表格 JSON）
   │              （扫描件走 OCR；表格保留结构）
   ▼
后处理 ──► 清洗（页眉页脚/水印）→ 语义切分
   │        （按标题层级 + token 窗口，默认 ~512 token，重叠 ~64）
   │        表格整块切分，不做句子级拆散
   ▼
元数据标注 ──► document_id / 页码 / 章节 / 来源类型 / 审计年度 / 部门
   │
   ▼
Embedding ──► bge-m3（同时产出稠密 + 稀疏向量，服务混合检索）
   │
   ▼
写入 Milvus（collection: kb_chunks）
```

### 5.2 Milvus Collection 设计

```python
# collection: kb_chunks
fields = {
    "chunk_id":      VARCHAR(primary_key),
    "document_id":   VARCHAR,           # 过滤：限定文档范围检索
    "text":          VARCHAR(8192),
    "dense_vector":  FLOAT_VECTOR(1024),# bge-m3 dense
    "sparse_vector": SPARSE_FLOAT_VEC,  # bge-m3 sparse (BM25语意)，支持混合检索
    "page":          INT64,
    "section":       VARCHAR,           # 章节路径
    "doc_type":      VARCHAR,           # regulation / contract / ledger …
    "audit_year":    INT64,
    "department":    VARCHAR,
}
# 索引：dense 用 HNSW；partial filter 常用字段建 scalar index
```

### 5.3 增量更新与一致性

- 文档以 `document_id` 为粒度做幂等入库（重复导入 = 先删后插该 doc 全部 chunk）；
- MinIO/本地盘保留 MinerU 的原始解析结果，`documents` 表记录解析状态机：`uploaded → parsing → indexing → ready / failed`；
- 失败可从 parsing 阶段重试，不必重新上传。

---

## 6. 数据模型（PostgreSQL）

```sql
-- 会话与消息（长期记忆·归档）
CREATE TABLE conversations (
    id           UUID PRIMARY KEY,
    user_id      VARCHAR NOT NULL,
    audit_ctx_id UUID REFERENCES audit_contexts(id),
    title        VARCHAR,
    created_at   TIMESTAMPTZ DEFAULT now(),
    closed_at    TIMESTAMPTZ
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id),
    role            VARCHAR,          -- user / assistant
    content         TEXT,
    citations       JSONB,            -- AnswerWithCitations.citations
    verification    JSONB,            -- 验证 Agent 结论快照
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 长期记忆·事实
CREATE TABLE memory_facts (
    id              UUID PRIMARY KEY,
    user_id         VARCHAR NOT NULL,
    fact_type       VARCHAR,          -- preference / terminology / clarified
    content         TEXT,
    importance      FLOAT DEFAULT 0.5,
    last_accessed_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- 审计项目上下文
CREATE TABLE audit_contexts (
    id           UUID PRIMARY KEY,
    org_name     VARCHAR,            -- 被审计单位
    audit_year   INT,
    description  TEXT,
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- 知识库文档登记
CREATE TABLE documents (
    id          VARCHAR PRIMARY KEY,   -- document_id
    filename    VARCHAR,
    status      VARCHAR,               -- uploaded/parsing/indexing/ready/failed
    parse_meta  JSONB,                 -- MinerU 输出摘要（页数/表格数）
    checksum    VARCHAR,
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 评估数据集与评估记录
CREATE TABLE eval_datasets (
    id          UUID PRIMARY KEY,
    name        VARCHAR,
    items       JSONB                  -- question / ground_truth / expected_docs
);

CREATE TABLE eval_runs (
    id           UUID PRIMARY KEY,
    dataset_id   UUID REFERENCES eval_datasets(id),
    ragas_report JSONB,                -- RAGAS 指标快照
    langsmith_url VARCHAR,
    created_at   TIMESTAMPTZ DEFAULT now()
);
```

> LangGraph Checkpointer 自建其表（`checkpoints` / `checkpoint_writes`），与业务表共存于同一 PostgreSQL 实例、独立 schema（如 `langgraph`），便于备份与迁移。

---

## 7. 输出校验策略（Pydantic）

所有 LLM 调用统一走 **Structured Output 基座**：

```python
def structured_llm_call(
    llm, schema: type[T], prompt: str, *, max_retries: int = 2
) -> T:
    """
    1. 优先使用模型原生 tool/function calling（Claude / GPT 均支持）
    2. 失败/不支持时降级为 JSON mode + Pydantic validate
    3. 校验失败：将 Pydantic 错误信息回喂给 LLM 要求修正（ReAct-式自修复）
    4. 超过 max_retries 抛出 → Graph 层捕获 → 走降级路径
    """
```

分层校验要点：
- **Schema 层**：类型、枚举、必填字段（Pydantic 自动）；
- **语义层**（Pydantic `model_validator`）：
  - 引用 `index` 必须与正文 `[n]` 角标集合一致；
  - `citations` 非空（禁止无引用回答）；
  - `quote` 必须是 retrieved_chunks 中某片段的子串（防引用伪造）；
- **降级路径**：连续校验失败 → finalize 输出"系统暂时无法生成合规回答"+ 原始证据列表，绝不输出未校验内容。

---

## 8. 评估体系（LangSmith + RAGAS）

### 8.1 LangSmith（在线观测）

- 全链路 Trace：每个 Graph 节点、每次 LLM 调用、Milvus 检索均打点（latency / tokens / cost / retries）；
- Prompt 管理：分类/生成/验证三套 prompt 纳入 LangSmith Prompt Hub 版本化，支持 A/B；
- 在线反馈：API 返回的 `answer_id` 支持👍/👎回传，落库用于后续纳入评估集。

### 8.2 RAGAS（离线评估）

维护黄金评估集（`eval_datasets`），每次 prompt/检索策略/模型变更后批量回放：

| RAGAS 指标 | 映射到本系统 |
|------------|--------------|
| `faithfulness` | 答案对引用片段的忠实度（与验证 Agent 结论交叉印证） |
| `answer_relevancy` | 回答与审计问题的相关性 |
| `context_precision` | 检索片段的排序质量（Top-K 命中率） |
| `context_recall` | 召全率（对照 ground_truth 文档） |
| 自定义：`citation_accuracy` | 引用 quote 与原文的一致率（验证 Agent 聚合） |

**评估流水线**：CLI（`make eval`）→ 拉取数据集 → 图迭代回放 → RAGAS 计算 → 报告写入 `eval_runs` + LangSmith 链接。版本变更（prompt/模型/参数）必须附 eval 对比结果。

---

## 9. 项目结构

```
reviewAgent/
├── app/
│   ├── main.py                     # FastAPI 入口
│   ├── api/                        # 路由：问答、文档管理、评估
│   ├── graph/
│   │   ├── state.py                # AgentState 定义
│   │   ├── builder.py              # LangGraph 组装
│   │   └── nodes/
│   │       ├── classifier.py       # 问题分类 Agent
│   │       ├── retriever.py        # 检索 Agent
│   │       ├── sufficiency.py      # 充分性判断
│   │       ├── generator.py        # 带引用生成
│   │       ├── verifier.py         # 验证 Agent
│   │       └── finalize.py         # 输出汇总 + 记忆写入
│   ├── schemas/                    # 全部 Pydantic 模型（含语义校验器）
│   ├── llm/
│   │   ├── client.py               # 模型封装 + structured_llm_call
│   │   └── prompts/                # prompt 模板
│   ├── memory/
│   │   ├── short_term.py           # Postgres checkpointer 配置
│   │   └── long_term.py            # memory_facts 读写
│   ├── knowledge/
│   │   ├── ingest.py               # MinerU 解析编排（状态机）
│   │   ├── chunker.py              # 语义切分
│   │   ├── embedder.py             # bge-m3 封装
│   │   └── milvus_store.py         # collection 管理/混合检索
│   ├── evaluation/
│   │   ├── datasets.py             # 评估集管理
│   │   └── ragas_runner.py         # 离线评估流水线
│   └── infra/
│       ├── config.py               # Pydantic Settings（.env）
│       ├── db.py                   # SQLAlchemy/asyncpg
│       └── tracing.py              # LangSmith 初始化
├── alembic/                        # 数据库迁移
├── eval_datasets/                  # 黄金集（YAML/JSON）
├── docker-compose.yml              # postgres + milvus + etcd + minio + api
├── Makefile
└── tests/
```

---

## 10. 关键时序（单次问答）

```
审计方          API        编排Graph      分类Agent   检索Agent    Milvus    生成节点    验证Agent    PostgreSQL
  │  POST /ask  │             │              │           │          │           │            │            │
  ├────────────►├─load state─►│              │           │          │           │            │            │
  │             │             ├─long-term mem──────────────────────────────────────────────►│            │
  │             │             │◄────────────memory_facts────────────────────────────────────│            │
  │             │             ├──classify───►│           │          │           │            │            │
  │             │             │◄─ClassificationResult      │          │           │            │            │
  │             │             ├──retrieve───────────────►│          │           │            │            │
  │             │             │                          ├──search─►│           │            │            │
  │             │             │◄─────────RetrievalResult─┤◄─chunks──┤           │            │            │
  │             │             ├─judge(sufficiency)        │          │           │            │            │
  │             │             ├──generate────────────────────────────────────►│            │            │
  │             │             │◄─AnswerWithCitations（Pydantic 校验通过）       │            │            │
  │             │             ├──verify──────────────────────────────────────────────────►│            │
  │             │             │◄─VerificationResult───────────────────────────────│        │            │
  │             │             ├──finalize（写 messages / memory_facts）────────────────────────────────►│
  │             │◄─FinalAnswer│              │           │          │           │            │            │
  │◄─200 + 答案──┤             │              │           │          │           │            │            │
```

---

## 11. 非功能性设计

| 关注点 | 方案 |
|--------|------|
| **安全** | LLM API Key 走环境变量；Milvus/PG 不对外暴露端口（compose 内网）；审计资料按项目隔离（`audit_ctx_id` 过滤）；API 层鉴权 |
| **性能** | 引用校验并行化；bge-m3 部署为独立推理服务（GPU 可选）；目标 P50 < 15s（不含 rerank） |
| **可靠性** | LLM 调用指数退避重试；Milvus/PG 健康检查；文档入库幂等 |
| **成本** | LangSmith 统计 tokens/cost；分类可用小模型、生成用大模型的分层策略 |
| **可移植** | LLM 客户端抽象（Claude 原生 / OpenAI 兼容），.env 一键切换 |

---

## 12. 迭代路线（建议）

| 阶段 | 内容 | 验收 |
|------|------|------|
| **M1 基座** | docker-compose（PG+Milvus）、LangGraph 骨架、structured_llm_call、分类 Agent | 问题分类端到端可跑，LangSmith 可见 trace |
| **M2 知识库** | MinerU 入库管线、切分、混合检索、检索 Agent + 充分性判断 | 检索命中率（context_precision@5）达标 |
| **M3 闭环** | 带引用生成、验证 Agent、重试/降级、FinalAnswer | citation_accuracy 达标，幻觉率显著低于裸 RAG |
| **M4 记忆** | Checkpointer、长期记忆提炼与注入、多轮追问 | 多轮会话指代消解可用 |
| **M5 评估与上线** | RAGAS 流水线、黄金集建设、FastAPI 对外、压测 | eval 对比纳入变更流程 |

---

## 13. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 扫描件 OCR 质量差 → 切分/检索噪声 | 检索召回下降 | MinerU 质量打分，低分页标记 `low_quality` 供 rerank 惩罚 |
| 充分性判断过严/过松 | 死循环 或 证据不足仍作答 | 规则+LLM 混合判断；轮次上限；日志复盘调阈值 |
| 验证 Agent 与生成模型同源偏差 | 幻觉漏检 | 判官 prompt 独立；必要时换模型；RAGAS faithfulness 交叉监控 |
| 长期记忆膨胀 | prompt 超限、成本上升 | importance 打分 + 摘要压缩 + 淘汰归档 |
| 表格类答案引用定位难 | 引用无法核验 | 表格整块切分 + cite 到表格对象；quote 记录单元格级文本 |

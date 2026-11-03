# 多 Agent 人才评估与推荐系统

课程贯穿项目的统一代码根目录

## 当前目录

| 路径 | 用途 |
|---|---|
| `backend/` | FastAPI 后端、uv 环境、依赖锁、解析器、数据模型与测试 |
| `database/init.sql` | PostgreSQL 完整建表 SQL |
| `database/migrations/005_chunking.sql` | 第 4 课到第 5 课的唯一幂等增量迁移 |
| `database/migrations/007_milvus_evidence_index.sql` | 第 6 课 Milvus 索引任务表的幂等增量迁移 |
| `database/migrations/012_talent_tools.sql` | 第 12 课岗位 JD 与人才工具调用审计表的幂等增量迁移 |
| `database/migrations/020_run_history.sql` | 第 20 课 Run 与步骤历史表的幂等增量迁移 |
| `database/migrations/021_conversation_history.sql` | 第 21 课会话列表投影的幂等增量迁移 |
| `frontend/` | React 前端 |
| `docker-compose.yml` | 默认启动 PostgreSQL、MinIO、Redis；`app` profile 启动 backend、worker、frontend |

## 产品定位

前端是多 Agent 人才评估与推荐系统的统一应用壳层。人才档案模块包含员工花名册、档案资料库和证据召回验收台，评估与推荐模块提供多轮人才要求、SSE 修订反馈、运行步骤、HITL、Markdown 报告、证据回查和按状态筛选的会话列表。后端使用 `AsyncPostgresSaver` 保存 Graph State，Run 与会话 API 负责实时事件、人工恢复、幂等取消和历史读模型查询

多轮入口继续使用 `POST /api/runs`，后续请求携带同一 `conversation_id`。父图 `_begin_turn` 返回局部更新，保留消息、当前输入、完整要求和标题，仅清理运行产物。请求子图输入显式声明 `messages`、`request_text`、`talent_request`、`conversation_title`，人工澄清也作为消息返回父图。首次理解生成标题，后续模型使用不含标题字段的修订 Schema。`requirement.revised` 的 `data` 只包含 `action`、`summary`。持久化 Run 的轮次从历史记录递增，后端重启后继续原会话不会重置编号

报告输出出现 Pydantic 结构校验错误时，报告写作器将校验信息交回模型纠正一次；再次失败仍上报错误，不截断证据或放宽引用校验。相关回归覆盖 `tests/test_talent_conversation.py`、`tests/test_talent_run_stream.py` 和 `tests/test_talent_evaluation_report.py`，在 `backend/` 执行 `uv run pytest -q`

## 运行模式

本项目只维护一套中间件环境：PostgreSQL、Redis、MinIO 始终由 Docker Compose 托管。开发模式和发布模式都共用这三类基础服务。

所有 Docker Compose 命令统一使用项目名参数 `-p talent-eval-agents-course`。

### 开发模式

开发模式的目标是保留前后端热更新，同时继续复用 Compose 内的中间件。

1. 启动基础设施

```bash
docker compose -p talent-eval-agents-course up -d
```

2. 启动 MinerU 本地服务

```bash
uv sync --project backend/mineru-runtime
uv run --project backend/mineru-runtime mineru-api --host 127.0.0.1 --port 18001
```

3. 启动后端 API

```bash
cd backend
uv sync --dev
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 18080
```

4. 启动本地 worker

```bash
cd backend
uv run python -m app.worker
```

5. 启动前端开发服务器

```bash
cd frontend
npm install
npm run dev
```

开发模式下的更新规则：

- 改前端页面：Vite 自动热更新，不需要重启 Docker
- 改后端 API：`uvicorn --reload` 自动重载，不需要重建镜像
- 改 worker、解析流水线或队列消费逻辑：手动重启本地 `uv run python -m app.worker`
- 改数据库结构：对已有数据库执行 migration；只有新建数据库卷时才会读取 `database/init.sql`

### 发布模式

发布模式用于测试环境、演示环境和生产口径的容器化运行。backend、worker、frontend 会和中间件一起由 Compose 托管。

```bash
docker compose -p talent-eval-agents-course --profile app up -d --build
```

发布模式下的更新规则：

- 改前端代码：`docker compose -p talent-eval-agents-course --profile app up -d --build frontend`
- 改后端代码：`docker compose -p talent-eval-agents-course --profile app up -d --build backend worker`
- 前后端都改了：`docker compose -p talent-eval-agents-course --profile app up -d --build`
- 只改了环境变量、挂载文件或容器运行参数，没有改镜像内容：`docker compose -p talent-eval-agents-course --profile app restart backend worker frontend`

`docker restart` 或 `docker compose restart` 只会重启旧容器，不会重新构建镜像，也不会把你本地新改的源码带进去。所以发布模式下只要改了代码，就必须用 `up -d --build`，不能只用 `restart`。


## 人才档案接口

| 接口 | 用途 |
|---|---|
| `GET /api/employees` | 查询员工花名册及关联材料数量 |
| `POST /api/employees` | 新建结构化员工档案 |
| `GET /api/knowledge-bases` | 查询档案知识库及文件数量 |
| `POST /api/knowledge-bases` | 创建档案知识库 |
| `DELETE /api/knowledge-bases/{id}` | 删除空知识库；存在材料时返回 409，要求先删除或迁移材料 |
| `GET /api/documents` | 查询知识库文件 |
| `POST /api/documents` | 上传文件、关联员工与知识库，并自动创建解析任务 |
| `DELETE /api/documents` | 批量删除文档，同时删除原文件、解析产物、切片和 Milvus 向量 |
| `GET /api/documents/{id}` | 查询原文件、基础元数据、解析任务、索引任务与 Markdown 产物 |
| `POST /api/documents/{id}/parse` | 创建异步解析任务 |
| `GET /api/documents/{id}/chunks` | 查询最新一次成功切片及 Parent-Child 关系 |
| `POST /api/documents/{id}/chunks` | 按材料结构自动执行 Markdown 结构化切分或纯文本递归切分 |
| `POST /api/documents/{id}/evidence-index` | 为当前文档版本手动创建异步 Milvus 索引任务 |
| `POST /api/evidence/search` | 按租户、权限范围和业务条件检索人才证据，并返回来源材料与候选人上下文（纯向量） |
| `POST /api/evidence/hybrid-search` | 稠密 + BM25 双路召回、RRF 融合、Rerank 精排的证据检索 |
| `POST /api/talent-search/plan` | 把自然语言人才需求编译为受控 Query Plan |
| `POST /api/talent-search/candidates` | 校验 Query Plan，并用确定性 SQL Builder 返回候选人编号集合 |
| `POST /api/talent-search` | 输入自然语言查询，依次执行 Query Plan、SQL 候选筛选、混合检索和可选查询优化，返回 Chunk 列表 |
| `POST /api/index-jobs/{id}/retry` | 为失败的 Milvus 索引任务创建新的幂等重试任务 |
| `POST /api/runs` | 创建人才评估 Run |
| `GET /api/runs/{id}/events` | 订阅当前 Run 的 SSE 事件 |
| `POST /api/runs/{id}/resume` | 提交 HITL 选项并恢复运行 |
| `DELETE /api/runs/{id}` | 幂等取消 Run |

## 第 8 课复合人才检索

| 路径 | 用途 |
|---|---|
| `backend/app/query_plan.py` | Query Plan、Filter DSL 注册表、自然语言结构化输出、SQLAlchemy 查询构造与查询优化计划 |
| `backend/tests/test_query_plan.py` | DSL 提示词、字段白名单、参数化 SQL、年龄边界、待澄清条件、空候选集和优化策略测试 |
| `backend/tests/test_talent_search_api.py` | 从 HTTP 请求到 Chunk 响应的端到端接口测试，覆盖标准检索与首轮为空后的自动优化检索 |

查询计划把结构化硬条件、语义条件、偏好和待澄清条件分开。Pydantic Schema 提供输出结构，`FILTER_FIELD_REGISTRY` 同时维护字段说明、允许操作符和 SQL 构造函数，并生成提示词中的 Filter DSL。租户从请求头注入，模型不能生成权限条件或原始 SQL。`/talent-search/candidates` 返回 `employee_no` 作为后续 Milvus 检索的 `candidate_ids`。`execute_composite_search()` 在候选集为空时直接结束，不调用 Milvus，避免空列表退化为全库搜索；多个语义要求按 `requirement_id` 分别保存证据。`search_with_optimization()` 在首轮无有效命中时选择 Rewrite、Multi Query 或 Decompose，生成最多 3 条受约束查询，再次检索并按 `chunk_id` 去重合并

## 第 9 课可信证据输出

`POST /api/talent-search` 增加可选参数 `include_evidence_pack`。默认值为 `false`，原有调用继续返回 Query Plan、候选集、检索记录和 Chunk 列表。参数设为 `true` 时，接口从 PostgreSQL 重新读取并鉴权检索命中的 Chunk，再按候选人和 `requirement_id` 输出 `Candidate Evidence Pack`

| 路径 | 用途 |
|---|---|
| `backend/app/evidence_pack.py` | Pydantic 抽取协议、事实来源校验、重复事实合并、yes/no 冲突推导和证据状态计算 |
| `backend/tests/test_evidence_pack.py` | Chunk 去重、来源合并、伪造引用拦截、冲突范围、时间格式和失败降级测试 |
| `backend/scripts/verify_evidence_pack.py` | 以隔离合成材料验证查询、事实合并、冲突输出和材料缺失 |
| `backend/samples/lesson09/` | C901、C902 虚构材料及数据性质说明 |

证据包协议版本为 `2.0`。事实字段为 `event`、`period`、`claim`、`answer` 和 `sources`，其中 `answer` 只允许 yes 或 no。每个 source 包含 `citation_id`、`chunk_id`、`quote`、`quote_start` 和 `quote_end`，坐标是 Chunk 文本内的半开区间 `[quote_start, quote_end)`。模型只抽取事实，程序校验来源后按 `event + period + claim + answer` 合并重复事实，再根据同一 `event + period + claim` 下的 yes/no 推导冲突。证据状态分为 `sufficient`、`partial`、`missing` 和 `conflicting`

模型返回的每条来源必须引用输入中的 `chunk_id`，`quote` 必须是对应 Chunk 的连续原文。`quote_start` 和 `quote_end` 由后端在校验通过后根据 PostgreSQL 重读的 Chunk 计算，不接受模型生成的位置。校验失败时，系统返回 `partial + extraction_failed`，保留原始引用，不返回未经核验的事实

隔离演示默认使用内存 SQLite、固定检索结果和固定抽取结果，不读取现有员工数据库或 Milvus

```bash
cd backend
uv run --no-sync python -m scripts.verify_evidence_pack
```

使用项目配置的模型验证时，只有 `backend/samples/lesson09/` 下明确标注为虚构的三份材料会发送到模型 API

```bash
uv run --no-sync python -m scripts.verify_evidence_pack --live-model
```

## 第 10 课检索链路与引用展示

| 路径 | 用途 |
|---|---|
| `backend/app/evidence_citations.py` | 从 PostgreSQL 重读 Chunk、绑定材料版本并按当前租户和权限解析引用 |
| `backend/tests/test_evidence_citations.py` | 版本绑定、租户与权限检查、历史版本和引用接口测试 |
| `frontend/src/EvidencePackPanel.tsx` | 展示证据状态、事实来源、待核查冲突与引用原文抽屉 |

引用接口为 `GET /api/evidence/citations/{chunk_id}?quote_start={start}&quote_end={end}`。事实来源打开引用时同时传递 quote 坐标；未指定坐标时仍可查看整个 Chunk。接口校验坐标成对出现且不超出 Chunk，并根据当前请求上下文重新检查 Chunk、材料版本、文档、资料库、候选人、租户和权限范围。历史版本仍可访问时返回 `version_state=historical`，权限撤销或材料不可用时统一返回 404。前端抽屉展示 `heading_path`、Chunk 起止位置和 quote 起止位置，并在完整 Chunk 中高亮 quote

第 10 课把当前界面定位为档案知识库中的检索验收台。正式的人才评估与推荐 Chat 页面留到后续课程接入 LangGraph、SSE 和会话状态

第 10 课相关回归命令：

```bash
cd backend
uv run --no-sync pytest -q \
  tests/test_query_plan.py \
  tests/test_hybrid_search_service.py \
  tests/test_talent_search_api.py \
  tests/test_evidence_pack.py \
  tests/test_evidence_citations.py
```

结果输出：

```text
43 passed, 3 warnings
```

结果说明：这组测试使用数据库、Milvus 和模型替身，只用于代码分支回归，不能替代开发模式联调

第 10 课开发模式联调沿用 Compose 中已经启动的 PostgreSQL、Redis、MinIO、Milvus 和 etcd，并分别启动本地 backend、worker 和 frontend

运行材料导入脚本：

```bash
cd backend
uv run python scripts/import_markdown_reviews.py
```

结果输出：

```text
C001  C001_林晓岚_项目复盘  chunk=markdown  index=succeeded
C002  C002_陈泽宇_项目复盘  chunk=markdown  index=succeeded
C003  C003_周雨桐_项目复盘  chunk=markdown  index=succeeded
C004  C004_赵明远_项目复盘  chunk=markdown  index=succeeded
C005  C005_郭思远_项目复盘  chunk=markdown  index=succeeded
```

结果说明：

- PostgreSQL 新增 5 份材料，每份 20 个 Parent/Child Chunk，共 100 个 Chunk
- Milvus `talent_evidence_v2` 写入 50 个可召回 Child Chunk，每名候选人 10 个
- 查询 `筛选在上海且有企业知识库和大模型应用项目经验的候选人` 生成地区过滤和 1 项语义要求
- 浏览器返回 C001 的 `sufficient` 状态、5 条事实和 16 条原始证据
- 引用抽屉返回当前版本、标题路径、Markdown 起始位置和 PostgreSQL 原文
- 使用 `company_internal` 权限读取同一 `hr_private` 引用返回 `404`

模型抽取结果具有不确定性。同一材料的另一轮请求返回 `partial`，并把缺失项目时间记录为 `period`。演示和评测需要保留每次运行输出，不能把一次输出写成固定结果

## 第 11 课 LangGraph 状态模型与人才决策主图

| 路径 | 用途 |
|---|---|
| `backend/app/talent_decision_graph.py` | 定义人才决策输入、内部 State、输出、Runtime Context、节点、路由和可运行主图 |
| `backend/tests/test_talent_decision_graph.py` | 验证正常完成、空候选结束、可信 Context 和并行 Reducer 行为 |
| `backend/scripts/verify_talent_decision_graph.py` | 输出逐节点 State 增量、空候选结果和 Reducer 前后对照 |

`TalentDecisionState` 将消息、人才要求、候选人范围、证据引用、评估结果、报告和运行状态分开保存。租户与权限通过只读 `DecisionContext` 注入，不接受用户消息或模型输出提供的同名值。主图当前使用注入式候选人 Provider 和占位评估结果，后续课程在保持 State Contract 的前提下接入真实工具、任务理解、并行评估和人机协同

运行本节独立验证：

```bash
cd backend
uv run --no-sync python -m scripts.verify_talent_decision_graph
```

运行本节测试：

```bash
cd backend
uv run --no-sync pytest -q tests/test_talent_decision_graph.py
```

结果输出为 `6 passed in 0.16s`

## 第 12 课人才数据工具与统一证据协议

| 路径 | 用途 |
|---|---|
| `backend/app/talent_tools.py` | 四类只读人才工具、Runtime Context、统一返回协议、重试、熔断和审计执行器 |
| `backend/tests/test_talent_tools.py` | 验证工具 Schema、租户隔离、Evidence Pack 版本、重试、熔断、审计与主图适配 |
| `backend/scripts/verify_talent_tools.py` | 使用隔离 SQLite 数据输出四类工具与故障链路结果 |
| `backend/samples/lesson12/job_descriptions.json` | 第 12 课合成岗位 JD 数据 |
| `database/migrations/012_talent_tools.sql` | 新增 `job_descriptions` 与 `tool_call_audits` |

工具清单为 `lookup_job_descriptions`、`filter_candidates`、`search_candidate_evidence` 和 `get_candidate_profiles`。模型可见 Schema 只包含业务参数，租户、权限、身份和 Run 编号通过 `ToolRuntime` 注入。结构化筛选复用第 8 课 Query Plan 与 SQL Builder，证据检索继续返回第 9 课 Candidate Evidence Pack `2.0`

`ToolExecutor` 统一返回 `ok`、`data`、`error` 和 `meta`，仅对瞬时依赖错误与超时执行受控重试。`policy` 设置默认超时与重试参数，`tool_policies` 可以按工具名称覆盖。连续失败达到阈值后打开熔断器。`SqlAuditSink` 只保存参数键名与调用摘要，不保存查询原文、候选人信息或证据正文

在代码仓库根目录启动 PostgreSQL 并执行第 12 课幂等迁移：

```bash
docker compose -p talent-eval-agents-course up -d postgres
docker compose -p talent-eval-agents-course exec -T postgres \
  psql -U talent -d talent_docs -v ON_ERROR_STOP=1 -f /dev/stdin \
  < database/migrations/012_talent_tools.sql
```

迁移使用 `CREATE TABLE IF NOT EXISTS` 和 `CREATE INDEX IF NOT EXISTS`，可以重复执行。全新环境由 `database/init.sql` 直接创建最新结构

运行本节独立验证：

```bash
cd backend
uv run --no-sync python -m scripts.verify_talent_tools
```

运行本节与第 11 课主图测试：

```bash
uv run --no-sync pytest -q tests/test_talent_tools.py tests/test_talent_decision_graph.py
```

结果输出为 `23 passed in 0.65s`。后端完整回归结果为 `151 passed, 3 warnings in 4.85s`

## 第 13 课 MCP 人才能力服务

| 路径 | 用途 |
|---|---|
| `backend/app/talent_mcp_server.py` | 人才 MCP Server、4 个 Tools、3 类 Resources、评估 Prompt 与 Access Token Context 映射 |
| `backend/app/talent_mcp_client.py` | 将 MCPAdapter 发现的 LangChain Tools 放入 LangGraph ToolNode |
| `backend/samples/lesson13/codestats_mcp_v2.py` | FastMCP v1 示例的 SDK v2 迁移版，包含 Tool、Resource、目录边界与 STDIO 入口 |
| `backend/tests/test_codestats_mcp_v2.py` | 验证 CodeStats Tool、Resource、`.venv` 排除和目录越界拦截 |
| `backend/tests/test_talent_mcp_server.py` | 验证能力发现、结构化结果、Resource、Prompt、scope、audience 和租户隔离 |
| `backend/tests/test_talent_mcp_client.py` | 验证 MCP 工具在 LangGraph ToolNode 中的异步执行 |
| `backend/scripts/verify_talent_mcp.py` | 使用隔离 SQLite 数据启动 Streamable HTTP，验证协议、鉴权和 LangGraph 链路 |
| `backend/scripts/run_talent_mcp_http.py` | 长驻启动无鉴权的本机 Streamable HTTP Demo Server，供 Inspector 和其他 Host 联调 |

MCP 适配层直接复用第 12 课的 `TalentToolService` 和 `ToolExecutor`。`talent_tools.py` 只新增了按 `job_code` 读取单个岗位 JD 的方法，且 SQL 继续强制使用可信 `tenant_id`

Server 能力清单：

- Tools：`lookup_job_descriptions`、`filter_candidates`、`search_candidate_evidence`、`get_candidate_profiles`
- Resource Templates：`talent://jobs/{job_code}`、`talent://candidates/{candidate_id}/profile`
- 固定 Resource：`talent://policies/evaluation/current`
- Prompt：`talent_assessment`

Streamable HTTP 保护边界使用 MCP `AuthSettings` 与可注入 `TokenVerifier`。OAuth scope `talent:read` 决定客户端能否访问 MCP 服务，token claims 中的 `tenant_id` 和 `permission_scopes` 映射为 `TalentToolContext`。工具参数与 `clientInfo` 不参与可信身份判定

本节新增直接依赖 `mcp[cli]>=2.2,<3`、`langchain[mcp]>=1.4,<2` 和 `langchain-core>=1.2,<2`。`MCPAdapter` 在当前 LangChain 版本中仍为 Beta API，升级后需运行本节回归测试

SDK v1 的 `FastMCP` 在 SDK v2 中更名为 `MCPServer`，Tool 与 Resource 装饰器以及 `mcp dev` 的基本用法保持不变。`mcp dev` 会启动 Inspector v2，启动 URL 使用 `MCP_INSPECTOR_API_TOKEN`。Inspector 的 `6274` 端口是调试页面，不是业务 MCP endpoint

接受 `mcpServers` JSON 的 Host 可以用 `command`、`args` 和 `env` 启动 STDIO Server。Codex 当前使用 `~/.codex/config.toml` 或项目内 `.codex/config.toml`，远程服务的 `url` 应指向实际 Streamable HTTP 地址，例如 `http://127.0.0.1:8000/mcp`

启动 CodeStats 的可视化调试：

```bash
cd backend
CODE_STATS_ROOT=/Users/noora/projects \
uv run --no-sync mcp dev samples/lesson13/codestats_mcp_v2.py
```

启动可供外部 MCP Client 连接的人才能力 Demo Server：

```bash
cd backend
uv run --no-sync python scripts/run_talent_mcp_http.py
```

默认 endpoint 为 `http://127.0.0.1:18081/mcp`。服务使用临时 SQLite、固定 `tenant-a` Context、`stateless_http=True` 和 `json_response=True`，退出后删除临时数据。该入口没有鉴权，只用于本机调试

浏览器可视化测试：

```bash
npx @modelcontextprotocol/inspector
```

Inspector 中选择 `Streamable HTTP`，URL 填写 `http://127.0.0.1:18081/mcp`，Authentication 留空。命令行检查使用：

```bash
npx @modelcontextprotocol/inspector --cli http://127.0.0.1:18081/mcp --transport http --method tools/list
```

运行协议、鉴权与 LangGraph 独立验证：

```bash
cd backend
uv run --no-sync python scripts/verify_talent_mcp.py
```

核心输出：

```text
[protocol] {"protocol": "2026-07-28", "tools": ["lookup_job_descriptions", "filter_candidates", "search_candidate_evidence", "get_candidate_profiles"], "resource_templates": ["talent://jobs/{job_code}", "talent://candidates/{candidate_id}/profile"], "resource_job_code": "JD-AI-001", "prompt_role": "user"}
[auth] {"missing_token": 401, "missing_scope": 403, "wrong_audience": 401, "true_audience": 200}
[langgraph] {"discovered_tools": ["lookup_job_descriptions", "filter_candidates", "search_candidate_evidence", "get_candidate_profiles"], "candidate_ids": ["C001"], "tool_status": "success"}
```

运行本节定向测试：

```bash
uv run --no-sync pytest -q tests/test_codestats_mcp_v2.py tests/test_talent_mcp_client.py tests/test_talent_mcp_server.py tests/test_talent_tools.py
```

结果输出为 `38 passed in 2.05s`。后端完整回归结果为 `177 passed, 2 warnings in 6.69s`

## 第 14 课人才任务理解、条件澄清与执行计划

| 路径 | 用途 |
|---|---|
| `backend/app/talent_request_graph.py` | 区分详细要求与岗位名称，完成岗位确认、统一要求修订和执行计划编译 |
| `backend/app/lesson14_demo_data.py` | 按 `job_code` 幂等写入课程演示岗位 |
| `backend/app/talent_tools.py` | 岗位查询结果增加 `exact`、`contains`、`fuzzy` 匹配类型 |
| `backend/langgraph.json` | 为本地 Agent Server 注册 `talent_request` 图 |
| `backend/samples/lesson14/job_descriptions.json` | 2 条 `course-demo` 岗位与 1 条跨租户隔离岗位 |
| `backend/scripts/seed_lesson14_jobs.py` | 将演示岗位导入项目 PostgreSQL |
| `backend/scripts/verify_talent_request_graph.py` | 用固定解释器、岗位结果和 `InMemorySaver` 验证三条运行路径 |
| `backend/tests/test_lesson14_demo_data.py` | 验证演示数据导入的插入、更新和幂等性 |
| `backend/tests/test_talent_request_graph.py` | 验证详细要求直达、精确项直达、歧义岗位恢复和无匹配结果 |

详细人才要求直接编译为 `TalentRequest` 与第 8 课的 `QueryPlan`。岗位名称先调用 `lookup_job_descriptions`，唯一 `exact` 项自动确认，只有 `contains` 或 `fuzzy` 候选时进入 `confirm_job`。中断载荷包含 `type`、问题、原始请求和岗位选项，恢复值使用 `{"action":"select","job_code":"..."}`

第 17 课在候选人筛选启用时增加统一的 `resolve_requirements` 节点。计划无法执行或候选集为空都会返回 `requirement_revision` 载荷，前端使用载荷中的完整 `request_text` 初始化编辑框，用户通过 `revision_text` 提交修改后的完整要求。节点直接覆盖当前 `request_text`，再回到 `interpret_input` 重新生成草稿和查询计划。岗位非精确匹配支持选择或取消，岗位无匹配以 `no_job_match` 终态结束

岗位查询和 `interrupt()` 位于两个节点。恢复会从 `confirm_job` 开头重启，但不会再次执行已经完成的 `lookup_jobs`。租户与权限仍由 `DecisionContext` 注入，不进入模型生成的 `TalentRequest` 或 `QueryPlan`

本节新增开发依赖 `langgraph-cli[inmem]>=0.4.31,<0.5` 和 `debugpy`。Agent Server 从 `backend/langgraph.json` 加载图，并在开发态管理 Checkpoint

导入合成岗位数据：

```bash
cd backend
uv run --no-sync python -m scripts.seed_lesson14_jobs
```

首次输出为 `{"inserted": 3, "updated": 0, "total": 3}`，重复运行输出为 `{"inserted": 0, "updated": 3, "total": 3}`

启动本地 Studio 演示：

```bash
uv run langgraph dev --no-browser --no-reload --port 2024
```

服务健康检查返回 `{"ok":true}`，`/assistants/search` 返回 `graph_id=talent_request`。Studio 使用 Graph mode，Assistant Context 设置为 `tenant_id=course-demo` 和 `permission_scopes=["hr_private"]`

断点调试 Agent Server：

```bash
uv run langgraph dev --no-browser --debug-port 5678 --wait-for-client --port 2024
```

在 VS Code 中选择 `Attach LangGraph dev (5678)`。`confirm_job` 的恢复值检查位于 `backend/app/talent_request_graph.py`，在 `interrupt()` 后一行设置断点可以检查 `selection` 的实际类型和值

运行确定性验证：

```bash
uv run --no-sync python -m scripts.verify_talent_request_graph
```

歧义岗位路径先输出 `next=["confirm_job"]`，使用同一 `thread_id` 恢复后输出 `status=plan_ready`、`selected_job=JD-AI-002`、`next=[]` 和 `lookup_calls=1`

运行本节定向测试：

```bash
uv run --no-sync pytest -q \
  tests/test_lesson14_demo_data.py \
  tests/test_talent_request_graph.py \
  tests/test_talent_tools.py
```

结果输出为 `24 passed, 1 warning in 1.19s`。后端完整回归结果为 `174 passed, 2 warnings in 8.40s`

## 第 15 课动态评估维度与 Subagent 分发

| 路径 | 用途 |
|---|---|
| `backend/app/talent_evaluation_dispatch.py` | 定义动态评估维度、Requirement Evidence、确定性 Branch Worker、任务矩阵与并行分发图 |
| `backend/app/talent_evaluation_runtime.py` | 装配模型、混合检索、PostgreSQL 原文回表、Evidence Pack 生成器和 Branch Worker，导出 Agent Server 可加载的编译图 |
| `backend/scripts/verify_talent_evaluation_dispatch.py` | 直接引用运行时模块已注册的 `graph`，传入示例状态与可信 Runtime Context 验证独立分发子图 |
| `backend/tests/test_talent_evaluation_dispatch.py` | 覆盖权重校验、候选人 × 维度分发、reducer 聚合、可信上下文、工具边界、任务上限与失败隔离 |
| `backend/tests/test_talent_evaluation_branch_agent_live.py` | 使用真实 Chat Model 验证事实抽取、连续原文引用、Evidence Pack 与 Branch Worker 的完整传递 |

本节接收第 14 课的 `TalentRequest` 与 `QueryPlan`。硬条件决定候选人范围，语义要求和评估偏好用于生成动态评估维度。每个维度包含定义、整数权重、证据要求、0/3/5 分锚点、检索提示和需求来源。维度生成 Prompt 要求一般不超过 4 个维度，同一主题的要求合并为一个维度，并通过多个 `retrieval_hints` 扩展检索。模型输出不包含维度标识，程序按照维度列表顺序生成 `dimension_number=1..N`，避免模型生成重复标识。Pydantic 负责字段结构约束，`validate_dimension_plan` 只检查权重合计是否为 100。

候选人与评估维度组成笛卡尔积任务矩阵。LangGraph `Send` 将每个任务发送给独立评估分支，`branch_results` 使用 `operator.add` reducer 聚合结果。验证脚本默认允许 6 个分支并发执行，分支内部再并发处理当前维度的全部 `evidence_requirements`。每条要求组合 `retrieval_hints` 后调用 `search_candidate_evidence`，Future 按提交顺序聚合，因而完成顺序不会改变输出顺序。结果保留 Candidate Evidence Pack 2.0 的事实、来源、冲突、缺失信息和引用指针。`get_candidate_profiles` 不再在候选人 × 维度分支中重复调用。

运行真实链路验证：

```bash
cd backend
uv run --no-sync python -m scripts.verify_talent_evaluation_dispatch --log-level INFO
```

运行前需要启动 PostgreSQL、Milvus、MinIO 和 etcd，并配置模型、Embedding 与 Rerank 服务。脚本直接查询真实候选人和证据索引，不创建固定候选人或固定分支结果。`langgraph.json` 以 `talent_evaluation_dispatch` 注册该独立子图，完整主流程接入留到后续课程。

运行本节与前置课程的定向测试：

```bash
uv run --no-sync pytest -q \
  tests/test_evidence_pack.py \
  tests/test_talent_evaluation_dispatch.py \
  tests/test_talent_tools.py \
  tests/test_talent_decision_graph.py \
  tests/test_talent_request_graph.py
```

Chat Model 保持同步 `invoke` 调用方式，全链路仅使用 `CHAT_MODEL`。凡是包含外部 LLM 调用的工具和复合链路，都不在项目的 `ToolExecutor` 中设置额外超时。

`execution_status` 描述分支调用是否完整。每条 `RequirementEvidence` 分别保留 `status`、`reason`、`extraction_status`、`facts`、`conflicts`、`missing_information` 和 `citations`。模型输出校验失败或两个模型均不可用时，取证分支标记为 `degraded`，保留从 PostgreSQL 重读并通过权限校验的原始 `citations`，丢弃未通过校验的模型 `facts`。降级结果不会伪造事实，报告只能使用仍然通过证据门禁的内容。

两组可比实测都包含 6 个候选人 × 维度任务项和 12 次要求级模型调用。外层并发为 3 且分支内串行时，总耗时为 164.30 秒，分支阶段约 147.6 秒。外层默认并发改为 6 且分支内并发后，总耗时为 72.53 秒，分支阶段约 57.5 秒，总耗时降低约 55.9%。12 次要求级调用在约 14 毫秒内全部进入执行，分支阶段最终受最慢一次 57.48 秒的模型调用限制。

抽取输入使用 `source_1`、`source_2` 这类短来源编号，返回后由程序恢复真实 `chunk_id`，避免模型把 UUID 缩写为不可验证的前缀。`quote` 只规范化首尾空白，仍必须是对应 Chunk 的连续原文，不使用模糊匹配接受模型改写。失败日志会区分 `unknown_chunk_id`、`blank_quote`、`quote_not_found`、`schema_validation_failed` 和 `model_extraction_failed`，并携带函数、阶段、候选人、要求和 Chunk 编号，不输出履历原文或 Prompt。

DashScope 的 OpenAI 兼容端点对 `json_schema` 约束编译可能返回 `grammar validation or compilation failed`。证据抽取改用 `json_mode`，输入中显式附带 `EvidenceExtraction` 的 JSON Schema，再由 Pydantic 在本地校验结果。原 `BranchAgentFinding` 中“`evidence_refs` 与 `missing_items` 不能同时为空”的全局校验已经删除；抽取失败时允许只保留 citations。第 16 课再按评分状态执行引用和缺失信息的本地完整性校验，该业务规则不是服务端 grammar 错误的原因。

第 16 课从 `facts[].sources[].chunk_id` 构建证据白名单和链接，直接使用 `missing_information`，不再要求上游额外维护 `evidence_refs`、`missing_items` 或独立的 `evidence_items` 输入。

真实模型证据抽取测试命令：

```bash
RUN_LIVE_MODEL_TESTS=1 uv run --no-sync pytest \
  tests/test_talent_evaluation_branch_agent_live.py -q -s
```

该测试会把用例中的测试证据发送到项目配置的外部模型端点，应在已获得数据发送授权的环境中执行。不设置 `RUN_LIVE_MODEL_TESTS=1` 时默认跳过，避免普通回归依赖外部模型网络。

## 第 16 课证据评估与报告合成

| 路径 | 用途 |
|---|---|
| `backend/app/talent_evaluation_report.py` | 定义并发单维度评分、候选人级并发一致性检查、候选人聚合、稳定排序、短引用映射、报告校验与 Markdown 渲染图 |
| `backend/app/talent_evaluation_runtime.py` | 使用项目 Chat Model 装配第 16 课评分、一致性检查和报告模型，导出正式 `report_graph` |
| `backend/langgraph.json` | 以 `talent_evaluation_report` 注册第 16 课正式图 |
| `backend/scripts/verify_talent_evaluation_report.py` | 读取第 15 课真实分发结果，复用 `report_graph` 输出节点日志、排名、证据状态和 Markdown 报告 |
| `backend/tests/test_talent_evaluation_report.py` | 覆盖缺失不计 0 分、引用白名单、短引用恢复、并发上限与稳定顺序、一致性风险、得分聚合、运行时装配、图注册与报告渲染 |

第 16 课接收第 15 课的评估维度和 `branch_results`，程序从 Requirement Evidence 的事实来源生成内部 `evidence_items`。单维度模型评分输出 `sufficient`、`partial`、`missing` 或 `conflicting`，分支执行失败由程序转为 `failed`。`missing`、`conflicting` 和 `failed` 的分数保持为 `null`，不将材料缺失折算为 0 分

`score_dimensions` 默认最多并发执行 12 个候选人 × 维度评分任务，并使用保持输入顺序的聚合方式写回 State。`review_consistency` 按候选人隔离输入后默认最多并发检查 12 名候选人。聚合排名依赖一致性结果，报告依赖完整排名，最终校验依赖报告草稿，这三段保持串行以维护数据依赖

候选人聚合由程序按维度权重计算已确认得分、理论上限和可评分覆盖率。排名依次使用已确认得分、可评分覆盖率和 `candidate_id`，模型不参与排名。跨维度一致性问题作为风险保留，降低置信度，但不自动改写已核验分数

程序为每条证据分配稳定的 `E1`、`E2` 短编号，模型只读写短编号，State 同时保留短编号与完整 `chunk_id` 的映射。评分、一致性检查和报告模型返回后，程序先将短编号恢复为完整 Chunk ID，再执行引用白名单和候选人归属校验。报告正文显示短编号，链接仍由完整 Chunk ID、原文偏移和可信 `citation_url` 渲染，模型输入不包含链接

`talent_evaluation_runtime.py` 通过项目配置的 Chat Model 装配 `dimension_scorer`、`consistency_reviewer` 和 `report_writer`，并导出 `report_graph`。验证脚本只读取 `backend/samples/lesson16/talent_evaluation_dispatch_result_20260921_205347.json` 中的 `dimensions` 与 `branch_results`，固定维度、固定评分和固定报告均已删除

运行独立验证：

```bash
cd backend
uv run --no-sync python -m scripts.verify_talent_evaluation_report \
  --input samples/lesson16/talent_evaluation_dispatch_result_20260921_205347.json
```

运行第 16 课与前置课程定向测试：

```bash
uv run --no-sync pytest -q \
  tests/test_talent_evaluation_report.py \
  tests/test_talent_evaluation_dispatch.py \
  tests/test_talent_tools.py \
  tests/test_talent_decision_graph.py \
  tests/test_talent_request_graph.py
```

结果输出为 `64 passed, 2 warnings in 2.32s`，后端完整回归为 `218 passed, 1 skipped, 2 warnings in 89.75s`

## 第 18 课人才评估子图与决策引擎整合

| 路径 | 用途 |
|---|---|
| `backend/app/talent_decision_engine.py` | 定义决策引擎主 State、三张子图的编排和终态路由 |
| `backend/app/talent_decision_runtime.py` | 装配要求确认、评估分发和报告合成子图，导出 Agent Server 可加载的主图 |
| `backend/scripts/verify_talent_decision_events.py` | 使用确定性子图逐节点输出 scope、节点名和 State 局部更新，`--pause` 用于课堂单步演示 |
| `backend/tests/test_talent_decision_engine.py` | 验证候选人 State 传递、终态截断和主图注册 |
| `backend/langgraph.json` | 以 `talent_decision_engine` 注册整合后的决策引擎 |

主图按 `request_subgraph -> evaluation_subgraph -> report_subgraph` 顺序编排三张已有业务子图。要求确认子图只有在 `candidates_ready` 状态下才进入评估分发。评估分发产出 `branches_ready` 或 `branches_ready_with_failures` 时继续生成报告，其他终态不进入报告子图

第 17 课已确认的 `candidate_ids` 会直接传入评估分发子图。分发图使用 `candidates_preselected` 区分主图传入与独立调用：主图传入时跳过重复候选人查询，独立运行时仍使用原有 `candidate_provider`

Agent Server 为主图提供 Checkpointer。三张子图保持默认的单次调用持久化，运行时继承主图 Checkpointer，因此要求确认子图中的 `interrupt()` 可以暂停和恢复，评估子图的内部状态不会跨请求积累

运行逐节点事件演示：

```bash
cd backend
uv run --no-sync python -m scripts.verify_talent_decision_events
```

增加 `--pause` 后，脚本在每个节点输出后等待回车

运行第 14～18 课联合回归：

```bash
cd backend
uv run --no-sync pytest -q \
  tests/test_talent_request_graph.py \
  tests/test_talent_request_hitl.py \
  tests/test_talent_evaluation_dispatch.py \
  tests/test_talent_evaluation_report.py \
  tests/test_talent_decision_engine.py \
  tests/test_talent_decision_graph.py
```

结果输出为 `57 passed, 2 warnings in 1.71s`

后端完整回归结果为 `230 passed, 1 skipped, 2 warnings in 10.57s`

## 第 19 课会话式交互与 SSE 事件协议

| 路径 | 用途 |
|---|---|
| `backend/app/talent_run_stream.py` | 驱动真实决策主图，维护 Run 状态、活动连接事件缓冲、HITL 恢复、取消和引用投影 |
| `backend/app/talent_event_projection.py` | 将内部图更新投影为稳定的 SSE 业务事件和白名单节点输出 |
| `backend/app/talent_run_api.py` | 提供 Run 创建、SSE 订阅、HITL 恢复和取消接口 |
| `backend/scripts/verify_talent_run_live.py` | 实际调用 PostgreSQL、Milvus、Rerank 和模型验收完整链路 |
| `frontend/src/TalentChatPage.tsx` | 完成输入锁定、进度与耗时展示、节点输出抽屉、HITL、取消、Markdown 报告和引用抽屉交互 |
| `frontend/src/talentRunState.ts` | 使用幂等事件投影构建 Run 状态，累计步骤耗时并冻结暂停或取消步骤 |

默认 Run 并发上限为 6。Chat Model 保持同步调用，全链路仅使用 `CHAT_MODEL`，代码不额外施加 LLM 工具超时。模型取证在连接错误、限流和短暂失败时执行受控重试。条件解析 Prompt 注入 DSL 字段语义，地名约束映射到 `region`，不误写为 `current_position`。决策图返回 `capacity_exceeded`、`dimension_invalid` 或 `failed` 时，Run 服务发送 `run.failed` 并保留具体错误，前端停止运行中步骤动画并显示失败原因

运行真实链路验收：

```bash
cd backend
uv run python -m scripts.verify_talent_run_live
```

该脚本先用完整自然语言岗位问题触发岗位确认并提交真实 JD，再用带地区、年限、现任岗位和能力要求的自然语言问题完成小候选集评估。两个场景都读取已入库的合成业务材料，候选人筛选、证据检索、模型取证、评分、报告和引用回查都由正式链路执行，不在 API 层构造固定候选人或报告

## 第 20 课运行记录持久化与结果回看

| 路径 | 用途 |
|---|---|
| `database/migrations/020_run_history.sql` | 创建 `talent_runs` 与 `talent_run_steps` 的幂等迁移 |
| `backend/app/talent_run_history.py` | 将 Run 业务事件合并为 Run 与步骤最新快照 |
| `backend/tests/test_talent_run_history.py` | 验证快照合并、排序与 HITL 耗时 |
| `frontend/src/talent-run-history.css` | 运行历史列表样式 |

`TalentRunManager` 在生成 `RunEvent` 后同步调用运行记录 Store。`run.started` 创建 Run，`step.started` `step.progress` `step.completed` 按 `run_id + step_id` 更新同一行，`report.completed` 保存 Markdown 报告、引用和 Trace ID。SSE 仍然只承担当前连接的实时交付

| 接口 | 用途 |
|---|---|
| `GET /api/runs/history` | 按开始时间倒序返回 Run 列表 |
| `GET /api/runs/history/{run_id}` | 返回 Run、有序步骤、报告和引用快照 |

前端通过 `historyDetailToRunView()` 将历史详情转换为第 19 课已有的 `RunView`，继续复用步骤时间线、节点输出抽屉、Markdown 报告和证据抽屉

## 第 21 课多轮对话与人才要求修订

| 路径 | 用途 |
|---|---|
| `backend/app/talent_checkpoint.py` | 管理 `AsyncPostgresSaver` 连接池、`setup()` 与应用生命周期 |
| `backend/app/talent_request_graph.py` | 使用历史 `messages` 和当前结构化要求完成增删改 |
| `backend/scripts/verify_postgres_checkpointer.py` | 用最小 Graph 验证两轮恢复与四张 Checkpoint 表 |
| `database/migrations/021_conversation_history.sql` | 创建会话标题、状态与更新时间投影 |
| `frontend/src/talent-conversation.css` | 会话状态筛选、多轮消息与修订反馈的页面样式 |

同一会话复用 `conversation_id` 作为 `thread_id`，`AsyncPostgresSaver` 恢复父图的 `messages` 与当前结构化要求，请求子图合并本轮修订后继续完整执行评估与报告链路

创建后续 Run 时提交当前会话标识：

```json
{
  "message": "地区要求删除",
  "conversation_id": "9cf60af0-0f74-4ec4-b887-29f29ed43ee7"
}
```

前端通过 `GET /api/conversations?status=completed` 按状态读取会话标题、状态和更新时间，要求增删改通过 `requirement.revised` SSE 事件实时展示。打开会话时，历史详情接口一次返回截至当前轮的所有 Run、步骤和报告快照，前序轮次答案默认收起，最后一轮默认展开

## 第 22 课 Agent 模块评测与可复现 Case 设计

| 路径 | 用途 |
|---|---|
| `backend/app/evaluation_case.py` | 定义首期四个评测模块、可重放 Case 协议与 SQL 存储器 |
| `backend/app/talent_request_graph.py` | 在人才要求理解的真实调用边界自动记录输入与实际输出 |
| `backend/app/talent_run_stream.py` | 为每个 Run 注入 `run_id`、`conversation_id` 和 `turn_no` |
| `database/migrations/022_evaluation_cases.sql` | 创建四个模块共用的 `evaluation_cases` 原始 Case 表 |
| `backend/scripts/verify_evaluation_case.py` | 调用真实 FastAPI Run 和 SSE，完成后从 PostgreSQL 读回自动采集结果 |
| `backend/tests/test_evaluation_case_store.py` | 使用开发环境 PostgreSQL 验证持久化与调用序号，测试后删除自身记录 |

完整消息继续保存在 LangGraph Checkpoint 中，人才要求理解模块使用最近四轮消息和上一轮完整 `TalentRequest` 形成独立输入。每次 Graph 调用都自动落库，新 Case 的标注状态为 `unlabeled`

执行第 22 课真实链路验证前，需要按开发模式启动后端 API，并完成 `022_evaluation_cases.sql` 迁移。验证脚本会实际调用模型、检索服务和全部人才评估 Graph，直到接收 `run.completed`

## Chunk 模块

| 路径 | 用途 |
|---|---|
| `backend/app/chunking.py` | Markdown 层级切分与递归兜底切分 |
| `backend/app/chunk_service.py` | 解析结果对齐、策略自动判断与 Parent-Child 持久化 |
| `backend/app/document_parsers.py` | DOCX/PPTX/Markdown/音频转录的归一化文本解析 |

第 5 课的代码口径是先统一把材料归一化成可切分文本，再由后端根据结构自动选择切分方式，不再让前端指定策略

默认规则如下

- Markdown、DOCX、PPTX、MinerU 解析后的 PDF 和图片：优先走 Markdown 结构化切分，再建立 Parent-Child
- 语音转录：只保留纯文本和时间区间，直接走递归切分
- DOCX：使用 `python-docx` 读取 OOXML 样式后转成 Markdown
- PPTX：使用 OOXML 解析幻灯片文本并转成 Markdown
- PDF 和图片：统一走 MinerU，保留标题层级、页码和 bbox
- 前端不暴露策略选择，只允许调整 `chunk_size` 和 `chunk_overlap`

所有 Chunk 使用 `markdown_start` 和 `markdown_end` 定位归一化 Markdown

PDF、图片、PPTX 和音频的辅助定位写入 `document_chunks.source_locators`

Markdown 和 DOCX 的 `source_locators` 为空数组

## 第 5 课数据库变更

`005_chunking.sql` 集中包含第 5 课的全部数据库变更

- 新增 `chunking_status` 枚举
- 新增 `chunk_strategy` 枚举
- 新增 `chunking_runs` 切片运行表
- 新增 `document_chunks` Chunk 表
- 新增切片运行、文档版本和候选人查询索引
- `document_chunks` 保存 Parent-Child、前后 Chunk 和标题路径
- `document_chunks` 新增 Markdown 起止偏移
- `document_chunks` 新增原文件辅助定位集合

### 从第 4 课迁移到第 5 课

先确保 PostgreSQL 已经通过同一个项目名启动：

```bash
docker compose -p talent-eval-agents-course up -d postgres
```

然后在代码仓库根目录执行一次以下命令

```bash
docker compose -p talent-eval-agents-course exec -T postgres \
  psql -U talent -d talent_docs -v ON_ERROR_STOP=1 -f /dev/stdin \
 < database/migrations/005_chunking.sql
```
迁移脚本使用 `IF NOT EXISTS` 与重复对象处理，可以在已执行旧版 `005` 的开发环境中再次执行。

全新环境由 `database/init.sql` 直接创建最新结构，不需要再执行增量迁移

## Milvus 证据索引

Milvus 作为独立向量检索服务，PostgreSQL 继续保存文档、版本、Chunk 和索引任务状态

| 路径 | 用途 |
|---|---|
| `backend/app/milvus_store.py` | Collection Schema、HNSW、Upsert、标量过滤、搜索与按版本删除 |
| `backend/app/evidence_index_service.py` | Chunk 向量化、Evidence Record 映射、索引任务执行与状态更新 |
| `backend/app/document_pipeline.py` | 上传后的自动解析、自动切片、自动索引编排与处理状态汇总 |
| `backend/app/document_cleanup.py` | 文档删除时的对象存储、PostgreSQL 和 Milvus 级联清理 |
| `backend/scripts/verify_milvus.py` | 使用确定性向量验证 Collection、Upsert、权限过滤和 HNSW 搜索 |
| `backend/tests/test_milvus_store.py` | Milvus 存储适配器的行为回归测试 |
| `backend/tests/test_evidence_index_service.py` | Chunk 到 Evidence Record 的转换与索引编排测试 |
| `backend/tests/test_document_pipeline.py` | 自动处理编排与任务入队测试 |
| `backend/tests/test_document_cleanup.py` | 材料删除时的跨存储级联清理测试 |

开发环境使用 Milvus Standalone 2.6.17、etcd 和已有 MinIO

- 宿主机 Milvus gRPC 端口：`19531`
- 宿主机 Milvus 健康检查端口：`19091`
- 容器内 backend 与 worker 通过 `http://milvus:19530` 访问

先确保 PostgreSQL 已经通过同一个项目名启动：

```bash
docker compose -p talent-eval-agents-course up -d postgres
```

然后在代码仓库根目录执行：

```bash
docker compose -p talent-eval-agents-course exec -T postgres \
  psql -U talent -d talent_docs -v ON_ERROR_STOP=1 -f /dev/stdin \
  < database/migrations/007_milvus_evidence_index.sql
```

全新环境由 `database/init.sql` 直接创建最新结构，不需要再执行增量迁移

启动服务并执行 Milvus 独立验收：

```bash
docker compose -p talent-eval-agents-course up -d
cd backend
uv run python -m scripts.verify_milvus
```

验收脚本输出

```json
{"collection": "lesson6_verification_v1", "upserted": 2, "matched": 1, "top_candidate": "C001", "top_score": 1.0, "permission_scope": "hr_private"}
```

Collection 默认使用 1024 维向量、COSINE 距离和 HNSW 索引

- `M=16`

- `efConstruction=128`

- 查询默认 `ef=80`

- 常规查询默认使用 Bounded consistency

- 写后读验收使用 Strong consistency

## 混合检索与精排

Collection `talent_evidence_v2` 在稠密向量之外增加 BM25 稀疏向量，由 Milvus 原生 Function 在写入时从 `content` 自动生成，`content` 使用 `chinese`（jieba + cnalphanumonly）分析器分词

| 路径 | 用途 |
|---|---|
| `backend/app/reranker.py` | DashScope `gte-rerank-v2` 的 Cross-Encoder 精排封装 |
| `backend/app/hybrid_search_service.py` | 稠密 + BM25 召回、RRF 融合、Rerank 精排的编排 |
| `backend/scripts/verify_hybrid.py` | 对比纯向量、纯 BM25、混合召回和精排的召回与延迟 |
| `backend/scripts/import_markdown_reviews.py` | 上传 5 份项目复盘 Markdown 并走完解析、切片、索引 |
| `backend/tests/test_hybrid_search_service.py` | 混合检索编排与 Rerank 映射的回归测试 |

稠密召回使用 `text-embedding-v3` 生成 1024 维查询向量，BM25 召回直接传入查询原文，两路结果由 `RRFRanker(k=60)` 融合，最后用 `gte-rerank-v2` 对候选做 Cross-Encoder 精排

相关配置

- `RERANK_MODEL`：精排模型，默认 `gte-rerank-v2`

- `RERANK_TOP_N`：精排候选数，默认 20

- `MILVUS_COLLECTION`：证据集合，当前为 `talent_evidence_v2`

混合检索验收

```bash
cd backend
uv run python -m scripts.verify_hybrid "有 Flink 实时计算经验的数据平台工程师" 5
```

## 验证

```bash
cd backend
uv sync --dev
uv run pytest tests -q
```

当前回归结果以本次本地 `pytest` 结果为准

结果输出：`177 passed, 2 warnings in 6.69s`

## 服务日志

backend 与 worker 使用统一 Python 日志配置，控制台日志同时写入 `backend/logs/`

```text
backend/logs/backend-YYYY-MM-DD.log
backend/logs/backend-error-YYYY-MM-DD.log
backend/logs/worker-YYYY-MM-DD.log
backend/logs/worker-error-YYYY-MM-DD.log
```

普通日志记录 HTTP 请求、员工与知识库写入、文件上传、MinIO 读写、解析任务、产物和 Worker 消费过程。ERROR 及以上日志额外写入对应服务的独立 error 文件。服务跨越零点运行时自动切换到新的日期文件

本地运行默认使用 `backend/logs/`。发布模式下，Compose 为 backend 和 worker 配置 `LOG_DIR=/app/logs` 与 `TZ=Asia/Shanghai`，并将宿主机 `./backend/logs` 挂载到容器内

## 端口约定

- PostgreSQL：`15432`
- Redis：`16379`
- MinIO API：`19000`
- MinIO Console：`19001`
- Milvus gRPC：`19531`
- Milvus Health：`19091`
- Backend API：`18080`
- Frontend：`15173`

## 第 23 课线上 Case 采集与评测中心

四个模块共用 `evaluation_cases`。`capture_call` 保存调用前深拷贝的输入与返回结果或异常，`safe_record` 隔离写入失败；PostgreSQL 事务锁串行分配同一 Run/模块的调用序号。事实抽取在每次模型尝试边界采集，保留短 Chunk 标识、冻结文本、输出 Schema、来源映射和候选人/维度/要求身份。冲突与引用校验属于后处理，不写成模型输出。

| 路径 | 用途、关键接口 |
| --- | --- |
| `backend/app/evaluation_case.py` | `capture_call(call, context, module_key, input_payload, ...)` 返回原业务结果；`extraction_scope` 管理线程内业务身份 |
| `backend/app/talent_request_graph.py` | 岗位候选及路由采集、要求理解采集失败隔离 |
| `backend/app/talent_evaluation_dispatch.py` | 维度生成采集、证据要求线程设置身份 |
| `backend/app/evidence_pack.py` | 模型输入与原始短标识输出采集，模型重试逐次记录 |
| `backend/app/talent_tools.py` | `copy_context().run` 将调用方上下文传给工具执行线程 |
| `backend/app/models.py` | `EvaluationFeedbackRecord`，反馈外键关联 Run 与可选 Case |
| `backend/app/evaluation_api.py` | Case 分页筛选、详情和反馈创建接口 |
| `database/migrations/023_evaluation_feedback.sql` | 幂等新增反馈表与索引、范围组合约束 |
| `frontend/src/EvaluationCenter.tsx` | 评测中心、步骤调用选择与反馈表单 |
| `frontend/src/evaluation.css` | 评测中心与反馈表单样式 |
| `backend/scripts/verify_evaluation_case.py` | 第 22 课验收继续明确筛选要求理解模块，避免其他模块加入后读取错误记录 |
| `backend/scripts/verify_evaluation_center.py` | 真实模型 Run → 四模块采集 → 两种反馈 → PostgreSQL 关联断言 |
| `backend/tests/test_lesson23_collection.py` | PostgreSQL 并发、异常隔离、跨工具线程身份验证 |
| `backend/tests/test_evaluation_feedback.py` | 请求范围、关联有效性、原始载荷不变与查询验证 |

接口：

- `GET /api/evaluation/cases`：支持 `source_run_id`、`module_key`、`status`、`has_feedback`、`offset`、`limit`，返回 `items` 与 `has_more`
- `GET /api/evaluation/cases/{case_id}`：原始输入输出、上下文、版本和关联反馈
- `POST /api/evaluation/feedback`：`source_run_id`、`case_id`、`feedback_scope`、`note`，模块必须引用同 Run 的 Case，报告级 Case 为空

`has_feedback` 仅表示模块直接反馈，`run_has_feedback` 单独表示报告反馈。反馈不会自动标注得分或修改 `annotation_status`。页面每页 100 条调用，同 Run 可跨页；步骤抽屉按每页 200 条读取当前 Run 的全部记录，再按步骤模块筛选。

工程根目录执行迁移：

```bash
docker compose -p talent-eval-agents-course exec -T postgres \
  psql -U talent -d talent_docs -v ON_ERROR_STOP=1 -f /dev/stdin \
  < database/migrations/023_evaluation_feedback.sql
```

按开发模式启动后端 18080 与前端 15173，进入评测中心；真实验收要求已有岗位、人才材料及 Milvus 索引，且模型、Embedding 和 Rerank 配置有效。

```bash
cd backend
uv run --no-sync python -m scripts.verify_evaluation_center
```

验收 Run `1e5a2d03-4db3-4897-86dc-b041277213fd` 完成报告，共采集 58 条 Case，其中事实抽取 54 条、失败 Case 1 条，两种反馈各 1 条。模型结果与数量可能变化。

当前沿用课程单租户开发访问边界，没有新增身份认证；原文 Case 访问需要在部署前与既有 Run、人才材料接口统一实施授权。采集采用尽力写入，数据库失败会记录 `case_collection_failed`，不会阻断业务，也没有持久化补偿队列。岗位 Case 保存候选版本与规则版本，严格重放还需要固定当时岗位库。

## 第 24 课 LLM Judge、Golden 审核与模块重放

复用现有评测中心，新增两张表和手动批次接口，每批最多 20 条顺序执行，不引入队列、定时任务或实验平台。

| 文件 | 用途与接口 |
| --- | --- |
| `backend/app/evaluation_judge.py` | `judge_case(case, golden_output=None)`：四模块评分、候选答案与 Judge Prompt 脱敏；`replay_output(item)`：调用当前人才要求理解函数 |
| `backend/app/evaluation_api.py` | `POST /api/evaluation/judge` 接收 `case_ids`；`POST /cases/{case_id}/confirm` 接收完整 `golden_output`、`dataset_split` 和 `tags`；`GET /dataset`；`POST /replay` 接收 `dataset_item_ids`，以上均在 `/api/evaluation` 前缀下 |
| `backend/app/models.py` | Annotation 以 Case 为主键，Dataset Item 以来源 Case 唯一，确认在同一事务中完成 |
| `database/migrations/024_evaluation_judge.sql` | 幂等新增标注、数据集表及评分索引；`database/init.sql` 同步支持新库 |
| `frontend/src/EvaluationCenter.tsx` | 模块、反馈、状态、时间、未标注及 0—2 分筛选，选择最多 20 条 Case 批量 Judge |
| `frontend/src/EvaluationReview.tsx` | Golden 编辑和确认、数据集分页、选择条目与手动重放 |
| `backend/tests/test_evaluation_judge.py` | 独立模型、错误隔离、审核入集、原始数据不变、固定输入及重放分母测试 |
| `backend/scripts/verify_evaluation_judge.py` | `judge CASE_ID...`、`confirm CASE_ID GOLDEN_FILE`、`replay DATASET_ITEM_ID...` 三个手动步骤，不自动确认模型答案 |

配置沿用 `.env` 中用户提供的 `get_judge_model()` 配置：`JUDGE_MODEL`、`JUDGE_API_KEY`、`JUDGE_BASE_URL`，通常使用不同于 `CHAT_MODEL` 的评判模型。缺少 Judge 配置时报错，不回退至 Chat 模型。四个业务模块重放仍调用 `get_chat_model()` 或模块原有确定性实现。修复 Chat 工厂 `model` 未定义的问题，未更改用户的模型选择。

Judge 输入使用 JSON 区分 `task_spec`、`input`、`actual_output` 和可选 `reference_output`。人才要求理解、动态维度生成和事实抽取直接复用各自运行时的原始任务 Prompt，岗位匹配使用对应的确定性路由规则；每次只发送当前模块的任务规范，不发送 `output_schema`。实际输出在模型调用前通过模块 Schema 校验；Golden 候选在调用后校验，候选无效时保留评分并记录 `golden_candidate_invalid`。`verdict` 由程序根据分数生成。

工程根目录迁移：

```bash
docker compose -p talent-eval-agents-course exec -T postgres \
  psql -U talent -d talent_docs -v ON_ERROR_STOP=1 -f /dev/stdin \
  < database/migrations/024_evaluation_judge.sql
```

启动 API 18080 和现有前端 15173，打开评测中心，筛选未标注 Case，选择条目后点击批量 Judge；查看原始输入、输出、得分和原因，编辑完整 Golden 后选择开发集或测试集并确认。数据集区可查看冻结快照并选择四个模块的条目重放。

脚本从 `backend/` 运行，帮助入口：

```bash
uv run --no-sync python -m scripts.verify_evaluation_judge --help
```

`--api-base` 位于子命令之前，默认 `http://127.0.0.1:18080`。Case 和 Dataset ID 从页面或列表接口读取；`confirm` 显式读取经过复核的 JSON 文件。

行为边界：

- 原始 Case 不修改，标注状态以关联 Annotation 为准，历史 `annotation_status` 不参与新逻辑
- 每条 Case 保留一次 Annotation，已有标注跳过，Judge 服务失败仍可手动重试
- 原始模块失败规则标 0 分、候选为空；Judge 失败不产生质量分数
- 人工可以不经过 Judge 直接填写 Golden，分数留空，结构校验不能代替业务复核
- 确认后的 Dataset Item 不可覆盖，唯一来源约束防止重复确认，候选答案保留在 Annotation
- 岗位评价不具备完整历史岗位库，不能评价完整召回率，规则明确唯一 exact 的优先级
- 人才要求理解、岗位匹配、维度生成和事实抽取均按冻结输入重放，只执行对应模块，不运行整个 Graph
- 重放失败的业务模块计 0 分；Judge 失败无分数；`complete=false` 时平均分仅代表已评分部分
- 开发集用于调试，测试集用于固定回归，划分时应人工避免同一会话或近似样本跨集合
- 继续使用课程现有访问边界，未新增用户认证和审核人审计体系

验收产物在 `backend/artifacts/lesson24-annotations.json`、`lesson24-dataset.json`、`lesson24-replay.json` 和 `lesson24-replay-judge-error.json`，开发集样本由本次开发验收复核生成，不能等同于业务专家审核数据。

验收结果：四模块成功 Case 分别得到 4、4、4、3 分，失败 Case 由规则标 0 分；固定开发集重放完成，单条及平均分为 3，业务模型 `qwen3.8-max`、Judge `gpt-5.5`。精简后的 Judge 使用真实人才要求理解 Case 返回 4 分、完整候选和 0.98 置信度。后端回归 293 passed、1 skipped，前端 15 passed，构建通过。

### 评测中心页面布局

评测中心沿用全局侧栏图标、品牌、环境标识和折叠状态，顶部区分“评测样例”和“数据集”。样例页默认展示全部 Case，“按 Run 分组查看”复选框与筛选项位于同一区域，低分 Case（0—2 分）可直接勾选筛选，支持整行打开详情，复选框独立选中。

`GET /api/evaluation/cases?group_by=run&limit=10` 先对符合筛选的 Case 按 Run 聚合，再按 `completed_at` 倒序分页；返回会话标题、完成时间、Run ID 和筛选后的 Case 数。缺少完成时间的 Run 排在最后。每页 10 个 Run，分组默认折叠，展开后按每页 10 条懒加载 Case；默认列表每页 10 条。

数据集 Tab 每页 10 条，以表格展示 split、模块、source_case_id 和创建时间，支持 split 与模块筛选，点击行查看固定输入与 Golden。所有模块的条目均可通过复选框选择，并沿用原有重放接口。更新后需要重启后端以加载分组查询参数，不涉及数据库迁移。

工作台通过 `GET /api/dashboard/metrics` 展示员工数量、档案数量、总评估会话数、总评估轮次数、评测样例数和数据集条目数。指标直接基于现有业务表聚合，不新增缓存或统计表。

## 第 25 课外部模型数据脱敏与出站防护

| 路径 | 用途 |
| --- | --- |
| `backend/scripts/verify_langchain_pii.py` | 使用本地回显模型展示 `PIIMiddleware` 的 `redact`、`mask`、`hash` 和 `block` 实际输出 |
| `backend/app/prompt_sanitizer.py` | `sanitize_prompt(original_prompt)` 把原始 Prompt 字符串转换为脱敏字符串 |
| `backend/app/evaluation_judge.py` | Judge 在调用外部模型前对用户 Prompt 执行一次脱敏 |
| `backend/tests/test_prompt_sanitizer.py` | 覆盖姓名、手机号、邮箱、身份证号和重复值替换 |
| `backend/tests/test_verify_langchain_pii.py` | 固定 LangChain 四种 PII 策略的演示输出 |

默认规则检测带标签的中文姓名、中国大陆手机号、邮箱和身份证号。函数只接收一个 `str`，并返回一个脱敏后的 `str`。同一原值在一次转换中使用同一占位符。

定向验证：

```bash
cd backend
uv run --no-sync python -m scripts.verify_langchain_pii

uv run --no-sync pytest -q \
  tests/test_prompt_sanitizer.py \
  tests/test_evaluation_judge.py::test_judge_redacts_pii_before_external_model_call
```

全量后端回归为 `297 passed, 1 skipped`。当本机设置 SOCKS 代理但当前 `httpx` 未安装 `socksio` 时，构造 `ChatOpenAI` 的工厂测试会先因代理依赖失败；应补齐 `httpx[socks]` 或在本次本地回归中取消该代理环境变量。

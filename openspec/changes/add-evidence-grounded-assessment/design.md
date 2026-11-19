# Design

## Context

动机见 [proposal.md](proposal.md)。以下为本次只读检查确认的实现基础：

- `backend/app/talent_evaluation_report.py` 已定义五类证据状态；`validate_dimension_judgement()` 保证缺失和冲突无分数，`aggregate_candidate_assessments()` 对未评分维度只增加理论上限，`rank_candidate_assessments()` 按已确认分、覆盖率和候选人编号排序。需要补全语义约束，而非重写评分公式。
- `ReportDraft.overview`、候选人 `summary` 与 `risks: list[str]` 是自由文字。`validate_report_draft()` 主要校验候选人顺序和 highlight 引用归属，没有覆盖这些字段的语义及数值一致性。`render_report_markdown()` 的数字头部虽由程序生成，后续文字仍能自相矛盾。
- `evidence_pack.py` 保存 `event / period / claim / answer`，校验连续原文并计算区间。报告的 `evidence_items_from_branches()` 以 `chunk_id` 去重，同块不同 quote 可能仅保留首个引用；这一身份粒度必须在本次修正。
- `talent_decision_engine.py` 通过共享 State 接入报告子图；`_begin_turn()` 显式清理轮次产物。`talent_run_stream.py` 在 `validate_report` 更新时发出 `report.completed`，`talent_run_history.py` 仅投影 Markdown、引用与 trace。
- `evaluation_case.py` 已有安全采集和版本快照，`evaluation_judge.py` 只覆盖四类模块；`evaluation_api.py` 已有人工确认、dev/test 数据集与回放。新能力应扩展这些入口，避免并行建设第二套评测系统。
- 工作区父目录的 `../work/e2e-20261119/验收记录.md` 记录了功能成功与文字偏差；这些观察不能被解释成已完成语义质量基准。

## Goals / Non-Goals

**Goals:**

- 在评分聚合前和报告发布前分别实施依据检查，任何降级都有稳定原因码与可见状态。
- 精确区分“材料声称”“系统评分”和“尚未核实”；通过模型审查仍不表示事实已独立证实。
- 对现有 Run/SSE/历史和评测 API 采用可选字段扩展，兼容旧数据。
- 每个决定都能以冻结输入独立回放，不依赖当时数据库最新内容。

**Non-Goals:**

- 本次不改变权重、排序公式、原有检索行为，也不把模型置信度当作真实概率。
- 不做面试记录写入与自动重评，不新增解析器、Agent 平台或独立向量库。
- 不借本变更扩大现有身份或权限模型；生产身份认证仍是独立工作。

## Decisions

### 1. 两层状态与版本化协议

新增独立模块 `backend/app/assessment_grounding.py`，将协议与 `talent_evaluation_report.py` 的编排分离。保留现有 `EvidenceStatus`，新增以下内部协议：

| 对象 | 关键字段与约束 |
| --- | --- |
| `EvidenceAnchor` | `evidence_id`、候选人、材料/版本标识、chunk、quote 起止及原文、现有引用 URL |
| `AssessmentClaim` | 稳定 `claim_id`、候选人集合、维度、`kind`、命题文本、`anchor_ids` 或 `assessment_refs`；kind 为事实、状态或比较 |
| `ClaimReview` | `claim_id`、`supported / contradicted / insufficient / conflicting / error`、公开原因码与简短依据；不得自行新增引用 |
| `AssessmentGroundingSnapshot` | `schema_version=1.0`、`policy_version`、聚合前后评分、命题及审查结果、核实清单、审查汇总与模型/提示词版本 |

“原文支持一个否定命题”仍是 `supported`；`contradicted` 指命题被原文反驳。`missing` 指材料不足，`failed` 指取证/评分/审查执行或协议失败，二者不混用。模型建议标签只作为受约束审查输出，不能修改状态、权重和数值规则。

备选方案是继续增加提示词提醒。这能减少偏差，但不能阻止自由摘要绕过检查，因此选择结构化协议与发布过滤。

### 2. 证据身份从块升级到精确原文区间

`EvidenceAnchor` 的唯一身份包含候选人、文档版本、chunk 和 `[quote_start, quote_end)`；不使用 quote 文本本身作为唯一身份。保留现有 `/api/evidence/citations/{chunk_id}?quote_start=...&quote_end=...` URL 格式。为新报告生成的 E1/E2 别名映射精确 anchor，不再只映射 chunk；旧报告原映射不变。

从已鉴权的来源加载阶段补齐材料版本信息，进入评分和报告前复核原文、范围与候选人归属；复用 `evidence_citations.py` 的访问校验，不允许模型指定租户或权限。报告、评分、一致性审查和 Run 引用收集的去重逻辑统一迁移到 anchor 身份，避免只改展示而评分仍看到错误片段。

已绑定的历史版本继续可引用；删除或权限撤销时，引用回查沿用当前权限行为并提示不可访问，不用最新文本替换旧文本。

备选方案是仅在 UI 保存多个 URL。这不能修复评分输入阶段已经丢失的 quote，因此必须贯穿模型输入和状态传递。

### 3. 先校验评分，再聚合

扩展评分输出为有界结构化评分理由：每个维度最多四个事实命题及一个与评分锚点对应的评分说明。评分前后的状态约束仍由程序检查。新增 `ground_dimension_assessments` 节点，按候选人批量检查事实命题和评分锚点支持关系，放在 `score_dimensions` 与 `review_consistency` 之间。

- `sufficient / partial` 只有在必需评分命题及锚点关系都为 supported 时保留原分数。
- 审查发现原文不支持或反驳评分理由时不自动创造新分数；将该维度置为 `failed + score_grounding_rejected + score=null`，保留原始草稿供评测，并生成中立核实事项。
- 审查超时或协议错误置为 `failed + grounding_unavailable`，只生成技术重试事项。
- 已有 `missing / conflicting / failed` 不送模型重复证明状态，其展示和计分资格由记录决定。明确否定事实在通过审查且符合评分锚点时可以得到实际零分。

一致性审查只接收保留评分的维度，随后复用现有聚合和排序。报告阶段被拦截的修辞不会反向修改已通过检查的维度分数。

备选方案是只审查最终报告；它无法阻止不受支持的理由先影响排名，因此需要聚合前检查。

### 4. 报告发布只使用已批准命题和确定性模板

将模型草稿中的自由 overview、summary、risks 改为结构化命题集合。标题由固定模板和运行上下文生成，数值、排名、覆盖率、置信等级和未评分状态由程序生成。跨候选人的总览默认只比较程序数值与覆盖状态；若需要事实比较，必须引用双方通过审查的命题，再经同一结论审查器检查，不允许用一方证据推出另一方缺乏能力。

新增 `ground_report_claims` 节点，复用同一审查器，只有 supported 事实可以作为主要依据或事实风险。状态风险依据 `missing_items / conflicts / failure` 标识确定性生成，不依赖另一个模型自由复述。模型引用了不存在、错误候选人或错误范围的 ID 时记录拦截，不静默删掉后宣称报告完整。

渲染节点保留名称 `validate_report`，输出 Markdown、精确引用与快照，避免破坏现有事件触发。有效评分与命题为零时仍可以输出“当前材料无法完成评估”的程序化结果，审查汇总显示 degraded/partial，不能显示“全部结论通过”。

建议新链路：`receive_input → score_dimensions → ground_dimension_assessments → review_consistency → aggregate_candidates → compose_report → ground_report_claims → build_verification_checklist → validate_report`。

### 5. 有界模型调用与可观察降级

业务审查复用现有 CHAT 凭据，允许用 `GROUNDING_MODEL` 覆盖模型名；不依赖尚未配置的评测 Judge，也不引入新外部服务。构造独立审查客户端并显式关闭 SDK 隐式重试，避免与应用重试相乘。

- 每批至多 24 个命题，按候选人隔离并按稳定 ID 分批；报告每名候选人至多 16 个事实命题，超限为协议错误，不能无提示截断。
- 默认并发四批，每批至多两次调用、单次请求超时 30 秒、整批截止 60 秒；重试仅用于可恢复连接错误或协议修正。有效但不受支持的命题不反复请求直到“通过”。
- 记录批次、尝试、耗时、提交/批准/拦截/错误数量及策略版本；原文只保存在既有受控案例输入中，常规日志不打印完整材料或凭据。
- 模型返回重复、遗漏或额外 claim ID，或理由引用了未提供依据，按协议失败处理；不会把未知 ID 忽略后批量放行。
- Run 取消传播到审查任务；使用实际模型请求超时和并发限制，不以线程等待超时代替网络请求终止。

备选方案是继续共享无限等待的模型客户端。当前启动排障已暴露长时间停滞体验，因此审查必须有独立边界。上述数值是第一版默认配置，可在不改变规格状态语义的前提下调优。

### 6. 核实清单采用确定性模板

新增 `backend/app/assessment_verification.py`。输入只包含缺失记录、冲突记录、被拦截事实命题和技术失败，输出 `item_id / candidate_id / dimension_numbers / reason_type / trigger_ids / anchor_ids / question / expected_material / priority`。

条目按维度最大权重降序、原因类型固定顺序（conflicting、missing、unsupported）、稳定 ID 排序；按候选人、维度、触发记录归并，最多八条，额外返回 omitted_count。技术失败走独立 technical_actions 列表，不挤占面试问题名额。问题以“请说明……并提供可核实材料”模板呈现，来源文字作为引用展示而非指令执行。模板版本进入快照，不增加生成问题的模型调用。

备选方案是让模型另写面试题。第一期采用模板可保持中立、可重复且便于验证；多轮面试工作流另立变更。

### 7. 以快照贯穿子图、事件、历史和页面

为报告子图和 `TalentDecisionEngineState/Output` 增加 `assessment_grounding`，在 `_begin_turn()` 中清空，避免上一轮数据泄露到下一轮。为 `TalentRun` 收集该快照，并将 `report.completed` 扩展为保留原字段外加 `assessment_grounding`。

为 `talent_runs` 新增可空 JSON 列 `assessment_grounding`，更新 `models.py`、`database/init.sql`，新增 `database/migrations/025_assessment_grounding.sql` 的幂等迁移。历史投影在同一事务保存 Markdown、引用和快照；Run 详情及每个 conversation_turn 都返回可选快照。未知版本快照按不可解析处理，展示可读报告和“当前客户端无法展示本版审查信息”，不猜测状态。

`talentRunState.ts` 的实时 reducer、历史恢复及每轮类型同步扩展；`TalentChatPage.tsx` 显示维度计分状态、通过审查的材料命题、被拦截数量和核实清单，复用引用抽屉。把当前固定“结论均可回溯到人才原始材料”文案改为由快照决定的状态描述，旧报告显示历史标记。

不把“完成运行”与“所有结论获支持”合并成一个状态：Run 仍可 completed，快照另标 degraded/partial/complete。

### 8. 扩展现有采集和冻结回放

`EvaluationModule` 新增 `dimension_scoring` 与 `report_grounding`；后者同时记录 `phase=dimension|report`。使用显式 `DecisionContext` 传入报告子图节点、每个线程调用的 `capture_call` 及版本快照，避免依赖未传播到线程池的隐式上下文。既有采集失败不影响业务的行为继续保留。

新模块输入冻结维度锚点、命题和完整 anchor，输出协议可被现有人工 Golden 编辑器校验；人工确认的 `report_grounding` 输出提供逐 claim 标签，评分 Golden 提供计分资格及合法状态。扩展 `TASK_SPECS`、输出协议映射、`REPLAY_HANDLERS`、模块筛选和步骤反馈映射。

`POST /api/evaluation/replay` 保持现有 items 和通用 Judge 指标，新增仅适用于新模块的 `quality_metrics`。新指标直接对比人工 Golden 与模块实际输出，不要求外部 Judge 配置；通用 Judge 失败单独记录，不取消已经完成的确定性对比。新增基准脚本按相同冻结输入运行基线和当前配置，并生成版本对比文件，不为本期增加新的常驻评测服务。

### 9. 指标、基线与验收口径

以下是实施阶段的目标，当前尚未测得：

| 指标 | 分子 / 分母 | 第一版验收目标 |
| --- | --- | --- |
| 评分资格一致率 | 与人工 Golden 一致的可评分/不可评分状态数 / 已标注维度数 | 固定离线边界样例 100%；真实模型基准至少 95% |
| 发布结论支持率 | 人工标为 supported 且被发布的命题数 / 全部被发布命题数 | 至少 95% |
| 无依据结论拦截率 | 未发布的人工非 supported 命题数 / 人工非 supported 命题数 | 至少 95% |
| 正确结论误拦率 | 未发布的人工 supported 命题数 / 人工 supported 命题数 | 不高于 10%，含执行失败造成的拦截 |
| 引用校验 | 通过候选人、版本、区间、权限检查的引用数 / 提交引用数 | 非法引用被发布数为零 |

每项输出计数、比例与不可计算原因；语义结论为 error 的命题在端到端指标中按未发布处理，同时单列失败数。任何执行失败、不兼容或空分母都会使验收状态为 incomplete，不能只凭部分样本宣布达标。通用 Judge 的 0–4 分保留独立含义，不替代这些比例。

基线集至少 60 个虚构模块案例，40 个 dev、20 个 test；留出 test 至少 60 条人工标注命题，至少各 20 条 supported 和非 supported。按项目/候选人场景整体分割，避免同一材料仅换措辞泄漏到 test。覆盖规格列出的失败模式及合法零分、明确否定、等价技术但未点名框架等正常样本。冻结标签及清单 hash 后再跑版本比较；test 不用于逐次提示词调优。

受限样本结果只代表该基准，必须披露样本量、版本及耗时，不宣称真实招聘准确率。已有完整链路记录可用于还原故障，但默认模型基准仅使用新建虚构材料。

## Risks / Trade-offs

- [模型审查可能重复生成器的偏差] → 人工 Golden 为正式参考，加入强负例和误拦指标，允许换审查模型但不把通过等同事实真实性。
- [审查增加延迟与费用] → 候选人隔离批处理、只审查事实与锚点关系、状态及问题走程序模板，记录阶段耗时与调用次数。暂不承诺未实测的提速或成本比例。
- [严格检查导致更多维度未评分] → 明示原因与可核实材料，监测正确结论误拦率，不以放宽来源边界提高完成率。
- [anchor 身份变化影响现有评分和引用测试] → 为新协议升级端到端映射并保留旧历史 URL；补充同块多 quote、跨维度共享 quote 与精确范围回归。
- [快照增加存储且含人才材料] → 每轮保存受限大小的审查产物并复用已有来源引用，不引入新的公开材料接口；沿用当前访问边界，不扩大外部模型输入范围。
- [全部删除结论掩盖质量问题] → 同时约束发布支持率和正确结论误拦率，并使执行不完整不能通过验收。

## Migration Plan

1. 实现并验证协议、anchor 映射、评分前检查与报告发布检查；离线测试使用注入审查器，不访问外部模型。
2. 执行幂等新增列迁移，再发布能写读快照的后端，最后发布支持可选快照的前端；旧客户端继续读取 Markdown 与 citations。
3. 不重写既有历史报告；存量快照为空，页面标注旧版。对迁移执行两次及旧记录读取做数据库验证。
4. 显式运行合成模型基准，审查人工标注、完成情况与指标；未达目标先分析案例，不把目标写成实测结果。
5. 如需回滚业务实现，保留新增可空列和快照数据，以及能识别空快照的兼容前端；旧后端只读写原字段，回滚期间新生成报告显示未审查标记，不能宣称继续有本版保证。不删除历史材料、评测记录或迁移列。

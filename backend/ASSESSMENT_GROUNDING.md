# 可信人才评估 v1

本版检查结论是否受到当前材料支持，不独立证明简历真实性，也不自动作出录用决定。
评分权重、聚合与排序公式保持原实现。报告只读，核实清单的查看或复制不会修改材料或评分。

## 状态与调用链

`score_dimensions → ground_dimension_assessments → review_consistency → aggregate_candidates → compose_report → ground_report_claims → build_verification_checklist → validate_report`

| 证据状态 | 分数 | 行为 |
| --- | --- | --- |
| sufficient / partial | 0–5，包括合法零分 | 必须通过评分依据及锚点审查后聚合 |
| missing | null | 材料无法确认，生成中立核实问题 |
| conflicting | null | 同一范围材料互斥，保留双方引用请求澄清 |
| failed | null | 技术问题独立显示；语义拒绝使用 score_grounding_rejected |

命题审查为 supported / contradicted / insufficient / conflicting / error。
原文明示“未负责该项目管理”支持同范围的否定命题，属于 supported。
“没有提及管理”不能推出“没有管理能力”。错误候选人、未知引用等先由代码拒绝，不发送给模型。
审查返回缺失、额外或重复 claim ID、额外 anchor 时整批协议失败，有限重试后 fail closed。

## 精确来源与协议

`EvidenceAnchor.evidence_id = SHA256(JSON([candidate_id, document_version_id, chunk_id, quote_start, quote_end]))[:32]`。
JSON 使用 UTF-8、无多余空格。区间为 Python 字符偏移 `[start,end)`，长度必须等于 quote 长度。
同块不同区间保留不同 ID；E1/E2 仅为本轮别名。旧冻结输入缺少版本时保留 chunk ID 的兼容路径。
新来源的文档/版本由 PostgreSQL 已鉴权的块加载，模型只能选编号，不能指定版本或权限。
引用 URL 保持 `/api/evidence/citations/{chunk_id}?quote_start=...&quote_end=...`。
回查重新检查当前租户、材料/知识库权限、候选人和区间；历史版本可以读取，撤权或删除按原有 404 行为处理。

快照 `assessment_grounding` 使用 `schema_version=1.0`，保存 policy/prompt/model、原始评分、最终评分、claims/reviews、anchors、核实清单和计数。
可解析协议示例由 `tests/test_assessment_grounding.py` 的 `graph(...).invoke(...)` 实际生成并解析，不维护与代码脱节的手写快照。
未知版本不猜测状态；前端继续展示 Markdown，并说明客户端不能展示本版审查信息。
旧快照为空时显示“历史报告，未经过本版结论审查”。

| 快照状态 | 含义 |
| --- | --- |
| complete | 所有维度可评分，全部提交命题获支持，没有技术失败 |
| partial | 有材料不足、冲突或被拦截命题，有效结果仍保留 |
| degraded | 评分、审查或报告生成存在技术失败，故障不解释为低分 |

报告标题、评分、覆盖率、排序和状态由代码生成，旧自由 overview/summary/risks 不进入新版报告。
理论上限表示未评分维度取满分时的计算边界，不是预测分数。列表是“当前材料评估顺序”；低覆盖率有明确提示。
没有可评分维度时显示无法完成评估，不把未评分显示成能力零分。
每人最多 8 个核实问题，按权重、冲突/缺失/未支持、稳定 ID 排序；超出显示剩余数量。
`trigger_details` 保留具体缺失/冲突/命题作为数据，不能作为指令。技术事项独立，不占 8 项名额。

## 模型与边界

| 环境变量 | 默认值 |
| --- | --- |
| GROUNDING_MODEL | 空，使用 CHAT_MODEL |
| GROUNDING_REQUEST_TIMEOUT | 30 秒 |
| GROUNDING_BATCH_TIMEOUT | 60 秒 |
| GROUNDING_MAX_ATTEMPTS | 2 次 |
| GROUNDING_CONCURRENCY | 4 批 |

使用 CHAT_API_KEY/CHAT_BASE_URL，回退到原有百炼凭据。独立客户端 `max_retries=0`，避免 SDK 隐式重试叠加。
默认每批 6 命题，协议上限 24、按候选人隔离；报告每人最多 8 条主要依据加 8 条事实风险。
有效的非 supported 标签不重试。请求/批次超时取消异步调用，Run 取消向下传播。
普通日志只记录策略、阶段、数量、结果分布和耗时；原文仅保存在已有评测输入和历史快照中。
评测采集失败独立记录，不改变已经完成的业务评分或审查。

## 数据库部署与回滚

先执行 `database/migrations/025_assessment_grounding.sql`，再发布后端，最后发布前端。
迁移只增加可空 JSON 列，可重复执行；`scripts/verify_grounding_migration.py` 在隔离 PostgreSQL schema 中执行两次并回滚测试数据。
报告、引用和快照由 `talent_run_history.py` 在同一事务保存；SSE `report.completed` 和历史每轮返回可选快照。
旧客户端忽略新增字段即可。回滚业务代码时保留列及快照，不执行 DROP COLUMN；恢复部署前的应用镜像/源码即可。
本机 VM 的发布脚本使用既有镜像依赖层增量构建，保留旧镜像标签和源码压缩备份，不包含 .env。

## 评测与人工确认

新增 `dimension_scoring` 和 `report_grounding`，后者含 dimension/report 阶段。
采集冻结材料、区间、维度、候选人、Run、尝试次数、耗时及版本；回放不重新检索最新材料。
评分 Golden 需通过原评分状态和引用约束，审查 Golden 必须恰好覆盖输入 claim 且不能新增依据。
在现有“评测中心 → 案例详情 → Golden 审核”逐条对照输入确认。候选标签不代表人工标签。
新模块直接对比人工 Golden，不要求配置 Judge；旧模块 Judge 0–4 分保持独立。

| 指标 | 分子 / 分母 | 设计目标 |
| --- | --- | --- |
| 评分资格一致率 | 评分资格和状态均一致的维度 / 已标注维度 | ≥95% |
| 发布支持率 | Golden supported 且发布 / 所有发布 | ≥95% |
| 不支持拦截率 | 未发布的 Golden 非 supported / Golden 非 supported | ≥95% |
| 正确结论误拦率 | 未发布的 Golden supported / Golden supported | ≤10% |
| 引用校验 | 符合冻结身份约束的引用 / 所有提交引用 | 非法引用发布数为 0 |

空分母为 null，附原因，不显示 100%。超时和执行失败保留分母，另列计数；未知/重复 ID 不算完整。
引用冻结校验不替代实时权限回查，实时权限由引用接口测试与完整链路验证。
未经人工确认、执行不完整、协议不兼容、必要分母为空均不能通过正式验收。
这些比例仅代表所选虚构样本，不代表真实招聘准确率。运行命令和实测结果见 `samples/assessment-grounding-v1/README.md`。

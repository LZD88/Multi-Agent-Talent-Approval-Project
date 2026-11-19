# Proposal

## Why

完整链路验收已经验证了 PDF 解析、评估和引用回查可用，但合成样本报告仍出现“未评分”被概括为“0 分”、材料未提及被写成否定事实的偏差。现有校验能证明引用属于候选人且原文存在，尚不能充分约束结论是否被原文支持，需要让评价状态、结论依据和待核实事项形成可评测的闭环。

## What Changes

- 保留已有 `sufficient / partial / missing / conflicting / failed` 证据状态和确定性聚合、排序规则，将事实命题的支持或否定与取证状态分开表达；`missing / conflicting / failed` 继续保持 `score=null`。
- 为事实判断、风险和跨候选人比较建立结构化结论及依据，实施候选人、维度、原文区间、评分锚点和语义支持关系检查；未经校验的自由文字不得直接进入正式报告。
- 由程序生成分数、覆盖率、评分状态和技术失败提示；语义审查不通过、超时或不可用时隐藏对应事实判断并明确提示待核实，不将失败转成低分。
- 根据已有缺失、冲突和被拦截结论生成有来源的待核实清单与面试问题，技术执行失败单独提示重试；本期不建设面试录入与自动回写重评流程。
- 扩展现有模块采集、人工 Golden、回放和评测界面，覆盖维度评分与报告结论审查，输出可重现的状态一致性、结论支持率和拦截指标。
- 为新报告增加带版本的可信评估快照；实时输出、历史报告和前端状态一致，历史旧报告明确显示未经过本版审查。

## Capabilities

### New Capabilities

- `assessment-claim-grounding`：管理评价状态、结论与精确证据的支持关系、评分资格、程序渲染、失败降级及报告历史兼容。
- `assessment-verification-checklist`：把有记录依据的信息缺失和冲突转为中立、可追溯的核实事项和面试问题，并区分技术重试事项。
- `assessment-quality-regression`：在现有评测基础上采集、人工标注、冻结回放并量化评分及报告结论质量。

### Modified Capabilities

无。初始化后的 `openspec/specs/` 尚无主规格；上述增量仅定义本次新增约束，不宣称既有业务能力尚未实现。

## Impact

- 后端：`backend/app/talent_evaluation_report.py`、`talent_evaluation_runtime.py` 及调用报告子图的父图；新增独立的结论校验与核实清单模块，复用 `evidence_pack.py` 和 `evidence_citations.py` 的原文与权限约束。
- 接口与持久化：`talent_run_stream.py`、`talent_run_history.py`、`models.py`、`database/init.sql` 和新增幂等迁移；既有 `report.completed` 事件与历史查询保留 Markdown、引用和 trace 字段，新增可选版本化快照。
- 前端：`TalentChatPage.tsx`、`talentRunState.ts`、`EvaluationCenter.tsx`、`EvaluationReview.tsx` 及相关样式和测试，展示结论依据、评分状态、核实事项及审查降级状态。
- 评测：扩展 `evaluation_case.py`、`evaluation_judge.py`、`evaluation_api.py`，保留现有四类模块和 dev/test 划分，新增评分与结论审查模块及合成回归样本。
- 依赖与成本：优先复用现有 LangChain、Pydantic、模型提供器和 PostgreSQL，增加有上限的语义审查调用与耗时记录；模型审查属于辅助判断，不作为语义正确性的绝对证明。
- 本期范围外：MinerU 或切分策略改造、PDF 标注工作台、增量重算、面试记录写入、任务队列恢复、身份认证体系和自动录用决策。

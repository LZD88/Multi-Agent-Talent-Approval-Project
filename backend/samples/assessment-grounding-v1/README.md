# 虚构可信评估基准 v1

所有候选人、项目及材料专为测试构造，无真实个人信息。候选标签由脚本准备，初始均没有人工确认。
60 个模块案例：40 dev / 20 test；48 个审查案例（192 命题）、12 个评分案例。
留出 test 有 16 个审查案例、64 命题（候选 supported 32、非 supported 32），另有 4 个评分案例。
每个场景有独立项目、候选人、文档版本与 split，同一材料不会跨 split；模板化合成数据的多样性有限。
包含原型/生产、参与/主导、重复计数、时期、等价技术、缺失/否定、明确否定、指标、冲突、指令注入、跨候选人、同块多区间，以及评分零分与未评分。
审查不可用通过离线 `--fault` 和超时/取消测试注入，不伪造成功模型结果。

从 backend 目录运行，设置 `PYTHONPATH=.`。使用项目 Python 环境。

```text
python scripts/generate_assessment_benchmark.py
python scripts/benchmark_assessment.py --output samples/assessment-grounding-v1/results/offline.json
python scripts/benchmark_assessment.py --fault --output samples/assessment-grounding-v1/results/outage.json
python scripts/benchmark_assessment.py --import-pending
python scripts/benchmark_assessment.py --export-confirmed samples/assessment-grounding-v1/confirmed.json
```

导入只创建待审核案例和候选答案，不调用确认接口、不入正式数据集。案例实际输出为空并明确标记未执行。
人工在现有评测中心核验并确认后导出，导出保留 dataset_item_id、确认时间、输入 hash，整体标签另有 hash。
正式使用 `--confirmed confirmed.json`；标签必须匹配冻结输入。没有确认文件时 quality_metrics 保持 incomplete。

```text
python scripts/benchmark_assessment.py --live --split test --baseline --output samples/assessment-grounding-v1/results/live-baseline.json
python scripts/benchmark_assessment.py --live --split test --compare samples/assessment-grounding-v1/results/live-baseline.json --output samples/assessment-grounding-v1/results/live-current.json
```

只有显式 `--live` 才发送所选虚构材料。也可通过重复 `--case-id` 指定案例。
baseline 使用精简审查提示词，current 使用 v1 严格提示词，两者模型和评分器保持相同；这不是原版完整产品的招聘准确率对照。
输出共同样本、不兼容样本、版本、输入 hash、每例耗时、失败类型及差异。
`offline_injected` 注入固定结果，仅验证协议、边界、计数与回放线路，其 100% 不代表语义模型效果。
`provisional_diagnostics` 对照未确认候选标签，只作诊断；正式指标仅来自 `quality_metrics`。
设计目标和公式见 `backend/ASSESSMENT_GROUNDING.md`；只有正式标签齐全、必要指标可计算、无执行失败才可能得到 passed。

# 基准结果的适用范围

全部结果的正式验收均为 incomplete，当前没有人工确认的 Golden。
最终数据版本为 synthetic-assessment-v1.1，hash 以父目录 manifest.json 为准。

| 文件 | 含义 |
| --- | --- |
| offline.json | 最终输入的 60 个固定结果注入，仅验证线路和协议 |
| outage-final.json | 最终输入的审查故障注入，48 审查案例执行失败、12 评分样本为固定输出 |
| outage.json | 较早数据版本的故障注入历史结果 |
| live-baseline-first.json / live-current-first.json | 早期配置的真实模型对照，20 个旧输入，未人工确认、存在审查错误 |
| live-baseline-billing.json | 百炼欠费后重跑，20/20 失败 |
| live-current-billing.json | 早期 current 的保留副本，不代表欠费后成功重跑 |
| live-baseline.json / live-current.json | 工作文件，分别为欠费重跑和旧 current，不能配成最新正常 A/B |
| live-baseline-recovered.json / live-current-recovered.json | 充值恢复后最终输入的真实模型对照；20 个共同 test 案例，最终执行错误为 0，正式标签尚待人工确认 |

旧冲突评分 fixture 缺失反向材料，已修正；旧报告中的 3/4 评分一致不能作为有效模型质量结论。
每批 6 条及独立客户端的配置已在充值后完成上述 recovered 对照；当前配置对照候选标签为 32/32 支持命题保留、32/32 非支持命题拦截、0/32 误拦、4/4 评分资格一致。
这些是待人工确认标签的暂定诊断，正式质量指标仍为空；不能宣称人工 Golden 质量验收通过。
逐文件 hash、输入兼容样本列表和失败计数见仓库 `docs/verification/grounding-20261119/comparison-audit.json`。
详细限制见同目录验收记录。provisional_diagnostics 对照的是脚本候选标签，不是人工 Golden。

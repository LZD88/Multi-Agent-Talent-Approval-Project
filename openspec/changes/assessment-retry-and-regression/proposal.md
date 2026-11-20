# Proposal

## Why

真实评估复验已成功取证，但评分输出校验和结论审查超时使报告降级。虚构回归集已有候选标签，需要人工确认并接入可重复的回放对照。

## What Changes

- 在报告中显示可重试的失败项、候选人、维度及公开错误原因，补跑后保存修订结果。
- 拆小重试未完成的审查命题，复用冻结的取证输入与已通过结果。
- 优化虚构样本的人工确认入口，并在代码变更时自动运行离线回归。支持显式运行真实模型与人工基准对照。

## Capabilities

### New Capabilities

- `assessment-local-retry`：按失败项恢复已完成运行的评分和审查。
- `assessment-regression-automation`：人工确认、冻结标签和自动回归的执行入口。

### Modified Capabilities

无已归档主规格需要修改。

## Impact

涉及报告节点、运行历史数据库、报告和评测页面、回放命令及 GitHub Actions。

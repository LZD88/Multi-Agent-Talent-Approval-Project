# Windows 本地启动说明

> **2026-11-19 当前机器部署说明：以下本节优先于后面的原始课程环境说明。**
>
> 当前仓库位于 `D:\codex\2026-11-03\https-github-com-lzd88-multi-agent\Multi-Agent-Talent-Approval-Project`。
> VM 为 `D:\WorkSpace\CentOS 7.vmx`，VMware 位于 `D:\VMware`，SSH 为 `root@192.168.163.128`。
> 前端、后端、worker、MinerU、PostgreSQL、Redis、MinIO、Milvus、etcd 全部运行在 VM 的 Docker 中。
> VM 仓库为 `/root/talent-eval-agents`，沿用其 `.env` 中已有的百炼配置，无需启动 Windows 本地 PostgreSQL 或后端。

## 当前机器一键启动

在当前仓库目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

脚本检查服务，必要时启动 VM、已有的 9 个容器并建立 SSH 转发；SSH 会交互要求输入已有 root 密码。保持终端打开以维持转发。密码不会保存到文件。服务已正常时直接退出，不会重复建立转发。

只检查健康状态：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1 -CheckOnly
```

- 页面：<http://127.0.0.1:15173/>
- 后端：<http://127.0.0.1:18080/api/health>
- MinerU：<http://127.0.0.1:18001/health>
- MinIO 控制台：<http://127.0.0.1:19001/>，文件端口为 `19000`
- Milvus 健康检查：<http://127.0.0.1:19091/healthz>

`ens33` 已设置 `ONBOOT=yes`，容器重启策略为 `unless-stopped`。Windows 重启后需要重新建立转发。VM IP 改变时向脚本传入 `-VmAddress 新IP`。

## 当前完整链路验收

本机已安装 Python、httpx 和 reportlab。以下命令调用真实模型，会产生模型费用；它使用明确标注的虚构候选人和 PDF，不使用真实个人材料。

```powershell
py backend/scripts/verify_full_pipeline.py --output ../work/e2e-20261119 --tag 20261119 --stage all
```

覆盖上传、MinerU 3.4.4、父子切片、向量索引、稠密/混合检索、自然语言查询、证据包、SSE 评估报告、同会话续问、HITL 修订恢复、原文引用及权限、取消幂等性。相同 output/tag 会复用测试员工、知识库和文档，评估 Run 会重新创建。可使用 `--stage` 单独执行一段。

证据保存在 output 目录：`state.json`、原始 SSE、文档/切片 JSON、报告 Markdown 和逐条引用核对结果。对应页面知识库为“完整链路验收（虚构数据）20261119”。

VM 上目前没有 Docker Compose 可执行命令，启动脚本直接启动已有容器。本次修复已同步到 VM 源码、容器和重新构建的镜像；重建镜像本身不会替换已有容器。前端 API 端口应为 `18080`，旧构建产物中的 `18081` 会导致页面无数据。

后端部分文件解析测试依赖当前仓库未提供的 `sample-data/generated`，应将缺失测试数据与真实链路结果分别记录。Docker 环境中的默认配置测试还需避免 `MILVUS_URI` 环境变量覆盖默认值。

---

以下为原始课程部署参考，路径、IP 和启动方式不适用于上述当前机器。

## 当前环境

- 本地代码目录：`D:\Workspace\PycharmProjects\PythonProject\talent-eval-agents`
- VMware 虚拟机：`D:\VirualMachine\CentOS 7 64\CentOS 7 64 位.vmx`
- 虚拟机 IP：`192.168.200.130`
- 虚拟机 SSH：`root`（使用本机 SSH Key 或在启动时交互输入密码）
- 虚拟机项目目录：`/root/talent-eval-agents`
- Docker Compose 项目名：`talent-eval-agents-course`
- 本地 PostgreSQL：`D:\PostgreSQL\17`
- 本地 PostgreSQL 服务名：`postgresql-x64-17`
- 本地 PostgreSQL 端口：`5432`

## 启动顺序

1. 在 VMware Workstation Pro 中启动 CentOS 7 虚拟机。
2. 确认能 ping 通虚拟机。
3. 启动本地 PostgreSQL。
4. 启动虚拟机里的 Docker 基础设施。
5. 建立 Windows 到虚拟机的 SSH 端口转发。
6. 启动本地后端 API。
7. 启动本地 worker。
8. 启动本地前端。

## 1. 启动虚拟机

打开 VMware Workstation Pro，启动：

```text
D:\VirualMachine\CentOS 7 64\CentOS 7 64 位.vmx
```

确认 SSH 端口是否仍然可用：

```powershell
Test-NetConnection 192.168.200.130 -Port 22
```

确认 `TcpTestSucceeded` 为 `True`。这台虚拟机可能禁用 ICMP，因此 `ping` 失败不代表虚拟机不可用。如果虚拟机 IP 变了，需要把下面的 `192.168.200.130` 全部替换成新 IP。

## 2. 启动本地 PostgreSQL

在 Windows 中确认 PostgreSQL 服务已经启动：

```powershell
Get-Service postgresql-x64-17
```

如果状态不是 `Running`，启动它：

```powershell
Start-Service postgresql-x64-17
```

本地 PostgreSQL 连接信息：

```text
Host: localhost
Port: 5432
Database: talent_docs
User: talent
Password: talent_dev_password
```

命令行工具位于：

```text
D:\PostgreSQL\17\bin\psql.exe
```

首次拉取包含数据库变更的新代码后，在项目根目录执行增量迁移：

```powershell
$env:PGPASSWORD = "talent_dev_password"
$migrations = 20..24 | ForEach-Object { "database/migrations/0${_}_*.sql" }
foreach ($pattern in $migrations) {
  Get-ChildItem $pattern | ForEach-Object {
    & "D:\PostgreSQL\17\bin\psql.exe" `
      -h 127.0.0.1 -p 5432 -U talent -d talent_docs `
      -v ON_ERROR_STOP=1 -f $_.FullName
  }
}
Remove-Item Env:PGPASSWORD
```

这些迁移是幂等的，重复执行会跳过已经存在的表、列或索引。

## 3. 启动 Docker 基础设施

用 FinalShell 或 PowerShell SSH 连接虚拟机：

```powershell
ssh root@192.168.200.130
```

不要在仓库中保存虚拟机密码。建议配置 SSH Key；使用密码登录时请在终端提示后手动输入。

进入虚拟机项目目录并启动基础设施：

```bash
cd /root/talent-eval-agents
docker compose -p talent-eval-agents-course up -d minio redis etcd milvus
```

查看容器状态：

```bash
docker compose -p talent-eval-agents-course ps
```

需要看到以下服务为 `Up` 或 `healthy`：

- minio
- redis
- etcd
- milvus

## 4. 建立 SSH 端口转发

在 Windows 新开一个 PowerShell 窗口，执行：

```powershell
ssh -N `
  -L 19000:127.0.0.1:19000 `
  -L 19001:127.0.0.1:19001 `
  -L 16379:127.0.0.1:16379 `
  -L 19531:127.0.0.1:19531 `
  -L 19091:127.0.0.1:19091 `
  -L 18001:127.0.0.1:18001 `
  root@192.168.200.130
```

这个窗口要保持打开。它负责把虚拟机的 Docker 端口转发到本机。

也可以在 FinalShell 中配置等价的本机端口转发。

## 5. 启动后端 API

新开一个 PowerShell 窗口：

```powershell
cd D:\Workspace\PycharmProjects\PythonProject\talent-eval-agents\backend
uv sync --dev
uv run python run_windows.py --reload
```

新版运行历史使用 PostgreSQL 异步 Checkpointer。Windows 默认的 `ProactorEventLoop` 与 psycopg 异步连接不兼容，必须通过 `run_windows.py` 让 Uvicorn 使用 `SelectorEventLoop`；不要直接运行 `uvicorn app.main:app`。

验证：

```powershell
Invoke-WebRequest http://127.0.0.1:18080/api/health
```

正常应返回：

```json
{"service":"talent-document-api","status":"ok"}
```

接口文档：

```text
http://127.0.0.1:18080/docs
```

## 6. 启动 worker

再开一个 PowerShell 窗口：

```powershell
cd D:\Workspace\PycharmProjects\PythonProject\talent-eval-agents\backend
uv run python -m app.worker
```

正常日志里会出现：

```text
worker_started queues=talent:parse:queue,talent:index:queue
```

## 7. 启动前端

再开一个 PowerShell 窗口：

```powershell
cd D:\Workspace\PycharmProjects\PythonProject\talent-eval-agents\frontend
npm install
npm run dev
```

前端地址：

```text
http://127.0.0.1:15173/
```

## 常用验证地址

- 前端：`http://127.0.0.1:15173/`
- 后端健康检查：`http://127.0.0.1:18080/api/health`
- 后端接口文档：`http://127.0.0.1:18080/docs`
- Milvus 浏览器健康检查：`http://127.0.0.1:19091/healthz`

注意：`http://127.0.0.1:19531/` 是 Milvus SDK 使用的 gRPC 地址，不是浏览器页面，直接访问 404 是正常的。

## 模型能力配置

项目根目录 `.env` 中：

```env
DASHSCOPE_API_KEY=阿里云百炼 API Key
CHAT_API_KEY=
CHAT_BASE_URL=
CHAT_MODEL=qwen-plus
```

默认情况下，人才评估、报告、Embedding 与 Rerank 都使用 `DASHSCOPE_API_KEY`，聊天模型为 `qwen-plus`。只有切换其他 OpenAI 兼容聊天服务时才填写 `CHAT_API_KEY` 和 `CHAT_BASE_URL`。修改后需要重启后端和 worker。

只做员工、知识库、文档上传、解析和切片时，这些 Key 可以暂时为空。

## 停止项目

需要停止时：

1. 在对应 PowerShell 窗口按 `Ctrl+C` 停止前端、worker、后端。
2. 关闭 SSH 端口转发窗口。
3. 如需停止 Docker 基础设施，进入虚拟机项目目录后执行：

```bash
cd /root/talent-eval-agents
docker compose -p talent-eval-agents-course down
```

建议开发时不要频繁执行 `down`，通常停止本地前后端和 worker 即可，Docker 基础设施可以保持运行。

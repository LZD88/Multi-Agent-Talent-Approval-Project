# Windows 本地启动说明

## 当前环境

- 本地代码目录：`D:\Workspace\PycharmProjects\PythonProject\talent-eval-agents`
- VMware 虚拟机：`D:\VirualMachine\CentOS 7 64\CentOS 7 64 位.vmx`
- 虚拟机 IP：`192.168.200.130`
- 虚拟机 SSH：`root` / `123456`
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

密码：

```text
123456
```

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
CHAT_API_KEY=DeepSeek 或其他 OpenAI 兼容聊天模型的 API Key
CHAT_BASE_URL=https://api.deepseek.com
CHAT_MODEL=deepseek-chat
DASHSCOPE_API_KEY=用于 Embedding 与 Rerank 的 DashScope API Key
```

人才评估和报告使用 `CHAT_*` 配置；向量索引、人才检索的 Embedding 与 Rerank 继续使用 `DASHSCOPE_API_KEY`。修改后需要重启后端和 worker。

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

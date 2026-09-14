# digitalManCreateHub · 数字人创建与编排平台

基于 Palantir 本体思想的数字人平台：**六元组数字人**（identity / ontology /
actions / guardians / interface / reflection）+ **pipeline 编排**（节点/关系 DAG）+
**RAG 知识库**（PG + pgvector，分段摘要 + 多标签聚合）。
三条体系铁律：**绝对准确、不知道就说不知道、LLM 无终审权**（提名-裁决分离，
用户是唯一终审点）。

## 架构总览

| 层 | 技术 | 说明 |
|---|---|---|
| 后端 | FastAPI + uvicorn（`backend/app/`） | `--reload` 热重载；serve 前端 dist |
| 前端 | React 18 + Vite + TypeScript（`client/`） | 流程图画布可拖动/缩放/撤销重做 |
| 数据库 | PostgreSQL 17 + pgvector（容器 `rag_pg` :5432） | schema 由 `app/db.py` `_init_schema` 自动迁移 |
| LLM | 双通道：generator `deepseek-flash` + judge `deepseek-v4-pro` | 配置存 `backend/data/settings.json`（Settings 页可改） |
| Embedding | Ollama bge-m3（宿主 :11434） | 不可用时自动降级 hash embedding |
| 沙箱 | 一次性 python 容器（能力题判分） | `CAPABILITY_SANDBOX_IMAGE` |

## 启动方式（三选一）

### 方式 A：Windows 原生（开发常用）

```powershell
cd rag_prototype
.\start.ps1          # http://localhost:8000
.\stop.ps1           # 停止
```

`start.ps1` 自动完成：停旧进程 → 建 venv + 装依赖 → 前端 dist 过期则构建 →
探测 Ollama（宿主优先，WSL 回退）→ 起 uvicorn :8000。
Ollama/WSL 问题**从不阻塞启动**（hash embedding 降级）。

### 方式 B：Windows 全 Docker

```powershell
.\start1.ps1                    # dev（默认）
.\start1.ps1 prod               # 生产 override
.\start1.ps1 -Rebuild           # 改代码后重建镜像
```

自动探测/安装 Docker Desktop → compose up（dev/prod override）→ 等 Ollama →
等后端应答。镜像默认复用（重启秒级）。

### 方式 C：WSL 原生 dockerd（**当前主力部署**）

```bash
# WSL (Ubuntu-22.04) 内，仓库位于 ~/rag_prototype
bash ops/wsl_up.sh               # 启动（复用镜像）
bash ops/wsl_up.sh --rebuild     # 改代码后重建
```

自动起 dockerd → `docker compose -f docker-compose.yml -f docker-compose.dev.yml
-f docker-compose.wsl.yml up -d` → 等后端应答。**Docker Desktop 必须保持关闭**
（其 WSL 集成会让 dockerd 反复重启）。

WSL 专属脚本：

| 脚本 | 用途 |
|---|---|
| `ops/wsl_boot.sh` | 首次部署：容器内构建前端 dist + 后端镜像 + up + 等就绪 |
| `ops/wsl_import_db.sh` | 把 Windows 侧导出的 `_pgdump.sql` 导入 rag_pg |
| `ops/wsl_verify.sh` | 部署验证：登录 + identities + 关键接口探活 |
| `ops/ollama.service` | WSL 侧 ollama **必须停用**（宿主已占 :11434，mirrored 网络下 WSL 侧 bind 失败会拖垮整个 init） |

Linux 裸机等价物：`./start.sh [dev|prod] [--rebuild]`。

## 访问与登录

- 地址：**http://localhost:8000**（WSL mirrored 网络 = Windows localhost 直达）
- 默认账号：`admin` / `123456`
- 前端开发模式：`cd client && npm run dev` → http://localhost:5173（代理 /api 与 /ws 到 :8000）

## LLM 配置

浏览器打开 Settings 页直接改（写入 `backend/data/settings.json`）：

```json
{
  "llm":  { "base_url": "https://api.deepseek.com", "model": "deepseek-flash",  "api_key": "sk-..." },
  "llm2": { "base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro", "api_key": "sk-..." }
}
```

双通道分工：`llm` = generator（提名/高频/长链路），`llm2` = judge（归纳/核验）。
`llm2` 不可用时自动降级到 `llm` 并打日志（`LLM_FALLBACK=off` 可关）——
**降级可用于链路不中断，不可用于结论可信**（验收场景宁可不打分）。

## 开发工作流

### 前端

```powershell
cd client
npm run build        # tsc -b && vite build → dist/
```

- **src/dist 同步铁律**：改完 `client/src` 必须重新 build，否则页面还是旧的。
- WSL 部署需把 dist 同步过去：`sudo rsync -aI --delete client/dist/ ~/rag_prototype/client/dist/`
- ⚠️ 中文路径下 npm 可能报 ENOENT（GBK 乱码）——把 repo 复制到纯 ASCII 路径再构建。

### 后端

- `backend/app/` 挂载进容器，`--reload` 监听：**有 job 在跑时禁止写 backend 目录**
  （任何写入都会重启进程并杀掉正在跑的 job）。只读诊断用
  `docker cp 脚本 rag_backend:/tmp/x.py && docker exec ... python /tmp/x.py`。
- 新增表列写在 `db.py` `_init_schema` 迁移块（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`），
  **绝不**把新列上的 `CREATE INDEX` 写进 `_SCHEMA_SQL`（老库会启动失败）。

### 常用种子脚本（容器内）

```bash
docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
  python seed_dfmea_identities.py      # DFMEA 工程师/专家/复核员（幂等，走模板）
docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
  python scripts/seed_pipeline_factory.py   # pipeline-factory 元流程（幂等）
docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
  python scripts/migrate_pipeline_versions.py  # pipeline 版本族归档（幂等）
```

### 验证套件（容器内，全部只读或自清理）

```bash
docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
  python scripts/verify_fmea_table_parse.py    # 106 断言
docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
  python scripts/verify_pipeline_fmea.py       # 引擎侧
docker exec -w /app/backend -e PYTHONPATH=/app/backend rag_backend \
  python scripts/verify_persona_templates.py   # 模板往返一致性
```

## 项目结构

```
rag_prototype/
├── start.ps1 / stop.ps1        # Windows 原生一键启停
├── start1.ps1                  # Windows 全 Docker
├── start.sh                    # Linux 全 Docker
├── ops/                        # WSL 专属（wsl_up / wsl_boot / wsl_import_db / wsl_verify）
├── docker-compose*.yml         # base + dev/prod/wsl override
├── .env                        # CN 镜像前缀 + LLM 出口代理
├── backend/
│   ├── app/                    # FastAPI 应用
│   │   ├── main.py             # 路由与 API
│   │   ├── db.py               # PG 连接层 + schema 迁移
│   │   ├── pipeline.py         # 编排引擎（nominate/deterministic/review 门/ask 边）
│   │   ├── pipeline_factory.py # §18 元流程（m2 盘点/m6 编译考卷/m7 考试迭代）
│   │   ├── persona_templates.py# §16 数字人模板（槽位+蓝图，零 LLM 渲染）
│   │   ├── fmea.py             # §15 DFMEA 领域逻辑（来源闭集/AP 以表为准）
│   │   ├── chat.py             # 对话 + tool-use loop
│   │   ├── trainer.py          # Pipeline 训练师（auto_iterate 闭环）
│   │   └── capability.py       # 能力题沙箱（隐藏 assert 判分）
│   ├── scripts/                # verify_* 验证套件 + seed/migrate 脚本
│   └── data/                   # settings.json / capability_tasks.json
├── client/
│   ├── src/pages/              # PipelinePage（可拖动画布）/ IdentityWorkbench / ChatPage 等
│   └── dist/                   # 构建产物（被 FastAPI serve）
└── docs/
    ├── design.md               # 设计单一事实源（§1~§18，变更先改这里）
    ├── requirement.md          # R 编号需求（逐条标 design § + 状态）
    └── test-metrics.md         # 指标族 T-A~T-N
```

## 文档三件套（必须联动）

**动设计/架构/实体/指标必须先改 `docs/design.md`**，`requirement.md` 与
`test-metrics.md` 同步；提交信息索引 design 章节号。新设计从 §19 起追加
（§14 是变更流程约定，已用到 §18）。

核心章节速查：§15 DFMEA 编排链路 · §16 数字人模板 · §17 LLM 出网路径 ·
§18 pipeline 创建元流程（pipeline-factory）。

## 环境铁律（踩坑沉淀）

- `start.ps1` / `requirements.txt` **必须纯 ASCII**（PowerShell 5.1 按 GBK 读无
  BOM 文件，中文注释崩一键启动）。
- WSL 侧 `ollama.service` **必须停用**（`systemctl stop ollama; systemctl disable ollama`）。
- Docker Desktop 启动前确保 `https_proxy` 与 `HTTPS_PROXY` **二者唯一**
  （大小写重复键会让它崩溃）。
- `curl http://127.0.0.1:8000/...` 必须加 `--noproxy "*"`（否则走系统代理返回 502 假故障）。
- rsync Windows→WSL 必须 `sudo` + `-I`（目标属主是 root；drvfs mtime 偏差会静默跳过）。
- git 提交在 **Windows 侧仓库**做（WSL 侧只 rsync 接收、从不提交）。

## 旧版 CLI（已被平台取代，保留作参考）

最早的 RAG 提取管线 CLI（`main.py ingest/query`、本地内存模式）仍可用，
见 `README_MVP.md`。当前平台已远超该阶段。

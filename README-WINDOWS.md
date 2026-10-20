# Alpha 选股 — Windows 本地运行说明

本文件说明如何在 **Windows（PowerShell 5.1+ / cmd）** 上把本仓库跑起来。
仓库自带的 `start.sh / stop.sh / restart.sh` 是 macOS/Linux 专用（依赖 `bash`、`ps -o stat`、`lsof`，
并强制 arm64 Python），Windows 下请使用本目录新增的等价脚本：

| 脚本 | 作用 | 等价 Linux 脚本 |
|------|------|-----------------|
| `start.ps1` | 后台启动 uvicorn（默认端口 18890） | `./start.sh` |
| `stop.ps1` | SQLite WAL checkpoint → 停止服务 | `./stop.sh` |
| `restart.ps1` | checkpoint → 停止 → 启动 | `./restart.sh` |

---

## 1. 环境要求

- Windows 10/11，PowerShell 5.1 及以上（系统自带即可）
- Python 3.11+（本次使用 3.12.14）
- Git（已安装）
- 联网：本机已配置系统代理 `http://127.0.0.1:10809`（v2rayN）。git / pip / 行情接口都会用到它

## 2. 一次性安装

```powershell
cd G:\Alpha选股

# 2.1 创建虚拟环境
python -m venv .venv

# 2.2 安装依赖（基础 + 训练后端 + MCP）
.\.venv\Scripts\python.exe -m pip install -r requirements-base.txt -r requirements-training.txt -r requirements-mcp.txt

# 2.3 可选：Kronos 预测依赖（torch CPU，体积较大）
.\.venv\Scripts\python.exe -m pip install -r requirements-kronos.txt
```

如果 pip 报网络错误，先设置代理再执行：

```powershell
$env:HTTPS_PROXY='http://127.0.0.1:10809'
$env:HTTP_PROXY='http://127.0.0.1:10809'
```

## 3. 配置文件

`.env` 已按 `.env.example` 生成，默认值：

```
PORT=18890
HOST=127.0.0.1
RELOAD=0
ENABLE_TUSHARE=false        # 不填 Token 也可用东财/新浪免费源
ENABLE_LIVE_MARKET_DATA=true
# LLM 三项默认注释掉（未配置 LLM 时公告打分/Hermes 面板会提示未启用，主功能不受影响）
# OPENAI_API_KEY=sk-xxxxxxxx
# OPENAI_BASE_URL=https://api.deepseek.com/v1
# HERMES_MODEL=deepseek-chat
```

### 3.1 配置 LLM（可选，非 Ollama 也可）

Alpha 的 LLM 全部走 **OpenAI 兼容协议**，所以任何兼容服务都能接：DeepSeek、通义千问（DashScope 兼容模式）、智谱 GLM、SiliconFlow、OpenAI 官方、LM Studio、llama.cpp server、vLLM。

一键配置（写入 `.env` + 重启 + 连通性自检）：

```powershell
cd G:\Alpha选股

.\set-llm.ps1 -Provider deepseek    -ApiKey sk-xxxx -Restart -Test
.\set-llm.ps1 -Provider qwen        -ApiKey sk-xxxx -Model qwen-plus -Restart -Test
.\set-llm.ps1 -Provider glm         -ApiKey xxxxxxxx -Model glm-4-flash -Restart -Test
.\set-llm.ps1 -Provider siliconflow -ApiKey sk-xxxx -Restart -Test
.\set-llm.ps1 -Provider openai      -ApiKey sk-xxxx -Restart -Test
.\set-llm.ps1 -Provider lmstudio    -Model <本地模型名> -Restart -Test
.\set-llm.ps1 -Provider custom      -BaseUrl http://127.0.0.1:8000/v1 -ApiKey local -Model my-model -Restart -Test
```

只做连通性自检（不改配置）：

```powershell
.\.venv\Scripts\python.exe scripts\test_llm.py
```

> 兼容说明：服务优先调用 `POST {base}/chat/completions`，若端点不支持（404/405）自动回退 `POST {base}/responses`；部分不认 `response_format` 的服务会自动去掉该参数重试。模型返回带 ```json 包裹或前后缀说明文字也能正确解析。

- 需要飞书通知时：填 `FEISHU_WEBHOOK_URL`
- `.env` 已被 `.gitignore` 忽略，不要把真实 Token 提交到 Git

## 4. 启动 / 停止 / 重启

```powershell
cd G:\Alpha选股

.\start.ps1                 # 后台启动，日志 logs\server-18890.log
.\start.ps1 -Foreground     # 前台运行（调试，Ctrl+C 停止）
.\start.ps1 -Port 18891     # 换端口
.\start.ps1 -Reload         # 开发热重载

.\stop.ps1                  # 先 WAL checkpoint 再停进程
.\restart.ps1               # 重启
```

首次启动会拉取全市场行情快照做预热，**约需 40–60 秒**端口才进入 LISTEN 状态，属正常现象。

## 5. 访问

- Web UI：<http://127.0.0.1:18890/>
- 公告页：<http://127.0.0.1:18890/notice>
- 接口自检：`/api/funnel`、`/api/market/hot-stocks`、`/api/strategy/rules`、`/api/predict/kronos/status`

首次使用建议打开「数据中心」Tab，点击**一键初始化**填充 K 线缓存（默认 120 天），
未初始化前「策略选股」「预测选股」页会显示引导横幅。

## 6. 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| `ParserError` / `Unexpected token` | 不要用无 BOM 的 UTF-8 中文脚本；本目录 `*.ps1` 正文均为纯 ASCII |
| `Cannot overwrite variable Host` | 已规避：参数名为 `-BindHost` |
| 启动失败，日志为空 | 端口被占用：换端口或先释放；脚本会直接提示占用 PID |
| 行情接口超时 | 代理未生效：`$env:HTTPS_PROXY='http://127.0.0.1:10809'` 后重试 |
| `ModuleNotFoundError` | 依赖未装全：重跑第 2 步，或用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` |
| 想要完整功能（含 Kronos） | 安装 `requirements-kronos.txt`；模型权重在首次预测时自动下载 |

## 7. 目录说明（Windows 相关新增）

```
Alpha选股/
├── .env                  # 本地环境变量（新增，已被 .gitignore 忽略）
├── .venv/                # Python 虚拟环境（新增，已加入 .git/info/exclude）
├── start.ps1             # Windows 启动脚本（新增）
├── stop.ps1              # Windows 停止脚本（新增）
├── restart.ps1           # Windows 重启脚本（新增）
├── README-WINDOWS.md     # 本说明（新增）
├── .run/                 # PID 与启动批处理（运行时生成）
├── logs/                 # 服务日志 server-<port>.log（运行时生成）
└── data/                 # SQLite 数据库与缓存（运行时生成）
```

---

## 8. 数据源降级（东方财富 403）

部分出口 IP（机房、被限速的代理出口）会被东方财富整段 `/api/qt/*` 接口拒绝，返回 **403 Forbidden**。
本项目已内置自动降级：

- 东财 K 线连续失败 3 次 → 熔断 600 秒，期间直接走备用源；
- 备用源为**腾讯日 K**（`ifzq.gtimg.cn` → `web.ifzq.gtimg.cn` → `proxy.finance.qq.com` 三域名轮换），
  全局限速 5 次/秒，单域名连续失败 2 次冷却 300 秒，规避腾讯 501 反爬；
- 腾讯不返回成交额，按 `收盘 × 成交量(手) × 100` 估算；成交量统一折算为「手」（科创板 688/689 上游返回「股」）；
- 落库时 `source=tencent`、`fallback_reason` 记录降级原因，可在「数据中心」按来源回溯。

手动验证当前实际使用的源：

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/market_kline.db');print(c.execute('select source,count(*) from kline_daily group by source').fetchall())"
```

## 9. 本机验证结果（2026-10-20）

| 项目 | 结果 |
|------|------|
| 服务地址 | <http://127.0.0.1:18890/>（`/` 返回 200，标题 Alpha，页面 JS 报错数 0） |
| K 线一键初始化 | `5022/5022` 成功，`0` 失败，耗时约 17 分钟，进度 100% |
| K 线缓存 | `data/market_kline.db` 共 967,146 行 / 5,026 只 / 覆盖 2025-11-06 ~ 2026-10-20 |
| 接口自检 | `/api/funnel`、`/api/market/hot-stocks`（5024 条）、`/api/strategy/rules`、`/api/predict/kronos/status` 均 200 |
| 单元测试 | `pytest tests/test_market_data_client.py tests/test_kline_cache_service.py tests/test_tushare_market_data_client.py -q` → 10 passed |
| 漏斗三池为空 | 属正常：本地 `funnel_state.db` 为全新空库，候选池需要盘中扫描 / 上游种子数据才会填充 |


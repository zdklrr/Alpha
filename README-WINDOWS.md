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
OPENAI_API_KEY=ollama       # 需要 LLM 打分/Hermes 时再启 Ollama，否则可留空
OPENAI_BASE_URL=http://127.0.0.1:11434/v1
HERMES_MODEL=qwen2.5:7b
```

- 需要 LLM 功能时：安装 Ollama → `ollama serve` → `ollama pull qwen2.5:7b`
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

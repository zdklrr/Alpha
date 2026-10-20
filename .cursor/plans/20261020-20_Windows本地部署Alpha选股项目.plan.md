# Windows 本地部署 Alpha 选股项目（实施计划）

- 日期：2026-10-20 20:2x（Asia/Shanghai）
- 目标目录：`G:\Alpha选股`
- 上游仓库：https://github.com/Shadowell/Alpha

## 背景 / 目标

上游仓库提供的是 macOS/Linux 形态的部署资产（`start.sh` / `stop.sh` / `restart.sh`，
依赖 `bash`、`ps -o stat`、`lsof`，且强制要求 arm64 Python）。当前运行环境是 Windows 10/11
（PowerShell 5.1 + cmd，代码页 936），工作区路径含中文 `G:\Alpha选股`，且 git/PyPI 需要走本机
代理 `http://127.0.0.1:10809`（系统代理已在注册表中配置，git 未继承）。

目标：把项目完整克隆到本工作区，配置好 Python 虚拟环境与依赖，提供 Windows 原生的启动/停止/
重启脚本，使服务能在 `http://127.0.0.1:18890` 正常运行，Web UI 与 API 均可访问。

## 任务分解（checklist）

- [x] 通过本机代理克隆仓库到工作区根目录 `git -c http.proxy=http://127.0.0.1:10809 clone ...`
- [x] 用 DSH 内置 Python 3.12.14 创建 `.venv` 虚拟环境
- [x] 安装 `requirements-base.txt` + `requirements-training.txt`（lightgbm）+ `requirements-mcp.txt`
- [x] 安装可选 `requirements-kronos.txt`（torch CPU + einops + huggingface_hub + safetensors）
- [x] 依据 `.env.example` 生成本地 `.env`（`PORT=18890`、`HOST=127.0.0.1`、`ENABLE_TUSHARE=false`）
- [x] 新增 Windows 脚本 `start.ps1` / `stop.ps1` / `restart.ps1`（ASCII 正文，规避 PS 5.1 编码坑）
- [x] `start.ps1` 后台拉起 uvicorn，写 `logs/server-<port>.log` 与 `.run/funnel-<port>.pid`
- [x] `stop.ps1` 严格按 AGENTS.md 顺序：先 SQLite WAL checkpoint，再停进程
- [x] 冒烟验证：`/`（Web UI）、`/api/funnel`、`/api/market/hot-stocks`、`/api/strategy/rules`、
      `/api/predict/kronos/status` 均 200
- [x] 编写 `README-WINDOWS.md` 本地运行说明
- [ ] 同步上游 `README.md` 增加 Windows 小节（结构变更说明）

## 验收标准

1. `.\start.ps1` 退出码 0，日志出现 “Started OK”，端口 18890 处于 LISTEN。
2. `http://127.0.0.1:18890/` 返回 200 且页面标题为 `Alpha`。
3. `.\stop.ps1` 后端口释放、SQLite checkpoint 输出 `checkpoint OK`。
4. `.\restart.ps1` 可完整跑通停止→启动闭环。
5. 无 Token / 无 Ollama 时核心链路仍可用（东财/新浪免费行情源）。

## 风险与回滚

- **编码坑**：Windows PowerShell 5.1 读取无 BOM 的 UTF-8 脚本会误解码并报 `ParserError`。
  规避：`.ps1` 正文一律使用纯 ASCII（中文说明放 `README-WINDOWS.md`）。
- **`$Host` 是只读自动变量**：脚本参数改名为 `-BindHost`。
- **中文路径**：批处理正文只写 ASCII，靠 `%~dp0` 展开，cmd 不需要编码中文目录名。
- **代理**：pip/git 需显式设置 `HTTPS_PROXY`/`http.proxy`，行情接口同样需要代理。
- **回滚**：所有新增文件均为本地新增（`.env`、`*.ps1`、`README-WINDOWS.md`、`.venv/`），
  删除即可恢复；`.venv/` 与 `.run/`、`logs/` 已写入 `.git/info/exclude`，不会污染仓库。

## 关键决策记录

1. **不修改上游 `start.sh` 等 Linux 脚本**，而是新增等价的 Windows 脚本，避免破坏上游可维护性。
2. **不 push 到上游 `origin/main`**：仓库属于第三方账号，本次仅做本地可运行化交付。
3. **torch 装 CPU 版**：Windows PyPI 默认即 CPU wheel，权重首次预测时按项目逻辑懒下载。
4. **端口沿用 18890**（与 `start.sh` 默认一致），`.env` 中显式写入，避免与 Docker 的 18888 混淆。

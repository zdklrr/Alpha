# Alpha 部署到 NAS（Docker）

本文档对应仓库里的 `docker-compose.nas.yml` / `.env.nas.template` / `deploy-nas.sh`。
宿主机端口 **923**（即 0923），容器内 18888。

## 本次实际部署记录（2026-10-20，已上线）

| 项 | 值 |
| --- | --- |
| NAS | 群晖 DSM，192.168.31.94（x86_64，Docker 24.0.2 + compose v2.20.1） |
| 部署目录 | `/volume1/docker/Alpha` |
| 容器 | `alpha`（镜像 `alpha:zdklrr`，`restart: unless-stopped`，健康检查通过） |
| 访问地址 | <http://192.168.31.94:923/>（局域网内） |
| 命名卷 | `alpha_data`（SQLite 库）、`alpha_hf_cache`（Kronos 权重） |
| 行情数据 | 已从本机迁移 967,146 行日线（`kline_daily`），无需重新初始化 |
| 写令牌 | 存放在 `/volume1/docker/Alpha/.env` 的 `ALPHA_WRITE_TOKEN` |

验证结果（部署当时）：

- `GET /` → 200；`/api/market/hot-stocks` → 200（5024 行实时行情，NAS 出口直连行情源正常）
- `/api/funnel`、`/api/strategy/rules`、`/api/predict/kronos/status` → 200
- 写接口鉴权生效：不带 `X-Alpha-Token` → 403，带正确 token → 200
- `docker restart alpha` 后自动恢复，K 线行数仍为 967,146

> **compose 文件名**：仓库自带 `docker-compose.nas.yml`；为了让群晖 Container Manager 的
> 「项目」功能能直接识别，部署时把它复制成了 `docker-compose.yml`（原仓库的
> `docker-compose.yml` 保留为 `docker-compose.upstream.yml`）。
> `deploy-nas.sh` 会自动按「有 `docker-compose.yml` 就用它」的规则选择。
> 容器是用命令行创建的，DSM 界面里请在「容器」页管理；若要改成 UI「项目」方式，
> 先 `docker compose down` 再新建项目，避免 `container_name: alpha` 冲突。

> **日志目录**：`./logs` 是 bind mount，宿主机上必须先存在，`deploy-nas.sh` 已自动 `mkdir -p logs`。


## 0. 前置

- NAS 已装 Docker（群晖 Container Manager / 威联通 Container Station / 斐乐纳 Docker 应用均可）。
- NAS 能访问 PyPI 与 `download.pytorch.org`（构建镜像要装 torch CPU）。
  - 国内网络慢的话，可以在 Dockerfile 里加 pip 镜像源，或给 daemon 配 registry mirror。
- 端口 923 未被占用。

## 1. 准备代码

**方式 A：NAS 上直接 clone（推荐）**

```bash
cd <你的 docker 目录>/Alpha
git clone https://github.com/zdklrr/Alpha.git .
```

**方式 B：从本机上传（无外网时）**

在 Windows 本机：

```powershell
cd G:\Alpha选股
# 只传运行必需的部分，排除 .venv / data / logs
tar -czf alpha-nas.tar.gz --exclude=.venv --exclude=.git --exclude=data --exclude=logs --exclude=__pycache__ .
scp alpha-nas.tar.gz user@<NAS_IP>:/tmp/
```

在 NAS 上：

```bash
cd <你的 docker 目录>/Alpha
tar -xzf /tmp/alpha-nas.tar.gz
```

> `Dockerfile` 只 `COPY app/ strategy/`，所以这两个目录必须存在；
> 完整上传最省事。

## 2. 配置 .env

```bash
cd <你的 docker 目录>/Alpha
cp .env.nas.template .env
# 生成一个写操作令牌并填进去
python3 -c "import secrets;print(secrets.token_hex(16))"
vi .env        # 把 ALPHA_WRITE_TOKEN= 改成上面那串
```

必改项：

| 键 | 说明 |
| --- | --- |
| `ALPHA_WRITE_TOKEN` | **必填**。端口对局域网开放，写接口靠它鉴权；不改等于任何人可写 |
| `ENABLE_TUSHARE` / `TUSHARE_TOKEN` | 可选。填 token 会有更稳的日线与交易日历兜底 |
| `OPENAI_*` / `HERMES_MODEL` | 可选。任意 OpenAI 兼容服务（DeepSeek / 通义 / 智谱 / LM Studio…） |
| `HTTP(S)_PROXY` | 可选。NAS 出口需要代理抓行情时填 |

## 3. 构建并启动

```bash
cd <你的 docker 目录>/Alpha
chmod +x deploy-nas.sh
./deploy-nas.sh
```

或手动：

```bash
docker compose -f docker-compose.nas.yml up -d --build
docker compose -f docker-compose.nas.yml logs -f --tail 100
```

启动后访问 `http://<NAS内网IP>:923/`。

## 4. 首次初始化（K 线全量同步）

首次启动服务本身约 40–90 秒就绪，但**还没有历史 K 线**，需要手动触发一次全市场同步：

- 页面 → 「数据中心」→ 一键初始化（默认 120 天）
- 或直接调接口：

```bash
curl -X POST "http://127.0.0.1:923/api/jobs/kline-cache/initialize?window_days=120" \
     -H "X-Alpha-Token: <你的 ALPHA_WRITE_TOKEN>"
```

> 请求头名是 **`X-Alpha-Token`**（不是 `X-Alpha-Write-Token`），见 `app/main.py` 的
> `access_guard` 中间件。

耗时参考：本机实测 5022 只标的约 **17 分钟**（腾讯源限速 5 req/s，零失败）。
若 NAS 出口被行情源限流/403，失败率会升高；查看日志 `docker compose logs -f | grep -i "failed"`。

### 迁移现成数据（省 17 分钟）

本机已有 967,146 行 K 线（`data/market_kline.db`，218MB）。要迁移：

```powershell
# Windows 本机（先把 WAL checkpoint 掉，保证 .db 本身是完整的）
cd G:\Alpha选股
.venv\Scripts\python.exe -c "import sqlite3;c=sqlite3.connect('data/market_kline.db');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()"
scp data\market_kline.db user@<NAS_IP>:/volume1/docker/Alpha/import_market_kline.db
```

```bash
# NAS 上：停容器 → 清掉旧库 → 拷进命名卷 → 再启动
cd <你的 docker 目录>/Alpha
docker compose -f docker-compose.yml stop
docker exec alpha rm -f /app/data/market_kline.db /app/data/market_kline.db-wal /app/data/market_kline.db-shm
docker cp import_market_kline.db alpha:/app/data/market_kline.db
docker compose -f docker-compose.yml start
rm -f import_market_kline.db   # 拷完删掉，省 218MB
```

> ⚠️ 覆盖前建议先 `docker compose cp alpha:/app/data/market_kline.db ./backup.db` 备份。
> 覆盖必须在容器**停止**状态下做，运行中替换文件会破坏 SQLite 的 WAL 一致性。

## 5. 日常运维

```bash
docker compose -f docker-compose.nas.yml logs -f --tail 100   # 看日志
docker compose -f docker-compose.nas.yml restart              # 重启（改 .env 后）
docker compose -f docker-compose.nas.yml down                 # 停止
docker compose -f docker-compose.nas.yml up -d                # 启动（不重新构建）
```

停止前建议做一次 WAL checkpoint（避免掉电时 SQLite 卡住）：

```bash
docker compose -f docker-compose.nas.yml exec -T alpha python3 - <<'PY'
import sqlite3
for db in ["/app/data/funnel_state.db", "/app/data/market_kline.db"]:
    try:
        conn = sqlite3.connect(db, timeout=5)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        print("checkpoint OK:", db)
    except Exception as e:
        print("checkpoint skip:", db, e)
PY
```

## 6. 架构说明

- 镜像基于 `python:3.12-slim`（多架构），torch 走 CPU 轮子（`download.pytorch.org/whl/cpu`），
  x86_64 与 aarch64 都有。
- 第一次 `build` 会下载 torch CPU 轮子（约 200MB）+ 其余依赖，**镜像约 1.5–2GB**，网速慢时耐心等。
- 内存建议 ≥2GB（CPU 推理 + 行情预热）。
- 若你的 NAS 是 ARM64 且 torch CPU 轮子拉取失败，改为在 `Dockerfile` 里用 `pip install torch`（PyPI 的 aarch64 轮子可用但体积更大），或直接预构建镜像再 `docker save/load` 过去。

## 7. 安全提醒

- 本版本 API **没有登录鉴权**，只读接口对外开放。端口 923 暴露在局域网是常见用法；
  若要暴露到公网，务必在前面加反代 + Basic Auth/Authelia，或只在内网用。
- 写操作（手动触发同步、写策略等）受 `ALPHA_WRITE_TOKEN` 保护，请保留该机制。
- `.env` 含 token，**不要提交到 git**（仓库已 `.gitignore`）。

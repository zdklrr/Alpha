#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# deploy-nas.sh —— 在 NAS 的 Alpha 文件夹里一键构建并启动
#
#   cd <你的 Alpha 文件夹>      # 需要放：Dockerfile / docker-compose.nas.yml / .env / app/ strategy/
#   chmod +x deploy-nas.sh
#   ./deploy-nas.sh
#
# 行为：构建镜像 → 后台启动 → 等待健康检查通过 → 打印访问地址
# ─────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")"

# 群晖的 docker 在 /usr/local/bin，sudo 下默认 PATH 可能不含它
export PATH="$PATH:/usr/local/bin:/var/packages/ContainerManager/target/usr/bin:/usr/local/sbin:/usr/sbin"

HOST_PORT="${HOST_PORT:-923}"
CONTAINER_PORT="${CONTAINER_PORT:-18888}"
# 兼容两种命名：仓库里的 docker-compose.nas.yml，以及群晖 Container Manager
# 「项目」功能要求的 docker-compose.yml（放同一目录即自动识别）。
if [ -f "docker-compose.yml" ]; then
  COMPOSE_FILE="docker-compose.yml"
else
  COMPOSE_FILE="docker-compose.nas.yml"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[deploy] ERROR: 找不到 docker 命令"
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "[deploy] ERROR: 没有 docker compose（v2 插件或 docker-compose 均可）"
  exit 1
fi

if [ ! -f .env ]; then
  echo "[deploy] .env 不存在，尝试从 .env.nas.template 复制"
  cp .env.nas.template .env
  echo "[deploy] 已生成 .env，请编辑 ALPHA_WRITE_TOKEN 后重新运行本脚本"
  exit 1
fi

if grep -q '^ALPHA_WRITE_TOKEN=please-change-me' .env; then
  echo "[deploy] WARNING: ALPHA_WRITE_TOKEN 仍是占位值，局域网内任何人都能写。"
  echo "[deploy]          请编辑 .env 换成随机串后重启：$DC -f $COMPOSE_FILE up -d"
fi

echo "[deploy] 构建镜像 ..."
$DC -f "$COMPOSE_FILE" build

echo "[deploy] 启动容器 ..."
# ./logs 是 bind mount，宿主机上必须先存在，否则容器起不来
mkdir -p logs
$DC -f "$COMPOSE_FILE" up -d

echo "[deploy] 等待服务就绪（首次启动含行情预热，约 40-90 秒）..."
ready=0
for i in $(seq 1 60); do
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "http://127.0.0.1:${HOST_PORT}/" >/dev/null 2>&1 && { ready=1; break; }
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null "http://127.0.0.1:${HOST_PORT}/" >/dev/null 2>&1 && { ready=1; break; }
  else
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' alpha 2>/dev/null || echo "")
    [ "$status" = "healthy" ] && { ready=1; break; }
  fi
  sleep 5
done

if [ "$ready" = "1" ]; then
  echo "[deploy] OK —— 访问地址: http://<NAS内网IP>:${HOST_PORT}/"
  echo "[deploy] 常用命令:"
  echo "         $DC -f $COMPOSE_FILE logs -f --tail 100"
  echo "         $DC -f $COMPOSE_FILE restart"
  echo "         $DC -f $COMPOSE_FILE down"
else
  echo "[deploy] 等待超时，最近 60 行日志："
  $DC -f "$COMPOSE_FILE" logs --tail 60
  exit 1
fi

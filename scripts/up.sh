#!/usr/bin/env bash
# 在本机启动：PostgreSQL、Python 接口（8000）、网页（3000）。
# 不启动仓库里旧的 Nest 接口（apps/api，端口 4000）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
API_DIR="$ROOT/services/api"
RESET=0

usage() {
  cat <<'EOF'
用法：./scripts/up.sh [--reset]

在本机把数据库、接口和网页一起启动。第一次会安装依赖、建表、写入演示数据。
已经启动过时再执行，会沿用现有数据，不会清空。

  --reset   清空数据库并重新写入演示数据（你改过的案件会丢掉）
  --help    看这段说明

打开 http://localhost:3000
停掉服务：./scripts/down.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset) RESET=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "不认识的参数：$1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\n启动失败：%s\n' "$*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "找不到命令 $1。$2"
}

compose() {
  docker compose -f "$ROOT/docker-compose.yml" "$@"
}

port_listener() {
  local pids
  pids="$(lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true)"
  printf '%s\n' "$pids" | awk 'NF { print; exit }'
}

# 监听端口的进程是不是我们上次记下的进程，或它的子进程。
listener_is_ours() {
  local recorded="$1" listener="$2" current="$2" i
  [[ -n "$recorded" && -n "$listener" ]] || return 1
  kill -0 "$recorded" 2>/dev/null || return 1
  for i in 1 2 3 4 5 6 7 8; do
    [[ "$current" == "$recorded" ]] && return 0
    [[ -n "$current" && "$current" != "1" ]] || return 1
    current="$(ps -o ppid= -p "$current" 2>/dev/null | tr -d '[:space:]' || true)"
  done
  return 1
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi
  if [[ -x "$HOME/.local/bin/uv" ]]; then
    export PATH="$HOME/.local/bin:$PATH"
    return
  fi
  die "找不到 uv。请先安装：curl -LsSf https://astral.sh/uv/install.sh | sh    装完后重新打开终端，再运行 ./scripts/up.sh"
}

ensure_env() {
  if [[ ! -f "$API_DIR/.env.local" ]]; then
    cp "$API_DIR/.env.example" "$API_DIR/.env.local"
    echo "已生成 services/api/.env.local"
  fi
  if [[ ! -f "$ROOT/apps/web/.env.local" ]]; then
    cat > "$ROOT/apps/web/.env.local" <<'EOF'
NEXT_PUBLIC_DATA_SOURCE=api
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
EOF
    echo "已生成 apps/web/.env.local（网页会读取接口，而不是浏览器里的本地演示数据）"
  elif ! grep -q '^NEXT_PUBLIC_DATA_SOURCE=api$' "$ROOT/apps/web/.env.local"; then
    echo "注意：apps/web/.env.local 没有 NEXT_PUBLIC_DATA_SOURCE=api，网页可能不会连这个接口。"
  fi
}

wait_postgres() {
  local i
  for i in $(seq 1 30); do
    if compose exec -T postgres pg_isready -U fcc -d complex_accounts >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  die "数据库 60 秒内没有就绪。请看：docker compose logs postgres"
}

ensure_test_database() {
  local exists
  exists="$(compose exec -T postgres psql -U fcc -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='complex_accounts_test'" || true)"
  if [[ "$exists" != "1" ]]; then
    compose exec -T postgres createdb -U fcc complex_accounts_test
    echo "已创建测试库 complex_accounts_test（日常打开网页用不到它）"
  fi
}

user_count() {
  compose exec -T postgres psql -U fcc -d complex_accounts -tAc "SELECT COUNT(*) FROM users" 2>/dev/null | tr -d '[:space:]' || true
}

wait_http() {
  local url="$1" label="$2" i
  for i in $(seq 1 90); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  die "$label 没有在预期时间内起来。日志：$3"
}

start_detached() {
  local name="$1" log="$2"
  shift 2
  if [[ -f "$RUN_DIR/$name.pid" ]]; then
    local old
    old="$(cat "$RUN_DIR/$name.pid")"
    if kill -0 "$old" 2>/dev/null; then
      echo "$name 已在运行（pid $old）"
      return 0
    fi
  fi
  nohup "$@" >>"$log" 2>&1 &
  echo $! > "$RUN_DIR/$name.pid"
  disown || true
}

cd "$ROOT"
mkdir -p "$RUN_DIR"

need docker "请安装并打开 Docker Desktop：https://www.docker.com/products/docker-desktop/"
docker info >/dev/null 2>&1 || die "Docker 没在运行。请先打开 Docker Desktop，等它显示已启动，再运行本脚本。"
docker compose version >/dev/null 2>&1 || die "Docker Compose 不可用。请更新 Docker Desktop。"
need node "请安装 Node.js 22：https://nodejs.org/"
need npm "Node.js 安装不完整，缺少 npm。"
need curl "请安装 curl。"
need lsof "macOS 一般自带 lsof。Linux 可安装：sudo apt-get install lsof"

node_major="$(node -p "process.versions.node.split('.')[0]")"
if [[ "$node_major" -lt 22 ]]; then
  die "需要 Node.js 22 或更高，当前是 $(node -v)。"
fi

ensure_uv
python_ok="$(uv python find '>=3.12' 2>/dev/null || true)"
if [[ -z "$python_ok" ]]; then
  say "正在安装 Python 3.12（只需一次）"
  uv python install 3.12
fi

ensure_env

say "启动数据库"
compose up -d postgres
wait_postgres
ensure_test_database

say "安装 Python 依赖"
(cd "$API_DIR" && uv sync)

say "更新数据库表结构"
(cd "$API_DIR" && uv run alembic upgrade head)

if [[ "$RESET" -eq 1 ]]; then
  say "清空并重新写入演示数据"
  (cd "$API_DIR" && uv run python -m fcc_api.seed --reset)
else
  count="$(user_count)"
  if [[ -z "$count" || "$count" == "0" ]]; then
    say "写入演示数据（5 个用户、7 笔案件）"
    (cd "$API_DIR" && uv run python -m fcc_api.seed)
  else
    echo "数据库里已有 $count 个用户，跳过演示数据。要清空重来：./scripts/up.sh --reset"
  fi
fi

say "安装网页依赖并编译共享规则包"
npm install
npm run build --workspace @fcc/domain

api_busy="$(port_listener 8000)"
web_busy="$(port_listener 3000)"
api_pid="$(cat "$RUN_DIR/api.pid" 2>/dev/null || true)"
web_pid="$(cat "$RUN_DIR/web.pid" 2>/dev/null || true)"
if [[ -n "$api_busy" ]] && ! listener_is_ours "$api_pid" "$api_busy"; then
  die "端口 8000 已被其他程序占用（pid $api_busy）。请先停掉它，或运行 ./scripts/down.sh"
fi
if [[ -n "$web_busy" ]] && ! listener_is_ours "$web_pid" "$web_busy"; then
  die "端口 3000 已被其他程序占用（pid $web_busy）。请先停掉它，或运行 ./scripts/down.sh"
fi

say "启动接口 http://127.0.0.1:8000"
start_detached api "$RUN_DIR/api.log" \
  bash -c 'cd "$1" && exec uv run uvicorn fcc_api.main:app --host 127.0.0.1 --port 8000 --no-access-log' \
  _ "$API_DIR"
wait_http "http://127.0.0.1:8000/health" "接口" "$RUN_DIR/api.log"

say "启动网页 http://127.0.0.1:3000"
start_detached web "$RUN_DIR/web.log" \
  bash -c 'cd "$1" && exec npm run dev --workspace @fcc/web -- --port 3000' \
  _ "$ROOT"
wait_http "http://127.0.0.1:3000" "网页" "$RUN_DIR/web.log"

cat <<'EOF'

已经启动。

  网页    http://localhost:3000
  接口    http://localhost:8000/health
  接口文档 http://localhost:8000/api/docs

网页左下角可以切换演示用户，不需要密码。默认是 Sarah Whitfield（顾问）。
其他用户：Marcus Lee（运营）、Priya Raman（合规）、Daniel Okafor（管理员）、Julien Tremblay（另一位顾问）。

停掉网页和接口：./scripts/down.sh
清空演示数据再启动：./scripts/up.sh --reset
日志：.run/api.log 和 .run/web.log
EOF

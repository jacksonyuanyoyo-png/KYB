#!/usr/bin/env bash
# 停掉 scripts/up.sh 拉起的网页和接口。默认保留数据库里的数据。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
WITH_DB=0

usage() {
  cat <<'EOF'
用法：./scripts/down.sh [--with-db]

停掉网页（3000）和接口（8000）。数据库容器继续运行，数据还在。

  --with-db  同时停掉数据库容器。数据卷还在，下次 ./scripts/up.sh 会接着用。
  --help     看这段说明
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-db) WITH_DB=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "不认识的参数：$1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

stop_recorded() {
  local name="$1" pid
  [[ -f "$RUN_DIR/$name.pid" ]] || return 0
  pid="$(cat "$RUN_DIR/$name.pid")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    local pgid
    pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -n "$pgid" ]]; then
      kill -TERM -"$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    else
      kill -TERM "$pid" 2>/dev/null || true
    fi
    local i
    for i in 1 2 3 4 5; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      if [[ -n "${pgid:-}" ]]; then
        kill -KILL -"$pgid" 2>/dev/null || true
      fi
      kill -KILL "$pid" 2>/dev/null || true
    fi
    echo "已停止 $name（pid $pid）"
  else
    echo "$name 没有在运行"
  fi
  rm -f "$RUN_DIR/$name.pid"
}

stop_recorded web
stop_recorded api

if [[ "$WITH_DB" -eq 1 ]]; then
  docker compose -f "$ROOT/docker-compose.yml" stop postgres
  echo "已停止数据库容器。数据还在，下次 ./scripts/up.sh 会继续用。"
else
  echo "数据库容器仍在运行。若要连它一起停：./scripts/down.sh --with-db"
fi

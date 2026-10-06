# 09 本地开发

记录日期：2026-10-06

## 本文依赖

- 仓库根 `docker-compose.yml`：本地 PostgreSQL（已存在，不修改）
- `docs/backend/00-scope-and-stack.md`：目录结构与第 5 节配置项
- `docs/backend/02-postgres-schema.md`：迁移与种子
- `docs/backend/04-authorization.md`：请求头身份
- `apps/web/package.json`：前端启动命令

## Agent 实现时禁止

- 本期不创建脚本、`Makefile`、`docker-compose` 文件或 `.env` 文件。本文只写后续实现完成后要能照着跑通的步骤和变量。
- 不修改根 `docker-compose.yml`，不新增服务。
- 不为 PostgreSQL 编写 Dockerfile，也不把初始化 SQL 放进数据库镜像。表结构只由 Alembic 迁移创建。
- 不用根 `package.json` 的 `db:generate`、`db:migrate`（它们是 Prisma 的）。
- 不启动 `apps/api`（NestJS，端口 4000）。
- 不把 `.env.local`、`LOCAL_STORAGE_ROOT` 目录提交进 git。

---

## 1. 前置条件

| 工具 | 版本 | 用途 |
| --- | --- | --- |
| Docker | 任意近期版本 | 跑 PostgreSQL |
| Python | 3.12 或更高 | API |
| uv | 0.4 或更高 | Python 依赖与虚拟环境 |
| Node.js | 与仓库 `README.md` 一致（22.x） | 前端 |
| npm | 10.x | 前端 |

---

## 2. 启动 PostgreSQL

使用仓库根 `docker-compose.yml` 已有的 `postgres` 服务（用户 `fcc`、密码 `fcc-local-only`、库 `complex_accounts`、端口 5432）。这个服务写的是 `image: postgres:17-alpine`，直接拉取官方镜像，没有 `build`。只启动这一个服务，`azurite` 本期不用（对象存储本地用文件系统）。

PostgreSQL 不需要仓库里的 Dockerfile。Dockerfile 只在要自己构建镜像时才写。本地数据库用官方镜像；表、扩展和种子在容器起来之后由 `alembic upgrade` 和 `python -m fcc_api.seed` 完成。生产数据库是托管实例，也不把 PostgreSQL 打进应用镜像。FastAPI 进程的镜像另放在 `services/api/Dockerfile`，见 `docs/backend/12-production-launch.md` 第 10 节。

```bash
docker compose up -d postgres
docker compose ps postgres          # STATUS 应为 healthy
```

建测试库（只需一次）：

```bash
docker compose exec postgres createdb -U fcc complex_accounts_test
```

---

## 3. 环境变量

在 `services/api/` 下建 `.env.local`（由实现阶段的 `.env.example` 复制，本期不创建）。`fcc_api.config.Settings` 读取它。

```dotenv
APP_ENV=local
API_HOST=127.0.0.1
API_PORT=8000
DATABASE_URL=postgresql+psycopg://fcc:fcc-local-only@localhost:5432/complex_accounts
DATABASE_URL_TEST=postgresql+psycopg://fcc:fcc-local-only@localhost:5432/complex_accounts_test
AUTH_MODE=header
CORS_ALLOWED_ORIGINS=http://localhost:3000
STORAGE_BACKEND=local
LOCAL_STORAGE_ROOT=./.local-storage
LOCAL_URL_SIGNING_SECRET=
UPLOAD_URL_TTL_SECONDS=900
DOWNLOAD_URL_TTL_SECONDS=300
UPLOAD_MAX_BYTES=25000000
UPLOAD_ALLOWED_MIME=application/pdf,image/jpeg,image/png
SCAN_ADAPTER=noop
EXTRACTION_ADAPTER=noop
AI_MODEL_ALLOWLIST=doc-extract-demo,entity-classifier-demo,case-assistant-demo,pre-review-demo
LOG_LEVEL=INFO
```

`LOCAL_URL_SIGNING_SECRET` 留空时启动随机生成，重启后旧的上传和预览地址失效，这是预期行为。

前端在 `apps/web/.env.local`（前端切换任务里再建）：

```dotenv
NEXT_PUBLIC_DATA_SOURCE=api
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_API_PAGES=identity,case-workspace
```

---

## 4. 安装依赖

```bash
cd services/api
uv sync
```

---

## 5. 迁移

```bash
cd services/api
uv run alembic upgrade head
uv run alembic current            # 应显示最新版本，如 0002_reference_data (head)
```

迁移完成后库里已有内置规则版本 `demo-2026-10-04` 和 `rule_library_state` 单行，但没有用户和案件。

---

## 6. 种子

```bash
cd services/api
uv run python -m fcc_api.seed --reset
```

- 只在 `APP_ENV` 为 `local` 或 `test` 时执行，否则退出码非 0。
- `--reset` 会删掉 `public` schema 重建，再跑迁移、写种子。
- 写入内容见 `docs/backend/02-postgres-schema.md` 第 6 节：5 个用户、7 笔案件、24 个节点、17 个文件元数据、23 条清单状态、3 个任务、14 条审计。
- 结束时自检，任何一项不符就回滚并报错。

---

## 7. 启动 API

```bash
cd services/api
uv run uvicorn fcc_api.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

`--no-access-log` 必须带：默认访问日志会打印完整 URL，包括预签名参数（见 `docs/backend/05-documents-and-files.md` 第 7 节）。

---

## 8. 启动前端

另开终端，在仓库根目录：

```bash
npm install
npm run dev --workspace @fcc/web
```

打开 `http://localhost:3000`。没设 `NEXT_PUBLIC_DATA_SOURCE=api` 时前端仍用浏览器本地数据，和现在一样。

---

## 9. 健康检查

```bash
curl -s http://localhost:8000/health
# {"status":"ok","service":"fcc-kyb-api","database":"ok","migration":"0002_reference_data"}

curl -s http://localhost:8000/api/v1/me -H 'X-User-Id: u-advisor' -H 'X-User-Role: ADVISOR'
# {"id":"u-advisor","name":"Sarah Whitfield","email":"sarah.whitfield@fidelity.ca","role":"ADVISOR","team":"WI Toronto"}

curl -s 'http://localhost:8000/api/v1/cases?limit=1' -H 'X-User-Id: u-ops' -H 'X-User-Role: OPERATIONS'
# counts.ALL 应为 7

curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/v1/me
# 401
```

OpenAPI 页面：`http://localhost:8000/api/docs`。

一次完整的冒烟：

```bash
# 版本冲突：case-0139 种子版本是 9，用 8 提交应得 409
curl -s -X POST http://localhost:8000/api/v1/cases/case-0139/status \
  -H 'X-User-Id: u-advisor' -H 'X-User-Role: ADVISOR' -H 'Content-Type: application/json' \
  -d '{"version":8,"status":"READY_FOR_COMPLIANCE","summary":"Submitted for compliance review"}'

# 门禁：case-0128（RETURNED，负责人 u-advisor-2）持股合计 91%，送审应得 422 Ownership structure is incomplete.
curl -s -X POST http://localhost:8000/api/v1/cases/case-0128/status \
  -H 'X-User-Id: u-advisor-2' -H 'X-User-Role: ADVISOR' -H 'Content-Type: application/json' \
  -d '{"version":17,"status":"READY_FOR_COMPLIANCE","summary":"Submitted for compliance review"}'
```

---

## 10. 测试

```bash
cd services/api
uv run pytest                       # 全部
uv run pytest tests/parity          # 只跑对拍
uv run ruff check src tests         # 含禁止相对导入（TID252）
```

TypeScript 规格本身的测试仍在仓库根运行：

```bash
npm test --workspace @fcc/domain
```

---

## 11. 失败时看哪里

| 现象 | 先看 | 常见原因 |
| --- | --- | --- |
| `docker compose up` 后连不上 5432 | `docker compose ps`、`docker compose logs postgres` | 本机已有 PostgreSQL 占用 5432；容器还没 healthy |
| `alembic upgrade` 报认证失败 | `DATABASE_URL` | 密码与 `docker-compose.yml` 不一致；驱动前缀不是 `postgresql+psycopg://` |
| `alembic upgrade` 报 `pg_trgm` 不存在 | 迁移日志 | 用的不是官方 postgres 镜像；`contrib` 扩展缺失 |
| 种子报「APP_ENV must be local or test」 | `.env.local` | `APP_ENV` 写错或没被读取 |
| 种子自检失败 | 命令输出里列出的案件 id 与期望 | `seed.ts` 改过而 Python 种子没同步；规则移植有误（先跑 `pytest tests/parity`） |
| API 启动即退出，提示 `AUTH_MODE` | 启动日志 | `APP_ENV=production` 配了 `header`，或配了尚未实现的 `entra` |
| 所有请求 401 | 请求头 | 缺 `X-User-Id` / `X-User-Role`；角色与 `users.role` 不一致；没跑种子所以没有用户 |
| 请求 404 但案件确实存在 | 当前身份 | 顾问看不到别人负责的案件（见 04 第 2 节） |
| `/health` 返回 503、`migration` 不是 head | `uv run alembic current` | 拉了新代码没跑迁移 |
| 浏览器报 CORS | `CORS_ALLOWED_ORIGINS` | 前端端口不是 3000 |
| 上传 `PUT` 返回 403 | 上传地址里的 `expires` | 地址过期（15 分钟）；API 重启换了随机签名密钥 |
| 完成登记返回 400 `TYPE_MISMATCH` | 文件本身 | 扩展名是 `.pdf` 但内容不是 PDF |
| 预览返回 422 `FILE_NOT_STORED` | 文件 `storageState` | 种子文件只有元数据，没有原件 |
| 抽取一直 `PROCESSING` | API 日志里该 `document_id` 的错误码 | 后台任务异常；`EXTRACTION_ADAPTER` 配错 |
| 想清空本地数据 | — | 重新运行 `python -m fcc_api.seed --reset`，并删除 `services/api/.local-storage/` |

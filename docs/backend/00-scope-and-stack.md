# 00 服务边界与技术栈

记录日期：2026-10-06

## 本文依赖

- `docs/business-flow-and-ai.md`：流程、角色、AI 边界
- `docs/workbench-usage-and-design.md`：每个页面在读写什么
- `docs/production-readiness.md`：机密分级、Entra、对象存储、审计、加拿大区域
- `packages/domain/src/index.ts`、`packages/domain/src/index.test.ts`：规则行为规格
- `apps/web/lib/types.ts`、`apps/web/lib/data/actions.ts`：正式数据模型与写入语义

## Agent 实现时禁止

- 不扩展、不修复、不删除 `apps/api`（NestJS + Prisma 草稿）。不新增任何 `@nestjs/*`、`prisma`、`@prisma/client` 依赖。
- 不修改 `apps/web`、`packages/domain`、`docs/business-flow-and-ai.md`、`docs/workbench-usage-and-design.md`、`docs/production-readiness.md`。
- 不修改根目录 `turbo.json`、`package.json` 的 workspaces 和脚本。若以后要把 Python 服务接入 turbo，先按根目录 `AGENTS.md` 读取已安装 turbo 包的 `docs/`。
- 不使用相对导入（`from .x import y`、`from ..x import y`）。所有 Python 导入写成 `from fcc_api.xxx import yyy`。
- 不把文件字节写进 PostgreSQL。
- 不用 `Base.metadata.create_all()` 建表，不手写 SQL 改表。表结构只通过 Alembic 迁移变更。

---

## 1. 已定决策

| 项 | 决定 |
| --- | --- |
| HTTP 框架 | Python FastAPI，全部接口 |
| 数据库 | PostgreSQL（本地用仓库根 `docker-compose.yml` 的 `postgres:17-alpine`） |
| 迁移 | 只用 Alembic |
| ORM / 驱动 | SQLAlchemy 2.x（同步会话）+ psycopg 3 |
| 校验与序列化 | Pydantic v2，`pydantic-settings` 读配置 |
| 规则运行时 | Python 纯函数包 `fcc_api.rules`，按 `packages/domain/src/index.ts` 移植 |
| 规则规格 | 对拍通过前 TypeScript 仍是规格（见 `docs/backend/07-rules-versioning.md`） |
| 文件字节 | 本地文件系统；生产 AWS S3；PostgreSQL 只存元数据 |
| 本地身份 | 请求头 `X-User-Id` + `X-User-Role` |
| 生产身份 | Entra ID OIDC。本地仍用请求头。生产校验见 `docs/backend/12-production-launch.md` 第 3 节 |
| 包管理 | `uv` + `pyproject.toml`，`requires-python = ">=3.12"` |
| 测试 | pytest + httpx（FastAPI `TestClient`） |
| OpenAPI | 由 FastAPI 自动生成，地址 `/api/docs` 与 `/api/openapi.json`；不手写第二份 schema |

以上依赖都是开源许可（MIT / BSD / Apache-2.0 / PostgreSQL License）。新增依赖前确认许可证属于这几类。

### NestJS 草稿的处理

`apps/api` 是过时草稿，保留原样，不再演进。它和前端的差异写在 `docs/backend/01-persistence-model.md` 第 4 节。根 `package.json` 里的 `db:generate`、`db:migrate` 指向 Prisma，新后端不使用这两个脚本。

### 存储与区域

`docs/production-readiness.md` 写的是 Azure Storage 和 Azure Canada Central。本期决定生产对象存储改为 AWS S3，**约束不变**，逐条映射：

| production-readiness.md 的约束 | S3 上的做法 |
| --- | --- |
| 私有存储，按需客户管理密钥 | 开启 Block Public Access；SSE-KMS，使用客户管理的 KMS key |
| 短期、限定范围的上传地址 | 预签名 `PUT`，单对象键、限定 `Content-Type` 与 `Content-Length`，有效期 `UPLOAD_URL_TTL_SECONDS` |
| 先隔离，校验类型与魔数，扫毒，再移到不可变的已接受容器 | 两个桶：`S3_BUCKET_QUARANTINE`、`S3_BUCKET_ACCEPTED`；已接受桶开启 Object Lock |
| 加拿大数据驻留 | `AWS_REGION=ca-central-1` |
| 无公网数据库端点、私有端点 | 数据库与 S3 走 VPC 内私有访问（S3 用 VPC Gateway/Interface Endpoint） |

数据库托管和密钥管理服务本期不定。无论最终放在哪里，必须满足：加拿大区域、私有网络、无公网端点、静态加密。`infra/main.bicep` 是 Azure 基线，本期不改、不引用。

---

## 2. 服务边界

后端只负责内部员工工作台（`apps/web`）的数据读写。

本期在范围内：

- 案件、股权树、账户详情、清单状态、自定义清单项、复核任务、状态流转、合规决定
- 文件元数据、上传地址、预览地址、扫描与抽取状态（占位实现）
- 只追加的审计
- 规则库：已发布版本、草稿、内置规则停用、内置规则字段覆盖、额外规则、发布
- 读接口：案件列表、案件详情、合规队列、实体与人员、Document AI 文件台账、审计日志、规则库

第一期本地默认用占位适配器。上线实现见 `docs/backend/12-production-launch.md`，生产进程禁止继续用这些占位。

| 适配器 | 模块位置 | 本地默认 | 上线 |
| --- | --- | --- | --- |
| Entra ID 令牌校验 | `fcc_api.auth.entra_provider` | 不启用；`AUTH_MODE=header` | 第 3 节，必须启用 |
| OCR / 文件抽取 | `fcc_api.adapters.extraction` | `noop`：立即 `EXTRACTED`，结果为空 | 第 5.4 节，`http` |
| 病毒扫描与内容消毒 | `fcc_api.adapters.scanning`、`fcc_api.adapters.cdr` | `noop`：立即 `CLEAN` | 第 5.3 节，异步 |
| 名单筛查 | `fcc_api.adapters.screening` | 不拦截送审 | 第 8.1 节 |
| uDirect / uniFide | `fcc_api.adapters.submission` | 调用返回 503 | 第 9.2 节 |
| PDF 回填 | `fcc_api.adapters.forms` | 模板缺失返回 422 | 第 9.1 节 |
| 客户门户 | `fcc_api.api.routers.portal` | 可测，令牌不能调用员工接口 | 第 9.3 节 |
| LLM 建议 | `fcc_api.adapters.llm` | `noop` 返回 503；前端仍用 `mock.ts` | 第 8.2 节 |

AI 边界沿用 `docs/business-flow-and-ai.md`「AI 接入原则」：AI 只建议，规则引擎判定，员工确认后才写库。后端不接受「AI 判定某人不是 PEP」这类写入，因为本期根本没有这种端点。

---

## 3. 目录约定

新后端放在仓库根 `services/api/`。不放进 `apps/`，因为 `apps/*` 是 npm workspaces，放进去会让 npm 把 Python 目录当成 workspace 处理。

```text
services/api/
├── pyproject.toml                  # 包名 fcc-api，导入名 fcc_api；uv 管理
├── uv.lock
├── alembic.ini                     # script_location = migrations
├── .env.example                    # 只列变量名与本地默认值，不放真实密钥
├── migrations/
│   ├── env.py                      # 从 fcc_api.config 读 DATABASE_URL，target_metadata = fcc_api.db.base.Base.metadata
│   ├── script.py.mako
│   └── versions/                   # 每次结构变更一个文件；只由 alembic revision 生成
├── src/
│   └── fcc_api/
│       ├── __init__.py
│       ├── main.py                 # create_app()：挂路由、异常处理、CORS、请求 ID 中间件
│       ├── config.py               # Settings(BaseSettings)，见第 5 节
│       ├── errors.py               # ApiError 与 6 种错误码，统一 JSON 外形
│       ├── logging.py              # 结构化日志与脱敏过滤器
│       ├── ids.py                  # new_id("case") -> "case_<随机>"，与前端 uid() 前缀一致
│       ├── labels.py               # STATUS_LABELS 等，从 apps/web/lib/labels.ts 移植，用于审计 changes
│       ├── db/
│       │   ├── base.py             # DeclarativeBase、命名约定
│       │   ├── session.py          # engine、SessionLocal、get_session 依赖
│       │   └── models/
│       │       ├── users.py
│       │       ├── cases.py        # cases、case_reference_counters
│       │       ├── parties.py
│       │       ├── checklist.py    # checklist_items、custom_requirements
│       │       ├── documents.py    # case_documents、upload_slots
│       │       ├── extractions.py  # document_extractions、extraction_pages、extraction_fields
│       │       ├── tasks.py        # review_tasks
│       │       ├── audit.py        # audit_events
│       │       └── rules.py        # rule_versions、rule_drafts、rule_library_state
│       ├── rules/                  # 纯 Python：不得导入 fastapi、sqlalchemy、fcc_api.db
│       │   ├── constants.py        # RULE_VERSION、ENTITY_TYPES、ACCOUNT_FEATURES、BENEFICIAL_OWNER_THRESHOLD
│       │   ├── types.py            # Party、AccountProfile、AccountCase、Requirement、ValidationIssue（dataclass）
│       │   ├── domain.py           # validate_ownership、validate_profile、effective_ownership、persons_to_identify、generate_requirements
│       │   ├── details.py          # validate_details、to_account_profile（移植自 apps/web/lib/insights.ts）
│       │   ├── library.py          # rule_applies、library_requirements、adjustments_for（移植自 apps/web/lib/rules/library.ts，语义按 07 修正）
│       │   ├── builtin_catalog.py  # 内置规则展示元数据（移植自 apps/web/app/(workspace)/rules/page.tsx 的 BUILTIN）
│       │   └── insight.py          # checklist_for、is_collected、analyze
│       ├── auth/
│       │   ├── actor.py            # Actor(id, role, name, team)
│       │   ├── header_provider.py  # AUTH_MODE=header
│       │   ├── entra_provider.py   # AUTH_MODE=entra，见 docs/backend/12-production-launch.md 第 3 节
│       │   ├── dependencies.py     # get_actor
│       │   └── policy.py           # 权限矩阵与案件可见范围，见 04
│       ├── schemas/                # Pydantic 请求与响应，JSON 用 camelCase 别名
│       │   ├── common.py
│       │   ├── cases.py
│       │   ├── parties.py
│       │   ├── documents.py
│       │   ├── tasks.py
│       │   ├── audit.py
│       │   └── rules.py
│       ├── services/               # 事务与业务逻辑；每个写函数对应 actions.ts 的一个函数
│       │   ├── case_write.py       # load_for_update / bump_version / append_audit 的公共流程
│       │   ├── cases.py            # create_case、change_status、compliance_decision
│       │   ├── parties.py          # update_parties、apply_ai_parties、record_ai_rejection
│       │   ├── profile.py          # update_profile
│       │   ├── checklist.py        # set_checklist_status、add_custom_requirement
│       │   ├── documents.py        # create_upload_slots、complete_uploads、assign_document、content_url
│       │   ├── tasks.py            # add_tasks、toggle_task
│       │   ├── rule_library.py     # start/save/remove/retire/override/discard/publish
│       │   ├── queries.py          # 列表、合规队列、实体、文件台账、审计
│       │   └── references.py       # FCC-YYYY-NNNN 编号
│       ├── storage/
│       │   ├── base.py             # ObjectStorage 协议：presign_put、presign_get、head、promote、delete_quarantine
│       │   ├── local.py            # STORAGE_BACKEND=local
│       │   └── s3.py               # STORAGE_BACKEND=s3，见 docs/backend/12-production-launch.md 第 5.1 节
│       ├── adapters/
│       │   ├── scanning.py         # NoopScanner
│       │   ├── extraction.py       # NoopExtractor
│       │   ├── screening.py        # 预留
│       │   ├── submission.py       # 预留：uDirect / uniFide
│       │   └── forms.py            # 预留：PDF 回填
│       ├── api/
│       │   ├── deps.py
│       │   └── routers/
│       │       ├── health.py
│       │       ├── me.py           # /me、/users
│       │       ├── cases.py        # 列表、详情、创建、状态、合规决定、案件审计
│       │       ├── parties.py
│       │       ├── profile.py
│       │       ├── checklist.py    # 清单状态、自定义清单项
│       │       ├── documents.py    # 上传槽、完成上传、挂接、预览地址、抽取结果、文件台账
│       │       ├── local_storage.py# 仅 STORAGE_BACKEND=local 时挂载
│       │       ├── tasks.py
│       │       ├── compliance.py   # 合规队列
│       │       ├── entities.py
│       │       ├── audit.py
│       │       └── rule_library.py
│       └── seed/
│           ├── __main__.py         # python -m fcc_api.seed [--reset]
│           └── data.py             # 移植 apps/web/lib/data/seed.ts
└── tests/
    ├── conftest.py                 # 测试库、事务回滚、按角色生成请求头
    ├── parity/
    │   ├── fixtures/domain_cases.json   # 与 packages/domain/src/index.test.ts 同一组输入输出
    │   └── test_domain_parity.py
    ├── rules/                      # 纯函数单测
    └── api/                        # 按端点分文件，用例编号见 10-test-catalog.md
```

### 分层规则

1. `api/routers` 只做：解析请求、取 `Actor`、调用一个 service 函数、返回响应模型。不写 SQL，不判断权限细节。
2. `services` 负责：权限判定（调用 `auth.policy`）、加锁、版本比较、写库、追加审计，全部在同一事务里。
3. `rules` 是纯函数，输入 dataclass，输出 dataclass。它不知道数据库，也不知道 HTTP。这样对拍测试可以单独跑。
4. `storage` 和 `adapters` 通过协议类注入，`config.py` 决定实现。

### 导入示例

```python
from fcc_api.rules.domain import generate_requirements, validate_ownership
from fcc_api.services.case_write import load_case_for_update
from fcc_api.auth.policy import require_case_write
```

禁止：

```python
from .domain import generate_requirements
from ..services import case_write
```

在 `pyproject.toml` 里打开 ruff 规则 `TID252`（禁止相对导入），并设 `ban-relative-imports = "all"`。

---

## 4. FastAPI 模块划分

| 路由模块 | 前缀 | 覆盖的 actions.ts 函数 / 读 |
| --- | --- | --- |
| `health` | `/health` | 健康检查（无鉴权） |
| `me` | `/api/v1/me`、`/api/v1/users` | 当前用户、用户名单（替代 `useSession`、`userName`） |
| `cases` | `/api/v1/cases` | `createCase`、`changeStatus`、`complianceDecision`；案件列表、详情、案件审计 |
| `parties` | `/api/v1/cases/{caseId}/parties`、`/ai-suggestions` | `updateParties`、`applyAiParties`、`recordAiRejection` |
| `profile` | `/api/v1/cases/{caseId}/profile` | `updateProfile` |
| `checklist` | `/api/v1/cases/{caseId}/checklist`、`/custom-requirements` | `setChecklistStatus`、`addCustomRequirement` |
| `documents` | `/api/v1/cases/{caseId}/uploads`、`/documents`；`/api/v1/documents` | `uploadDocuments`、`assignDocument`；Document AI 文件台账、预览、抽取结果 |
| `tasks` | `/api/v1/cases/{caseId}/tasks`；`/api/v1/tasks` | `addTasks`、`toggleTask`；未完成任务（仪表盘） |
| `compliance` | `/api/v1/compliance/queue` | 合规队列 |
| `entities` | `/api/v1/entities` | 实体与人员 |
| `audit` | `/api/v1/audit` | 审计日志 |
| `rule_library` | `/api/v1/rule-library` | `startRuleDraft`、`saveLibraryRule`、`removeLibraryRule`、`setBuiltinRetired`、`saveBuiltinOverride`、`discardRuleDraft`、`publishRuleDraft`；规则库读取、规则测试器 |
| `local_storage` | `/api/v1/local-storage` | 本地模式下代替 S3 的上传与下载 |

端点细节见 `docs/backend/03-api-contract.md`。

---

## 5. 配置项

全部从环境变量读取，由 `fcc_api.config.Settings` 定义。变量名固定如下，后续实现不得改名。

| 变量 | 本地默认 | 生产 | 说明 |
| --- | --- | --- | --- |
| `APP_ENV` | `local` | `production` | `local` / `test` / `production`。种子命令和 `AUTH_MODE=header` 只允许在 `local`、`test` |
| `API_HOST` | `127.0.0.1` | 由平台决定 | uvicorn 监听地址 |
| `API_PORT` | `8000` | 由平台决定 | 旧 NestJS 草稿用 4000，新服务用 8000，避免混淆 |
| `DATABASE_URL` | `postgresql+psycopg://fcc:fcc-local-only@localhost:5432/complex_accounts` | 由密钥服务注入 | 与根 `docker-compose.yml` 一致 |
| `DATABASE_URL_TEST` | `postgresql+psycopg://fcc:fcc-local-only@localhost:5432/complex_accounts_test` | 不设 | pytest 使用 |
| `AUTH_MODE` | `header` | `entra` | 生产配 `header` 时拒绝启动。校验步骤见 `docs/backend/12-production-launch.md` 第 3 节 |
| `ENTRA_TENANT_ID`、`ENTRA_CLIENT_ID`、`ENTRA_AUDIENCE`、`ENTRA_ISSUER` | 空 | 必填 | 预留 |
| `ENTRA_ROLE_GROUP_MAP` | 空 | 必填 | 预留：Entra 组 ID 到四个角色的 JSON 映射 |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | 正式前端域名 | 逗号分隔 |
| `STORAGE_BACKEND` | `local` | `s3` | |
| `LOCAL_STORAGE_ROOT` | `./.local-storage`（相对 `services/api/`） | 不设 | 下含 `quarantine/`、`accepted/`；加入 `.gitignore` |
| `LOCAL_URL_SIGNING_SECRET` | 启动时随机生成 | 不设 | 本地上传与预览地址的 HMAC 密钥 |
| `AWS_REGION` | 不设 | `ca-central-1` | |
| `S3_BUCKET_QUARANTINE`、`S3_BUCKET_ACCEPTED` | 不设 | 必填 | |
| `S3_KMS_KEY_ID` | 不设 | 必填 | 客户管理密钥 |
| `UPLOAD_URL_TTL_SECONDS` | `900` | `900` | 上传地址有效期 |
| `DOWNLOAD_URL_TTL_SECONDS` | `300` | `300` | 预览地址有效期 |
| `UPLOAD_MAX_BYTES` | `25000000` | `25000000` | 与前端 Dropzone 提示「up to 25 MB each」一致 |
| `UPLOAD_ALLOWED_MIME` | `application/pdf,image/jpeg,image/png` | 同左 | 与前端 `accept=".pdf,.jpg,.jpeg,.png"` 一致 |
| `SCAN_ADAPTER` | `noop` | `http` | 上线见 12 第 5.3 节 |
| `EXTRACTION_ADAPTER` | `noop` | `http` | 上线见 12 第 5.4 节 |
| `AI_MODEL_ALLOWLIST` | `doc-extract-demo,entity-classifier-demo,case-assistant-demo,pre-review-demo` | 正式模型名，由密钥配置注入 | 与 `apps/web/lib/ai/mock.ts` 的 `AI_MODELS` 同一组键；审计里的 `ai.model` 必须在此列表 |
| `LOG_LEVEL` | `INFO` | `INFO` | |

前端切换时新增的变量（前端改动不在本期）：`NEXT_PUBLIC_DATA_SOURCE`（`local` / `api`）、`NEXT_PUBLIC_API_BASE_URL`（本地 `http://localhost:8000`）。见 `docs/backend/08-frontend-cutover.md`。

---

## 6. 本地与生产差异

| 方面 | 本地（`APP_ENV=local`） | 生产（`APP_ENV=production`） |
| --- | --- | --- |
| 身份 | 请求头 `X-User-Id`、`X-User-Role`，用户必须存在于 `users` 表且角色一致 | Entra 访问令牌，见 12 第 3 节 |
| 启动保护 | 允许 `AUTH_MODE=header` | `AUTH_MODE=header` 时进程拒绝启动 |
| 文件存储 | `LOCAL_STORAGE_ROOT` 下的目录；上传与预览走 API 自己的 `/api/v1/local-storage/*`，带 HMAC 签名和过期时间 | S3 预签名地址；隔离桶与已接受桶分开 |
| 扫描 | `noop`，立即 `CLEAN` | 异步扫描与消毒，见 12 第 5.3 节 |
| 抽取 | `noop`，立即 `EXTRACTED`，结果为空 | 异步抽取，见 12 第 5.4 节 |
| OpenAPI | `/api/docs` 开放 | 关闭文档页；`/api/openapi.json` 仅 ADMIN，见 12 第 7.4 节 |
| 种子 | `python -m fcc_api.seed --reset` 可用 | 命令拒绝执行 |
| 日志 | 控制台 JSON | 平台日志，带脱敏；加拿大驻留 |

身份细节见 `docs/backend/04-authorization.md`，存储细节见 `docs/backend/05-documents-and-files.md`，本地步骤见 `docs/backend/09-local-development.md`。上线追加的配置、表和验收用例见 `docs/backend/12-production-launch.md`。

---

## 7. 统一约定

- JSON 字段一律 camelCase，与 `apps/web/lib/types.ts` 同名；数据库列一律 snake_case。Pydantic 模型用 `alias_generator=to_camel`、`populate_by_name=True`。
- 时间一律 UTC，输出格式与 JavaScript `toISOString()` 一致：`2026-10-06T02:57:00.000Z`。
- 枚举值与 `apps/web/lib/types.ts`、`packages/domain/src/index.ts` 完全相同，大小写不变（例如 `entityType` 是小写 `corporation`，`status` 是大写 `BUILDING`）。
- ID 格式与前端 `uid()` 一致：`<前缀>_<随机串>`。前缀：`case`、`doc`、`req`、`task`、`evt`、`upl`、`ext`。上线追加 `usr`、`apr`、`hld`、`job`、`sug`、`sub`，见 `docs/backend/12-production-launch.md`。种子数据保留 `seed.ts` 里的原始 ID（如 `case-0139`、`mr-root`、`d-1`）。
- 每个写请求带客户端看到的 `version`，例外只有 `createCase`（新资源没有旧版本）。见 `docs/backend/06-audit-and-concurrency.md`。
- 错误外形固定，见 `docs/backend/03-api-contract.md` 第 2 节。

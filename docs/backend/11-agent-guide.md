# 11 实现 Agent 一页说明

记录日期：2026-10-06

## 本文依赖

`docs/backend/00-scope-and-stack.md` 到 `docs/backend/12-production-launch.md`。

## Agent 实现时禁止

见本文第 2 节，全部是硬性要求。

---

## 1. 阅读顺序

| 顺序 | 文件 | 读完要知道什么 |
| --- | --- | --- |
| 1 | `docs/backend/00-scope-and-stack.md` | 技术栈、`services/api/` 目录树、配置变量、本期不做什么 |
| 2 | `apps/web/lib/types.ts`、`apps/web/lib/data/actions.ts` | 正式数据模型和 20 个写入函数的原始行为 |
| 3 | `packages/domain/src/index.ts`、`index.test.ts` | 规则规格和对拍输入 |
| 4 | `docs/backend/07-rules-versioning.md` | 怎样移植规则、版本怎样钉住 |
| 5 | `docs/backend/01-persistence-model.md`、`02-postgres-schema.md` | 表结构、ER 图、迁移、种子 |
| 6 | `docs/backend/04-authorization.md` | 身份、可见范围、权限矩阵、检查顺序 |
| 7 | `docs/backend/06-audit-and-concurrency.md` | 写事务模板、`version`、审计 |
| 8 | `docs/backend/03-api-contract.md` | 每个端点的请求、响应、错误 |
| 9 | `docs/backend/05-documents-and-files.md` | 上传三步、存储、抽取、日志 |
| 10 | `docs/backend/10-test-catalog.md` | 验收用例和期望值 |
| 11 | `docs/backend/09-local-development.md` | 本地怎么跑 |
| 12 | `docs/backend/12-production-launch.md` | 上线行为。AI 流水线在第 8.2–8.5 节，提示词、校验和验收在第 8.6 节 |
| 13 | `docs/backend/08-frontend-cutover.md` | 前端什么时候、怎么切过来（后端完成后才做） |

`apps/api/` 只在需要了解「旧草稿为什么不对」时看，见 `docs/backend/01-persistence-model.md` 第 4 节。

## 2. 禁止事项

1. **不扩展 NestJS。** 不往 `apps/api/` 加代码，不引入 `@nestjs/*`、Prisma。也不删除 `apps/api/`。
2. **不把文件字节写入数据库。** 不建 `bytea` 列，不把 Base64 存进 `text` 或 `jsonb`。字节只在本地文件系统或 S3。
3. **不跳过 `version`。** 每个改案件或规则库的写入都要带客户端 `version`，在 `SELECT ... FOR UPDATE` 之后比较，不一致返回 409。不提供「强制保存」参数。
4. **不在未对拍时改规则结果。** `fcc_api/rules/` 的输出与 TypeScript 不一致时，改 Python，不改期望值；`tests/parity` 全部通过之前，`packages/domain/src/index.ts` 是唯一规格。
5. **Python 导入用绝对路径。** 只写 `from fcc_api.services.cases import ...`，不写 `from .cases import ...`。ruff 的 `TID252` 必须开启。
6. 第一期不修改 `apps/web/`、`packages/domain/`。上线切换按 `docs/backend/08-frontend-cutover.md` 第 7 节改前端登录和规则发布按钮。
7. 不在 UI 层做权限。每个写端点在服务层调用 `fcc_api/auth/policy.py`。
8. 不 `UPDATE`、`DELETE` 审计行。
9. 第一期（只交付 `docs/backend/00` 到 `10` 的本地行为）不接真实 Entra、扫描、抽取、筛查、提交和 LLM，本地使用 noop。上线交付必须按 `docs/backend/12-production-launch.md` 实现，生产配置禁止再用 noop 冒充已扫描或已登录。
10. 不在日志里写文件名、签名地址、证件号、`registrationNumber`、提示词正文、页正文和 `quote`。
11. 不把提示词写进路由或服务函数的字符串。只放在 `fcc_api/ai/prompts/` 的版本文件里，见 `docs/backend/12-production-launch.md` 第 8.6 节。

## 3. 实现顺序

1. 工程骨架、配置、`/api/health`、ruff。
2. `fcc_api/rules/` 与 `tests/parity`（`P-*`、`S-*` 全绿再往下）。
3. 迁移 `0001_initial`、`0002_reference_data`，种子命令。
4. 身份与 `policy`，读端点。
5. 写端点：先 `updateProfile`（验证写事务模板），再其余 19 个。
6. 文件三步与存储。
7. 规则库草稿与发布。
8. 第一期 `tests/api` 与 `tests/parity` 全绿。
9. 再按 `docs/backend/12-production-launch.md` 做迁移 `0003_launch`、Entra、异步扫描、四眼、保留和 `tests/launch`。不要在第 8 步完成前打开生产开关。

## 4. 每完成一个端点要跑的测试

在 `services/api/` 下执行：

```bash
uv run ruff check src tests            # 含 TID252，禁止相对导入
uv run pytest tests/parity             # 规则对拍，任何改动都要跑
uv run pytest tests/api/test_<该端点所属模块>.py
uv run pytest tests/api/test_authorization_matrix.py -k <该端点>
uv run pytest tests/api/test_audit.py -k <该端点>
uv run pytest tests/api/test_files.py -k "FILE_01 or FILE_02"   # 确认没有字节进库
```

动了规则或规则库时，在仓库根目录再跑一次 TypeScript 规格，确认规格本身没变：

```bash
npm test --workspace @fcc/domain
```

端点完成的标准：

- `docs/backend/10-test-catalog.md` 中与该端点相关的用例全部通过。
- 成功路径：`version + 1`（不改 version 的端点见 `docs/backend/06-audit-and-concurrency.md` 第 1 节「操作 / 改 `cases.version` / 写审计」表），恰好写入约定条数的审计。
- 每条失败路径（400、401、403、404、409、422）都有测试，且断言「库无变化」。
- `/api/openapi.json` 里该端点的请求与响应模型与 `docs/backend/03-api-contract.md` 一致。

合并前跑全量：

```bash
uv run pytest
uv run pytest tests/launch    # 上线用例，见 docs/backend/12-production-launch.md 第 14 节
```

# 04 授权

记录日期：2026-10-06

## 本文依赖

- `docs/business-flow-and-ai.md`：四个角色在流程中的动作
- `docs/workbench-usage-and-design.md`：「角色」一节和各页面的可编辑条件
- `docs/production-readiness.md`：「Identity and authorization」
- `apps/web/lib/data/actions.ts`：`requireRole`
- 页面里的现有判断：`apps/web/app/(workspace)/cases/[caseId]/page.tsx`、`details/page.tsx`、`documents/page.tsx`、`review/page.tsx`、`layout.tsx`、`apps/web/app/(workspace)/rules/page.tsx`、`apps/web/components/shell/Topbar.tsx`、`apps/web/components/case/AssistantDrawer.tsx`
- `docs/backend/03-api-contract.md`：端点清单与错误外形

## Agent 实现时禁止

- 不靠前端隐藏按钮做权限。每个端点在服务端判断角色、可见范围和案件状态。
- 不在缺少身份头时使用默认用户（`apps/api/src/app.ts` 的 `local-advisor` 做法禁止沿用）。
- 不在 `APP_ENV=production` 时启用请求头身份。
- 不把「看不到的案件」返回 403。一律 404，不泄露案件是否存在。
- 不在日志里写请求头里的 token。

---

## 1. 身份

### 1.1 本地：请求头

| 头 | 值 |
| --- | --- |
| `X-User-Id` | `users.id`，如 `u-advisor` |
| `X-User-Role` | `ADVISOR`、`OPERATIONS`、`COMPLIANCE`、`ADMIN` 之一 |

`fcc_api.auth.header_provider` 的规则：

1. 两个头缺任意一个：401 `UNAUTHENTICATED`。
2. `X-User-Id` 不存在于 `users`，或 `active = false`：401。
3. `X-User-Role` 与 `users.role` 不一致：401，`message = "Identity headers do not match a known user."`。这样本地切换用户时，前端只要同时换两个头即可，不会出现「顾问 id + 管理员角色」的组合。
4. 通过后得到 `Actor(id, role, name, team)`，注入到每个 service 函数。

`AUTH_MODE=header` 且 `APP_ENV=production` 时，进程在启动阶段退出。

### 1.2 生产：Entra ID

生产实现按 `docs/backend/12-production-launch.md` 第 3 节。`fcc_api.auth.entra_provider` 返回与第 1.1 节相同的 `Actor`。`APP_ENV=production` 且 `AUTH_MODE` 不是 `entra` 时进程拒绝启动。本地不要调用 Entra。

---

## 2. 案件可见范围

| 角色 | 能看到的案件 |
| --- | --- |
| ADVISOR | `owner_id = 自己` 或 `created_by = 自己` |
| OPERATIONS | 全部 |
| COMPLIANCE | 全部 |
| ADMIN | 全部 |

可见范围同时作用于所有派生读：案件列表与计数、合规队列、实体与人员、Document AI 文件台账、未完成任务、审计日志、规则库的 `pinnedCaseCount`。

审计日志：ADVISOR 只能看到可见案件的 `CASE` 事件，以及全部 `RULE_LIBRARY` 事件（规则库本身对所有人只读公开）。其余三个角色看到全部事件。

单个资源不可见时返回 404，与不存在相同。

**前端与本决定不一致，已选定后端为准：** 前端演示里所有用户都能看到所有案件，案件列表的「分配给我」只是筛选。`docs/production-readiness.md` 要求「Enforce case access in the API」。切换到后端后，顾问在「All」里只会看到自己的案件。`created_by` 也计入，是为了顾问替别人建案（新建页可以选别的负责人）后不会立刻看不到自己刚建的案件。

种子数据下 `u-advisor` 可见 `case-0139`、`case-0142`、`case-0131`、`case-0119`；`u-advisor-2` 可见 `case-0137`、`case-0128`、`case-0144`。

---

## 3. 案件状态锁

案件状态决定谁还能写。缩写：B = `BUILDING`，D = `DOCS_REQUESTED`，R = `RETURNED`，Q = `READY_FOR_COMPLIANCE`，A = `APPROVED`。

| 写入类别 | 包含的端点 | ADVISOR / OPERATIONS / ADMIN 可写状态 | COMPLIANCE 可写状态 | 前端依据 |
| --- | --- | --- | --- | --- |
| 结构类 | `updateParties`、`applyAiParties`、`recordAiRejection`、`updateProfile` | B、D、R | 不可写 | 股权页、详情页 `editable` 要求状态为 B/D/R 且角色不是 COMPLIANCE；助手 `editable` 排除 Q、A 和 COMPLIANCE |
| 文件与清单类 | `setChecklistStatus`、上传三步、`assignDocument`、`addCustomRequirement`、`addTasks` | B、D、R | B、D、R、Q | 文件页 `editable = status !== APPROVED && (status !== READY_FOR_COMPLIANCE \|\| role === COMPLIANCE)` |
| 任务勾选 | `toggleTask` | B、D、R | B、D、R、Q | 见下方「与前端的差异」 |
| 状态推进 | `changeStatus` | B、D、R | 不可写 | 顶栏按钮 `editable` 排除 Q、A；合规用「决定」推进 |
| 合规决定 | `complianceDecision` | 不可写 | 仅 Q | 复核页 `canDecide` |

`APPROVED` 对所有角色、所有案件写入都是只读。

违反时返回 403：

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "This case is READY_FOR_COMPLIANCE and can no longer be edited by your role.",
    "details": { "reason": "CASE_STATUS", "status": "READY_FOR_COMPLIANCE", "operation": "updateParties" }
  },
  "requestId": "req_11"
}
```

**与前端的差异，已选定后端为准：** 复核页 `tasksEditable = status !== APPROVED`，顾问在案件待合规时仍能勾选任务。`docs/workbench-usage-and-design.md` 写明「案件处于待合规时，顾问不能再改结构和清单，避免复核过程中材料被改掉」。后端把任务勾选也纳入这条规则：待合规时只有合规能勾选。

---

## 4. 端点权限矩阵

✓ 允许（仍受第 3 节状态锁和第 2 节可见范围约束），✗ 拒绝（403 `reason: "ROLE"`）。

### 4.1 写端点

| `actions.ts` 函数 | 端点 | ADVISOR | OPERATIONS | COMPLIANCE | ADMIN |
| --- | --- | --- | --- | --- | --- |
| `createCase` | `POST /api/v1/cases` | ✓ | ✓ | ✗ | ✓ |
| `updateParties` | `PUT /cases/{caseId}/parties` | ✓ | ✓ | ✗ | ✓ |
| `applyAiParties` | `POST /cases/{caseId}/ai-suggestions/accept` | ✓ | ✓ | ✗ | ✓ |
| `recordAiRejection` | `POST /cases/{caseId}/ai-suggestions/reject` | ✓ | ✓ | ✗ | ✓ |
| `updateProfile` | `PUT /cases/{caseId}/profile` | ✓ | ✓ | ✗ | ✓ |
| `setChecklistStatus` 到 `MISSING`、`REQUESTED`、`RECEIVED` | `PUT /cases/{caseId}/checklist/{requirementId}` | ✓（当前不是 `VERIFIED`） | ✓ | ✓ | ✓ |
| `setChecklistStatus` 到 `VERIFIED`、`REJECTED` | 同上 | ✗ | ✓ | ✓ | ✓ |
| `uploadDocuments` ① 申请地址 | `POST /cases/{caseId}/uploads` | ✓ | ✓ | ✓ | ✓ |
| `uploadDocuments` ③ 完成登记 | `POST /cases/{caseId}/documents` | ✓ | ✓ | ✓ | ✓ |
| `assignDocument` | `PUT /cases/{caseId}/documents/{documentId}/requirement` | ✓ | ✓ | ✓ | ✓ |
| `addCustomRequirement` | `POST /cases/{caseId}/custom-requirements` | ✓ | ✓ | ✓ | ✓ |
| `addTasks` | `POST /cases/{caseId}/tasks` | ✓ | ✓ | ✓ | ✓ |
| `toggleTask` | `POST /cases/{caseId}/tasks/{taskId}/toggle` | ✓ | ✓ | ✓ | ✓ |
| `changeStatus` | `POST /cases/{caseId}/status` | ✓ | ✓ | ✗ | ✓ |
| `complianceDecision` | `POST /cases/{caseId}/compliance-decision` | ✗ | ✗ | ✓ | ✗ |
| `startRuleDraft` | `POST /rule-library/draft` | ✗ | ✗ | ✗ | ✓ |
| `saveLibraryRule` | `POST /rule-library/draft/extras`、`PUT .../extras/{ruleId}` | ✗ | ✗ | ✗ | ✓ |
| `removeLibraryRule` | `DELETE /rule-library/draft/extras/{ruleId}` | ✗ | ✗ | ✗ | ✓ |
| `setBuiltinRetired` | `PUT /rule-library/draft/builtins/{ruleId}/retired` | ✗ | ✗ | ✗ | ✓ |
| `saveBuiltinOverride` | `PUT /rule-library/draft/builtins/{ruleId}/override` | ✗ | ✗ | ✗ | ✓ |
| `discardRuleDraft` | `DELETE /rule-library/draft` | ✗ | ✗ | ✗ | ✓ |
| `publishRuleDraft` | `POST /rule-library/draft/publish` | ✗ | ✗ | ✗ | ✓ |

`FOUR_EYES_PUBLISH=true` 时，最后一行改为 COMPLIANCE ✓（且不能是起草人）、ADMIN ✗。见 `docs/backend/12-production-launch.md` 第 4.3 节。

②「向上传地址 PUT 字节」靠签名授权，不经过角色判断；签名只在①通过权限检查后才会签发。

### 4.2 读端点

| 端点 | ADVISOR | OPERATIONS | COMPLIANCE | ADMIN |
| --- | --- | --- | --- | --- |
| `GET /me`、`GET /users` | ✓ | ✓ | ✓ | ✓ |
| `GET /cases`、`GET /cases/{caseId}`、`GET /cases/{caseId}/audit` | ✓（可见范围） | ✓ | ✓ | ✓ |
| `GET /compliance/queue` | ✓（可见范围） | ✓ | ✓ | ✓ |
| `GET /entities` | ✓（可见范围） | ✓ | ✓ | ✓ |
| `GET /documents` | ✓（可见范围） | ✓ | ✓ | ✓ |
| `GET .../documents/{documentId}/content-url`、`.../extraction` | ✓（可见范围） | ✓ | ✓ | ✓ |
| `GET /audit` | ✓（第 2 节规则） | ✓ | ✓ | ✓ |
| `GET /tasks` | ✓（可见范围） | ✓ | ✓ | ✓ |
| `GET /rule-library` | ✓ | ✓ | ✓ | ✓ |
| `POST /rule-library/evaluate`，`target = PUBLISHED` | ✓ | ✓ | ✓ | ✓ |
| `POST /rule-library/evaluate`，`target = DRAFT` | ✗ | ✗ | ✗ | ✓ |
| `GET /health` | 无需身份 | | | |

合规队列对所有角色开放只读，与页面现状一致（非合规角色会看到「Viewing as … — switch to Compliance to decide」）。

---

## 5. 必须写死的几条规则

1. **Advisor 不能把清单项标成 `VERIFIED`。** 也不能标 `REJECTED`，也不能修改当前已是 `VERIFIED` 的项（否则可以先改回 `RECEIVED` 绕过核验）。返回 403，`reason: "ROLE"` 或 `reason: "ITEM_STATUS"`。前端依据：文件页 `statusOptions` 只给顾问 `MISSING`、`REQUESTED`、`RECEIVED`。上传和挂接不会把 `VERIFIED` 降级（`actions.ts` 已如此），所以顾问上传文件不受影响。
2. **Advisor 不能改 `APPROVED` 案件。** 实际上 `APPROVED` 对所有角色都只读，见第 3 节。
3. **只有 Compliance 能批准或退回。** `complianceDecision` 只允许 COMPLIANCE，且案件必须是 `READY_FOR_COMPLIANCE`。
4. **发布规则的人取决于 `FOUR_EYES_PUBLISH`。** `false`（本地默认）时七个规则库写入都只允许 ADMIN，COMPLIANCE 发布返回 403 `ROLE`。`true`（生产强制）时起草仍只允许 ADMIN，发布只允许 COMPLIANCE，且发布人必须不是草稿的 `started_by`，并带齐批准记录。见 `docs/backend/12-production-launch.md` 第 4 节。

`docs/production-readiness.md` 要求的四眼发布和 Compliance 签署按 `docs/backend/12-production-launch.md` 第 4 节实现。本地默认关闭，是为了让 `docs/backend/10-test-catalog.md` 的 T-AUTH-11 保持「COMPLIANCE 发布得到 403」。

**第 4 条在 `FOUR_EYES_PUBLISH=false` 时与前端不一致，已按已定决策选定 ADMIN：** `actions.ts` 的 `publishRuleDraft` 调用 `requireRole("COMPLIANCE")`，规则库页只给合规显示「Publish draft」，页面说明也写「Admin drafts a new version. Compliance publishes it.」。本地后端只有 ADMIN 可发布，COMPLIANCE 调用返回 403。生产打开四眼后，按钮回到 Compliance，并且发布人不能是起草人。前端切换见 `docs/backend/08-frontend-cutover.md`。

---

## 6. 判断顺序与状态码

一个写请求按以下顺序检查，遇到第一个失败就返回：

| 顺序 | 检查 | 失败时 |
| --- | --- | --- |
| 1 | 身份头有效 | 401 `UNAUTHENTICATED` |
| 2 | 请求体格式（Pydantic） | 400 `VALIDATION_FAILED` |
| 3 | 案件存在且在可见范围内 | 404 `NOT_FOUND` |
| 4 | 角色允许该操作 | 403 `FORBIDDEN`，`reason: "ROLE"` |
| 5 | 案件状态允许该角色写 | 403 `FORBIDDEN`，`reason: "CASE_STATUS"` |
| 6 | 加锁后比较 `version` | 409 `VERSION_CONFLICT` |
| 7 | 需要读库的校验（子资源存在、节点结构合法、清单项属于当前清单） | 404 或 400 |
| 8 | 清单项当前状态允许该角色修改 | 403 `FORBIDDEN`，`reason: "ITEM_STATUS"` |
| 9 | 业务门禁 | 422 `GATE_FAILED` |

规则库写入没有第 3、5 步。第 4 步不依赖资源时（例如 COMPLIANCE 调 `createCase`），可以在第 3 步之前完成。

所有 401 和 403 写一条结构化日志：`request_id`、`user_id`（如有）、`role`、`operation`、`case_id`（如有）、`reason`。用于 `docs/production-readiness.md` 要求的「repeated authorization failures」告警。不写请求体。

---

## 7. 实现位置

- `fcc_api.auth.policy` 维护一张表：`operation -> (允许的角色, 每个角色可写的案件状态)`，内容就是第 3、4 节。
- service 函数开头调用 `require_case_write(actor, case_row, operation)`，不在路由里分散判断。
- `tests/api/test_authorization_matrix.py` 用参数化测试遍历第 4.1 节每一格，见 `docs/backend/10-test-catalog.md`。

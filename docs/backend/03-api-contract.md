# 03 API 契约

记录日期：2026-10-06

## 本文依赖

- `apps/web/lib/data/actions.ts`：20 个写入函数的行为，本文逐一对应
- `apps/web/lib/types.ts`、`packages/domain/src/index.ts`：请求和响应的字段名、枚举
- `apps/web/lib/insights.ts`：`analyze`、`checklistFor`、`validateDetails`，读响应里的 `insight`
- `apps/web/app/(workspace)/**/page.tsx`：每个读接口要支持的筛选
- `docs/backend/04-authorization.md`：每个端点谁能调
- `docs/backend/05-documents-and-files.md`：上传三步的细节
- `docs/backend/06-audit-and-concurrency.md`：`version` 与审计
- `docs/backend/07-rules-versioning.md`：规则库端点语义

## Agent 实现时禁止

- 不手写 OpenAPI YAML/JSON。OpenAPI 由 FastAPI 从 Pydantic 模型生成，本文只写行为和例子。
- 不新增本文没有的写端点；不把多个写入函数合并成一个通用 `PATCH /cases/{id}`。
- 不接受缺少 `version` 的写请求（`createCase` 除外）。
- 不信任客户端传来的 `actorId`、`at`、`version` 新值、`uploadedBy`、`createdBy`、`sha256`、`ruleVersion`。这些全部由服务端决定。
- 不在错误响应里回显文件名、注册号、OCR 内容、人名之外的输入原文。
- 不改动门禁和冲突的提示文字。它们与 `actions.ts` 里的 `GateError`、`ConflictError` 文字一致，前端会直接展示。

---

## 1. 通用约定

### 1.1 基础

- 前缀：`/api/v1`。健康检查在 `/health`，不带前缀。
- OpenAPI：`/api/openapi.json`，交互文档：`/api/docs`。
- JSON 字段 camelCase，与 `apps/web/lib/types.ts` 同名。时间是 `toISOString()` 格式。
- 请求头：

| 头 | 必填 | 说明 |
| --- | --- | --- |
| `X-User-Id` | 本地必填 | 见 `docs/backend/04-authorization.md` |
| `X-User-Role` | 本地必填 | `ADVISOR` / `OPERATIONS` / `COMPLIANCE` / `ADMIN` |
| `Authorization: Bearer ...` | 生产必填 | Entra 访问令牌，校验见 `docs/backend/12-production-launch.md` 第 3.2 节。本地不使用 |
| `X-Correlation-Id` | 否 | 前端一次用户操作的关联 ID，写进审计 |

- 响应头 `X-Request-Id`：服务端生成，错误体里也带同一个值。

### 1.2 写请求的版本

- 案件写入：请求体带 `version`，是客户端看到的 `CaseRecord.version`。
- 规则库写入：请求体带 `version`，是客户端看到的 `RuleLibraryResponse.version`。`DELETE` 请求把 `version` 放在查询参数。
- 版本不一致返回 409 `VERSION_CONFLICT`，整次写入不生效。见 `docs/backend/06-audit-and-concurrency.md`。

### 1.3 案件写入的统一响应 `CaseDetailResponse`

所有案件写入成功后返回最新的案件和派生结果，前端用它整体替换本地状态：

```json
{
  "case": { "...": "CaseRecord，字段与 apps/web/lib/types.ts 完全相同" },
  "insight": {
    "ownershipIssues": [{ "code": "OWNERSHIP_TOTAL", "partyId": "pr-root", "message": "Pacific Rim Ventures LP ownership totals 91%." }],
    "detailsGaps": [{ "field": "province", "message": "Select the province or territory of registration." }],
    "checklist": [{ "id": "naaf", "section": "Entity Formation & Authorization", "name": "New Account Application Form (NAAF)", "conditional": false, "source": "FCC guide §5.2", "reason": "Required for every new account. Include the CRA Business Number when applicable.", "partyIds": [], "custom": false }],
    "collected": 4,
    "stage": "DOCUMENTS",
    "blocker": "9 documents outstanding",
    "identify": ["mr-alice", "mr-david"],
    "effective": { "mr-root": 100, "mr-alice": 40, "mr-harbour": 35, "mr-raj": 24.5, "mr-mei": 10.5, "mr-david": 25 },
    "openTasks": 0
  }
}
```

`insight` 由 `fcc_api.rules.insight.analyze` 计算，语义与 `apps/web/lib/insights.ts` 的 `analyze` 相同，两处 JSON 化差异：

- `identify` 是节点 id 数组（前端是 `Party[]`，切换时按 id 到 `case.parties` 取节点）。
- `effective` 是对象（前端是 `Map`）。

`case` 的内容：

- `checklist[requirementId].documentIds` 由文件的 `requirementId` 推出，按 `uploadedAt` 升序。
- `documents[]` 在 `CaseDocument` 原字段外多三个扩展字段：`sha256`（可空）、`storageState`、`scanStatus`。
- `parties` 按 `position` 排序。
- 不返回 `createdBy`、`submittedAt` 之外的后端内部列。`submittedAt` 作为扩展字段返回（可空）。

### 1.4 案件的状态锁

写入前除了角色，还要看案件状态。规则在 `docs/backend/04-authorization.md` 第 3 节，违反时返回 403，`details.reason = "CASE_STATUS"`。

---

## 2. 错误

所有错误同一外形：

```json
{
  "error": { "code": "<错误码>", "message": "<给人看的英文句子>", "details": {} },
  "requestId": "req_7f3k2m9q"
}
```

六个错误码用于第一期。上线增加第七个：HTTP 503，`code = "UNAVAILABLE"`，只表示存储或外部适配器不可用。见 `docs/backend/12-production-launch.md` 第 5.1 节。

| HTTP | `code` | 何时 | 对应前端现有行为 |
| --- | --- | --- | --- |
| 401 | `UNAUTHENTICATED` | 缺身份头、用户不存在或已停用、角色头与库中角色不一致 | 无（前端没有登录） |
| 403 | `FORBIDDEN` | 已识别身份，但角色不允许（`reason: "ROLE"`），或案件状态不允许（`reason: "CASE_STATUS"`），或清单项当前状态不允许该角色修改（`reason: "ITEM_STATUS"`） | `requireRole` 抛出的 `Error` |
| 404 | `NOT_FOUND` | 资源不存在，**或资源存在但调用者看不到** | `throw new Error("Case not found.")` |
| 409 | `VERSION_CONFLICT` | 客户端 `version` 与库中不同 | `ConflictError` |
| 422 | `GATE_FAILED` | 业务门禁没过：送合规、批准、标记已索取文件、规则草稿前置条件、文件尚不可预览 | `GateError`，以及规则库「先开草稿」「没有草稿可发布」 |
| 400 | `VALIDATION_FAILED` | 请求格式或字段值不合法（FastAPI 默认的 422 校验错误统一改写成 400） | 无 |

`VALIDATION_FAILED` 用 400，是为了和 `GATE_FAILED` 的 422 在状态码上就能区分。

### 2.1 例子

401 未登录：

```json
{
  "error": { "code": "UNAUTHENTICATED", "message": "Sign in to continue.", "details": {} },
  "requestId": "req_01"
}
```

403 角色不允许（Advisor 把清单项标成 VERIFIED）：

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "Your role cannot mark checklist items as VERIFIED or REJECTED.",
    "details": { "reason": "ROLE", "role": "ADVISOR", "operation": "setChecklistStatus" }
  },
  "requestId": "req_02"
}
```

403 案件状态不允许（编辑已批准案件）：

```json
{
  "error": {
    "code": "FORBIDDEN",
    "message": "This case is APPROVED and can no longer be edited.",
    "details": { "reason": "CASE_STATUS", "status": "APPROVED", "operation": "updateParties" }
  },
  "requestId": "req_03"
}
```

404 不存在或不可见：

```json
{
  "error": { "code": "NOT_FOUND", "message": "Case not found.", "details": { "resource": "case", "id": "case-0139" } },
  "requestId": "req_04"
}
```

409 版本冲突（案件）。`message` 与 `ConflictError` 一字不差：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "This case was changed by someone else. Reload to see the latest version before saving again.",
    "details": { "resource": "case", "id": "case-0139", "clientVersion": 9, "currentVersion": 10 }
  },
  "requestId": "req_05"
}
```

409 版本冲突（规则库）：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "The rule library was changed by someone else. Reload to see the latest version before saving again.",
    "details": { "resource": "ruleLibrary", "clientVersion": 3, "currentVersion": 4 }
  },
  "requestId": "req_06"
}
```

422 门禁不过。`message` 与 `changeStatus` 里的 `GateError` 一字不差：

```json
{
  "error": {
    "code": "GATE_FAILED",
    "message": "Ownership structure is incomplete.",
    "details": {
      "gate": "OWNERSHIP",
      "issues": [
        { "code": "OWNERSHIP_TOTAL", "partyId": "pr-root", "message": "Pacific Rim Ventures LP ownership totals 91%." },
        { "code": "ENTITY_LEAF", "partyId": "pr-gp", "message": "Pacific Rim GP Inc. must disclose an owner or controller." }
      ]
    }
  },
  "requestId": "req_07"
}
```

400 请求不合法：

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "Request body is invalid.",
    "details": { "fields": [{ "path": "parties[2].ownershipPercent", "message": "Input should be less than or equal to 100" }] }
  },
  "requestId": "req_08"
}
```

### 2.2 门禁文字表

| `details.gate` | `message` | 来源 |
| --- | --- | --- |
| `OWNERSHIP` | `Ownership structure is incomplete.` | `actions.ts` `changeStatus` |
| `DETAILS` | `Account details are incomplete.` | 同上 |
| `CHECKLIST` | `All checklist items must be collected first.` | 同上 |
| `TASKS` | `Resolve open follow-up tasks first.` | 同上 |
| `NO_DRAFT` | `Start a draft before removing a rule.` | `actions.ts` `removeLibraryRule` |
| `NO_DRAFT` | `There is no draft to publish.` | `actions.ts` `publishRuleDraft` |
| `NO_DRAFT` | `There is no draft to discard.` | 后端新增 |
| `RULE_PARITY` | `Rule engine parity check failed; publishing is blocked.` | 后端新增，见 07 |
| `FILE_NOT_READY` | `This file is not available until scanning has finished.` | 后端新增，见 05 |
| `FILE_NOT_STORED` | `The original file is not stored for this document.` | 后端新增，见 05 |
| `STATUS` | `The case is already in this status.` | 后端新增 |

门禁按表中顺序检查 `OWNERSHIP`、`DETAILS`、`CHECKLIST`、`TASKS`，遇到第一个不过就返回，与 `changeStatus` 的抛出顺序相同。

---

## 3. 写端点（与 `actions.ts` 一一对应）

总表：

| # | `actions.ts` 函数 | 方法与路径 | 成功码 | 审计动作 |
| --- | --- | --- | --- | --- |
| 1 | `createCase` | `POST /api/v1/cases` | 201 | `CASE_CREATED`（+ 可选 `AI_SUGGESTION`） |
| 2 | `updateParties` | `PUT /api/v1/cases/{caseId}/parties` | 200 | `OWNERSHIP_UPDATED` |
| 3 | `applyAiParties` | `POST /api/v1/cases/{caseId}/ai-suggestions/accept` | 200 | `AI_SUGGESTION`（accepted = true） |
| 4 | `recordAiRejection` | `POST /api/v1/cases/{caseId}/ai-suggestions/reject` | 200 | `AI_SUGGESTION`（accepted = false） |
| 5 | `updateProfile` | `PUT /api/v1/cases/{caseId}/profile` | 200 | `PROFILE_UPDATED` |
| 6 | `setChecklistStatus` | `PUT /api/v1/cases/{caseId}/checklist/{requirementId}` | 200 | `CHECKLIST_UPDATED` |
| 7 | `uploadDocuments` | ① `POST /api/v1/cases/{caseId}/uploads` ② 向返回的地址 `PUT` 字节 ③ `POST /api/v1/cases/{caseId}/documents` | ① 201 ③ 201 | ③ `DOCUMENT_UPLOADED` |
| 8 | `assignDocument` | `PUT /api/v1/cases/{caseId}/documents/{documentId}/requirement` | 200 | `CHECKLIST_UPDATED` |
| 9 | `addCustomRequirement` | `POST /api/v1/cases/{caseId}/custom-requirements` | 201 | `REQUIREMENT_ADDED` |
| 10 | `addTasks` | `POST /api/v1/cases/{caseId}/tasks` | 201 | `TASK_UPDATED` |
| 11 | `toggleTask` | `POST /api/v1/cases/{caseId}/tasks/{taskId}/toggle` | 200 | `TASK_UPDATED` |
| 12 | `changeStatus` | `POST /api/v1/cases/{caseId}/status` | 200 | `STATUS_CHANGED` |
| 13 | `complianceDecision` | `POST /api/v1/cases/{caseId}/compliance-decision` | 200 | `COMPLIANCE_DECISION` |
| 14 | `startRuleDraft` | `POST /api/v1/rule-library/draft` | 201（新建）/ 200（已存在） | `RULE_LIBRARY` |
| 15 | `saveLibraryRule` | 新增 `POST /api/v1/rule-library/draft/extras`；编辑 `PUT /api/v1/rule-library/draft/extras/{ruleId}` | 200 | `RULE_LIBRARY` |
| 16 | `removeLibraryRule` | `DELETE /api/v1/rule-library/draft/extras/{ruleId}?version=` | 200 | `RULE_LIBRARY` |
| 17 | `setBuiltinRetired` | `PUT /api/v1/rule-library/draft/builtins/{ruleId}/retired` | 200 | `RULE_LIBRARY` |
| 18 | `saveBuiltinOverride` | `PUT /api/v1/rule-library/draft/builtins/{ruleId}/override` | 200 | `RULE_LIBRARY` |
| 19 | `discardRuleDraft` | `DELETE /api/v1/rule-library/draft?version=` | 200 | `RULE_LIBRARY` |
| 20 | `publishRuleDraft` | `POST /api/v1/rule-library/draft/publish` | 200 | `RULE_LIBRARY` |

`switchUser` 不是数据写入，不对应端点（见 `docs/backend/08-frontend-cutover.md`）。

「谁可以调用」一列简写，完整矩阵在 `docs/backend/04-authorization.md`。

### 3.1 `createCase` → `POST /api/v1/cases`

谁可以调用：ADVISOR、OPERATIONS、ADMIN。

请求（对应 `NewCaseInput`）：

```json
{
  "legalName": "Okafor Dental Professional Corporation IPP",
  "entityType": "ipp_rca",
  "jurisdiction": "Alberta",
  "registrationNumber": "CRA RPP 1453870",
  "ownerId": "u-advisor-2",
  "aiEntityType": { "suggested": "ipp_rca", "accepted": true }
}
```

校验：

- `legalName` 去首尾空白后 1–300 字符；`entityType` 属于 `ENTITY_TYPES`。
- `jurisdiction`、`registrationNumber` 0–200 字符。
- `ownerId` 是启用中的用户，角色为 ADVISOR 或 OPERATIONS（与新建页 `advisors` 下拉一致）。
- `aiEntityType` 可省略；给出时 `suggested` 属于 `ENTITY_TYPES`。

效果（同一事务）：

1. 生成 `id`、`reference`（`FCC-{年}-{序号}`）。
2. `version = 1`，`status = BUILDING`，`ruleVersion = rule_library_state.published_version`，`dueDate = now + 10 天`，`created_by = 调用者`。
3. 插入根节点：`id` 为 `p_<随机>`、`parentId = null`、`kind = ENTITY`、`legalName`、`entityType` 同案件、`ownershipPercent = 100`、四个布尔为 `false`。
4. `profile` 为 `ProfileDraft` 默认值。
5. 审计 `CASE_CREATED`，`summary = "Created case for {legalName}"`，`version = 1`。
6. 有 `aiEntityType` 时再写 `AI_SUGGESTION`：`summary = "Entity type suggested: {suggested} ({accepted ? "accepted" : "overridden"})"`，`ai = { model: "entity-classifier-demo", accepted, ruleVersion: 案件的 ruleVersion }`。

响应 201：`CaseDetailResponse`，`Location: /api/v1/cases/{id}`。

与前端的差异：`actions.ts` 的 AI 审计写的是常量 `RULE_VERSION`，后端写案件实际钉住的版本。以后端为准，理由见 `docs/backend/06-audit-and-concurrency.md`。

### 3.2 `updateParties` → `PUT /api/v1/cases/{caseId}/parties`

谁可以调用：ADVISOR、OPERATIONS、ADMIN；案件状态 BUILDING、DOCS_REQUESTED、RETURNED。

请求：

```json
{
  "version": 4,
  "parties": [
    { "id": "nw-root", "parentId": null, "kind": "ENTITY", "legalName": "Northwind Community Foundation", "entityType": "charity", "ownershipPercent": 100, "isController": false, "isSigningAuthority": false, "isUsPerson": false, "isPepHio": false },
    { "id": "nw-grace", "parentId": "nw-root", "kind": "PERSON", "legalName": "Grace Morrison", "country": "Canada", "title": "Director", "ownershipPercent": 0, "isController": true, "isSigningAuthority": true, "isUsPerson": false, "isPepHio": false },
    { "id": "p_k2j8x0a1", "parentId": "nw-root", "kind": "PERSON", "legalName": "Jane Doe", "country": "Canada", "title": "Director", "ownershipPercent": 10, "isController": true, "isSigningAuthority": true, "isUsPerson": false, "isPepHio": false }
  ],
  "summary": "Added Jane Doe under Northwind Community Foundation",
  "changes": [{ "field": "Jane Doe · Ownership", "from": "0%", "to": "10%" }]
}
```

`parties` 是完整的新节点列表，形状同 domain `Party`。`summary` 1–500 字符，`changes` 可省略（与 `updateParties(record, parties, summary, changes?)` 相同）。

校验（不合法返回 400）：

- 恰好一个 `parentId = null`；它的 `id` 等于现有根节点 `id`，`kind = ENTITY`，`entityType` 等于案件 `entityType`。
- `id` 唯一且符合 `^[A-Za-z0-9_-]{1,64}$`；`parentId` 指向列表中某个 `kind = ENTITY` 的节点；无环。
- `ownershipPercent` 0–100；`entityType`、`usTaxClass` 只能出现在 `ENTITY` 上，且 `ENTITY` 必须有 `entityType`。
- 最多 500 个节点。

**不检查** `validateOwnership`：未完成的结构允许保存。

效果：按 `id` 对比，插入、更新、删除；按数组下标写 `position`；`version + 1`；审计 `OWNERSHIP_UPDATED`，`summary`、`changes` 取请求值，`before_value` / `after_value` 由服务端计算（见 06）。

响应 200：`CaseDetailResponse`。

### 3.3 `applyAiParties` → `POST /api/v1/cases/{caseId}/ai-suggestions/accept`

谁可以调用：同 3.2。

请求：

```json
{
  "version": 3,
  "parties": [ "...完整的新节点列表，规则同 3.2..." ],
  "summary": "Accepted 4 parties extracted from Shareholder_Register_2026.pdf",
  "model": "doc-extract-demo"
}
```

`model` 必须在 `AI_MODEL_ALLOWLIST` 内。

效果：节点写入同 3.2；审计 `AI_SUGGESTION`，`ai = { model, accepted: true, ruleVersion: 案件的 ruleVersion }`。

前端「解析设立文件」确认后先调这个端点，再用返回的新 `version` 调上传完成端点把文件挂到 `formation`（与 `apps/web/app/(workspace)/cases/[caseId]/page.tsx` 的 `applyExtraction` 顺序相同）。

响应 200：`CaseDetailResponse`。

### 3.4 `recordAiRejection` → `POST /api/v1/cases/{caseId}/ai-suggestions/reject`

谁可以调用：同 3.2。

请求：

```json
{ "version": 4, "summary": "Discarded assistant proposal for Jane Doe", "model": "case-assistant-demo" }
```

效果：案件数据不变，但 `version + 1`（与 `mutateCase(..., (current) => current, ...)` 一致）；审计 `AI_SUGGESTION`，`ai.accepted = false`。

响应 200：`CaseDetailResponse`。

### 3.5 `updateProfile` → `PUT /api/v1/cases/{caseId}/profile`

谁可以调用：同 3.2。

请求：

```json
{
  "version": 5,
  "profile": { "province": "ON", "taxResidency": "MIXED", "features": ["MARGIN", "OPTIONS"], "trustedContact": true, "trustedContactName": "Helen Chen" },
  "change": { "field": "Tax residency", "from": "Canada", "to": "Mixed" }
}
```

校验：`province` 为空串或 13 个代码之一；`taxResidency` 为四个值之一或 `null`；`features` 是 `ACCOUNT_FEATURES` 的无重复子集；`trustedContact` 为布尔或 `null`；`trustedContactName` 0–200 字符。

效果：整体替换 `ProfileDraft`；`version + 1`；审计 `PROFILE_UPDATED`，`summary = "Updated {change.field 转小写}"`，`changes = [change]`。不检查股权门禁（保存数据不等于推进流程）。

响应 200：`CaseDetailResponse`。

### 3.6 `setChecklistStatus` → `PUT /api/v1/cases/{caseId}/checklist/{requirementId}`

谁可以调用：ADVISOR、OPERATIONS、ADMIN（BUILDING、DOCS_REQUESTED、RETURNED）；COMPLIANCE（除 APPROVED 外的状态）。ADVISOR 不能设 `VERIFIED`、`REJECTED`，也不能改当前为 `VERIFIED` 的项。

请求：

```json
{ "version": 9, "status": "VERIFIED" }
```

校验：`requirementId` 必须在当前 `insight.checklist` 里，否则 404（`resource: "checklistItem"`）。

效果：

- 写 `checklist_items`：`status`、`updated_at = now`、`updated_by = 调用者`。
- `version + 1`；审计 `CHECKLIST_UPDATED`，`summary = "{清单项名称}: {旧状态小写} → {新状态小写}"`，`changes = [{ field: 清单项名称, from: 旧状态, to: 新状态 }]`。旧状态没有行时为 `MISSING`。
- 清单项名称由服务端从 `insight.checklist` 取（前端传的 `requirementName` 不再需要）。

响应 200：`CaseDetailResponse`。

### 3.7 `uploadDocuments` → 三步

谁可以调用：同 3.6 的角色与状态（不含 VERIFIED 限制）。完整流程、对象键、扫描与抽取见 `docs/backend/05-documents-and-files.md`。

**① 申请上传地址** `POST /api/v1/cases/{caseId}/uploads`。不改案件、不写审计、不需要 `version`。

```json
{
  "requirementId": "formation",
  "files": [{ "fileName": "Articles_of_Incorporation_2748113.pdf", "sizeBytes": 1820000, "mimeType": "application/pdf" }]
}
```

`requirementId` 可为 `null`（未归属文件）；非空时必须在当前清单里。每批 1–20 个文件，单个不超过 `UPLOAD_MAX_BYTES`，类型属于 `UPLOAD_ALLOWED_MIME`。

响应 201：

```json
{
  "batchId": "upb_9d2c1x7k",
  "expiresAt": "2026-10-06T03:12:00.000Z",
  "slots": [{
    "uploadId": "upl_4m1p0z8r",
    "fileName": "Articles_of_Incorporation_2748113.pdf",
    "method": "PUT",
    "url": "http://localhost:8000/api/v1/local-storage/uploads/upl_4m1p0z8r?expires=1791255120&sig=...",
    "headers": { "Content-Type": "application/pdf" }
  }]
}
```

**② 传字节**：客户端对每个 `slot.url` 发 `PUT`，带 `slot.headers`。本地是 API 自己的端点，生产是 S3 预签名地址。成功 204（本地）或 200（S3）。

**③ 完成登记** `POST /api/v1/cases/{caseId}/documents`：

```json
{ "version": 9, "batchId": "upb_9d2c1x7k", "uploadIds": ["upl_4m1p0z8r"] }
```

效果（任一文件校验失败则整批失败，返回 400 并在 `details.files` 列出原因，库里不留任何新文件行）：

1. 对每个上传槽：确认对象存在、实际大小等于声明大小、魔数与声明类型一致、计算 SHA-256、调用扫描适配器。
2. 加锁比较 `version`。
3. 每个文件插入 `case_documents`：`extraction = PROCESSING`、`storage_state = ACCEPTED`、`scan_status = CLEAN`、`uploaded_by = 调用者`。
4. 若有 `requirementId`：清单项原为 `VERIFIED` 则保持，否则设为 `RECEIVED`，更新 `updated_at`、`updated_by`（与 `actions.ts` 相同）。
5. `version + 1`；审计 `DOCUMENT_UPLOADED`，`summary = "Uploaded {文件名用 ", " 连接}"`。
6. 提交后启动抽取适配器。抽取状态变化不改 `version`。

响应 201：`CaseDetailResponse`。

### 3.8 `assignDocument` → `PUT /api/v1/cases/{caseId}/documents/{documentId}/requirement`

谁可以调用：同 3.7。

请求：

```json
{ "version": 9, "requirementId": "beneficial-owner" }
```

校验：文件属于该案件；`requirementId` 在当前清单里。

效果：文件 `requirementId` 改为目标；目标清单项原为 `VERIFIED` 则保持，否则设为 `RECEIVED`；`version + 1`；审计 `CHECKLIST_UPDATED`，`summary = "Linked {文件名} to {清单项名称}"`。已挂在其他清单项上的文件也允许改挂，原清单项状态不变（`documentIds` 由文件推出，不会残留）。

响应 200：`CaseDetailResponse`。

### 3.9 `addCustomRequirement` → `POST /api/v1/cases/{caseId}/custom-requirements`

谁可以调用：同 3.7。

请求：

```json
{ "version": 9, "name": "Certified translation of the trust deed" }
```

效果：插入 `custom_requirements`（`id = req_<随机>`）；`version + 1`；审计 `REQUIREMENT_ADDED`，`summary = "Added additional requirement “{name}”"`。

响应 201：`CaseDetailResponse`。

### 3.10 `addTasks` → `POST /api/v1/cases/{caseId}/tasks`

谁可以调用：同 3.7。

请求：

```json
{
  "version": 9,
  "source": "AI",
  "tasks": [
    { "title": "Signer name differs from ownership graph", "requirementId": "resolution", "partyId": "mr-alice" },
    { "title": "No ID evidence for David Okoye", "requirementId": "identity", "partyId": "mr-david" }
  ]
}
```

校验：`source` 只能是 `AI` 或 `MANUAL`（`COMPLIANCE` 只由合规决定端点产生）；1–50 条；`title` 1–500 字符；`partyId` 若给出必须是本案件节点。

效果：每条插入 `review_tasks`，`done = false`、`created_by = 调用者`；`version + 1`；审计 `TASK_UPDATED`，`summary = "Created {n} follow-up task"`，`n ≠ 1` 时加 `s`。

响应 201：`CaseDetailResponse`。

### 3.11 `toggleTask` → `POST /api/v1/cases/{caseId}/tasks/{taskId}/toggle`

谁可以调用：ADVISOR、OPERATIONS、ADMIN（BUILDING、DOCS_REQUESTED、RETURNED）；COMPLIANCE（除 APPROVED 外）。

请求：

```json
{ "version": 17 }
```

效果：`done` 取反；变为完成时写 `done_by`、`done_at`，重新打开时清空；`version + 1`；审计 `TASK_UPDATED`，`summary = "{原来已完成 ? "Reopened" : "Completed"} task: {title}"`。

版本号保证不会因为重复点击而来回翻转：第二次点击带的是旧 `version`，会得到 409。

响应 200：`CaseDetailResponse`。

### 3.12 `changeStatus` → `POST /api/v1/cases/{caseId}/status`

谁可以调用：ADVISOR、OPERATIONS、ADMIN；当前状态 BUILDING、DOCS_REQUESTED、RETURNED。

请求：

```json
{ "version": 9, "status": "READY_FOR_COMPLIANCE", "summary": "Submitted for compliance review" }
```

`status` 只允许 `DOCS_REQUESTED`、`READY_FOR_COMPLIANCE`。`APPROVED`、`RETURNED` 只能由 3.13 产生；`BUILDING` 不能手动回退。其他值返回 400。

门禁（422 `GATE_FAILED`）：

- 目标 `READY_FOR_COMPLIANCE`：按顺序检查 `OWNERSHIP`（`validate_ownership` 有问题）、`DETAILS`（`validate_details` 有缺口）、`CHECKLIST`（`collected < checklist.length`）、`TASKS`（有未完成任务）。与 `actions.ts` 完全相同。
- 目标 `DOCS_REQUESTED`：检查 `OWNERSHIP`、`DETAILS`。`actions.ts` 不检查，但页面只在「结构和详情都过」时显示该按钮（`apps/web/app/(workspace)/cases/[caseId]/layout.tsx`）。后端把页面条件变成接口规则，以后端为准。
- 目标等于当前状态：`STATUS`。

效果：`status` 更新；目标为 `READY_FOR_COMPLIANCE` 时 `submitted_at = now`；`version + 1`；审计 `STATUS_CHANGED`，`summary` 取请求值，`changes = [{ field: "Status", from: STATUS_LABELS[旧], to: STATUS_LABELS[新] }]`。`STATUS_LABELS` 取自 `apps/web/lib/labels.ts`，如 `Docs requested`、`Ready for compliance`。

响应 200：`CaseDetailResponse`。

### 3.13 `complianceDecision` → `POST /api/v1/cases/{caseId}/compliance-decision`

谁可以调用：只有 COMPLIANCE；当前状态必须是 `READY_FOR_COMPLIANCE`（否则 403 `CASE_STATUS`）。

请求（退回）：

```json
{
  "version": 17,
  "decision": "RETURN",
  "comments": [
    { "title": "Disclose the shareholders of Pacific Rim GP Inc. down to natural persons.", "partyId": "pr-gp" },
    { "title": "LP agreement is missing Schedule B (signing pages).", "requirementId": "formation" }
  ]
}
```

请求（批准）：

```json
{ "version": 14, "decision": "APPROVE", "comments": [] }
```

校验：`RETURN` 至少 1 条意见（与复核页「Return with N comments」按钮在 0 条时禁用一致）；`APPROVE` 的 `comments` 必须为空。

门禁：`APPROVE` 前重跑 3.12 的四项门禁。原因：合规在待审期间可以改清单状态（例如标成 `REJECTED`），批准时必须仍然满足送审条件。`actions.ts` 不重跑，以后端为准。

效果（与 `actions.ts` 相同）：

- `APPROVE`：`status = APPROVED`；所有 `RECEIVED` 的清单项改为 `VERIFIED`，`updated_by = 调用者`。审计 `summary = "Approved by Compliance"`。
- `RETURN`：`status = RETURNED`；每条意见变成任务，`source = COMPLIANCE`。审计 `summary = "Returned to advisor with {n} comment"`，`n ≠ 1` 时加 `s`。
- 两者都写 `changes = [{ field: "Status", from: "Ready for compliance", to: "Approved" 或 "Returned" }]`；`version + 1`。

响应 200：`CaseDetailResponse`。

### 3.14 规则库写入的统一响应 `RuleLibraryResponse`

```json
{
  "version": 3,
  "publishedVersion": "demo-2026-10-04",
  "publishedExtras": [],
  "publishedDisabled": [],
  "publishedOverrides": {},
  "draft": {
    "version": "draft-2026-10-06",
    "extras": [{ "id": "rule_8f2k1q", "name": "Certified translation of the trust deed", "section": "Entity Formation & Authorization", "conditional": true, "source": "Internal", "reason": "Trust deed is not in English or French.", "enabled": true, "trigger": { "kind": "ENTITY", "entityTypes": ["trust"] } }],
    "disabled": ["directors"],
    "overrides": { "naaf": { "name": "New Account Application Form (NAAF) — WI", "section": "Entity Formation & Authorization", "conditional": false, "source": "FCC guide §5.2", "reason": "Required for every new account." } }
  },
  "pinnedCaseCount": 7,
  "builtins": [{ "id": "naaf", "name": "New Account Application Form (NAAF)", "section": "Entity Formation & Authorization", "trigger": "Always", "source": "FCC guide §5.2", "conditional": false }]
}
```

- 前五个字段与前端 `RuleLibraryState` 相同。
- `version`：规则库乐观锁版本。
- `pinnedCaseCount`：`ruleVersion = publishedVersion` 的可见案件数（规则库页「N cases on the published version」）。
- `builtins`：16 条内置规则的展示元数据，内容等于 `apps/web/app/(workspace)/rules/page.tsx` 的 `BUILTIN` 常量。

七个规则库写入只有 ADMIN 能调用。

### 3.15 `startRuleDraft` → `POST /api/v1/rule-library/draft`

请求：`{ "version": 1 }`

- 已有打开的草稿：200，返回现状，不改版本、不写审计（`actions.ts` 此时直接返回已有草稿版本号）。版本号仍然要匹配。
- 没有草稿：从当前发布版本复制 `extras`、`disabled`、`overrides`，`version` 命名见 07；规则库 `version + 1`；审计 `summary = "{操作者姓名} started rule draft {draft.version}"`。返回 201。

### 3.16 `saveLibraryRule` → 新增 `POST /api/v1/rule-library/draft/extras`，编辑 `PUT /api/v1/rule-library/draft/extras/{ruleId}`

请求：

```json
{
  "version": 2,
  "rule": {
    "id": "rule_8f2k1q",
    "name": "Certified translation of the trust deed",
    "section": "Entity Formation & Authorization",
    "conditional": true,
    "source": "Internal",
    "reason": "Trust deed is not in English or French.",
    "enabled": true,
    "trigger": { "kind": "ENTITY", "entityTypes": ["trust"] }
  }
}
```

校验：

- `rule.id` 符合 `^[A-Za-z0-9_-]{1,64}$`，不能等于任何内置规则 id。新增时不能与草稿已有额外规则重复；编辑时必须存在（否则 404），且与路径 `ruleId` 相同。
- `name` 去空白后 2–200 字符（规则编辑器在少于 2 字符时禁用保存）。
- `section` 属于规则页 `SECTIONS` 的五个值。
- `trigger.kind` 属于 `RuleTriggerKind`；`ENTITY` 必须有非空 `entityTypes`，`TAX` 必须有非空 `taxResidencies`，`FEATURE` 必须有 `feature`；其他 `kind` 不得带这三个字段。

效果：没有草稿时先按 3.15 建草稿（同一事务、只写一条审计）；新增追加到末尾，编辑原位替换；规则库 `version + 1`；审计 `summary = "Added draft rule “{name}”"` 或 `"Updated draft rule “{name}”"`。

### 3.17 `removeLibraryRule` → `DELETE /api/v1/rule-library/draft/extras/{ruleId}?version=2`

- 没有草稿：422 `NO_DRAFT`，`Start a draft before removing a rule.`
- 草稿里没有该规则：404（`actions.ts` 此时照样写一条审计，后端不这样做）。
- 效果：从草稿 `extras` 移除；规则库 `version + 1`；审计 `summary = "Removed draft rule “{name}”"`。

### 3.18 `setBuiltinRetired` → `PUT /api/v1/rule-library/draft/builtins/{ruleId}/retired`

请求：`{ "version": 2, "retired": true }`

- `ruleId` 必须是 16 条内置规则之一，否则 404。
- 没有草稿时先建草稿。
- 效果：`retired = true` 加入 `disabled`（去重），`false` 移出；规则库 `version + 1`；审计 `summary = "Retired builtin rule {ruleId} in the draft"` 或 `"Restored builtin rule {ruleId}"`。

### 3.19 `saveBuiltinOverride` → `PUT /api/v1/rule-library/draft/builtins/{ruleId}/override`

请求：

```json
{
  "version": 2,
  "override": { "name": "New Account Application Form (NAAF) — WI", "section": "Entity Formation & Authorization", "conditional": false, "source": "FCC guide §5.2", "reason": "Required for every new account." }
}
```

- `ruleId` 必须是内置规则，否则 404。字段规则同 3.16 的 `name`、`section`；`source`、`reason` 1–500 字符。触发条件不可改（`BuiltinOverride` 没有 `trigger`）。
- 没有草稿时先建草稿。
- 效果：`overrides[ruleId] = override`；规则库 `version + 1`；审计 `summary = "Updated builtin rule “{override.name}”"`。

### 3.20 `discardRuleDraft` → `DELETE /api/v1/rule-library/draft?version=3`

- 没有草稿：422 `NO_DRAFT`，`There is no draft to discard.`（`actions.ts` 此时照样写审计，后端拒绝）。
- 效果：草稿 `status = DISCARDED`、写 `closed_by`、`closed_at`；规则库 `version + 1`；审计 `summary = "Discarded the unpublished rule draft"`。

### 3.21 `publishRuleDraft` → `POST /api/v1/rule-library/draft/publish`

请求：`{ "version": 3 }`

- 只有 ADMIN（`FOUR_EYES_PUBLISH=false` 时，含本地默认）。`actions.ts` 要求 COMPLIANCE；第一期按已定决策改为 ADMIN，见 `docs/backend/04-authorization.md` 第 5 节。`FOUR_EYES_PUBLISH=true` 时改为只有另一名 COMPLIANCE 能发布，见 `docs/backend/12-production-launch.md` 第 4.3 节。
- 没有草稿：422 `NO_DRAFT`，`There is no draft to publish.`
- 对拍不过：422 `RULE_PARITY`。见 `docs/backend/07-rules-versioning.md`。
- 效果：新建 `rule_versions` 行（`extras` 只保留 `enabled = true`）；`rule_library_state.published_version` 指向新版本；草稿 `status = PUBLISHED`；规则库 `version + 1`；审计 `summary = "Published rule version {version}. New cases use it; existing cases keep their pinned version."`。**不修改任何案件。**

---

## 4. 读端点

所有读端点都按 `docs/backend/04-authorization.md` 第 2 节的可见范围过滤。不可见的单个资源返回 404。

### 4.1 `GET /api/v1/me`、`GET /api/v1/users`

- `/me` 返回当前 `User`：`{ id, name, email, role, team }`。
- `/users` 返回全部启用用户 `User[]`，用于负责人下拉、审计页「All users」筛选、`userName()`。

### 4.2 案件列表 `GET /api/v1/cases`

对应 `/cases` 页、仪表盘、侧栏计数。

| 参数 | 取值 | 页面来源 |
| --- | --- | --- |
| `filter` | `ALL`（默认）、`MINE`、`BUILDING`、`DOCS_REQUESTED`、`READY_FOR_COMPLIANCE`、`RETURNED`、`APPROVED` | 状态芯片；`MINE` 是 `ownerId = 当前用户` |
| `entityType` | `ENTITY_TYPES` 之一，可省略 | 实体类型下拉 |
| `q` | 字符串，可省略 | 搜索框，不区分大小写匹配 `legalName`、`reference`、`registrationNumber` 的子串 |
| `limit` | 1–500，默认 200 | |
| `cursor` | 上一页返回的 `nextCursor` | |

排序：`updatedAt` 降序。

响应：

```json
{
  "items": [{
    "id": "case-0139", "reference": "FCC-2026-0139", "version": 9,
    "legalName": "Maple Ridge Holdings Inc.", "entityType": "corporation", "status": "DOCS_REQUESTED",
    "ownerId": "u-advisor", "jurisdiction": "Ontario (OBCA)", "registrationNumber": "OBCA 2748113",
    "ruleVersion": "demo-2026-10-04", "createdAt": "...", "updatedAt": "...", "dueDate": "...", "submittedAt": null,
    "documentCount": 5,
    "insight": { "stage": "DOCUMENTS", "blocker": "9 documents outstanding", "collected": 4, "checklistTotal": 13, "openTasks": 0, "identifyCount": 2, "hasPep": true }
  }],
  "counts": { "ALL": 4, "MINE": 4, "BUILDING": 2, "DOCS_REQUESTED": 1, "READY_FOR_COMPLIANCE": 0, "RETURNED": 0, "APPROVED": 1 },
  "nextCursor": null
}
```

上例以 `u-advisor`（ADVISOR）身份查询种子数据：顾问只看得到自己负责或自己创建的 4 笔案件。以 OPERATIONS 身份查询时 `ALL` 为 7，`MINE` 为 0。

`counts` 只按可见范围计算，不受 `entityType`、`q` 影响（与页面芯片上的数字算法一致）。

### 4.3 案件详情 `GET /api/v1/cases/{caseId}`

响应：`CaseDetailResponse`（第 1.3 节）。用于案件页骨架、股权图、账户详情、文件清单、复核页。

### 4.4 案件审计 `GET /api/v1/cases/{caseId}/audit`

响应：`{ "items": AuditEvent[] }`，最新在前。复核页「Case history」使用。`AuditEvent` 字段见 `docs/backend/06-audit-and-concurrency.md` 第 3 节。

### 4.5 合规队列 `GET /api/v1/compliance/queue`

| 参数 | 取值 |
| --- | --- |
| `status` | `READY_FOR_COMPLIANCE`（默认）、`RETURNED`、`APPROVED` |

排序：`submittedAt` 升序，`submittedAt` 为空的用 `updatedAt`。

**前端与业务文档不一致，已选定业务文档为准：** `docs/workbench-usage-and-design.md` 写「按提交时间从早到晚排」，而 `apps/web/app/(workspace)/compliance/page.tsx` 按 `updatedAt` 升序排，再从审计里找 `summary` 以 `Submitted` 开头的事件当提交时间。后端用 `cases.submitted_at`，不解析审计文字。

响应：

```json
{
  "items": [{
    "id": "case-0137", "reference": "FCC-2026-0137", "legalName": "Thompson Family Trust", "entityType": "trust",
    "status": "READY_FOR_COMPLIANCE", "version": 14, "ownerId": "u-advisor-2", "dueDate": "...", "updatedAt": "...",
    "submittedAt": "...", "waitingSince": "...",
    "identifyCount": 3, "hasPep": false, "collected": 5, "checklistTotal": 5, "openTasks": 0
  }],
  "counts": { "READY_FOR_COMPLIANCE": 1, "RETURNED": 1, "APPROVED": 1 }
}
```

### 4.6 实体与人员 `GET /api/v1/entities`

| 参数 | 取值 | 含义（与 `apps/web/app/(workspace)/entities/page.tsx` 相同） |
| --- | --- | --- |
| `kind` | `ALL`（默认）、`PERSON`、`ENTITY`、`PEP`、`US` | `PEP` 是 `isPepHio`，`US` 是 `isUsPerson`，其余按 `kind` |
| `q` | 字符串 | 匹配节点 `legalName` 或案件 `legalName` |

只返回非根节点（`parentId` 不为空）。

```json
{
  "items": [{
    "caseId": "case-0139", "caseReference": "FCC-2026-0139", "caseLegalName": "Maple Ridge Holdings Inc.",
    "party": { "id": "mr-david", "parentId": "mr-root", "kind": "PERSON", "legalName": "David Okoye", "country": "Canada", "title": "Officer", "ownershipPercent": 25, "isController": false, "isSigningAuthority": false, "isUsPerson": false, "isPepHio": true },
    "parentName": "Maple Ridge Holdings Inc.",
    "identify": true,
    "effective": 25
  }],
  "counts": { "ALL": 17, "PERSON": 14, "ENTITY": 3, "PEP": 1, "US": 1 }
}
```

上例以 OPERATIONS 身份查询种子数据。`counts` 不受 `q` 影响。

### 4.7 Document AI 文件台账 `GET /api/v1/documents`

| 参数 | 取值 | 含义（与 `apps/web/app/(workspace)/document-ai/page.tsx` 相同） |
| --- | --- | --- |
| `filter` | `ALL`（默认）、`PROCESSING`、`UNASSIGNED` | `PROCESSING` 是 `extraction = PROCESSING`；`UNASSIGNED` 是 `requirementId` 为空 |

排序：`uploadedAt` 降序。

```json
{
  "items": [{
    "document": { "id": "d-5", "requirementId": "margin", "fileName": "Margin_Agreement_signed.pdf", "sizeBytes": 420000, "mimeType": "application/pdf", "uploadedBy": "u-ops", "uploadedAt": "...", "extraction": "EXTRACTED", "sha256": null, "storageState": "METADATA_ONLY", "scanStatus": "NOT_APPLICABLE" },
    "caseId": "case-0139", "caseReference": "FCC-2026-0139", "caseLegalName": "Maple Ridge Holdings Inc.",
    "requirementName": "Margin agreement"
  }],
  "counts": { "ALL": 17, "PROCESSING": 0, "UNASSIGNED": 1 }
}
```

`requirementName` 按该案件钉住版本算出的清单取名；清单里已没有该项时为 `null`。

### 4.8 文件预览与抽取结果

- `GET /api/v1/cases/{caseId}/documents/{documentId}/content-url` → `{ "url", "expiresAt", "mimeType", "fileName" }`。种子文件返回 422 `FILE_NOT_STORED`；扫描未完成返回 422 `FILE_NOT_READY`。
- `GET /api/v1/cases/{caseId}/documents/{documentId}/extraction?include=pages` → 最近一次抽取：

```json
{
  "extraction": { "id": "ext_1", "status": "EXTRACTED", "adapter": "noop", "model": null, "pageCount": 0, "startedAt": "...", "finishedAt": "...", "errorCode": null },
  "fields": [{ "groupKey": "x1", "fieldKey": "legalName", "value": "Jordan Blake", "confidence": 0.95, "pageNo": 2, "citation": "Shareholder_Register_2026.pdf · p.2, line 1" }],
  "pages": [{ "pageNo": 1, "text": "..." }]
}
```

`pages` 只在 `include=pages` 时返回。从未抽取过时 `extraction` 为 `null`。

### 4.9 审计日志 `GET /api/v1/audit`

| 参数 | 取值 | 页面来源 |
| --- | --- | --- |
| `action` | `AuditAction` 之一 | 「All actions」下拉 |
| `caseId` | 案件 id，或 `rule-library` | 「All cases」下拉 |
| `actorId` | 用户 id | 「All users」下拉 |
| `limit` | 1–500，默认 200 | |
| `cursor` | 上一页的 `nextCursor` | |

排序：`seq` 降序（最新在前）。响应 `{ "items": AuditEvent[], "nextCursor": "..." }`。

### 4.10 规则库 `GET /api/v1/rule-library`

响应：`RuleLibraryResponse`（3.14）。所有角色可读，包括草稿内容。

### 4.11 规则测试器 `POST /api/v1/rule-library/evaluate`

只读计算，不写库、不需要 `version`。对应规则库页右侧「Rule tester」。

```json
{
  "target": "DRAFT",
  "input": { "entityType": "trust", "taxResidency": "US", "features": ["MARGIN"], "trustedContact": false, "usPerson": true, "pep": false }
}
```

- `target`：`PUBLISHED`（默认）或 `DRAFT`。`DRAFT` 只有 ADMIN 可用（页面里只有管理员预览草稿），且必须有草稿。
- 服务端按页面同样的方式造一个测试案件：根实体 + 一个持股 100%、控制且签字的自然人，`province = ON`。
- 响应 `{ "ruleVersion": "...", "requirements": Requirement[] }`。

### 4.12 未完成任务 `GET /api/v1/tasks`

| 参数 | 取值 |
| --- | --- |
| `done` | `false`（默认）或 `true` |
| `limit` | 1–100，默认 5 |

响应：`{ "items": [{ "task": ReviewTask, "caseId", "caseReference", "caseLegalName" }], "total": 3 }`。仪表盘「未完成任务」和顶栏任务数使用。

### 4.13 健康检查 `GET /health`

无需身份头。

```json
{ "status": "ok", "service": "fcc-kyb-api", "database": "ok", "migration": "0002_reference_data" }
```

数据库不可达或迁移不是最新时 `status = "degraded"`，HTTP 503。

### 4.14 本地存储端点（仅 `STORAGE_BACKEND=local`）

- `PUT /api/v1/local-storage/uploads/{uploadId}?expires=&sig=`：请求体是文件字节，写入隔离目录。签名无效或过期返回 403。
- `GET /api/v1/local-storage/documents/{documentId}?expires=&sig=`：返回原件，`Content-Disposition: inline`。

这两个端点靠签名授权，不需要身份头，行为模拟 S3 预签名地址。生产不挂载。

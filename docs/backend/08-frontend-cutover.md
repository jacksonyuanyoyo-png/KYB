# 08 前端切换到后端

记录日期：2026-10-06

## 本文依赖

- `apps/web/lib/data/store.ts`、`apps/web/lib/data/hooks.ts`、`apps/web/lib/data/actions.ts`：现在的读写入口
- `apps/web/app/(workspace)/**`、`apps/web/components/**`：每个页面读了什么
- `docs/workbench-usage-and-design.md`：页面用途
- `docs/backend/03-api-contract.md`：端点与响应字段
- `docs/backend/04-authorization.md`：切换后行为会变的地方

## Agent 实现时禁止

- 本期不改前端。本文是切换时的对照表，切换工作另开任务。
- 切换时不改页面的业务判断来迁就后端；需要改的地方本文第 5 节已经列出。
- 不让同一个页面一部分数据来自本地、一部分来自 API。
- 不在前端重新实现权限。前端按钮可以继续按角色隐藏，但以 API 的 403 为准。
- 不在 API 模式下继续写 `localStorage` 的 `fcc-kyb-workbench:v1`。

---

## 1. 切换方式

新增前端环境变量（前端改动时再加）：

| 变量 | 取值 | 说明 |
| --- | --- | --- |
| `NEXT_PUBLIC_DATA_SOURCE` | `local`（默认）/ `api` | 总开关 |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | |
| `NEXT_PUBLIC_API_PAGES` | 逗号分隔的切换单元，如 `case-workspace,compliance` | 只有列出的单元走 API，其余继续用本地数据 |

切换单元（同一单元内的页面必须一起切）：

| 单元 | 页面 / 组件 | 前置端点 |
| --- | --- | --- |
| `identity` | 侧栏用户卡片、所有 `useSession()` | `GET /me`、`GET /users` |
| `case-workspace` | `/cases`、`/cases/new`、`/cases/{id}` 及其 `details`、`documents`、`review`，`AssistantDrawer`、`ParseDocumentsModal` | `GET /cases`、`GET /cases/{id}`、`GET /cases/{id}/audit`、13 个案件写端点、上传三步、预览 |
| `dashboard` | `/`、顶栏任务数与搜索、侧栏计数 | `GET /cases`、`GET /audit`、`GET /tasks`、`GET /entities` |
| `compliance` | `/compliance` | `GET /compliance/queue` |
| `entities` | `/entities` | `GET /entities` |
| `document-ai` | `/document-ai` | `GET /documents` |
| `audit` | `/audit` | `GET /audit`、`GET /cases`、`GET /users` |
| `rules` | `/rules` | `GET /rule-library`、`POST /rule-library/evaluate`、7 个规则库写端点 |

`case-workspace` 依赖 `identity`；`dashboard`、`compliance`、`entities`、`document-ai`、`audit` 依赖 `case-workspace`（否则 API 里新建的案件在这些页面找不到）。依赖单元没切时，该单元不能切。

`case-workspace` 的页面必须一起切：API 新建的案件 id 在本地 store 里不存在，反过来也一样。

还没切的页面继续读本地 store，页面顶部显示「本地演示数据」提示，因为它和数据库内容可能不同。

### 1.1 函数签名

前端新增一个 API 客户端（例如 `apps/web/lib/data/api.ts`），导出与 `actions.ts` 同名、同参数的函数，内部调端点。页面只换导入来源，不改调用方式。差异：

- 案件写入原来返回 `CaseRecord`，API 返回 `{ case, insight }`。客户端函数返回 `case`，同时把整个响应放进缓存，供 `useCase` 读取 `insight`。
- 每个案件写入从 `record.version` 取 `version` 放进请求体；规则库写入从最近一次 `RuleLibraryResponse.version` 取。
- 错误映射：`VERSION_CONFLICT` → 抛 `ConflictError`；`GATE_FAILED` → 抛 `new GateError(error.message)`；其他 → 抛 `new Error(error.message)`。现有 `useAction` 的提示逻辑不用改。
- 每次用户操作生成一个 `X-Correlation-Id`；连续两步的操作（解析文件后先接受节点再上传）共用一个。

---

## 2. 读取对照表（`store.ts` / `hooks.ts`）

| 现在的读取 | 位置 | 换成 | 读响应里的字段 |
| --- | --- | --- | --- |
| `hydrate()` | `store.ts`，由 `AppShell` 调用 | API 模式下不调用 | — |
| `useDb()` / `useSession()` 的 `user` | 所有页面 | `GET /api/v1/me` | `id`、`name`、`role`、`team` |
| `useSession()` 的 `db.users`、`userName(db, id)` | 侧栏切换用户、新建页负责人、审计页筛选、各列表头像 | `GET /api/v1/users` | `User[]` |
| `useCase(caseId)` 的 `record` | `cases/[caseId]/layout.tsx` 及四个步骤页 | `GET /api/v1/cases/{caseId}` | `case` |
| `useCase(caseId)` 的 `insight`（`analyze(record, db.ruleLibrary)`） | 同上 | 同上 | `insight` |
| `db.cases.map(analyze)` | `/cases` | `GET /api/v1/cases?filter=&entityType=&q=` | `items[]`（含 `insight.stage`、`blocker`、`collected`、`checklistTotal`）、`counts` |
| `db.cases.map(analyze)`、`db.audit.slice(0, 6)`、`db.cases.flatMap(tasks)` | `/`（仪表盘） | `GET /api/v1/cases`、`GET /api/v1/audit?limit=6`、`GET /api/v1/tasks?done=false&limit=5` | `items[].insight.stage`（结构卡住 = `OWNERSHIP` 或 `DETAILS`）、`counts`；`items`；`items`、`total` |
| `session.db.cases` 计数 | `Sidebar.tsx` | `GET /api/v1/cases?limit=1` | `counts.ALL - counts.APPROVED`、`counts.READY_FOR_COMPLIANCE` |
| 顶栏任务数 | `Topbar.tsx` | `GET /api/v1/tasks?done=false&limit=1` | `total` |
| 顶栏搜索（案件和人名） | `Topbar.tsx` | `GET /api/v1/cases?q=`、`GET /api/v1/entities?q=` | `items` |
| 合规队列 `rows`、`submittedAt()` | `/compliance` | `GET /api/v1/compliance/queue?status=` | `items[]`（含 `identifyCount`、`hasPep`、`collected`、`checklistTotal`、`openTasks`、`waitingSince`）、`counts` |
| 实体与人员 `rows` | `/entities` | `GET /api/v1/entities?kind=&q=` | `items[]`（`party`、`parentName`、`identify`、`effective`、案件信息）、`counts` |
| Document AI `rows` | `/document-ai` | `GET /api/v1/documents?filter=` | `items[]`（`document`、`requirementName`、案件信息）、`counts` |
| 审计 `db.audit.filter(...)`、`caseName()` | `/audit` | `GET /api/v1/audit?action=&caseId=&actorId=`；案件下拉 `GET /api/v1/cases?limit=500` | `items[]`；`items[].reference`、`legalName` |
| 案件历史 `db.audit.filter(caseId)` | `review/page.tsx` | `GET /api/v1/cases/{caseId}/audit` | `items[]` |
| `db.ruleLibrary`、`pinned` | `/rules` | `GET /api/v1/rule-library` | 见第 4.5 节 |
| 规则测试器 `generateRequirements(...)` | `/rules` | `POST /api/v1/rule-library/evaluate` | `requirements[]` |
| 预览 `useBlobUrl(file.blob)` | `DocumentCompare.tsx` | 刚选的文件继续用本地 blob；已上传文件用 `GET .../documents/{documentId}/content-url` | `url`（加 `#page=`） |

## 3. 写入对照表（`actions.ts`）

| `actions.ts` | 端点 | 调用位置 |
| --- | --- | --- |
| `createCase` | `POST /api/v1/cases` | `cases/new/page.tsx` |
| `updateParties` | `PUT /api/v1/cases/{caseId}/parties` | `cases/[caseId]/page.tsx`（新增、修改、删除节点） |
| `applyAiParties` | `POST /api/v1/cases/{caseId}/ai-suggestions/accept` | `cases/[caseId]/page.tsx`（解析设立文件）、`AssistantDrawer.tsx` |
| `recordAiRejection` | `POST /api/v1/cases/{caseId}/ai-suggestions/reject` | 同上 |
| `updateProfile` | `PUT /api/v1/cases/{caseId}/profile` | `details/page.tsx` |
| `setChecklistStatus` | `PUT /api/v1/cases/{caseId}/checklist/{requirementId}` | `documents/page.tsx` |
| `uploadDocuments` | `POST .../uploads` → `PUT` 字节 → `POST .../documents` | `documents/page.tsx`（按清单项上传、未归属上传）、`cases/[caseId]/page.tsx`（解析后挂 `formation`） |
| `assignDocument` | `PUT /api/v1/cases/{caseId}/documents/{documentId}/requirement` | `documents/page.tsx`（未归属文件） |
| `addCustomRequirement` | `POST /api/v1/cases/{caseId}/custom-requirements` | `documents/page.tsx` |
| `addTasks` | `POST /api/v1/cases/{caseId}/tasks` | `documents/page.tsx`（AI 预审发现转任务，`source = AI`） |
| `toggleTask` | `POST /api/v1/cases/{caseId}/tasks/{taskId}/toggle` | `review/page.tsx` |
| `changeStatus` | `POST /api/v1/cases/{caseId}/status` | `cases/[caseId]/layout.tsx`（Mark docs requested、Submit for compliance） |
| `complianceDecision` | `POST /api/v1/cases/{caseId}/compliance-decision` | `review/page.tsx` |
| `startRuleDraft` | `POST /api/v1/rule-library/draft` | `rules/page.tsx` |
| `saveLibraryRule` | `POST /api/v1/rule-library/draft/extras`（add）/ `PUT .../extras/{ruleId}`（edit） | `rules/page.tsx` |
| `removeLibraryRule` | `DELETE /api/v1/rule-library/draft/extras/{ruleId}?version=` | `rules/page.tsx` |
| `setBuiltinRetired` | `PUT /api/v1/rule-library/draft/builtins/{ruleId}/retired` | `rules/page.tsx` |
| `saveBuiltinOverride` | `PUT /api/v1/rule-library/draft/builtins/{ruleId}/override` | `rules/page.tsx` |
| `discardRuleDraft` | `DELETE /api/v1/rule-library/draft?version=` | `rules/page.tsx` |
| `publishRuleDraft` | `POST /api/v1/rule-library/draft/publish` | `rules/page.tsx` |
| `switchUser` | 无端点。本地 API 模式下改为切换请求头 `X-User-Id`、`X-User-Role`（存 `sessionStorage`），然后重新拉取数据。生产隐藏 | `Sidebar.tsx` |
| `resetDb`（`store.ts`） | 无端点。API 模式下隐藏按钮，改为提示运行 `python -m fcc_api.seed --reset` | `Sidebar.tsx` |

---

## 4. 各区域读哪个字段

### 4.1 股权图（`cases/[caseId]/page.tsx`、`OwnershipGraph`、`PartyInspector`）

| 用途 | 字段 |
| --- | --- |
| 节点与连线 | `case.parties`（按顺序） |
| 蓝色 Identify 标签、Persons to identify 列表 | `insight.identify`（节点 id），按 id 到 `case.parties` 取节点 |
| 节点上的 Effective | `insight.effective[partyId]` |
| 黄色顶条、右侧缺口说明、`explainGaps` | `insight.ownershipIssues` |
| 是否可编辑 | `case.status` + 当前角色（与现在相同，API 最终判断） |

### 4.2 账户详情（`details/page.tsx`）

| 用途 | 字段 |
| --- | --- |
| 表单值 | `case.profile` |
| 三项门禁 | `insight.detailsGaps` |
| 右侧预览 | `insight.checklist` |
| PEP / 美国人士汇总 | `case.parties` 的 `isPepHio`、`isUsPerson` |

### 4.3 文件清单（`documents/page.tsx`）

| 用途 | 字段 |
| --- | --- |
| 清单行、分组、required / if applicable 计数 | `insight.checklist`（`section`、`conditional`、`custom`、`partyIds`、`reason`、`source`） |
| 每行状态、谁何时改的 | `case.checklist[requirementId]`（`status`、`updatedAt`、`updatedBy`）；没有键即 `MISSING` |
| 每行挂的文件 | `case.checklist[requirementId].documentIds` → `case.documents` |
| 已收集数 | `insight.collected` |
| 规则版本标签 | `case.ruleVersion` |
| 未归属文件 | `case.documents` 中 `requirementId` 为空的 |
| 最近上传 | `case.documents` 按 `uploadedAt` |
| 抽取中的转圈 | `case.documents[].extraction`；需要轮询 `GET /cases/{id}`，或只轮询 `GET /documents?filter=PROCESSING` |
| 能否打开原件 | `case.documents[].storageState === "ACCEPTED"` 且 `scanStatus === "CLEAN"` |
| AI 预审输入 | `case` + `insight`（`preReview` 仍在前端模拟） |

### 4.4 复核（`review/page.tsx`）

| 用途 | 字段 |
| --- | --- |
| Follow-up tasks | `case.tasks`；`partyId` 到 `case.parties`，`requirementId` 到 `insight.checklist` |
| Readiness 四项 | `insight.ownershipIssues.length`、`insight.detailsGaps.length`、`insight.collected` 与 `insight.checklist.length`、`insight.openTasks` |
| Case history | `GET /cases/{caseId}/audit` 的 `items[]`：`action`、`summary`、`at`、`version`、`actorId`、`changes`、`ai` |
| Case record | `case.reference`、`case.version`、`case.ruleVersion`、`case.ownerId` |
| 决定条 | `case.status === "READY_FOR_COMPLIANCE"` 且角色为 COMPLIANCE |

### 4.5 审计（`/audit`）

`GET /audit` 的 `items[]`：`at`、`action`、`summary`、`caseId`（`"rule-library"` 表示规则库事件）、`actorId`、`version`、`changes[]`（`field`、`from`、`to`）、`ai`（`model`、`accepted`、`ruleVersion`）。扩展字段 `ruleVersion`、`correlationId` 可选展示。

### 4.6 规则库（`/rules`）

`GET /rule-library`：

| 用途 | 字段 |
| --- | --- |
| 版本号与草稿标签 | `publishedVersion`、`draft.version` |
| 内置规则表 | `builtins[]`，配合 `draft.disabled` 或 `publishedDisabled`、`draft.overrides` 或 `publishedOverrides` |
| 额外规则表 | `draft.extras` 或 `publishedExtras` |
| 「N cases on the published version」 | `pinnedCaseCount` |
| 下一次写入的版本号 | `version` |

---

## 5. 切换后行为会变的地方

这些是后端按已定决策或业务文档做出的选择，前端切换时要同步改页面或文案：

| 变化 | 原因 | 页面要改的 |
| --- | --- | --- |
| 本地发布规则只有 ADMIN；生产改回 COMPLIANCE 且发布人不能是起草人 | 本地见 `docs/backend/04-authorization.md` 第 5 节；生产见 `docs/backend/12-production-launch.md` 第 4.3 节 | 本地切 API 时发布按钮给 ADMIN。生产再切四眼时按钮还给 COMPLIANCE，并在发布请求里带 `approvalIds`。页头文案按当前 `FOUR_EYES_PUBLISH` 显示 |
| 顾问只看到自己负责或自己创建的案件 | `docs/production-readiness.md` | 列表、仪表盘计数会变少；「Assigned to me」芯片可保留 |
| 待合规时顾问不能勾选任务 | `docs/workbench-usage-and-design.md`「待合规时顾问不能改」 | `review/page.tsx` 的 `tasksEditable` 加上状态与角色条件 |
| 「Mark docs requested」要求结构和详情都通过 | 把按钮显示条件变成接口规则 | 无需改，按钮条件本来就是这样 |
| 批准前重跑送审门禁 | 合规在待审期间可改清单状态 | 批准可能返回 `GateError`，现有提示即可显示 |
| 合规队列按提交时间排序 | `docs/workbench-usage-and-design.md` | 删除 `submittedAt()` 对审计 `summary` 的解析，改读 `waitingSince` |
| 旧案件清单不再随新发布变化 | `docs/backend/07-rules-versioning.md` 第 4 节 | 无需改 |
| 规则库写入可能 409 | 规则库加了乐观锁 | 无需改，`ConflictError` 已有提示 |
| 删除不存在的额外规则、丢弃不存在的草稿返回错误 | 后端不为无效操作写审计 | 无需改 |
| `insight.identify` 是 id 数组、`insight.effective` 是对象 | JSON 化 | `useCase` 返回前转成现在的 `Party[]` 和 `Map`，页面不改 |
| `ChecklistItemState.documentIds` 由文件推出 | 去掉冗余 | 无需改，字段仍在 |
| 新建案件的实体类型 AI 审计记录案件钉住的规则版本 | 修正 | 无需改 |
| 上传后文件可以再次打开 | 字节进对象存储 | `DocumentCompare` 对已上传文件用 `content-url` |

## 7. 上线切换还要做的页面

第一期切换完成后再做。规格在 `docs/backend/12-production-launch.md`。

- 登录改为 Entra 授权码 + PKCE，见 12 第 3.1 节。去掉可以手填 `X-User-Id` 的演示入口。
- 规则页读 `GET /rule-approvals`，发布按钮在生产只给 COMPLIANCE，请求体带 `approvalIds`。
- 文件预览在 `FILE_NOT_READY` 时继续轮询，不把隔离中的文件显示成可打开。
- 解析设立文件调用 `POST .../ai/extract-formation`，用返回的 `entities`、`includeByDefault`、`citation` 填对照表。实体类型、预审、助手分别调用 12 第 8.4 与 8.5 节。`503` 时沿用页面上的失败提示。Apply 仍走 `ai-suggestions/accept`，并带上 `suggestionId`。
- 筛查未完成时，送审按钮展示 `Name screening is incomplete.`
- 门户是单独的 `/portal` 页面，不复用员工工作台布局。


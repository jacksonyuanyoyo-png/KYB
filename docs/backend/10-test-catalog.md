# 10 验收用例目录

记录日期：2026-10-06

## 本文依赖

- `packages/domain/src/index.test.ts`：对拍用例
- `packages/domain/src/index.ts`：期望值来源（已用 `packages/domain/dist/index.js` 实际运行得到本文的期望值）
- `apps/web/lib/data/actions.ts`：门禁、冲突、审计行为
- `apps/web/lib/data/seed.ts`：种子案件
- `docs/backend/02-postgres-schema.md` 到 `docs/backend/07-rules-versioning.md`

## Agent 实现时禁止

- 不修改本文的期望值去迁就实现。期望值不对时，先用 TypeScript 规格重新算一遍；TypeScript 也算出同样结果，才说明本文写错，再改本文。
- 不跳过、不标记 `xfail` 任何 `P-*`、`S-*` 用例。
- 不用 mock 代替数据库跑 `T-*` 用例。用 `DATABASE_URL_TEST` 指向的真实 PostgreSQL，每个用例在独立事务或重建的库里运行。
- 不让测试依赖执行顺序。

用例编号前缀：`P` 对拍，`S` 种子期望，`T-GATE` 门禁，`T-CONC` 并发，`T-CHK` 清单变化，`T-AUTH` 权限，`T-FILE` 文件，`T-RULE` 规则版本，`T-AUD` 审计，`T-TREE` 股权树。

「库变化」一栏里「无变化」的含义：该案件的 `version`、`updated_at` 不变，所有子表不变，`audit_events` 不新增行。

---

## 1. 规则对拍（`tests/parity/test_domain_parity.py`）

输入全部取自 `packages/domain/src/index.test.ts`。基准案件 `account`：

- `entityType = corporation`，`profile = { province: "ON", taxResidency: "CANADA", features: ["MARGIN"], trustedContact: false }`
- `parties`：`root`（ENTITY，corporation，100，`isController: true`）、`person`（PERSON，Alice Chen，`parentId: root`，100，控制且签字）

fixture 存在 `services/api/tests/parity/fixtures/domain_cases.json`。实现阶段应写一个 Node 脚本从 `packages/domain/dist/index.js` 导出这些期望值（先 `npm run build --workspace @fcc/domain`），不手抄。

| 编号 | 函数 | 输入 | 期望输出 |
| --- | --- | --- | --- |
| P-01 | `validate_ownership` | `account` | `[]` |
| P-02 | `generate_requirements` | `account` | id 依次为 `naaf`、`formation`、`resolution`、`beneficial-owner`、`directors`、`identity`、`margin`（含 `margin`） |
| P-03 | `persons_to_identify` | `root`；`holdco`（ENTITY，root 下 50）；`bob`（holdco 下 60）；`carol`（holdco 下 40）；`dan`（root 下 50）；`erin`（root 下 0，签字） | `["bob", "dan", "erin"]` |
| P-03b | `effective_ownership` | 同 P-03 | `{root: 100, holdco: 50, bob: 30, carol: 20, dan: 50, erin: 0}` |
| P-04 | `generate_requirements` 中 `identity` | `account` | `partyIds = ["person"]` |
| P-05a | `generate_requirements` | `account`，`taxResidency = CANADA` | `naaf`、`formation`、`resolution`、`beneficial-owner`、`directors`、`identity`、`margin`（含 `directors`，不含 `rc519`） |
| P-05b | 同上 | `taxResidency = US` | 上列 + `w9`、`rc519`（不含 `w8`） |
| P-05c | 同上 | `taxResidency = MIXED` | 上列（P-05a）+ `w8`、`rc519`（不含 `nffe`） |
| P-05d | 同上 | `taxResidency = INTERNATIONAL` | 上列（P-05a）+ `w8`、`rc519`、`nffe` |
| P-05e | 同上 | `entityType = trust` | `naaf`、`formation`、`beneficial-owner`、`identity`、`margin`（不含 `directors`） |

补充用例（不在 `index.test.ts` 里，但 `validate_profile` 和提示文字需要覆盖，期望值同样由 TypeScript 运行得出）：

| 编号 | 函数 | 输入 | 期望输出 |
| --- | --- | --- | --- |
| P-06 | `validate_profile` | `account.profile` | `[]` |
| P-07 | `validate_profile` | `province = ""`；以及 `None` | 都是 `[{code: "PROFILE_INCOMPLETE", message: "Province or territory of registration is required."}]` |
| P-08 | `validate_ownership` | `account`，`person.ownershipPercent = 60` | `[{code: "OWNERSHIP_TOTAL", partyId: "root", message: "Maple Holdings Inc. ownership totals 60%."}]`（注意是 `60%` 不是 `60.0%`） |
| P-09 | `validate_ownership` | `parties = []` | `[{code: "MISSING_ROOT", message: "A root entity is required."}]` |

---

## 2. 种子案件的期望结果（`tests/parity/test_seed_insight.py`）

对种子库里每笔案件跑 `analyze`，结果必须如下。种子命令的自检也用这张表。

| 编号 | 案件 | `ownershipIssues` | `detailsGaps` 的 `field` | 清单 id（按顺序） | `collected` | `stage` | `blocker` | `identify` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S-01 | case-0139 | 无 | 无 | `naaf` `formation` `resolution` `beneficial-owner` `directors` `identity` `pep` `margin` `options` `tcp` `w9` `w8` `rc519`（13） | 4 | `DOCUMENTS` | `9 documents outstanding` | `mr-alice`、`mr-david` |
| S-02 | case-0142 | 无 | `province`、`trustedContact` | `naaf` `formation` `resolution` `directors` `identity`（5） | 0 | `DETAILS` | `Select the province or territory of registration.` | `nw-grace`、`nw-omar`、`nw-lena` |
| S-03 | case-0137 | 无 | 无 | `naaf` `formation` `beneficial-owner` `identity` `cod-dvp`（5） | 5 | `COMPLIANCE` | `null` | `tf-robert`、`tf-emily`、`tf-james` |
| S-04 | case-0131 | `MISSING_OWNER`（`lc-root`） | `province`、`taxResidency`、`trustedContact` | `naaf` `formation` `resolution` `directors`（4） | 0 | `OWNERSHIP` | `Lakeshore Condominium Corporation No. 482 must disclose an owner or controller.` | 无 |
| S-05 | case-0128 | `OWNERSHIP_TOTAL`（`pr-root`，`Pacific Rim Ventures LP ownership totals 91%.`）、`ENTITY_LEAF`（`pr-gp`） | 无 | `naaf` `formation` `beneficial-owner` `identity` `fpl` `w8` `rc519` `nffe`（8） | 2 | `OWNERSHIP` | `3 compliance tasks open` | `pr-wei`、`pr-sophie` |
| S-06 | case-0119 | 无 | 无 | `naaf` `formation` `resolution` `identity`（4） | 4 | `DONE` | `null` | `er-chief`、`er-council` |
| S-07 | case-0144 | `ENTITY_LEAF`（`od-sponsor`） | `taxResidency`、`trustedContact` | `naaf` `formation` `beneficial-owner`（3） | 0 | `OWNERSHIP` | `Okafor Dental Professional Corporation must disclose an owner or controller.` | 无 |

附加断言：

- S-01 的 `partyIds`：`resolution` = `[mr-alice]`；`beneficial-owner`、`identity` = `[mr-alice, mr-david]`；`pep` = `[mr-david]`；`w9` = `[mr-mei]`。`effective` 中 `mr-raj = 24.5`、`mr-mei = 10.5`、`mr-david = 25`。
- S-05 的 `nffe.partyIds` = `[pr-wei, pr-sophie]`。`formation` 是 `REJECTED`，不计入 `collected`。
- S-02 说明只有控制人、持股都为 0 时不报 `OWNERSHIP_TOTAL`。

---

## 3. 门禁（`tests/api/test_gates.py`）

| 编号 | 前置 | 输入 | 期望状态码 | 期望库变化 |
| --- | --- | --- | --- | --- |
| T-GATE-01 **持股合计不是 100% 不能送合规** | 种子 case-0128（RETURNED，v17，根下持股 1 + 60 + 30 = 91） | `u-advisor-2` `POST /cases/case-0128/status {version: 17, status: READY_FOR_COMPLIANCE, summary: "Submitted for compliance review"}` | 422，`code = GATE_FAILED`，`message = "Ownership structure is incomplete."`，`details.gate = OWNERSHIP`，`details.issues[0].code = OWNERSHIP_TOTAL` | 无变化：`version = 17`、`status = RETURNED`、`submitted_at` 为空 |
| T-GATE-02 | 构造：case-0128 补齐 GP 背后自然人、把 Wei 改为 69%，其余不变（合计 100），提交成功 v18 | 同上，`version: 18` | 不再因 `OWNERSHIP` 失败（会因 `CHECKLIST` 失败，422 `gate = CHECKLIST`） | 无变化（v18） |
| T-GATE-03 | 构造：三个子节点 33.33、33.33、33.34 | `validate_ownership` | 无 `OWNERSHIP_TOTAL`（容差 0.01） | — |
| T-GATE-04 | 种子 case-0142（v4，省份空、Trusted Contact 未回答） | `u-advisor` 送合规 | 422，`message = "Account details are incomplete."`，`gate = DETAILS` | 无变化 |
| T-GATE-05 | 种子 case-0139（v9，4/13 已收集） | `u-advisor` 送合规 | 422，`message = "All checklist items must be collected first."`，`gate = CHECKLIST` | 无变化 |
| T-GATE-06 | case-0139：`u-ops` 把其余 9 项设为 `RECEIVED`，再 `addTasks` 一条 `MANUAL` 任务 | `u-advisor` 送合规 | 422，`message = "Resolve open follow-up tasks first."`，`gate = TASKS` | 无变化 |
| T-GATE-07 | 接 T-GATE-06，勾选完成该任务 | `u-advisor` 送合规 | 200 | `status = READY_FOR_COMPLIANCE`；`submitted_at` 已写；`version + 1`；新增 1 条 `STATUS_CHANGED`，`changes = [{field: "Status", from: "Docs requested", to: "Ready for compliance"}]` |
| T-GATE-08 | 种子 case-0131（只有根） | `u-advisor` `status = DOCS_REQUESTED` | 422，`gate = OWNERSHIP` | 无变化 |
| T-GATE-09 | 种子 case-0137（READY，5/5） | `u-compliance` 把 `identity` 设为 `REJECTED`（200，v15），再 `APPROVE`，`version: 15` | 422，`gate = CHECKLIST` | 第二次请求无变化 |
| T-GATE-10 | 种子 case-0137 | `u-compliance` `APPROVE`，`version: 14`，`comments: []` | 200 | `status = APPROVED`；`beneficial-owner`、`identity`、`cod-dvp` 由 `RECEIVED` 变为 `VERIFIED`，`updated_by = u-compliance`；`naaf`、`formation` 不变；1 条 `COMPLIANCE_DECISION`，`summary = "Approved by Compliance"` |
| T-GATE-11 | 种子 case-0137 | `u-compliance` `RETURN`，2 条意见 | 200 | `status = RETURNED`；新增 2 个 `source = COMPLIANCE` 的任务；`summary = "Returned to advisor with 2 comments"` |
| T-GATE-12 | 种子 case-0137 | `RETURN`，`comments: []` | 400 `VALIDATION_FAILED` | 无变化 |

---

## 4. 并发（`tests/api/test_concurrency.py`）

| 编号 | 前置 | 输入 | 期望状态码 | 期望库变化 |
| --- | --- | --- | --- | --- |
| T-CONC-01 **先后提交冲突** | 种子 case-0139 v9 | ① `u-ops` 完成一次上传（`version: 9`）② `u-advisor` `PUT /profile`（`version: 9`） | ① 201 ② 409，`message` 与 `ConflictError` 相同，`details = {clientVersion: 9, currentVersion: 10}` | ① 后 v10、1 个新文件、1 条 `DOCUMENT_UPLOADED`；② 后仍 v10，`tax_residency` 仍为 `MIXED`，审计无新增 |
| T-CONC-02 **同时提交冲突** | 种子 case-0142 v4 | 两个独立数据库连接、两个线程，用屏障同时发出：`u-advisor` `PUT /parties`（v4，新增一个节点）与 `u-ops` `PUT /profile`（v4，`province = ON`） | 恰好一个 200、一个 409 | `version = 5`；只有成功一方的修改；`audit_events` 对该案件只新增 1 行 |
| T-CONC-03 | 种子 case-0128 v17，`task-1` 未完成 | 连续两次 `POST /tasks/task-1/toggle {version: 17}` | 第一次 200，第二次 409 | `task-1.done = true`；v18；只有 1 条 `TASK_UPDATED` |
| T-CONC-04 | 规则库 `version = 1` | `u-admin` 两次 `POST /rule-library/draft/extras`，都带 `version: 1` | 第一次 200，第二次 409，`details.resource = ruleLibrary` | 草稿只有第一条规则；规则库 `version = 2`（建草稿和加规则在同一事务，只加一） |
| T-CONC-05 **不部分更新** | case-0139 v9；一批两个上传槽，第一个是真 PDF，第二个声明 PDF 实为 PNG | 完成登记 | 400，`details.files = [{uploadId: 第二个, reason: TYPE_MISMATCH}]` | 无变化：没有新文件行，`margin` 清单项不变，v9 |
| T-CONC-06 | case-0139 v9 | `updateParties` 带 `version: 9`，但新节点列表含两个根 | 400 | 无变化（校验失败不应先写入一部分节点） |

---

## 5. 清单随实体类型、税务身份等变化（`tests/api/test_checklist_rules.py`）

| 编号 | 前置 | 输入 | 期望状态码 | 期望库变化与响应 |
| --- | --- | --- | --- | --- |
| T-CHK-01 **税务身份 MIXED → CANADA** | 种子 case-0139 v9 | `u-advisor` `PUT /profile`，只把 `taxResidency` 改为 `CANADA` | 200 | `cases.tax_residency = CANADA`，v10；`insight.checklist` 变为 11 项，去掉 `w8`、`rc519`，保留 `w9`（Mei Lin 是美国人士）；`checklist_items` 中 `rc519` 的 `REQUESTED` 行**仍在库里**但不在清单中；`collected = 4` |
| T-CHK-02 | 种子 case-0139 | `taxResidency = US` | 200 | 清单含 `w9`、`rc519`，不含 `w8`、`nffe` |
| T-CHK-03 | 种子 case-0139 | `taxResidency = INTERNATIONAL` | 200 | 清单含 `w8`、`rc519`、`nffe`；`nffe.partyIds = [mr-alice, mr-david]` |
| T-CHK-04 **实体类型** | 空库加种子 | `u-advisor` 新建两笔案件：`entityType = corporation` 与 `entityType = trust` | 201 | corporation 清单 `naaf`、`formation`、`resolution`、`beneficial-owner`、`directors`；trust 清单 `naaf`、`formation`、`beneficial-owner`（无 `resolution`、`directors`；只有根节点所以无 `identity`） |
| T-CHK-05 | case-0142 补齐详情（`ON`、`CANADA`、`trustedContact = false`） | 再 `PUT /profile`：`features = [OPTIONS, FPL]`、`trustedContact = true`、`trustedContactName = "A B"` | 200 | 清单新增 `options`、`fpl`、`tcp` |
| T-CHK-06 | 种子 case-0142 | `updateParties` 把 Omar 标成 `isPepHio = true` | 200 | 清单新增 `pep`，`partyIds = [nw-omar]` |
| T-CHK-07 | 种子 case-0142 | `updateParties` 把 Grace 标成 `isUsPerson = true` | 200 | 清单新增 `w9`，`partyIds = [nw-grace]`（税务身份仍是 `CANADA`） |
| T-CHK-08 | 种子 case-0139 | `addCustomRequirement("Certified translation")` | 201 | 清单末尾新增一项，`section = "Additional Requirements"`、`custom = true`、`conditional = true` |
| T-CHK-09 | 种子 case-0139（`naaf` 为 VERIFIED） | `u-ops` 上传一个文件到 `naaf` | 201 | `naaf` 保持 `VERIFIED`；`documentIds` 变为 `[d-1, 新文件]` |
| T-CHK-10 | 种子 case-0139（`d-3` 未归属） | `assignDocument(d-3, beneficial-owner)` | 200 | `d-3.requirement_id = beneficial-owner`；`beneficial-owner` 由 `REQUESTED` 变为 `RECEIVED`；审计 `summary = "Linked Shareholder_Register_2026.pdf to Beneficial Owner Identification"` |

---

## 6. 权限（`tests/api/test_authorization_matrix.py`）

| 编号 | 输入 | 期望状态码 | 期望库变化 |
| --- | --- | --- | --- |
| T-AUTH-01 **未登录** | `GET /api/v1/cases`，不带身份头 | 401 `UNAUTHENTICATED` | — |
| T-AUTH-02 | `X-User-Id: u-advisor`、`X-User-Role: ADMIN` | 401 | — |
| T-AUTH-03 **Advisor 不能标 VERIFIED** | `u-advisor` `PUT /cases/case-0139/checklist/formation {version: 9, status: VERIFIED}` | 403，`reason = ROLE` | 无变化 |
| T-AUTH-04 | `u-advisor` 把 case-0139 的 `naaf`（VERIFIED）改为 `MISSING` | 403，`reason = ITEM_STATUS` | 无变化 |
| T-AUTH-05 | `u-advisor` 把 case-0139 的 `formation` 设为 `REJECTED` | 403，`reason = ROLE` | 无变化 |
| T-AUTH-06 | `u-ops` 把 case-0139 的 `formation` 设为 `VERIFIED` | 200 | v10，`formation = VERIFIED`，`updated_by = u-ops` |
| T-AUTH-07 **Advisor 不能改 APPROVED 案件** | `u-advisor` `PUT /cases/case-0119/parties`（v21） | 403，`reason = CASE_STATUS` | 无变化 |
| T-AUTH-08 | `u-admin` `PUT /cases/case-0119/profile`（v21） | 403，`reason = CASE_STATUS` | 无变化 |
| T-AUTH-09 **只有 Compliance 能批准或退回** | `u-advisor-2`、`u-ops`、`u-admin` 分别对 case-0137 `APPROVE` | 都是 403，`reason = ROLE` | 无变化 |
| T-AUTH-10 | `u-compliance` 对 case-0139（DOCS_REQUESTED）`APPROVE` | 403，`reason = CASE_STATUS` | 无变化 |
| T-AUTH-11 **只有 Admin 能发布规则** | 有草稿时，`u-compliance` `POST /rule-library/draft/publish` | 403，`reason = ROLE` | 规则库不变 |
| T-AUTH-12 | `u-ops` `POST /rule-library/draft` | 403 | 规则库不变 |
| T-AUTH-13 | `u-compliance` `POST /api/v1/cases` | 403 | 没有新案件 |
| T-AUTH-14 | `u-compliance` `PUT /cases/case-0137/parties`（READY） | 403，`reason = ROLE` | 无变化 |
| T-AUTH-15 | `u-advisor-2` `PUT /cases/case-0137/profile`（READY，自己负责） | 403，`reason = CASE_STATUS` | 无变化 |
| T-AUTH-16 | `u-advisor-2` `POST /cases/case-0137/tasks/{id}/toggle`（READY） | 403，`reason = CASE_STATUS` | 无变化 |
| T-AUTH-17 | `u-compliance` 对 case-0137（READY）`setChecklistStatus(identity, REJECTED)` | 200 | v15 |
| T-AUTH-18 **可见范围** | `u-advisor` `GET /cases/case-0137`（属于 `u-advisor-2`） | 404 | — |
| T-AUTH-19 | `u-advisor` `GET /cases` | 200，`counts.ALL = 4` | — |
| T-AUTH-20 | `u-advisor` 新建案件，`ownerId = u-advisor-2`，再 `GET` 它 | 201，然后 200（`created_by` 计入可见） | — |
| T-AUTH-21 | `u-advisor` `GET /entities` | 只含 case-0139、0142、0131、0119 的节点 | — |
| T-AUTH-22 | 参数化：`docs/backend/04-authorization.md` 第 4.1 节每一格 × 合适的种子案件 | 与矩阵一致 | 被拒的格子无变化 |
| T-AUTH-23 | `APP_ENV=production`、`AUTH_MODE=header` 启动应用 | 启动失败 | — |

---

## 7. 文件（`tests/api/test_files.py`）

| 编号 | 输入 | 期望状态码 | 期望库变化 |
| --- | --- | --- | --- |
| T-FILE-01 **数据库里没有文件字节** | 迁移后查询 `information_schema.columns WHERE table_schema = 'public' AND data_type = 'bytea'` | — | 0 行 |
| T-FILE-02 **数据库里没有文件字节** | 上传一个内容含唯一标记 `FCC-BYTES-MARKER-7f3c` 的 PDF 并完成登记；然后对每张表执行 `SELECT count(*) FROM t WHERE t::text LIKE '%FCC-BYTES-MARKER-7f3c%'`，以及对 Base64 编码后的前 64 字节同样搜索 | 201 | 所有表计数为 0；`case_documents.sha256` 等于测试里 `hashlib.sha256(bytes)`；`{LOCAL_STORAGE_ROOT}/accepted/{object_key}` 存在且哈希一致；`quarantine/` 下不再有该对象 |
| T-FILE-03 | 同上 | — | `object_key` 不包含文件名的任何部分，格式为 `cases/{caseId}/documents/{uploadId}/original` |
| T-FILE-04 | 申请上传 `sizeBytes = 25000001` | 400 | 没有 `upload_slots` 行 |
| T-FILE-05 | 申请上传 `mimeType = application/zip` | 400 | 没有 `upload_slots` 行 |
| T-FILE-06 | 实际写入字节数与声明不同 | 完成登记 400，`SIZE_MISMATCH` | 无变化 |
| T-FILE-07 | 上传地址过期后 `PUT` | 403 | — |
| T-FILE-08 | 申请上传地址 | 201 | 案件 `version` 不变，审计无新增 |
| T-FILE-09 | 完成登记后（noop 抽取） | — | 文件 `extraction` 由 `PROCESSING` 变为 `EXTRACTED`；1 行 `document_extractions`；案件 `version` 只因登记加了 1，抽取不再加 |
| T-FILE-10 | 种子文件 `d-1` 请求 `content-url` | 422，`gate = FILE_NOT_STORED` | — |
| T-FILE-11 | 真实上传的文件请求 `content-url`，再 `GET` 该地址 | 200，然后 200，`Content-Type: application/pdf`，内容哈希一致 | — |
| T-FILE-12 | `u-advisor` 请求 case-0137 的文件 `content-url` | 404 | — |
| T-FILE-13 | 跑完 T-FILE-02 和 T-FILE-11，收集全部日志输出 | — | 日志里没有文件名、没有 `sig=`、没有标记串、没有 `registrationNumber` 的值 |

---

## 8. 规则版本（`tests/api/test_rule_versions.py`）

| 编号 | 前置与输入 | 期望状态码 | 期望库变化 |
| --- | --- | --- | --- |
| T-RULE-01 | 跑第 1 节全部 `P-*` | 全部通过 | — |
| T-RULE-02 | `u-admin` 加一条额外规则 E（`ALWAYS`），发布 | 200 | 新 `rule_versions` 行 `published-{今天}`，`extras` 只含 E；`published_version` 指向它；草稿 `status = PUBLISHED`；7 笔种子案件的 `rule_version` 仍是 `demo-2026-10-04` |
| T-RULE-03 **规则发布不改写已有案件清单** | 接 T-RULE-02：新建案件 X（钉住 `published-{今天}`，清单含 E）；再开草稿、删除 E、发布（得到 `published-{今天}-2`）；读 X | 200 | X 的 `rule_version` 不变，`insight.checklist` **仍含 E**（前端 `publishedAdjustments` 在这里会丢掉 E，后端不能）；新建案件 Y 不含 E |
| T-RULE-04 **规则发布不改写已有案件清单** | 发布前对 `checklist_items`、`cases` 全表算一次 `md5(string_agg(t::text, '' ORDER BY ...))`，发布后再算 | 200 | 两次哈希相同；7 笔种子案件的 `insight.checklist` 与第 2 节完全相同 |
| T-RULE-05 | 草稿停用 `directors`，发布；新建 corporation 案件 | 201 | 新案件清单无 `directors`；case-0139 仍有 `directors` |
| T-RULE-06 | 草稿覆盖 `naaf.name`，发布；新建案件 | 201 | 新案件 `naaf.name` 为新名称，`id`、`partyIds` 不变；case-0139 的 `naaf.name` 不变 |
| T-RULE-07 | 草稿里加一条 `enabled = false` 的规则，发布 | 200 | 新版本 `extras` 不含它 |
| T-RULE-08 | 没有草稿时发布 | 422，`message = "There is no draft to publish."` | 无变化 |
| T-RULE-09 | 没有草稿时删除额外规则 | 422，`message = "Start a draft before removing a rule."` | 无变化 |
| T-RULE-10 | 把引擎换成故意出错的实现（测试里替换 `generate_requirements`），发布 | 422，`gate = RULE_PARITY` | 无变化 |
| T-RULE-11 | 直接对 `rule_versions` 的 `BUILTIN` 行 `UPDATE ... SET disabled = '{naaf}'` | 数据库报错 | — |
| T-RULE-12 | 有草稿时再 `POST /rule-library/draft` | 200 | 不改规则库 `version`，不写审计 |
| T-RULE-13 | `saveLibraryRule` 的 `rule.id = "naaf"` | 400 | 无变化 |
| T-RULE-14 | `POST /rule-library/evaluate`，`target = PUBLISHED`，`entityType = trust`、`taxResidency = US`、`usPerson = true` | 200 | 不写库；结果与规则库页测试器在同样输入下一致 |

---

## 9. 审计（`tests/api/test_audit.py`）

| 编号 | 输入 | 期望 |
| --- | --- | --- |
| T-AUD-01 | `UPDATE audit_events SET summary = 'x'`；`DELETE FROM audit_events`；`TRUNCATE audit_events` | 三条都报错 |
| T-AUD-02 | 参数化：20 个写入函数各成功调用一次 | 每次恰好新增约定数量的审计行；`action`、`summary` 模板与 `docs/backend/03-api-contract.md` 第 3 节一致；`actor_id` 等于调用者；`version` 等于写入后的版本 |
| T-AUD-03 | `createCase` 带 `aiEntityType: {suggested: ipp_rca, accepted: false}` | 2 行：`CASE_CREATED` 与 `AI_SUGGESTION`；后者 `summary = "Entity type suggested: ipp_rca (overridden)"`、`ai.accepted = false`、`ai.ruleVersion` 等于新案件的 `ruleVersion` |
| T-AUD-04 | 第 3–6 节所有被拒的请求 | 审计无新增 |
| T-AUD-05 | 规则库写入后 `GET /audit?caseId=rule-library` | 返回的事件 `caseId = "rule-library"`；库中 `scope = RULE_LIBRARY`、`case_id` 为空、`version` 等于规则库新版本 |
| T-AUD-06 | `applyAiParties` 的 `model = "gpt-unknown"` | 400 |
| T-AUD-07 | 带 `X-Correlation-Id: c-1` 的写入 | 审计 `correlation_id = c-1` |
| T-AUD-08 | `updateProfile` 成功 | `before_value`、`after_value` 分别是旧、新 `ProfileDraft` |

---

## 10. 股权树（`tests/api/test_parties.py`）

| 编号 | 输入 | 期望状态码 | 期望库变化 |
| --- | --- | --- | --- |
| T-TREE-01 | `updateParties` 含两个 `parentId = null` | 400 | 无变化 |
| T-TREE-02 | 不含原根节点（删根） | 400 | 无变化 |
| T-TREE-03 | A 的父是 B，B 的父是 A | 400 | 无变化 |
| T-TREE-04 | 自然人下面挂节点 | 400 | 无变化 |
| T-TREE-05 | 绕过 API，直接 `INSERT` 同一案件的第二个根 | 数据库报唯一约束错误 | — |
| T-TREE-06 | case-0128 删除 `pr-gp` | 200 | `task-1` 仍在，`party_id` 变为空；`task-2`（`pr-root`）不受影响 |
| T-TREE-07 | 保存一个持股合计 80% 的结构 | 200 | 写入成功；`insight.ownershipIssues` 含 `OWNERSHIP_TOTAL` |
| T-TREE-08 | 只改一个节点的 `title` | 200 | 其他节点行不被删除重建（任务的 `party_id` 不变） |
| T-TREE-09 | 节点顺序调换后保存 | 200 | `position` 按新顺序；`identify` 输出顺序随之变化 |

## 11. 上线用例

上线用例 `T-LAUNCH-01` 到 `T-LAUNCH-24` 在 `docs/backend/12-production-launch.md` 第 14 节。它们用第 13 节的测试开关，不替换上面的 `P-*`、`S-*`、`T-*`。第一期全绿之后再跑 `tests/launch`。

# 07 规则运行时与版本

记录日期：2026-10-06

## 本文依赖

- `packages/domain/src/index.ts`：内置规则与五个函数，**行为规格**
- `packages/domain/src/index.test.ts`：对拍用例来源
- `apps/web/lib/rules/library.ts`：`ruleApplies`、`libraryRequirements`、`publishedAdjustments`
- `apps/web/lib/insights.ts`：`checklistFor`、`validateDetails`、`toAccountProfile`、`analyze`
- `apps/web/lib/data/actions.ts`：七个规则库写入
- `apps/web/app/(workspace)/rules/page.tsx`：`BUILTIN` 展示元数据、`SECTIONS`、规则测试器
- `docs/production-readiness.md`：「Rules that require Compliance approval」
- `docs/business-flow-and-ai.md`：「后续讨论方向」里「保留当时的规则版本」
- `docs/backend/03-api-contract.md` 3.14–3.21、4.10、4.11 节

## Agent 实现时禁止

- 不在 Python 对拍通过前修改任何规则结果。对拍不过时改 Python，不改期望值。
- 不修改 `packages/domain`。规则内容要变，由人和 Compliance 先改 TypeScript 规格与测试，再同步 Python。
- 不「顺手改进」规则（例如改阈值、改 W-8 条件、改提示文字）。连提示文字的标点都要一致，因为它们会出现在门禁错误里。
- 不让发布新版本改写已有案件：不更新 `cases.rule_version`，不改 `checklist_items`，不改已发布的 `rule_versions` 行。
- 不让 FastAPI 依赖 Node 或 `@fcc/domain` 运行。TypeScript 只用于生成对拍期望值。

---

## 1. Python 规则包

`fcc_api.rules` 是纯函数包（不导入 `fastapi`、`sqlalchemy`、`fcc_api.db`）。必须移植的函数与来源：

| Python | 来源 | 说明 |
| --- | --- | --- |
| `fcc_api.rules.domain.validate_ownership` | `index.ts` `validateOwnership` | |
| `fcc_api.rules.domain.validate_profile` | `index.ts` `validateProfile` | 只用于对拍，不作门禁，见第 7 节 |
| `fcc_api.rules.domain.effective_ownership` | `index.ts` `effectiveOwnership` | 返回 `dict[str, float]`，保持插入顺序 |
| `fcc_api.rules.domain.persons_to_identify` | `index.ts` `personsToIdentify` | |
| `fcc_api.rules.domain.generate_requirements` | `index.ts` `generateRequirements` | 16 条内置规则 |
| `fcc_api.rules.details.validate_details`、`to_account_profile` | `insights.ts` | 门禁 `DETAILS` 用的是它 |
| `fcc_api.rules.library.rule_applies`、`library_requirements` | `library.ts` | |
| `fcc_api.rules.library.adjustments_for` | `library.ts` `publishedAdjustments` | 语义按第 4 节修正 |
| `fcc_api.rules.insight.checklist_for`、`is_collected`、`analyze` | `insights.ts` | |
| `fcc_api.rules.constants.RULE_VERSION` | `index.ts` | `"demo-2026-10-04"` |
| `fcc_api.rules.constants.BENEFICIAL_OWNER_THRESHOLD` | `index.ts` | `25` |

移植时容易出错的地方：

1. **顺序。** 所有输出按 `parties` 数组顺序、规则数组顺序。`index.test.ts` 断言 `personsToIdentify` 输出 `["bob", "dan", "erin"]`。
2. **数字。** 用 `float` 计算，与 JavaScript number 一致。`validateOwnership` 用 `abs(total - 100) > 0.01`，`total > 0` 才检查（只有控制人、持股都为 0 时不报合计错误）。
3. **数字转文字。** 提示文字 `"{name} ownership totals {total}%."` 里的 `total` 要按 JavaScript `String(number)` 输出：`91` 不能写成 `91.0`，`24.5` 写 `24.5`。实现一个 `js_number_str` 并单测。
4. **`effectiveOwnership` 的递归与缓存。** 父节点不存在时视为 100。
5. **规则 `applies` 条件逐字照搬**，包括 `w8` 在 `INTERNATIONAL` 或 `MIXED` 时触发、`nffe` 只在 `INTERNATIONAL` 时触发、`identity` 只要有自然人就触发、`w9` 看税务身份 `US` 或任何节点 `isUsPerson`（包括实体节点）。
6. **`toAccountProfile`。** `taxResidency` 为空当 `CANADA`，`trustedContact` 为空当 `false`。所以账户详情没填完时，列表里的「文件进度」分母也按加拿大算，与前端一致。

### 对拍通过之前

TypeScript 是规格。Python 与 TypeScript 结果不同，一律视为 Python 的缺陷。对拍测试见第 6 节和 `docs/backend/10-test-catalog.md`。

---

## 2. 三层概念

| 概念 | 存在哪里 | 能否修改 |
| --- | --- | --- |
| 内置规则实现（builtin） | Python 代码 `fcc_api.rules.domain`，按 `builtin_version` 区分 | 只能改代码，见第 3.1 节 |
| 已发布版本（published version） | `rule_versions` 一行：引用一个 `builtin_version`，加上 `extras`、`disabled`、`overrides` 快照 | 发布后不可修改 |
| 草稿（draft） | `rule_drafts` 中 `status = OPEN` 的一行 | ADMIN 可改，直到发布或丢弃 |

`rule_library_state.published_version` 指向「新案件用哪个版本」。

系统初始只有一个版本 `demo-2026-10-04`（`kind = BUILTIN`），三项调整都为空，即前端 `initialLibrary()`。

---

## 3. 四种变更

### 3.1 内置规则

16 条：`naaf`、`formation`、`resolution`、`beneficial-owner`、`directors`、`identity`、`pep`、`margin`、`options`、`cod-dvp`、`fpl`、`tcp`、`w9`、`w8`、`rc519`、`nffe`。触发条件写在代码里，规则库页面不能改（`BuiltinOverride` 没有 `trigger`）。

要改触发条件或增删内置规则，是一次代码发布，不是规则库操作：

1. 由人与 Compliance 先在 `packages/domain` 改规格和测试（不在本期）。
2. Python 新增一套实现，用新的 `builtin_version`（例如 `demo-2026-11-01`），**旧实现保留**，按版本分派：`generate_requirements(account, builtin_version=...)`。
3. 新增 Alembic 数据迁移插入新的 `BUILTIN` 行。
4. 钉在旧版本上的案件继续用旧实现。

不允许原地修改某个 `builtin_version` 的实现，否则钉在它上面的案件清单会变。

### 3.2 停用内置规则

- 草稿 `disabled` 里放内置规则 id。`setBuiltinRetired(ruleId, true)` 加入（去重），`false` 移出。
- 生效方式：`generate_requirements` 的结果里过滤掉这些 id。
- 已有案件如果对该项有 `checklist_items` 行，行保留。新版本的案件不会出现这一项。
- 只允许 16 个内置 id；额外规则不用停用，用删除或 `enabled = false`。

### 3.3 字段覆盖

- 草稿 `overrides[ruleId] = BuiltinOverride`，可改 `name`、`section`、`conditional`、`source`、`reason`。
- 生效方式：与 `checklistFor` 相同，`{ ...item, ...override }` 浅合并，`id`、`partyIds` 不变。
- 只允许内置 id 作为键。
- 被停用的规则即使有覆盖，也不出现。

### 3.4 额外规则

- 草稿 `extras: LibraryRule[]`。新增追加到末尾，编辑原位替换，删除移除。
- 触发条件 `RuleTrigger.kind`：`ALWAYS`、`ENTITY`、`TAX`、`FEATURE`、`PERSON`、`PEP`、`US_PERSON`、`TRUSTED_CONTACT`，判断逻辑与 `ruleApplies` 逐字相同。
- 生成的清单项 `partyIds` 恒为空数组。
- 草稿里可以有 `enabled = false` 的规则；发布时只保留 `enabled = true`（与 `publishRuleDraft` 相同）。
- `id` 不能与内置规则 id 冲突。

### 3.5 清单拼装顺序

与 `checklistFor` 相同：

1. 内置规则结果，去掉 `disabled`，套上 `overrides`
2. 额外规则结果
3. 案件的 `customRequirements`，`section = "Additional Requirements"`、`conditional = true`、`source = "Added by staff"`、`reason = "Additional requirement recorded on this case."`、`custom = true`

---

## 4. 新案件钉住版本，旧案件不被改写

- `createCase` 写入 `cases.rule_version = rule_library_state.published_version`，之后不再改变。
- 计算任何案件的清单：取 `rule_versions[case.rule_version]` 这一行的 `builtin_version`、`extras`、`disabled`、`overrides`。
- 发布新版本只做三件事：插入新的 `rule_versions` 行、移动 `published_version` 指针、关闭草稿。

**前端与业务文档不一致，已选定业务文档为准：** `apps/web/lib/rules/library.ts` 的 `publishedAdjustments` 只在 `ruleVersion === library.publishedVersion` 时返回调整，否则返回空。前端只保存「当前发布版本」的内容，所以：

- 案件 X 建在 `published-2026-10-06` 上，带着额外规则 E。
- 之后发布 `published-2026-10-07`。
- 在前端，案件 X 的 `ruleVersion` 不再等于 `publishedVersion`，额外规则 E 从 X 的清单里消失——清单被改写了。

这违反 `docs/business-flow-and-ai.md`「已生成清单的案件保留当时的规则版本，不因新规则自动改写历史结果」和 `docs/production-readiness.md`「Existing cases must retain the rule version under which their checklist was generated」。后端为每个已发布版本保存不可变快照，`adjustments_for` 按案件钉住的版本取快照，X 永远带着 E。验收用例见 `docs/backend/10-test-catalog.md` T-RULE-03。

---

## 5. 草稿生命周期与版本命名

| 操作 | 规则 |
| --- | --- |
| 开草稿 | 从当前已发布版本复制 `extras`、`disabled`、`overrides`（与 `blankDraft` 相同）。`base_version` 记下来源 |
| 命名 | `draft-{UTC 日期 YYYY-MM-DD}`；当天已有同名草稿（含已关闭的）则加 `-2`、`-3` |
| 自动开草稿 | `saveLibraryRule`、`setBuiltinRetired`、`saveBuiltinOverride` 在没有草稿时自动开，与 `actions.ts` 相同 |
| 不自动开 | `removeLibraryRule`、`discardRuleDraft`、`publishRuleDraft` 没有草稿时返回 422 `NO_DRAFT` |
| 丢弃 | `status = DISCARDED`，行保留 |
| 发布 | 版本号 = 草稿版本号把 `draft-` 换成 `published-`（与 `actions.ts` 相同）；若已存在则加 `-2`、`-3` |

**与前端的差异：** `actions.ts` 同一天发布两次会得到同一个 `published-YYYY-MM-DD`，两批案件钉在同名版本上却对应不同规则。后端加后缀保证版本号唯一。

同一时刻只能有一份打开的草稿（数据库部分唯一索引保证）。草稿基于的发布版本在草稿打开期间不会变化，因为只有发布草稿本身才会移动发布指针。

---

## 6. 发布前检查

`publishRuleDraft` 在同一事务里、写入前依次执行：

### 6.1 草稿内容校验（失败 400）

- `extras` 的 `id` 唯一，且都不是内置 id；每条通过 `LibraryRule` 校验。
- `disabled` 是内置 id 的子集。
- `overrides` 的键是内置 id 的子集。

### 6.2 引擎对拍（失败 422 `RULE_PARITY`）

用 `services/api/tests/parity/fixtures/domain_cases.json` 的每一条输入，跑草稿引用的 `builtin_version` 实现（不带任何调整），结果必须与期望完全相同。这些用例就是 `packages/domain/src/index.test.ts` 的同一组输入输出，另加种子案件。任何一条不同，说明部署的 Python 引擎和规格不一致，禁止发布。

fixture 格式：

```json
{
  "generatedFrom": "packages/domain/src/index.test.ts",
  "builtinVersion": "demo-2026-10-04",
  "cases": [
    {
      "name": "accepts a fully disclosed ownership structure",
      "fn": "validateOwnership",
      "input": { "parties": [ "...index.test.ts 里的 account.parties..." ] },
      "expected": []
    },
    {
      "name": "identifies indirect owners at or above 25% and controllers below it",
      "fn": "personsToIdentify",
      "input": { "parties": [ "...root、holdco、bob、carol、dan、erin..." ] },
      "expected": ["bob", "dan", "erin"]
    }
  ]
}
```

完整用例清单与期望值见 `docs/backend/10-test-catalog.md` 第 1 节。

### 6.3 草稿影响预览（不阻断，记录）

对 6.2 的每个 `generateRequirements` 输入，以及规则测试器的十种实体类型 × 四种税务身份组合，分别按「当前发布版本」和「草稿」计算清单 id，记录差异：

```json
{
  "engine": "pass",
  "builtinVersion": "demo-2026-10-04",
  "comparedTo": "demo-2026-10-04",
  "results": [
    { "name": "corporation / CANADA / MARGIN", "published": ["naaf", "formation", "resolution", "beneficial-owner", "directors", "identity", "margin"], "draft": ["naaf", "formation", "resolution", "beneficial-owner", "identity", "margin", "rule_8f2k1q"], "added": ["rule_8f2k1q"], "removed": ["directors"] }
  ]
}
```

写入新版本的 `rule_versions.parity_report`，并放进发布审计的 `after_value`。规则变化本来就是有意的，所以差异不阻断发布，但发布人和日后的审核人能看到这次发布具体改了哪些案件形态的清单。

### 6.4 批准记录与四眼

每条规则的负责人、来源、生效日、复核日、测试编号和双人批准见 `docs/backend/12-production-launch.md` 第 4 节。本地 `FOUR_EYES_PUBLISH=false`、`ALLOW_DEMO_RULES=true`，演示规则可以建案。生产两个开关分别为 `true` 和 `false`：没有未过期批准记录就不能发布差异，也不能用 `demo-2026-10-04` 创建案件。

规则判断本身仍以 domain 为准。Compliance 改口径时先改 TypeScript 规格，再增加 `builtin_version`，再走第 4 节的批准。发布不代替书面签署；书面签署落在 `rule_approvals.approval_ticket`。

---

## 7. `publishedVersion` 仍是演示版时 `publishedDisabled` 的行为

前端现状（`apps/web/lib/types.ts` 注释、`library.ts` `publishedAdjustments`、`rules/page.tsx` 测试器）：

- 只要 `publishedVersion === RULE_VERSION`（`demo-2026-10-04`），`publishedExtras`、`publishedDisabled`、`publishedOverrides` 一律被忽略，所有案件按纯内置规则出清单。
- 正常操作下这种情况里三项本来就是空的：`initialLibrary()` 给的是空数组，而 `publishRuleDraft` 一定会把 `publishedVersion` 改成 `published-*`。只有浏览器 `localStorage` 里留着旧数据时，才会出现「版本是演示版但带着停用列表」。
- 从演示版开草稿，草稿复制到的也是空的三项。

后端：

- `rule_versions` 上的 `CHECK` 约束让 `kind = BUILTIN` 的行不能带任何调整，所以「演示版带着停用列表」这种状态无法存在。
- 接口返回演示版时，`publishedExtras = []`、`publishedDisabled = []`、`publishedOverrides = {}`。
- 钉在 `demo-2026-10-04` 上的案件（全部种子案件）永远按纯内置规则出清单，任何后续发布都不影响它们。这与前端行为一致。

---

## 8. 与业务文档不一致处

`docs/business-flow-and-ai.md`「规则引擎的输入与输出」表写「国际税务身份 → W-8 系列」「国际或混合税务身份 → RC519」，`docs/workbench-usage-and-design.md` 写「混合会带出 RC519」，都没有提混合税务身份也触发 W-8。

`packages/domain/src/index.ts` 的 `w8` 在 `INTERNATIONAL` 或 `MIXED` 时都触发，`index.test.ts` 断言 `MIXED` 包含 `w8`。另外 `US` 也触发 `rc519`。

**选定 domain 为准**：它有测试，且已是前端实际行为。业务文档保持原样，此处记录差异，留给 Compliance 确认时一并处理。

`validateProfile`（domain，只查省份）与 `validateDetails`（前端，查省份、税务身份、Trusted Contact 是否回答、Trusted Contact 姓名）不一致。**选定前端 `validateDetails` 作为门禁 `DETAILS`**，因为 `changeStatus` 和页面都用它，且 `docs/workbench-usage-and-design.md` 写明三项门禁。`validate_profile` 仍按要求移植并对拍，但服务端不用它做判断。

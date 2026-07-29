# KST Runtime TOCTOU 与 Peer Diagnostics 修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 关闭快商通 runtime 构建期间的身份数据库竞态，并让旧版自动发现保留坏 peer 的脱敏类型化诊断。

**Architecture:** Registry 在一次 runtime 请求中捕获一份动态完整安装快照，并让 state reader 与 builder 共享该对象；缓存返回前和构建完成后重新捕获并与 binding baseline 比较，变化即标记 stale、清缓存并拒绝当前请求。旧版发现返回 `list` 兼容的诊断结果，组合发现继续合并这些 diagnostics，最终沿用统一优先级聚合。

**Tech Stack:** Python 3、pytest、SQLite、Windows 本地快商通双客户端适配。

## Global Constraints

- 不运行真实快商通业务任务，不读写目标 Excel。
- runtime 竞态只允许 fail-closed，不自动重试。
- Electron 只枚举当前 identity 的全部 `VISITOR*.db`；legacy 只枚举当前公司全部 shard。
- diagnostics 只保留固定安全类别与文案，不泄露路径、SQL 或身份信息。
- 普通 `list` 注入保持兼容；已有好 binding 时坏 peer 不影响 ready。

---

### Task 1: Runtime 统一捕获与多阶段一致性校验

**Files:**
- Modify: `modules/kst_local/identity_registry.py`
- Test: `tests/test_kst_identity_registry.py`

**Interfaces:**
- Consumes: `capture_installation_identity(installation, cancel_event=None) -> tuple[KstInstallationLike, fingerprint]`
- Produces: `_capture_bound_identity_unlocked(project_id, installation, cancel_event=None) -> tuple[KstInstallationLike, fingerprint]`

- [x] **Step 1: 写五阶段失败回归**

  参数化注入新数据库出现于 state 前、state 后、builder 前、builder 中和 cache 命中返回前；每种情况断言当前请求抛出 `KstIdentityMappingError`，不返回旧 runtime。

- [x] **Step 2: 验证 RED**

  Run: `.venv\Scripts\python.exe -m pytest tests\test_kst_identity_registry.py -q -k runtime_capture`

  Expected: 五个阶段均因旧实现返回不完整 runtime 而失败。

- [x] **Step 3: 实现统一捕获**

  初始捕获必须匹配 binding baseline；state reader 与 runtime builder 接收同一个 captured installation。state 完成后、cache 返回前和 builder 完成后分别重新动态捕获并比较；差异调用 `_mark_binding_stale_unlocked`、移除项目 runtime cache 并抛出。

- [x] **Step 4: 验证 GREEN 与稳定刷新**

  同一参数化测试在失败后执行稳定 `refresh()`，断言 builder 收到包含新增数据库的完整路径清单，且没有内部重试。

### Task 2: Legacy Peer Diagnostics 传播

**Files:**
- Modify: `modules/kst_local/legacy_discovery.py`
- Modify: `modules/kst_local/discovery.py`
- Test: `tests/test_kst_legacy_discovery.py`
- Test: `tests/test_kst_identity_registry.py`

**Interfaces:**
- Consumes/Produces: `KstInstallationDiscoveryResult`, 一个保持 `list` 行为并公开脱敏 `diagnostics` 元组的结果类型。

- [x] **Step 1: 写 peer diagnostics 失败回归**

  构造自动发现中一个可读公司和一个 locked/corrupt peer，分别覆盖坏 peer 排序在好 peer 前后。断言 legacy 结果携带 busy/incompatible 安全诊断，`discover_all_installations` 保留诊断。

- [x] **Step 2: 验证 RED**

  Run: `.venv\Scripts\python.exe -m pytest tests\test_kst_legacy_discovery.py tests\test_kst_identity_registry.py -q -k peer`

  Expected: 旧版非空结果没有 `diagnostics`，或组合结果丢失该类别。

- [x] **Step 3: 实现结果传播**

  `discover_legacy_installations` 在有 found 时返回 `KstInstallationDiscoveryResult(found, diagnostics=automatic_errors)`；组合发现从返回结果读取 diagnostics 并加入自己的安全诊断集。显式来源和全失败路径维持原有抛错行为。

- [x] **Step 4: 验证 registry 聚合**

  无匹配 binding 时 busy/incompatible 高于 unmatched identity；有匹配好 binding 时 health 仍为 ready。不同 peer 顺序结果一致，输出不含路径、SQL 或身份原文。

### Task 3: 完整验证、报告与提交

**Files:**
- Modify: `.superpowers/sdd/2026-07-29-kst-legacy-dual-client/task-6-integrity-fix-report.md`（按仓库规则忽略，仅本地报告）

- [x] **Step 1: 运行聚焦和 14 文件回归**

  Run: `.venv\Scripts\python.exe -m pytest <14 个 KST/GUI/Qt 测试文件> -q`

  Expected: 至少原基线 233 项全部通过。

- [x] **Step 2: 运行静态验证**

  Run: `.venv\Scripts\python.exe -m py_compile <修改的生产与测试文件>`

  Run: `git diff --check`

- [x] **Step 3: 自审要求逐项覆盖**

  核对五个 runtime 窗口、Electron/legacy 动态清单、缓存丢弃、无重试、peer diagnostics、优先级、好 binding 忽略坏诊断及脱敏。

- [x] **Step 4: 追加报告并提交**

  记录 RED/GREEN 证据、最终测试数和未执行真实业务数据验证；使用短格式 commit message。

# 云端 Token 客户端就绪与即时停止 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** 让缺少云端 Token 引导配置的同事端在运行前得到明确诊断，禁止生产 API 静默退回旧本地刷新；单项目卡在百度数据阶段时可立即、安全停止。

**Architecture:** 以 `secrets/secrets.json` 中的 `baidu_api_gateway.token_url/client_key/app_id` 和项目所需 `baidu_api.<profile>.app_id` 作为云端 API 就绪契约。预检只输出布尔诊断，不暴露密钥；运行时缺少契约则快速失败并进入现有浏览器降级。GUI 单项目只有在停止门成功抢占、证明 Excel 尚未开始后才终止子进程；多项目仍保持“当前项目完成后停止队列”。

**Tech Stack:** Python 3、PySide6/QProcess、pytest、JSON 配置。

## Global Constraints

- 不运行真实小时报/日报，不读取真实百度业务数据，不写目标 Excel。
- 不输出、复制或提交 Token、HMAC 密钥与真实授权文件。
- 保留多项目“不得中断当前项目”的既有规则。
- 仅在停止门成功写入 `cancel` 后终止单项目进程；Excel 已认领时不得终止。
- 保留无关工作区改动。

## Task 1: 固化云端 Token 就绪契约

**Files:**
- Modify: `tests/test_basic.py`
- Modify: `modules/preflight.py`
- Modify: `modules/baidu_token_manager.py`

- [ ] 添加失败测试：旧本地 Token 完整但缺少 `token_url` 时，预检不得通过并给出重新导入提示。
- [ ] 添加失败测试：云端网关和 profile appId 匹配时，即使本地 access/refresh Token 为空也应通过。
- [ ] 添加失败测试：生产 token provider 缺少云端配置时快速抛出 `configuration_error`，不得调用旧本地刷新网络。
- [ ] 最小实现安全布尔诊断：HTTPS token URL、client key、gateway appId、profile 存在、profile appId 匹配。
- [ ] 删除生产 cloud-first provider 的静默旧本地刷新分支。
- [ ] 运行聚焦测试并确认不泄露配置值。

## Task 2: 单项目百度阶段即时停止

**Files:**
- Modify: `tests/test_basic.py`
- Modify: `gui/main_window.py`

- [ ] 添加失败测试：单项目停止门抢占成功后调用一次 `runner.stop()`。
- [ ] 添加失败测试：QProcess 被 kill 后即使退出码不是 130，界面仍识别为用户停止。
- [ ] 添加回归测试：多项目只写队列停止门，不调用 `runner.stop()`。
- [ ] 最小实现：成功抢占停止门后立即终止单项目子进程；多项目保持协作式停止。
- [ ] 保留 Excel 已认领时停止无效的保护。

## Task 3: 同步部署与排障说明

**Files:**
- Modify: `docs/baidu_cloud_token_store.md`
- Modify: `README_同事使用说明.md`

- [ ] 说明程序更新包不会携带 HMAC 密钥，旧电脑必须一次性导入管理员最新授权配置。
- [ ] 说明预检中“云端 Token 配置未就绪”的处理方式。
- [ ] 删除“缺少 token_url 自动退回旧本地刷新”的过时说明。

## Task 4: 验证与审查

**Files:**
- Verify only

- [ ] 运行 token/preflight/GUI stop 聚焦测试。
- [ ] 运行 `tests/test_basic.py` 基础测试。
- [ ] 检查 git diff，确认没有 secrets、报告、日志或无关配置进入改动。
- [ ] 按完成前验证与代码审查流程复核安全边界。

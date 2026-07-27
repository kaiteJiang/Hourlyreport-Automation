# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Windows 本地运行的百度竞价日报/小时报自动化工具。从百度营销后台读取展现/点击/消费，默认通过快商通本地只读 API 获取对话转化数据（可切人工导出），写入本地 Excel。

当前版本 v2.0，不做 QQ、不做截图、不自动发送消息。

全程用中文回复。

## 开发规则

> ⚠️ **权威规则文件：[AGENTS.md](AGENTS.md)**
>
> 所有参与本仓库开发的 AI Agent（Claude Code、Codex、HERMES 等）必须以 AGENTS.md 为最高优先级行为准则。AGENTS.md 包含完整的硬规则、Superpowers 使用策略、百度 API 边界、Chrome 与登录态规则、Excel 安全约束、版本与在线更新规则、发布检查清单和禁止行为清单。
>
> CLAUDE.md 仅供 Claude Code 会话上下文，AGENTS.md 是跨 Agent 的统一约束。两者有差异时以 AGENTS.md 为准。

摘要（详见 AGENTS.md 全文）：

1. 分阶段开发，不允许一次性做全流程
2. Excel 写入前必须先备份原文件
3. Excel 区域识别必须通过扫描表头/账户区域/字段名，不允许写死固定坐标
4. 浏览器自动化不允许依赖绝对屏幕坐标，优先用 URL、文本、表头、表格结构、选择器
5. 浏览器必须优先使用 Google Chrome，不允许默认启动 Edge
6. 当前版本不做截图，不操作 QQ，不自动发送消息
7. 遇到不确定的 Excel 结构不要猜测写入，必须中断并输出诊断信息
8. 每次修改代码后必须说明修改了哪些文件、修改了什么
9. 每次运行后必须输出日志和自检结果

## 常用命令

```cmd
# 安装同事运行环境（缺少 Python 时会自动下载项目专用版本）
install_env.bat

# 补充开发/测试/打包依赖
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 运行菜单（推荐同事使用）
.venv\Scripts\python.exe menu.py

# 桌面 GUI（PySide6）
.venv\Scripts\python.exe gui\app.py
# 或双击 run_desktop_gui.bat

# 检查运行环境
.venv\Scripts\python.exe main.py --mode doctor

# 查看项目列表 / 当前项目 / 校验项目配置
.venv\Scripts\python.exe main.py --mode list-projects
.venv\Scripts\python.exe main.py --mode show-project
.venv\Scripts\python.exe main.py --mode validate-project

# 临时指定项目（不修改 app_config.json）
.venv\Scripts\python.exe main.py --mode doctor --project qingdao_npx

# Preflight 预检（HERMES / 一键流前置检查）
.venv\Scripts\python.exe main.py --mode preflight --quick              # 小时报快速预检
.venv\Scripts\python.exe main.py --mode preflight --task daily --quick # 日报快速预检
.venv\Scripts\python.exe main.py --mode preflight                      # 完整预检（含 Excel 扫描）
.venv\Scripts\python.exe main.py --mode preflight --task daily

# 凭据检查
.venv\Scripts\python.exe main.py --mode test-baidu-credentials

# 第一阶段：识别 Excel 结构（时段数据 sheet）
.venv\Scripts\python.exe main.py --mode inspect-excel
.venv\Scripts\python.exe main.py --mode dump-sheet-text   # 仅导出 sheet 文本

# 百度浏览器连接测试
.venv\Scripts\python.exe main.py --mode test-browser-connect

# 百度页面检测 / 概览页准备
.venv\Scripts\python.exe main.py --mode baidu-detect
.venv\Scripts\python.exe main.py --mode baidu-prepare-overview

# 百度自动读取（小时报）
.venv\Scripts\python.exe main.py --mode fetch-baidu-auto --period 15点

# 快商通导出文件解析（小时报）
.venv\Scripts\python.exe main.py --mode parse-kst-export --period 15点 --file "导出文件路径"

# 半自动一键流（小时报，自动选最新 kst_exports 文件）
.venv\Scripts\python.exe main.py --mode run --period 15点 --yes

# 日报流程各阶段
.venv\Scripts\python.exe main.py --mode inspect-daily-excel
.venv\Scripts\python.exe main.py --mode fetch-baidu-daily --date 2026-05-07
.venv\Scripts\python.exe main.py --mode parse-kst-daily --date 2026-05-07 --file "导出文件路径"
.venv\Scripts\python.exe main.py --mode merge-daily --date 2026-05-07
.venv\Scripts\python.exe main.py --mode write-daily --date 2026-05-07
.venv\Scripts\python.exe main.py --mode run-daily --date 2026-05-07

# 启用详细输出
.venv\Scripts\python.exe main.py --mode run --period 15点 --verbose
```

## 测试

```cmd
# 运行全量基础测试
.venv\Scripts\python.exe -m pytest tests\test_basic.py

# 运行单个测试
.venv\Scripts\python.exe -m pytest tests\test_basic.py -k "test_project_config"

# 详细输出
.venv\Scripts\python.exe -m pytest tests\test_basic.py -v
```

测试文件 `tests/test_basic.py` 持续扩充，覆盖：项目配置、Excel 读写、百度解析、快商通解析、数据合并、一键流编排、浏览器设置、Chrome 调试启动、preflight、桌面 GUI、HERMES 入口、发布包、控制台 UI、任务状态等。

## 浏览器调试端口启动

preflight / run 会先复用 Chrome `9222`；未就绪时自动尝试启动项目专用调试 Chrome。下面命令仅作为人工排障入口。

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --profile-directory="Default" https://cc.baidu.com/report
```

或双击 `start_chrome_debug.bat`。

## HERMES（夏思道）固定 BAT 入口

入口同步日期：2026-07-10。

```cmd
run_hermes_hourly.bat 11点
run_hermes_hourly.bat 15点
run_hermes_hourly.bat 18点
run_hermes_daily.bat
run_hermes_daily.bat 2026-07-09
```

BAT 会固定工作目录、UTF-8 环境和 `.venv` Python，缺少运行环境时自动安装，随后先跑 preflight，失败则停止。

## 构建与打包

```cmd
# 构建桌面 EXE（PyInstaller）
build_desktop_exe.bat

# 运行桌面 GUI 开发版
run_desktop_gui.bat
```

## 核心架构

### 入口点

- `main.py` — CLI 入口，argparse 统一路由所有 `--mode` 到对应模块函数
- `menu.py` — 交互式中文菜单，面向非技术同事；内部组装配置后调用与 main.py 相同的 pipeline 函数
- `gui/app.py` — PySide6 桌面 GUI 入口，提供项目选择、日报/小时报执行、备份恢复、控制台日志

### 模块分层

| 模块 | 职责 |
|---|---|
| `project_config.py` | 项目配置的 CRUD、标准化、校验；多项目切换的核心 |
| `config_manager.py` | 简单的 JSON 配置读取（含 fallback） |
| `excel_inspector.py` / `daily_excel_inspector.py` | 扫描 Excel 表结构，不写入 |
| `excel_writer.py` | `openpyxl` 引擎写入，含备份、写入、复核三步 |
| `excel_engine.py` | 引擎选择抽象（openpyxl / excel_com） |
| `baidu_auto.py` | 百度搜索推广数据自动读取（小时报） |
| `baidu_daily.py` | 百度日报数据读取 |
| `baidu_browser.py` | Playwright Chrome CDP 连接/管理 |
| `baidu_parser.py` | 百度页面表格解析 |
| `baidu_detector.py` | 百度页面类型检测 |
| `baidu_overview.py` | 百度数据概览页面导航 |
| `baidu_validator.py` | 百度数据自检校验 |
| `baidu_session.py` | 百度会话管理（登录态维护） |
| `baidu_multi_source.py` | 多来源百度数据（多账户体系） |
| `baidu_unknown_accounts.py` | 未识别账户处理 |
| `browser_manager.py` | 浏览器启动/连接测试 |
| `browser_login_state.py` | 浏览器登录状态检测 |
| `chrome_debug.py` / `chrome_debug_launcher.py` | Chrome 调试端口管理、启动 |
| `kst_export_parser.py` | 快商通小时报导出文件解析 |
| `kst_daily_parser.py` | 快商通（商务通）日报导出文件解析 |
| `kst_parser.py` | 快商通字段口径逻辑 |
| `data_merger.py` | 合并百度 + 快商通数据 |
| `run_pipeline.py` | 一键流编排（小时报 run / 日报 run-daily） |
| `preflight.py` | 运行前预检（Chrome 端口、配置、凭据、Excel 路径等） |
| `doctor.py` | 运行环境自检 |
| `validators.py` | 通用数据校验 |
| `credential_manager.py` | 读取 `credentials.local.json` / `secrets/secrets.json` |
| `console_ui.py` | 终端 UI 输出（rich 表格、状态、banner） |
| `logger.py` | 日志配置 |
| `text_normalizer.py` | 文本标准化工具 |
| `task_status.py` | 任务完成状态追踪（日报/小时报是否已跑） |

### GUI 模块 (`gui/`)

基于 PySide6 的桌面应用，面向非技术同事：

| 文件 | 职责 |
|---|---|
| `gui/app.py` | 入口，初始化 QApplication、项目存储、主窗口 |
| `gui/main_window.py` | 主窗口布局：项目选择、日报/小时报执行、控制台输出 |
| `gui/project_store.py` | 项目列表管理，排除模板项目 |
| `gui/command_builder.py` | 将 GUI 操作组装为 main.py CLI 命令 |
| `gui/task_runner.py` | QProcess 子进程执行，强制 UTF-8 环境，隐藏子进程窗口 |
| `gui/log_formatter.py` | 控制台日志高亮格式化 |
| `gui/environment_check.py` | GUI 启动前的环境检查 |

### 配置系统

```
configs/app_config.json               # 默认项目 + 配置目录路径
configs/projects/{project_id}.json    # 每项目一个配置文件
secrets/secrets.json                  # 密码/凭据（不提交）
credentials.local.json                # 本机百度账号（不提交，.gitignore）
```

`project_config.py` 是配置层的核心：`load_app_config` → `get_current_project` → `build_runtime_config_from_project` 将项目配置转换为运行时配置（兼容旧的 config.json 格式），供各模块使用。

菜单中的"切换项目"修改 `app_config.json` 的 `default_project_id`。

支持多来源（multi-source）项目配置，一个项目可配置多个百度凭据 profile，通过 `baidu_multi_source.py` 协调。

### 数据流

小时报一键流 (`run`): `fetch_baidu_auto` + `parse_kst_export_file` → `merge_data_files` → `write_merged_hourly_data`

日报一键流 (`run-daily`): `fetch_baidu_daily` + `parse_kst_daily_file` → `merge_daily_files` → `write_merged_daily_data`

Pipeline 在执行前会跑 credential precheck，失败则中断不写 Excel。

### 输出目录

- `reports/` — 所有 JSON 报告、自检报告
- `logs/run.log` — 运行日志
- `backups/` — Excel 写入前自动备份
- `kst_exports/` — 快商通人工导出文件放置目录

### 固定账户

银康01、银康银屑02、银康03 — 三个账户的别名映射在项目配置的 `accounts` 数组中定义，通过 `get_account_alias_maps()` 生成百度别名→账户、商务通ID→账户等映射表，所有模块统一使用。

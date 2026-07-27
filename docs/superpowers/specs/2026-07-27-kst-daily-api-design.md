# 快商通日报本地 API 设计

## 目标

让日报与小时报共用快商通本地 API 和推广 ID 项目映射。全局选择“API 自动获取”时，日报不得扫描或解析人工导出文件；选择“人工导出对话”时保留现有日报导出流程。

## 数据流

本地 API 已按 `project_id` 将请求路由到唯一的已登录快商通身份。服务端从自动推送会话集合中按日期读取数据，使用现有日报聚合规则生成六项指标：总对话、有效对话、无效对话、一般有效对话、有效转潜、总转潜。新增接口为：

`GET /v1/kst/daily?project_id=<项目ID>&date=YYYY-MM-DD`

响应必须包含匹配的 `project_id`、`source=kst_local_api`、日期、账户明细、汇总和错误列表。缺少项目 ID、项目未绑定、身份不唯一或数据读取失败时拒绝返回其他项目数据。

## 客户端与流水线

日报客户端只允许访问固定回环地址 `http://127.0.0.1:18766`，并继续使用 `KST_LOCAL_API_TOKEN`。成功响应写入现有 `reports/kst_daily_data.json` 和 `reports/kst_daily_parse_report.json`，使合并器和 Excel 写入模块无需改变。

`run_daily_pipeline` 按 `config.kst.data_source` 分支：

- `local_api`：调用日报本地 API，不查找人工导出文件。
- `export`：保留现有人工导出查找与解析。

当 API 不可用且 `allow_zero_on_unavailable=true` 时，为所有当前项目账户生成六项全零日报数据，记录警告后继续百度日报、合并和 Excel 写入。项目不匹配、响应结构错误等同样视为不可用，但不得回退读取历史人工导出。

## 多项目与安全边界

多项目日报仍逐项目运行同一 `run_daily_pipeline`；每个项目请求携带自身 `project_id`，因此复用现有推广 ID 唯一映射，不新增手工账号关系表。并发百度准备不改变 KST 串行项目写入和 Excel 所有权保护。

## 验收

自动化测试必须覆盖日报聚合、HTTP 路由、客户端项目校验、API 不可用记零、API 模式不扫描导出、人工模式保持兼容，以及多项目日报逐项目路由。最终完整测试通过后重建 EXE，并实机验证 `/health`、昆明牛小时接口和昆明牛日报接口。

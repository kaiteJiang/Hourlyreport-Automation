# README v118 实际界面与配图刷新设计

## 目标

将根目录 `README.md` 更新为与 `2026.7.31.118` 一致，并用当前 GUI 源码渲染的脱敏界面截图替换旧版主横幅和桌面截图。

## 设计决定

- 采用真实 v118 GUI 截图，不使用生成式插画替代产品界面。
- 截图使用演示项目、脱敏日志和示例站点 ID，不包含 Token、密码、真实导出数据或本机敏感路径。
- 保留现有 `app_icon`、Clawd 宠物和 Mermaid 流程图；只更新会误导用户的旧界面图片和 v118 文案。
- README 重点同步：快商通 Electron/Java-JCEF 双客户端、站点 ID + 推广 ID 映射、服务启动与当前项目可用状态分离、重新扫描入口、脱敏诊断、45 秒无输出提醒。
- 不修改业务代码、Excel 写入规则、百度数据口径或发布包内容。

## 交付物

- `docs/images/desktop-console-v118.png`：v118 主控制台截图。
- `docs/images/kst-api-diagnostics-v118.png`：快商通 API 状态、重新扫描和诊断日志截图。
- `docs/images/readme-hero-v118.webp`：由上述真实 GUI 截图与现有品牌元素组成的 README 横幅。
- `README.md`：版本、能力、快速开始、v118 更新说明和图片引用全部同步。

## 验收标准

- README 只引用存在的图片文件，图片路径使用仓库相对路径。
- 所有 v118 文件名、版本号和功能描述一致；旧版本条目仅保留在历史更新表中。
- 截图尺寸适合 GitHub README，文字可读，且不含凭据、Token、真实路径或业务数据。
- `git diff --check` 通过；图片可被 Pillow 打开并报告尺寸与色彩模式。

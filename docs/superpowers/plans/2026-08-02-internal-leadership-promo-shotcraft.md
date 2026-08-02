# 蚁之力内部汇报宣传片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用 video-shotcraft 的 Remotion + 真实 GUI 素材 + 镜头配方卡工作流，制作一支约 60 秒、真人主持出镜、带中文配音和字幕的内部汇报宣传片。

**Architecture:** 新建独立的 `promo_video_shotcraft` Remotion 工程，不改生产业务代码。主持人片段由 HeyGen 公共数字人生成；产品段由 Remotion 组合真实 GUI 截图、PageCam 2.5D 运镜、状态流、嵌入式行和发布会合影收尾。上一版 `promo_video/audio/narration.mp3` 作为配音风格基线，按新脚本用同一公共音色重新生成。

**Tech Stack:** Remotion 4.0.484、React、TypeScript、video-shotcraft template components、FFmpeg、HeyGen public avatar/TTS、Microsoft YaHei。

## Global Constraints

- 不运行真实 `run` / `run-daily`，不写目标 Excel，不修改生产 secrets 或配置。
- 内部 GUI 画面必须遮盖内部路径、账号、客户名、令牌和真实业务数据。
- 最终视频为 1920×1080、30fps、约 60 秒，旁白和字幕均为中文。
- 产品镜头优先使用真实截图，不用低质量手搓 UI 复刻现有界面。
- 所有动画使用确定性参数，不使用 `Date.now()`、`Math.random()` 或无参 `new Date()`。
- 交付带 BGM 与无 BGM 两版；无 BGM 版保留旁白和 SFX。

### Task 1: 建立安全素材与 Remotion 工程边界

**Files:**
- Create: `promo_video_shotcraft/package.json`
- Create: `promo_video_shotcraft/src/index.ts`
- Create: `promo_video_shotcraft/src/Root.tsx`
- Create: `promo_video_shotcraft/public/textures/desktop-console.png`
- Create: `promo_video_shotcraft/public/brand/app_icon.png`
- Create: `promo_video_shotcraft/public/audio/narration.mp3`
- Create: `promo_video_shotcraft/public/audio/narration.srt`
- Create: `promo_video_shotcraft/public/asset-note.txt`

- [ ] 从 video-shotcraft template 复制 Remotion 基础依赖和 PageCam/Caption/FlashCut 组件，保留独立工程目录。
- [ ] 复制上一版已检查的桌面 GUI 截图和品牌图标；将截图内部日志区域的素材说明记录在 `asset-note.txt`，不复制日志、报告、备份或 secrets。
- [ ] 将上一版配音风格复用为本版旁白基线，准备 HeyGen 重新生成的 60 秒音频和对应 SRT。
- [ ] 运行 `npm install`，确认 Remotion CLI、React 和 TypeScript 可用。

### Task 2: 完成 shotcraft 分镜实现

**Files:**
- Create: `promo_video_shotcraft/src/ShotcraftMain.tsx`
- Create: `promo_video_shotcraft/src/components/PresenterScene.tsx`
- Create: `promo_video_shotcraft/src/components/ProductFlowScene.tsx`
- Create: `promo_video_shotcraft/src/components/SafetyScene.tsx`
- Create: `promo_video_shotcraft/src/components/OutroAssembly.tsx`
- Create: `promo_video_shotcraft/src/timeline.ts`

- [ ] 按设计 spec 写 7 个镜头和帧级时间轴，保证开场主持动作至少 3 秒、信息落定后有呼吸、收尾品牌 hold 至少 1 秒。
- [ ] 使用 `spotlight-hero-card` 的单主角聚光和推近语法，但将目标换为真实 GUI 的数据入口卡。
- [ ] 使用 `ai-stream-response` 的“结论先到、证据行逐条汇入、完成态静止”语法展示读取/校验/合并/复核。
- [ ] 使用 `row-embed` 的 rotateX 收平与真实槽位嵌入语法展示备份/目标识别/安全写入/复核。
- [ ] 使用 `outro-group-photo-launch` 的四方飞入合影、crane 落机位、riser→impact→sparkle 结尾句式。
- [ ] 通过 `bgm` inputProp 控制带 BGM/无 BGM 两版，不另建第二条时间线。

### Task 3: 真人主持、配音和声音设计

**Files:**
- Create: `promo_video_shotcraft/public/presenter/presenter.mp4`
- Create: `promo_video_shotcraft/public/audio/bgm-tech-house.mp3`
- Create: `promo_video_shotcraft/public/audio/sfx/`
- Create: `promo_video_shotcraft/src/audio.ts`
- Create: `promo_video_shotcraft/audio/narration.txt`
- Create: `promo_video_shotcraft/audio/narration-generation.md`

- [ ] 使用 HeyGen 公共数字人生成中文主持片段，画幅与主片一致，主持段只承担开场和结尾，不覆盖产品功能镜头。
- [ ] 用同一公共中文音色重新生成新脚本，保持上一版的声音风格；不上传用户本人身份素材。
- [ ] 从 shotcraft 音频资产复制已授权或随 skill 提供的 BGM/SFX，并记录来源与相对帧钉点。
- [ ] 对旁白、BGM、SFX 做混音，检查旁白清晰度、SFX 不削波和长样本不拖尾。

### Task 4: 分镜静帧、整片渲染与独立检查

**Files:**
- Create: `promo_video_shotcraft/renders/蚁之力内部汇报宣传片_60s.mp4`
- Create: `promo_video_shotcraft/renders/蚁之力内部汇报宣传片_60s-nobgm.mp4`
- Create: `promo_video_shotcraft/renders/qa/`
- Create: `promo_video_shotcraft/verification.json`
- Create: `promo_video_shotcraft/README.md`

- [ ] 每个镜头至少抽取入场中、动作峰值和落定后三帧，检查人像边缘、GUI 清晰度、字幕有效字高、转场接缝和数据安全。
- [ ] 用 Remotion 渲染带 BGM 与无 BGM 两版，使用 FFprobe 核对时长、画幅、帧率、视频/音频编码。
- [ ] 使用 FFmpeg 对成片做峰值检查，确认旁白清晰、BGM 不盖住人声、SFX 不削波。
- [ ] 对照 `final-review.md` 与 `aesthetic-rules.md` 输出逐条检查报告；无法使用独立 subagent 时，记录为“当前会话自检”，不冒充第三方审查。
- [ ] 扫描成片工程，确认没有 `secrets.json`、`.baidu-secrets`、日志、报告、备份、浏览器数据和真实业务导出文件。

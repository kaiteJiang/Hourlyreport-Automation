# 客户宣传片制作实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改动主程序的前提下，制作一支 20 秒、16:9、带中文配音和字幕的“蚁之力 · 竞价数据自动化”客户宣传片。

**Architecture:** 新建独立的 `promo_video/` HyperFrames 工程。工程只引用复制后的公开产品截图、Logo 和抽象化 SVG/CSS 图形；HyperFrames HTML 负责动画、字幕和时间线，Hermes CLI 负责中文配音，FFmpeg/HyperFrames 负责最终 MP4 导出与媒体检查。

**Tech Stack:** HyperFrames CLI、HTML/CSS/GSAP、Hermes CLI 阿里 TTS、FFmpeg、PowerShell、Microsoft YaHei。

## Global Constraints

- 视频规格固定为 1920×1080、30fps、20 秒。
- 只使用 `docs/images/readme-hero.webp`、`docs/images/desktop-console.png` 和 `assets/app_icon.png`，不复制任何业务数据目录。
- 不运行真实 `run` / `run-daily`，不写目标 Excel，不改主程序代码。
- 不展示真实凭据、Token、日志、报告、备份、浏览器数据或快商通导出数据。
- 画面文案必须与 `docs/superpowers/specs/2026-08-02-customer-promo-video-design.md` 一致。

---

### Task 1: 创建独立视频工程与视觉身份

**Files:**
- Create: `promo_video/DESIGN.md`
- Create: `promo_video/package.json`
- Create: `promo_video/index.html`
- Create: `promo_video/public/README.md`
- Copy: `docs/images/readme-hero.webp` → `promo_video/public/readme-hero.webp`
- Copy: `docs/images/desktop-console.png` → `promo_video/public/desktop-console.png`
- Copy: `assets/app_icon.png` → `promo_video/public/app_icon.png`

**Interfaces:**
- Produces a standalone HyperFrames project whose composition entry is `promo_video/index.html`.
- Assets are local, relative, and contain no secrets or runtime reports.

- [ ] **Step 1: Scaffold the HyperFrames project**

  Run from the repository root:

  ```powershell
  & 'C:\Program Files\nodejs\npx.cmd' --yes hyperframes init promo_video --example product-promo --non-interactive
  ```

  If the template command is unavailable, create the minimal HyperFrames structure with `index.html`, `compositions/`, `audio/`, `public/`, and `renders/` while preserving the same composition entry.

- [ ] **Step 2: Record the visual identity**

  Write `promo_video/DESIGN.md` with these exact values: `#F7FAFF` background, `#0B1B31` dark text, `#3B82F6` brand blue, `#F05A3C` brand orange, `#D7E6FF` border, Microsoft YaHei typography, white/light-blue data-workbench style, and no dark cyberpunk/glitch treatment.

- [ ] **Step 3: Copy only the approved product assets**

  Copy the three source images into `promo_video/public/` and verify that the new directory contains no `secrets`, `logs`, `reports`, `backups`, `kst_exports`, `browser_profile`, or Excel files.

- [ ] **Step 4: Add an asset manifest**

  Write `promo_video/public/README.md` listing each copied asset, its original repository path, and its role in the video. Do not include credentials or runtime file paths.

- [ ] **Step 5: Run the project doctor**

  Run:

  ```powershell
  Set-Location promo_video
  & 'C:\Program Files\nodejs\npx.cmd' hyperframes doctor
  ```

  Expected: Node.js, Chrome, and FFmpeg are detected; if a dependency warning appears, record it in `promo_video/README.md` and continue only if lint/render can still run.

### Task 2: Generate and validate the Chinese narration

**Files:**
- Create: `promo_video/audio/narration.txt`
- Create: `promo_video/audio/narration.mp3`
- Create: `promo_video/audio/narration.srt`
- Create: `promo_video/audio/narration-generation.md`

**Interfaces:**
- `narration.mp3` is the audio track consumed by the HyperFrames composition.
- `narration.srt` is the caption timing source and must cover the spoken content without exceeding 20 seconds.

- [ ] **Step 1: Write the approved narration text**

  Put exactly this text in `promo_video/audio/narration.txt`:

  ```text
  竞价数据，还在手工抄表？蚁之力，自动读取百度与快商通数据，完成校验、合并与安全写入。写入前自动备份，动态识别目标区域。小时报、日报、多项目，一键完成。
  ```

- [ ] **Step 2: Use Hermes CLI's enabled TTS tool**

  Run Hermes in one-shot mode with a prompt that asks its enabled `tts` tool to synthesize the exact contents of `promo_video/audio/narration.txt` in clear Mandarin using the configured Alibaba voice, save the result as `promo_video/audio/narration.mp3`, and report the actual duration. Do not ask Hermes to run any project task or inspect secrets.

- [ ] **Step 3: Verify the audio file**

  Run:

  ```powershell
  ffprobe -v error -show_entries format=duration:stream=codec_name,sample_rate,channels -of default=noprint_wrappers=1 promo_video/audio/narration.mp3
  ```

  Expected: audio exists, duration is between 15 and 19.8 seconds, and at least one audio stream is present. If Hermes TTS is unavailable, use the HeyGen speech tool with a Chinese starfish-compatible voice as the documented fallback and record that fact in `narration-generation.md`.

- [ ] **Step 4: Produce captions**

  Use the narration duration and phrase boundaries to create `narration.srt` with five caption blocks matching the storyboard: hook, product, automated pipeline, safety, and final payoff. Each block must be readable for at least 0.8 seconds and end no later than the narration duration.

### Task 3: Author the 20-second HyperFrames composition

**Files:**
- Create: `promo_video/compositions/customer-promo.html`
- Modify: `promo_video/index.html`

**Interfaces:**
- Composition id: `customer-promo`.
- Duration: 20 seconds at 30fps.
- Media input: `audio/narration.mp3`.
- Visual inputs: `/public/readme-hero.webp`, `/public/desktop-console.png`, `/public/app_icon.png`.

- [ ] **Step 1: Build the static hero-frame layout first**

  Define a 1920×1080 canvas with a `#F7FAFF` background, a subtle dot/grid texture, a left-aligned message area, and a right-side product-console panel. Keep the main content in a full-size flex container with padding; reserve absolute positioning for decorative lines, glows, and icons only.

- [ ] **Step 2: Add the five timed scenes**

  Implement these ranges: `0–3s` hook, `3–8s` product reveal, `8–14s` three-step flow, `14–17s` Excel safety, `17–20s` brand outro. Use the exact screen copy from the design spec and keep all copy inside a 10% safe margin.

- [ ] **Step 3: Add restrained motion**

  Use GSAP timeline entrances and exits: cards slide 20–40px with `power3.out`, data connectors draw in, the console scales from 0.96 to 1, and the orange Logo has a single soft pulse. Avoid continuous rotation and avoid masking readable Chinese text with transitions.

- [ ] **Step 4: Add audio and captions**

  Add the local narration track and subtitle blocks. Use the caption color/highlight rules from `DESIGN.md`, with brand orange limited to key emphasis words and the logo.

- [ ] **Step 5: Lint and inspect before rendering**

  Run from `promo_video/`:

  ```powershell
  & 'C:\Program Files\nodejs\npx.cmd' hyperframes lint --json
  & 'C:\Program Files\nodejs\npx.cmd' hyperframes inspect --json --samples 15
  ```

  Expected: no lint errors and no text overflow/canvas clipping. Fix any overflow before rendering.

### Task 4: Render, verify, and package the deliverables

**Files:**
- Create: `promo_video/renders/蚁之力客户宣传片_20s.mp4`
- Create: `promo_video/README.md`
- Create: `promo_video/verification.json`

**Interfaces:**
- Final MP4 is the customer-facing deliverable.
- `README.md` documents preview/render commands and the exact narration text.
- `verification.json` records machine-checkable duration, dimensions, fps, stream presence, and lint/inspect status.

- [ ] **Step 1: Render a draft**

  Run:

  ```powershell
  & 'C:\Program Files\nodejs\npx.cmd' hyperframes render --output renders/draft.mp4 --quality draft --fps 30
  ```

  Inspect a frame sheet or preview and fix any visual issue before final render.

- [ ] **Step 2: Render the final MP4**

  Run:

  ```powershell
  & 'C:\Program Files\nodejs\npx.cmd' hyperframes render --output 'renders/蚁之力客户宣传片_20s.mp4' --quality high --fps 30
  ```

- [ ] **Step 3: Run final media verification**

  Run:

  ```powershell
  ffprobe -v error -show_entries format=duration:stream=codec_name,width,height,r_frame_rate,codec_type -of json 'renders/蚁之力客户宣传片_20s.mp4'
  ```

  Expected: duration `19.8–20.2`, one video stream at `1920×1080` and `30/1` fps, and one audio stream.

- [ ] **Step 4: Generate a contact sheet for visual QA**

  Extract frames at 0s, 3s, 8s, 14s, 17s, and 19.5s with FFmpeg, inspect them visually, then remove only the temporary frame files. Record any fixes before final packaging.

- [ ] **Step 5: Package the handoff**

  Write `promo_video/README.md` with links to the MP4, source composition, narration, captions, and render commands. Write `verification.json` only from fresh command output; do not claim completion without those results.

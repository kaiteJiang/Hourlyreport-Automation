# README v118 Actual UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh `README.md` and its product imagery so the repository accurately presents the v2026.7.31.118 GUI and feature boundaries.

**Architecture:** Render the existing PySide6 `MainWindow` offscreen with a fake KST manager so screenshots come from the real v118 widget tree without starting a real task or touching Excel. Compose a sanitized hero image locally, then update only README copy and image references.

**Tech Stack:** Python 3.14, PySide6, Pillow, Markdown, Mermaid.

## Global Constraints

- Do not run `run` or `run-daily`, write Excel, start Chrome, or read real business exports.
- Do not include secrets, tokens, logs, reports, backups, diagnostics, browser data, or KST exports in images or README.
- Keep release baseline `2026.7.31.118` and existing business/Excel rules unchanged.

### Task 1: Generate sanitized v118 screenshots

**Files:**
- Create: `docs/images/desktop-console-v118.png`
- Create: `docs/images/kst-api-diagnostics-v118.png`

- [x] Render `MainWindow` with `QT_QPA_PLATFORM=offscreen`, a fake KST manager, and existing GUI styles.
- [x] Reset the typewriter log after startup checks and insert only the approved demo lines for project mapping, site ID, rescan, and diagnostic state.
- [x] Capture one idle dashboard and one KST-ready diagnostic state at 966x700.
- [x] Inspect both images and verify no credentials, real paths, or business rows are visible.

### Task 2: Compose the v118 README hero

**Files:**
- Create: `docs/images/readme-hero-v118.webp`

- [x] Use the v118 console and diagnostics screenshots as the primary visual content.
- [x] Preserve the existing clean white/blue product style and Clawd brand accent.
- [x] Export a wide WebP with readable UI and no added product claims in the image itself.
- [x] Open the output and verify the two screenshot panels are legible at README width.

### Task 3: Update README copy and references

**Files:**
- Modify: `README.md`

- [x] Replace old hero/desktop image references with the v118 assets.
- [x] Add v118-specific descriptions for dual-client KST discovery, site/promotion ID routing, current-project health, rescan, safe diagnostics, and the 45-second no-output reminder.
- [x] Keep installation names, API endpoint, HERMES BAT rules, Excel safety rules, and release filenames consistent with v118.
- [x] Keep historical v115-v117 entries in the release table without presenting them as current behavior.

### Task 4: Verify the documentation package

**Files:**
- Test: `README.md` and `docs/images/*-v118.*`

- [x] Check all README image links resolve to files in the repository.
- [x] Run Pillow metadata inspection for PNG/WebP dimensions and modes.
- [x] Search README for stale current-version wording and forbidden sensitive terms.
- [x] Run `git diff --check` and report any remaining untracked temporary directories separately.

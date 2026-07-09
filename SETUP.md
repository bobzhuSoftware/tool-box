# Setup Guide / 安装配置指南

> First-time setup instructions for Video Transcript Generator.  
> 首次使用前请按本文档完成环境配置。

---

## English

### Prerequisites

The following tools must be installed **system-wide** before running the project. All four are required for the core features.

| Tool | Minimum Version | Install |
|------|----------------|---------|
| **Git** | any | `winget install Git.Git` or https://git-scm.com |
| **Python** | 3.10+ | `winget install Python.Python.3.12` or https://python.org |
| **Node.js** | 18+ | `winget install OpenJS.NodeJS` or https://nodejs.org |
| **FFmpeg** | any | `winget install FFmpeg` or https://ffmpeg.org/download.html |

> **Important:**
> - When installing Python, check **"Add Python to PATH"**.
> - After installing FFmpeg, verify it works by running `ffmpeg -version` in a new terminal. If you installed via `winget`, open a new terminal window first.

---

### Step 1 — Clone the repository

```powershell
git clone <repository-url>
cd "Video Transcript"
```

---

### Step 2 — One-click dependency setup

```powershell
npm run setup
```

This single command does three things automatically:
1. Creates an isolated Python virtual environment at `.venv/`
2. Installs all Python packages from `requirements.txt` into the venv
3. Installs all frontend Node packages inside `frontend/`

> This may take several minutes on first run — Whisper and other ML packages are large.

---

### Step 3 — Install Playwright browsers (for web export features)

```powershell
.venv\Scripts\python.exe -m playwright install chromium firefox
```

This is required for Discord, WeChat, Teams, and web-to-PDF export features. You can skip this step if you only need video transcription.

> **Note:** Both Chromium and Firefox are needed. Chromium is used for most web exports; Firefox is used for PDF conversion (it copies your Firefox session profile to preserve login state).

---

### Step 4 — Start the development server

```powershell
.\start-dev.cmd
```

Or equivalently:

```powershell
npm run dev
```

Then open **http://localhost:5173** in your browser.

---

### Optional — Calibre (for EPUB → PDF conversion)

If you need the Book Converter feature (EPUB → PDF):

1. Download and install Calibre from https://calibre-ebook.com/download
2. After installation, the app will detect it automatically on the next start.
3. If it is not detected, open the Book Converter page in the app and click **设置路径** to point to your Calibre install directory.

> Calibre is **not** installed by `npm run setup`. It must be installed separately.  
> If Calibre is not installed, the app will fall back to a cloud API for conversion where available.

---

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `python` not found during `npm run setup` | Re-install Python and check "Add to PATH", then open a new terminal |
| `ffmpeg` not found at runtime | Re-install FFmpeg via `winget install FFmpeg` and open a new terminal |
| Port 8000 or 5173 already in use | `start-dev.cmd` detects this automatically and picks the next free port |
| Whisper model download is slow | The model is downloaded once on first transcription; subsequent runs use the local cache |
| YouTube "Sign in to confirm" error | Export `cookies.txt` from your browser and place it in the project root — see README for details |
| PDF conversion fails with "Please run `playwright install`" | Playwright browsers are outdated. Run `.venv\Scripts\python.exe -m playwright install firefox` to update. |

---

---

## 中文

### 前置要求

在运行项目之前，以下工具需要**在系统级别**安装完毕。核心功能依赖这四项。

| 工具 | 最低版本 | 安装方式 |
|------|---------|---------|
| **Git** | 任意版本 | `winget install Git.Git` 或 https://git-scm.com |
| **Python** | 3.10+ | `winget install Python.Python.3.12` 或 https://python.org |
| **Node.js** | 18+ | `winget install OpenJS.NodeJS` 或 https://nodejs.org |
| **FFmpeg** | 任意版本 | `winget install FFmpeg` 或 https://ffmpeg.org/download.html |

> **注意事项：**
> - 安装 Python 时，务必勾选 **"Add Python to PATH"**。
> - 安装 FFmpeg 后，打开一个新终端窗口，运行 `ffmpeg -version` 验证是否生效。用 `winget` 安装后需要重新打开终端。

---

### 第一步 — 拉取项目

```powershell
git clone <仓库地址>
cd "Video Transcript"
```

---

### 第二步 — 一键初始化依赖

```powershell
npm run setup
```

这条命令会自动完成三件事：
1. 在 `.venv/` 目录创建独立的 Python 虚拟环境
2. 将 `requirements.txt` 中的所有 Python 包安装到虚拟环境
3. 安装 `frontend/` 目录下的所有前端 Node 依赖

> 首次运行可能需要数分钟，Whisper 等 AI 模型包体积较大。

---

### 第三步 — 安装 Playwright 浏览器（用于网页导出功能）

```powershell
.venv\Scripts\python.exe -m playwright install chromium firefox
```

Discord 聊天记录导出、微信导出、Teams 导出、网页转 PDF 等功能依赖此步骤。如果只使用视频转录功能，可以跳过。

> **说明：** Chromium 和 Firefox 均需安装。Chromium 用于大多数网页导出；Firefox 用于 PDF 转换（会复制你的 Firefox 登录会话以保持登录状态）。

---

### 第四步 — 启动开发服务器

```powershell
.\start-dev.cmd
```

或等价地运行：

```powershell
npm run dev
```

启动后在浏览器中打开 **http://localhost:5173**。

---

### 可选 — Calibre（用于 EPUB → PDF 转换）

如果需要电子书转换功能（EPUB → PDF）：

1. 从 https://calibre-ebook.com/download 下载并安装 Calibre
2. 安装完成后，下次启动 app 时会自动检测
3. 如果未自动检测到，在 app 的电子书转换页面点击「**设置路径**」，手动指定 Calibre 安装目录

> Calibre **不会**被 `npm run setup` 自动安装，需要手动下载安装。  
> 未安装 Calibre 时，部分转换功能会自动降级为云端 API。

---

### 常见问题

| 问题 | 解决方法 |
|------|---------|
| 运行 `npm run setup` 时提示 `python` 找不到 | 重新安装 Python 并勾选"Add to PATH"，然后重开终端 |
| 运行时提示 `ffmpeg` 找不到 | 运行 `winget install FFmpeg`，然后重开终端 |
| 端口 8000 或 5173 被占用 | `start-dev.cmd` 会自动检测并切换到下一个可用端口 |
| Whisper 模型下载很慢 | 模型在首次转录时自动下载，之后使用本地缓存，无需重复下载 |
| YouTube 提示"请登录确认你不是机器人" | 将浏览器导出的 `cookies.txt` 文件放到项目根目录，详见 README |
| PDF 转换报错"Please run `playwright install`" | Playwright 浏览器版本过旧，运行 `.venv\Scripts\python.exe -m playwright install firefox` 更新。 |

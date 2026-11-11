---
last_mapped_commit: 953c1cc506faa8731517922dfb9aaa818ca2b187
last_mapped_at: 2026-11-11
---
# Technology Stack

**Analysis Date:** 2026-11-11

## Languages

**Primary:**
- Python 3.12 (target in `runtime.txt` and `.python-version`; `python:3.11-slim` in `Dockerfile`) - All application logic, UI, YouTube extraction, video processing, and tests (`app.py`, `streamlit_app.py`, `yt_processor.py`, `video_trimmer.py`, `utils.py`, `tests/test_processor.py`)

**Secondary:**
- HTML / CSS (inline inside `app.py`) - Custom card styling (`.clip-card`, `.score-badge`, `.source-badge`, `.crop-badge`) and CSS visual crop framing preview rendered via `st.markdown(..., unsafe_allow_html=True)`
- TOML (`.streamlit/config.toml`) - Streamlit theme and server upload configuration
- Dockerfile / Shell (`Dockerfile`) - Container image build and Streamlit startup command

## Runtime

**Environment:**
- Python 3.12 (`runtime.txt`: `python-3.12`, `.python-version`: `3.12`; local environment verified on Python 3.12.9; container base image in `Dockerfile` uses `python:3.11-slim`)
- JavaScript runtime (Node.js via `packages.txt` `nodejs` / system `/opt/homebrew/bin/node`, plus Deno via PyPI package `deno>=2.3.0`) - Used by `yt-dlp` EJS solver for YouTube signature and `n`-challenge decryption (`utils.py` `get_js_runtimes()`)
- FFmpeg binary - System `ffmpeg` (`packages.txt` / `Dockerfile` `apt-get`) or auto-provisioned via `imageio-ffmpeg` / `static-ffmpeg` and shimmed into tempdir by `ensure_ffmpeg()` (`video_trimmer.py`)

**Package Manager:**
- `pip` (`requirements.txt`, `Dockerfile` `pip install --no-cache-dir -r requirements.txt`)
- Lockfile: Missing (only lower-bound version specifiers in `requirements.txt`)

## Frameworks

**Core:**
- Streamlit `>=1.35.0` - Web frontend UI, sidebar controls, session state (`st.session_state`), inline media playback (`st.video`), ZIP/MP4 downloads (`st.download_button`), and cloud secrets (`st.secrets`) (`app.py`, `streamlit_app.py`). Includes `STRETCH_WIDTH` signature check for Streamlit `>=1.40` `width="stretch"` compatibility (`app.py`)

**Testing:**
- Python standard library `unittest` (`unittest.TestCase` in `tests/test_processor.py`) - 23 unit and integration tests
- `pytest` (documented in `README.md` as `pytest -v`, `.pytest_cache/` present) - Test runner

**Build/Dev:**
- Docker (`Dockerfile`) - Containerized build targeting port `7860` (Hugging Face Spaces)
- Streamlit Community Cloud (`packages.txt`, `runtime.txt`, `.streamlit/config.toml`, `streamlit_app.py`) - Managed PaaS deployment

## Key Dependencies

**Critical:**
- `yt-dlp[default]>=2025.1.0` - Fetches YouTube video metadata, `heatmap` (Most Replayed) engagement arrays, chapters, HLS/HTTPS stream URLs, and performs fallback downloads (`yt_processor.py`, `video_trimmer.py`)
- `pytubefix>=8.0.0` - Secondary fallback engine for metadata extraction (`yt_processor.py`) and video/audio downloading (`video_trimmer.py`) when `yt-dlp` encounters cloud IP blocks
- `deno>=2.3.0` - Python-packaged Deno binary discovered via `deno.find_deno_bin()` in `utils.py` (`get_js_runtimes()`) for `yt-dlp` challenge solving
- `static-ffmpeg>=2.7` & `imageio-ffmpeg>=0.4.9` - Embedded FFmpeg binary fallbacks discovered by `get_ffmpeg_path()` in `video_trimmer.py` when system `ffmpeg` is absent
- `moviepy>=1.0.3` - Python fallback for `trim_clip_ffmpeg()` (`video_trimmer.py`) if FFmpeg CLI execution fails (supports both MoviePy 1.x `moviepy.editor` and 2.x `moviepy`)
- `plotly>=5.18.0` - Renders the interactive Most Replayed area chart (`build_heatmap_figure()` in `app.py`)
- `pandas>=2.0.0` - Builds the extracted highlight segments table (`st.dataframe` in `app.py`)

**Infrastructure:**
- `curl_cffi>=0.7.0` - Included in `requirements.txt` for `yt-dlp[default]` browser TLS fingerprint impersonation
- `ffmpeg-python>=0.2.0` - Listed in `requirements.txt` (not directly imported in source; FFmpeg CLI is invoked via `subprocess`)
- `numpy>=1.24.0` - Listed in `requirements.txt` for numeric/dataframe/MoviePy support

## Configuration

**Environment:**
- `YTDLP_JS_RUNTIMES` - Defaulted to `"node,deno"` at module import time in `video_trimmer.py`
- Cookie env vars (`YOUTUBE_COOKIES`, `COOKIES`, or any env key containing `cookie`) - Intercepted by `sanitize_environment()` in `utils.py` on startup, written to `<tempdir>/env_cookies.txt`, and removed from `os.environ` alongside any variable exceeding `8192` bytes to avoid Linux `execve` `E2BIG` (`Errno 7`)
- `st.secrets` (`YOUTUBE_COOKIES`, `youtube_cookies`, `cookies`) - Optional Streamlit Cloud secrets storage checked in `utils.py` (`get_yt_cookies_file()`)

**Build:**
- `.streamlit/config.toml` - Dark theme (`#FF4B4B` primary, `#0E1117` background, `#1E232E` secondary background) and `server.maxUploadSize = 500`
- `requirements.txt` - Python dependency bounds
- `packages.txt` - Linux apt system dependencies (`ffmpeg`, `nodejs`) for Streamlit Cloud
- `runtime.txt` & `.python-version` - Pin Python `3.12`
- `Dockerfile` & `.dockerignore` - Container build configuration (`python:3.11-slim`, UID `1000`, port `7860`)

## Platform Requirements

**Development:**
- Python 3.9+ (3.12 recommended per `README.md`; `3.12.9` verified locally)
- FFmpeg via OS package manager (`ffmpeg` on PATH) or Python packages (`imageio-ffmpeg` / `static-ffmpeg`)
- Node.js (`node`) and/or `deno` for YouTube signature solving
- Outbound HTTPS access to YouTube / `googlevideo.com` / GitHub (`ejs:github`) for live video extraction (offline Sample Heatmap Demo mode works locally with FFmpeg only)

**Production:**
- **Streamlit Community Cloud**: Runs `streamlit_app.py` or `app.py` with `packages.txt` (`ffmpeg`, `nodejs`) and `runtime.txt` (`python-3.12`)
- **Docker / Hugging Face Spaces**: `Dockerfile` runs `streamlit run app.py --server.port=7860 --server.address=0.0.0.0` as non-root user `user` (UID `1000`)

---

*Stack analysis: 2026-11-11*

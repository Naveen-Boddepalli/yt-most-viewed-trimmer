---
last_mapped_commit: 93439675fb29331f3aabaa6cefada3bd2967cb13
last_mapped_at: 2026-11-12
---
# Technology Stack

**Analysis Date:** 2026-11-12

## Languages

**Primary:**
- Python 3.12 - All application, media processing, and test modules (`app.py`, `streamlit_app.py`, `yt_processor.py`, `video_trimmer.py`, `utils.py`, `tests/test_processor.py`); configured in `runtime.txt` (`python-3.12`) and `.python-version` (`3.12`), while `Dockerfile` uses `python:3.11-slim` (and `README.md` specifies Python 3.9+ compatibility).

**Secondary:**
- HTML/CSS - Inline card/badge styling and live crop-framing overlay injected via `st.markdown(..., unsafe_allow_html=True)` in `app.py` (`render_crop_framing_preview` and global `<style>` block).
- TOML - Streamlit theme and server configuration in `.streamlit/config.toml`.
- Dockerfile / Shell - Container build instructions in `Dockerfile`.

## Runtime

**Environment:**
- Python 3.12 (`runtime.txt`, `.python-version`) / Python 3.11 (`Dockerfile`)
- FFmpeg & FFprobe (system binary via `packages.txt` and `Dockerfile`, or auto-provisioned/shimmed from `imageio-ffmpeg` / `static-ffmpeg` in `video_trimmer.py` via `get_ffmpeg_path()` and `ensure_ffmpeg()`)
- Node.js (`nodejs` in `packages.txt`) and Deno (`deno>=2.3.0` in `requirements.txt`), discovered by `get_js_runtimes()` in `utils.py` and enabled via `YTDLP_JS_RUNTIMES=node,deno` in `video_trimmer.py` for `yt-dlp` EJS signature and n-challenge solving.

**Package Manager:**
- `pip` (`requirements.txt`; installed via `pip install --no-cache-dir -r requirements.txt` in `Dockerfile`)
- System packages managed via `apt` (`packages.txt` for Streamlit Community Cloud; `apt-get` in `Dockerfile` installing `ffmpeg` and `curl`)
- Lockfile: Missing (only `requirements.txt` with `>=` lower-bound specifiers; no `requirements.lock`, `poetry.lock`, or `Pipfile.lock`)

## Frameworks

**Core:**
- Streamlit `>=1.35.0` (`requirements.txt`) - Web frontend UI, sidebar controls, session state (`st.session_state`), secret loading (`st.secrets`), inline media preview (`st.video`), and file download buttons (`st.download_button`) in `app.py` and `streamlit_app.py`. Includes a runtime signature check (`app.py` lines 93-97) supporting Streamlit `>=1.40` (`width="stretch"`) and older versions (`use_container_width=True`).

**Testing:**
- `unittest` (Python standard library) - Test suite class `TestYouTubeMostViewedTrimmer(unittest.TestCase)` in `tests/test_processor.py` covering timestamp helpers, byte formatting, video/clip size estimation, heatmap peak extraction, fallback extraction, FFmpeg shimming, crop filter generation, end-to-end FFmpeg trimming/cropping/zipping, cookie sanitization, and local HTTP range relay trimming.
- `pytest` - Recommended test runner in `README.md` (`pytest -v`; `.pytest_cache` present).

**Build/Dev:**
- Docker (`Dockerfile`, `.dockerignore`) - Containerized deployment targeting Hugging Face Spaces on port `7860` with non-root user UID `1000`.
- Streamlit Community Cloud runtime configs (`packages.txt`, `runtime.txt`, `.streamlit/config.toml`, `streamlit_app.py`).

## Key Dependencies

**Critical:**
- `yt-dlp[default]>=2025.1.0` (`requirements.txt`) - Primary engine for fetching YouTube metadata (`heatmap`, `chapters`, `formats`) in `yt_processor.py` (`fetch_video_metadata`) and probing/downloading/slicing streams in `video_trimmer.py` (`_try_python_hls_direct_slice`, `_try_python_http_range_slice`, `download_single_clip_section`, `download_full_video`).
- `pytubefix>=8.0.0` (`requirements.txt`) - Secondary fallback engine for metadata extraction (`yt_processor.py`) and full-stream download (`video_trimmer.py` `_download_with_pytubefix`) across InnerTube clients (`ANDROID_VR`, `IOS`, `TV`, `MWEB`, `WEB`) when `yt-dlp` is blocked.
- `deno>=2.3.0` (`requirements.txt`) - Python-packaged Deno JS runtime binary (`deno.find_deno_bin()` in `utils.py`) supplied to `yt-dlp` for YouTube signature/n-challenge execution.
- `curl_cffi>=0.7.0` (`requirements.txt`) - HTTP/TLS browser impersonation support bundled for `yt-dlp[default]`.
- `plotly>=5.18.0` (`requirements.txt`) - Interactive dark-mode engagement heatmap area chart (`plotly.graph_objects` in `app.py` `build_heatmap_figure()`).
- `pandas>=2.0.0` (`requirements.txt`) - DataFrame construction for displaying extracted highlight segments and file size estimates (`app.py`).

**Infrastructure:**
- `imageio-ffmpeg>=0.4.9` (`requirements.txt`) - Bundled FFmpeg executable fallback discovered and copied to `<tempdir>/ffmpeg_bin/ffmpeg` by `get_ffmpeg_path()` in `video_trimmer.py`.
- `static-ffmpeg>=2.7` (`requirements.txt`) - Secondary bundled FFmpeg executable fallback in `get_ffmpeg_path()` (`video_trimmer.py`).
- `moviepy>=1.0.3` (`requirements.txt`) - Fallback video trimming and cropping engine in `trim_clip_ffmpeg()` (`video_trimmer.py`) when the FFmpeg CLI is unavailable or fails; supports both MoviePy 1.x (`moviepy.editor.VideoFileClip`) and 2.x (`moviepy.VideoFileClip`).
- `numpy>=1.24.0` (`requirements.txt`) - Declared numeric array dependency supporting `pandas` and `moviepy`.
- `ffmpeg-python>=0.2.0` (`requirements.txt`) - Declared in `requirements.txt` (FFmpeg operations in `video_trimmer.py` invoke the `ffmpeg` and `ffprobe` binaries directly via `subprocess.run`).
- Python stdlib networking & archiving (`http.server`, `http.client`, `socketserver`, `urllib.request`, `socket`, `threading`, `http.cookiejar`, `zipfile`) - Used in `video_trimmer.py` for the IPv4-enforced ephemeral `127.0.0.1` HTTP Range relay (`run_http_range_relay_trim`) and `.zip` archive creation (`create_clips_zip`), and in `utils.py` for `MozillaCookieJar` validation (`get_cookie_stats`).

## Configuration

**Environment:**
- `.streamlit/config.toml` sets the dark UI theme (`primaryColor = "#FF4B4B"`, `backgroundColor = "#0E1117"`, `secondaryBackgroundColor = "#1E232E"`, `textColor = "#FAFAFA"`, `font = "sans serif"`) and `[server] maxUploadSize = 500`.
- `utils.py` executes `sanitize_environment()` on module import (also invoked on import in `streamlit_app.py`, `app.py`, and `video_trimmer.py`) to strip any environment variable exceeding 8 KB (preventing Linux `execve` `[Errno 7] Argument list too long` / `E2BIG`) and persist cookie-related environment variables (`YOUTUBE_COOKIES`, `COOKIES`, `*cookie*`) to `<tempdir>/env_cookies.txt` before removing them from `os.environ`.
- `get_clean_env()` in `utils.py` builds a filtered environment dict (`< 4096` byte values plus essential keys like `PATH`, `HOME`, `TMPDIR`, `PYTHONPATH`) passed to `ffmpeg`/`ffprobe` `subprocess.run` calls in `video_trimmer.py`.
- `video_trimmer.py` sets `os.environ.setdefault("YTDLP_JS_RUNTIMES", "node,deno")` and calls `ensure_ffmpeg()` on module import to inject the discovered FFmpeg binary directory into `PATH`.
- No `.env` files are present in the repository; `user_cookies.txt` exists at the repository root (gitignored).

**Build:**
- `requirements.txt` - Python dependencies.
- `packages.txt` - System APT packages (`ffmpeg`, `nodejs`) for Streamlit Community Cloud.
- `runtime.txt` (`python-3.12`) & `.python-version` (`3.12`) - Python version pins.
- `Dockerfile` & `.dockerignore` - Container image definition (`python:3.11-slim`, `ffmpeg`, `curl`, UID `1000`, port `7860`).

## Platform Requirements

**Development:**
- Python 3.9+ (Python 3.12 configured in `runtime.txt` and `.python-version`)
- `pip` to install `requirements.txt`
- FFmpeg binary (system-installed or auto-resolved via `imageio-ffmpeg` / `static-ffmpeg`)
- Optional Node.js or Deno (Deno binary is also bundled via the `deno` Python package)

**Production:**
- **Streamlit Community Cloud**: Runs `app.py` or `streamlit_app.py`, installs `packages.txt` (`ffmpeg`, `nodejs`) and `requirements.txt` on Python 3.12 (`runtime.txt`).
- **Docker / Hugging Face Spaces**: Uses `Dockerfile` (`python:3.11-slim`), installs `ffmpeg` and `curl`, runs as non-root user `user` (UID `1000`), exposes port `7860`, and launches `streamlit run app.py --server.port=7860 --server.address=0.0.0.0`.

---

*Stack analysis: 2026-11-12*

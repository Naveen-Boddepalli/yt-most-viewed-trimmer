---
last_mapped_commit: 953c1cc506faa8731517922dfb9aaa818ca2b187
last_mapped_at: 2026-11-11
---
# External Integrations

**Analysis Date:** 2026-11-11

## APIs & External Services

**Video Platform & Media Streaming:**
- YouTube (`youtube.com`, `youtu.be`, `googlevideo.com`) - Metadata retrieval (title, duration, view count, thumbnail, chapters, description, Most Replayed `heatmap` telemetry) and media stream downloading/slicing
  - Primary SDK/Client: `yt-dlp` (`yt_processor.py` `fetch_video_metadata()`, `video_trimmer.py` `_try_python_hls_direct_slice()`, `_try_python_http_range_slice()`, `download_single_clip_section()`, `download_full_video()`)
  - Fallback SDK/Client: `pytubefix` (`yt_processor.py`, `video_trimmer.py` `_download_with_pytubefix()`) using InnerTube clients `ANDROID_VR`, `IOS`, `TV`, `MWEB`, `WEB`
  - Auth: Optional YouTube Netscape/JSON/DevTools session cookies passed to cookie-supporting `yt-dlp` profiles (`web`, `mweb`, `web_embedded`, `tv_samsung`, `default`); cookieless profiles (`visionos`, `android`, `android_vr`, `ios`) explicitly omit `cookiefile`

**YouTube Challenge Solver Scripts:**
- GitHub EJS Remote Components (`ejs:github`) - Configured via `"remote_components": ["ejs:github"]` in `yt-dlp` options (`yt_processor.py`, `video_trimmer.py`) to fetch YouTube JS challenge solver components executed by Node.js or Deno

**Media Assets & Embedded Player:**
- YouTube Image CDN (`https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg`) - Hardcoded thumbnail URL in Sample Heatmap Demo mode (`app.py`); live thumbnails come from `info.get("thumbnail")`
- Embedded YouTube Player - Rendered in sidebar/metadata column via `st.video(st.session_state["video_url"])` (`app.py`)

## Data Storage

**Databases:**
- None (stateless between container restarts; per-session data lives in `st.session_state`)

**File Storage:**
- Local filesystem and OS temporary directory (`tempfile.gettempdir()`) only:
  - Per-session workspace created via `tempfile.mkdtemp(prefix="yt_trimmer_")` (`app.py` `init_session_state()`) containing:
    - `downloads/` (`source_<id>.mp4` or `demo_source.mp4`)
    - `trimmed_clips/` (cropped/trimmed `.mp4` outputs)
    - `raw_clips_cache/` (uncropped streamed `.mp4` clips cached for instant re-cropping)
    - `<sanitized_title>_most_viewed_clips.zip` (bundled archive via `create_clips_zip()`)
  - Cookie files on disk: `user_cookies.txt` (gitignored, present at workspace root), `cookies.txt`, `<tempdir>/user_cookies.txt`, `<tempdir>/env_cookies.txt`, and per-PID working copies `<tempdir>/yt_working_cookies_<pid>.txt` (`utils.py`)
  - FFmpeg binary shim: `<tempdir>/ffmpeg_bin/ffmpeg` (`video_trimmer.py` `ensure_ffmpeg()`)

**Caching:**
- `yt-dlp` persistent disk cache at `<tempdir>/yt_dlp_cache` (`utils.py` `get_ytdlp_cache_dir()`) for player JS and EJS solver artifacts
- In-memory Streamlit `st.session_state` caching (`video_info`, `segments`, `source_video_path`, `raw_clips`, `trimmed_clips`, `all_clips_zip_path`) (`app.py`)

## Authentication & Identity

**Auth Provider:**
- None for application access (public web UI)
- Optional user-supplied YouTube session cookies for bypassing datacenter bot checks or accessing restricted videos (`utils.py`):
  - Supported formats parsed by `sanitize_cookie_content()`: Netscape (`#HttpOnly_` supported), JSON cookie arrays, Chrome/Edge DevTools table rows, `Cookie:` header / semicolon-separated pairs, and 2-column or alternating Name/Value lines
  - Session authentication indicator (`get_cookie_stats()`): checks `.youtube.com` cookies for `LOGIN_INFO`, `__Secure-1PSID`, `__Secure-3PSID`, or `SAPISID`
  - Resolution priority (`get_yt_cookies_file()`):
    1. `user_cookies.txt` or `cookies.txt` in CWD, or `<tempdir>/user_cookies.txt`
    2. `<tempdir>/env_cookies.txt` (dumped from environment variables by `sanitize_environment()`)
    3. `st.secrets` keys `YOUTUBE_COOKIES`, `youtube_cookies`, `cookies`
    4. `st.session_state["youtube_cookies_text"]`
  - Writes an isolated working copy via `_write_working_cookie_copy()` before passing to `yt-dlp` so `yt-dlp`'s in-place cookiejar writes do not mutate the master cookie file

## Monitoring & Observability

**Error Tracking:**
- None (no external error reporting SDK like Sentry)

**Logs:**
- `YdlCaptureLogger` (`utils.py`) - Silent in-memory logger passed to `yt-dlp` to collect warnings and errors without spamming stderr
- UI Diagnostics Panel (`app.py`) - Exposes `get_ffmpeg_diagnostics()` (`video_trimmer.py`) as JSON in an expandable sidebar section (Python version, FFmpeg/Node discovery, env byte size, JS runtimes, `yt-dlp` version)

## CI/CD & Deployment

**Hosting:**
- Streamlit Community Cloud (`streamlit_app.py`, `app.py`, `packages.txt`, `runtime.txt`, `.streamlit/config.toml`)
- Docker / Hugging Face Spaces (`Dockerfile` exposing port `7860`)

**CI Pipeline:**
- None detected (no `.github/workflows`, `.gitlab-ci.yml`, or CI configuration files)

## Environment Configuration

**Required env vars:**
- None required for core operation

**Optional env vars:**
- `YOUTUBE_COOKIES` / `COOKIES` / `*cookie*` - Automatically extracted to `<tempdir>/env_cookies.txt` and purged from `os.environ` at startup (`utils.py` `sanitize_environment()`)
- `YTDLP_JS_RUNTIMES` - Defaults to `"node,deno"` (`video_trimmer.py`)

**Secrets location:**
- Local runtime: `user_cookies.txt` (gitignored) or sidebar UI input
- Streamlit Cloud: `.streamlit/secrets.toml` / Streamlit Cloud Secrets dashboard (`YOUTUBE_COOKIES`)

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None (local ephemeral `127.0.0.1` HTTP Range relay server in `video_trimmer.py` `run_http_range_relay_trim()` only bridges `yt-dlp` `urlopen` to local `ffmpeg` during a single clip trim and shuts down immediately afterward)

---

*Integration audit: 2026-11-11*

---
last_mapped_commit: 93439675fb29331f3aabaa6cefada3bd2967cb13
last_mapped_at: 2026-11-12
---
# External Integrations

**Analysis Date:** 2026-11-12

## APIs & External Services

**YouTube & Google Video CDN (`youtube.com`, `youtu.be`, `googlevideo.com`, `img.youtube.com`):**
- Metadata, Chapters & Heatmap Telemetry - `yt_processor.py` (`fetch_video_metadata`) fetches video info, `heatmap` ("Most Replayed" engagement bins), `chapters`, `description`, and stream `formats` without downloading the full video.
  - SDK/Client: `yt-dlp` (`yt_dlp.YoutubeDL.extract_info(url, download=False)`) rotating across `player_client` profiles (`web`, `mweb`, `visionos`, `web_embedded`, `tv_samsung`, `android`, `tv_simply`), with `pytubefix` (`pytubefix.YouTube` using `ANDROID_VR`, `IOS`, `WEB`) as fallback.
  - Auth: Optional YouTube session cookies (`cookiefile` option in `yt-dlp`; cookieless clients such as `visionos`, `android`, `android_vr`, `ios` are kept isolated from cookies).
- Media Stream Slicing & Downloading - `video_trimmer.py` (`download_single_clip_section`, `download_full_video`) implements a 4-tier retrieval pipeline against YouTube/Google Video CDN:
  - Tier 1 (`_try_python_hls_direct_slice` + `slice_hls_playlist_to_file`): Fetches HLS `.m3u8` playlists and overlapping `.ts` / fMP4 init segments via `ydl.urlopen` (enforcing IPv4 and handling `available_at` ad-skip delays via `_wait_for_available_at`).
  - Tier 2 (`_try_python_http_range_slice` + `run_http_range_relay_trim`): Probes HTTPS DASH/progressive streams via `ydl.urlopen` and serves byte ranges through an ephemeral `127.0.0.1` HTTP server so local `ffmpeg` seeks and trims directly.
  - Tier 3 (`download_single_clip_section`): Falls back to `yt-dlp` `download_ranges` (`yt_dlp.utils.download_range_func`).
  - Tier 4 (`_download_with_pytubefix`): Falls back to `pytubefix.YouTube` (`ANDROID_VR`, `IOS`, `TV`, `MWEB`, `WEB`).
- Thumbnails & Inline Video Playback - `app.py` renders remote YouTube thumbnails (`info.get("thumbnail")` and sample demo thumbnail `https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg`) and embeds the YouTube URL in `st.video()`.

**GitHub Remote EJS Components (`ejs:github`):**
- `yt_processor.py` (line 110) and `video_trimmer.py` (lines 589 and 1493) configure `ydl_opts["remote_components"] = ["ejs:github"]` so `yt-dlp` can download remote JavaScript signature/n-challenge solver components into `<tempdir>/yt_dlp_cache` and execute them via Node.js or Deno (`utils.py` `get_js_runtimes()`).

## Data Storage

**Databases:**
- None (no SQL, NoSQL, or ORM integrations). Application state is stored in memory per Streamlit user session (`st.session_state` in `app.py`).

**File Storage:**
- Local filesystem and OS temporary directory (`tempfile.gettempdir()`) only:
  - Per-session temporary workspace (`tempfile.mkdtemp(prefix="yt_trimmer_")` initialized in `app.py` `init_session_state()`) for downloaded source videos, raw uncropped clips, trimmed `.mp4` clips, and `.zip` archives (`create_clips_zip` in `video_trimmer.py`).
  - Shimmed FFmpeg binary location: `<tempdir>/ffmpeg_bin/ffmpeg` (`video_trimmer.py` `get_ffmpeg_path()`, `ensure_ffmpeg()`).
  - Cookie files: `user_cookies.txt` (workspace root; file present and `.gitignore`d) or `<tempdir>/user_cookies.txt`, `<tempdir>/env_cookies.txt`, and per-process working copies `<tempdir>/yt_working_cookies_<pid>.txt` (`utils.py`).

**Caching:**
- Disk cache for `yt-dlp` player JS and EJS solver scripts at `<tempdir>/yt_dlp_cache` (`utils.py` `get_ytdlp_cache_dir()`).
- In-memory per-session caching of fetched video metadata, raw clips, trimmed clips, and downloaded source path in `st.session_state` (`app.py`). No external cache service (Redis/Memcached).

## Authentication & Identity

**Auth Provider:**
- None for application users (public Streamlit interface without user accounts or login).
- **YouTube Cookie Authentication (Optional):** Multi-format cookie parser and isolator in `utils.py` (`sanitize_cookie_content`, `save_yt_cookies`, `clear_yt_cookies`, `get_yt_cookies_file`, `get_cookie_stats`).
  - Supported input formats: Netscape HTTP Cookie File (including `#HttpOnly_` prefixes and 6-column auto-padding), JSON cookie arrays (Cookie-Editor / EditThisCookie), Chrome/Edge DevTools multi-column table copy-paste, semicolon-separated `Cookie` header strings, and two-column/alternating Name-Value lines.
  - Resolution order in `get_yt_cookies_file()`:
    1. Disk files (`user_cookies.txt`, `cookies.txt`, `<tempdir>/user_cookies.txt`)
    2. Environment-dumped file (`<tempdir>/env_cookies.txt`)
    3. Streamlit Secrets (`st.secrets["YOUTUBE_COOKIES"]`, `youtube_cookies`, `cookies`)
    4. Streamlit UI session state (`st.session_state["youtube_cookies_text"]`)
  - Isolation: `_write_working_cookie_copy()` creates a fresh `<tempdir>/yt_working_cookies_<pid>.txt` before each `yt-dlp` call so `yt-dlp` does not mutate the master cookie file.
  - Session validation: `get_cookie_stats()` inspects `.youtube.com` cookies via `http.cookiejar.MozillaCookieJar` for `LOGIN_INFO`, `__Secure-1PSID`, `__Secure-3PSID`, or `SAPISID`.

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, Rollbar, or APM SDKs). Exceptions are caught and surfaced in the Streamlit UI via `st.error`, `st.warning`, and `st.info` (`app.py`).

**Logs:**
- `YdlCaptureLogger` (`utils.py`) captures `yt-dlp` warnings and errors in memory while suppressing `stdout`/`stderr` noise.
- `get_ffmpeg_diagnostics()` (`video_trimmer.py`) + expandable `🔍 FFmpeg Diagnostics` panel (`app.py` lines 512-513) exposes runtime Python version, `PATH`, environment key/byte counts, FFmpeg binary probe results, JS runtimes, and `yt-dlp` version as JSON in the sidebar.

## CI/CD & Deployment

**Hosting:**
- Streamlit Community Cloud - Pre-configured with `app.py` / `streamlit_app.py`, `runtime.txt` (`python-3.12`), `packages.txt` (`ffmpeg`, `nodejs`), `requirements.txt`, and `.streamlit/config.toml` (documented in `README.md`).
- Hugging Face Spaces / Docker - Pre-configured via `Dockerfile` (`python:3.11-slim`, port `7860`, UID `1000` non-root user) and `.dockerignore`.

**CI Pipeline:**
- None detected (no `.github/workflows/`, `.gitlab-ci.yml`, or CI config files in the repository).

## Environment Configuration

**Required env vars:**
- None strictly required for baseline operation (application defaults to cookieless `visionos`/`web_embedded` client profiles and auto-discovers or shims FFmpeg).

**Optional env vars:**
- `YOUTUBE_COOKIES` / `COOKIES` / any `*cookie*` environment variable - Automatically extracted to `<tempdir>/env_cookies.txt` and popped from `os.environ` by `sanitize_environment()` in `utils.py` on import.
- `YTDLP_JS_RUNTIMES` - Defaults to `"node,deno"` via `os.environ.setdefault` in `video_trimmer.py`.

**Secrets location:**
- Local `user_cookies.txt` file (present at repository root; gitignored in `.gitignore`).
- `.streamlit/secrets.toml` (gitignored in `.gitignore`) or Streamlit Community Cloud Secrets (`YOUTUBE_COOKIES`, `youtube_cookies`, `cookies`).
- `.env` / `.env.*` (gitignored in `.gitignore`; not present in repository).

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None (only internal in-process Python `progress_callback` functions and `yt-dlp` / `pytubefix` progress hooks are used to update Streamlit progress bars in `app.py`).

---

*Integration audit: 2026-11-12*

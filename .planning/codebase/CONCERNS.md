---
last_mapped_commit: 953c1cc506faa8731517922dfb9aaa818ca2b187
last_mapped_at: 2026-11-11
---
# Codebase Concerns

**Analysis Date:** 2026-11-11

## Tech Debt

**Monolithic UI Script (`app.py`, 1155 lines):**
- Issue: All Streamlit widgets, HTML/CSS styling, session state initialization, Plotly charting, and nested trimming/caching closures (`ensure_source_video()`, `trim_single_segment_smart()`, `show_trim_error()`) live in one top-level script (`app.py:852-987`). Batch trimming (`app.py:989-1046`) duplicates ZIP bundling and fallback logic from `trim_single_segment_smart()`.
- Files: `app.py`
- Impact: Hard to test UI flows or modify trimming/caching behavior without breaking Streamlit rerun state.
- Fix approach: Extract clip orchestration (`trim_or_recrop_segment`, `trim_all_segments`, workspace management) into a dedicated service module and split sidebar/chart/card rendering into focused UI components.

**Oversized Streaming & Media Module (`video_trimmer.py`, 2002 lines):**
- Issue: Combines unrelated concerns in a single file: `CropConfig` / FFmpeg filter generation (`lines 50-215`), FFmpeg binary discovery/shim (`lines 221-398`), IPv4 HTTP handlers (`lines 491-573`), HLS segment slicer (`lines 680-872`), localhost HTTP Range relay server (`lines 1115-1365`), `pytubefix` fallback (`lines 1368-1466`), and local trimming/ZIP bundling (`lines 1854-2002`).
- Files: `video_trimmer.py`
- Impact: High cognitive load and merge conflict risk when updating YouTube client workarounds vs video cropping logic.
- Fix approach: Split into focused modules (e.g., `cropper.py`, `ffmpeg_utils.py`, `stream_slicer.py`, `yt_downloader.py`).

**Import-Time Global Side Effects & Reload Hack:**
- Issue: Importing `utils.py` executes `sanitize_environment()` (`utils.py:133`), importing `video_trimmer.py` mutates `os.environ["YTDLP_JS_RUNTIMES"]`, `os.environ["PATH"]`, and copies FFmpeg binaries (`video_trimmer.py:42-44,392`), and `app.py:71-90` wraps `video_trimmer` imports in `importlib.reload()` on `ImportError`.
- Files: `utils.py`, `video_trimmer.py`, `app.py`, `streamlit_app.py`
- Impact: Side effects execute during test collection or static imports, altering `os.environ` and filesystem state unpredictably.
- Fix approach: Move initialization into explicit startup functions called once at app entry.

**Runtime Version & Documentation Drift:**
- Issue: `Dockerfile` uses `python:3.11-slim`, whereas `runtime.txt` (`python-3.12`) and `.python-version` (`3.12`) target Python 3.12. `README.md:53` lists outdated client profile names (`tv`, `web_creator`, `web_safari`, `mweb`) that differ from `_build_client_profiles()` (`video_trimmer.py:404-485`). `requirements.txt` declares `ffmpeg-python>=0.2.0` and `numpy>=1.24.0` without direct imports in application source.
- Files: `Dockerfile`, `runtime.txt`, `.python-version`, `README.md`, `requirements.txt`, `video_trimmer.py`
- Impact: Subtle Python version behavior differences between Docker and Streamlit Cloud; misleading docs for contributors.
- Fix approach: Align `Dockerfile` base image to `python:3.12-slim`, update `README.md`, and audit unused dependencies.

## Known Bugs

**`raw_clips` Cache Desync Between "Trim All" and Re-Cropping (`app.py`):**
- Symptoms: Trimming via `Trim All` does not populate `st.session_state["raw_clips"]`, so clicking `🔄 Re-Trim / Apply Crop` on a batch-trimmed clip re-downloads from YouTube instead of cropping from cache; conversely, `trim_single_segment_smart()` uses `raw_clips[rank]` whenever `source_video_path` is absent (`app.py:920-930`) without checking if segment timestamps changed.
- Files: `app.py:909-930, 956, 989-1046`
- Trigger: Click `Trim All`, then change crop preset in sidebar and click `🔄 Re-Trim / Apply Crop`; or manually adjust segment boundaries/re-extract without clearing `raw_clips`.
- Workaround: Click `🗑️ Clear Temporary Workspace` in sidebar before re-trimming.

**`slice_hls_playlist_to_file()` Ignores Encryption (`#EXT-X-KEY`) and Audio Groups (`video_trimmer.py`):**
- Symptoms: HLS playlists using AES-128/SAMPLE-AES `#EXT-X-KEY` segments or `#EXT-X-MEDIA:TYPE=AUDIO` separate audio renditions inside a master playlist will produce unplayable `.ts` concatenations or video-only output.
- Files: `video_trimmer.py:708-761`
- Trigger: Streaming an HLS master playlist that separates audio via `GROUP-ID` or uses segment encryption.
- Workaround: Falls through to Tier 2 (`_try_python_http_range_slice`) or Tier 3 (`yt-dlp` range download) if FFmpeg fails.

**Silent Failure Through MoviePy Fallback Drops `9:16_blur` (`video_trimmer.py`):**
- Symptoms: If `ffmpeg` CLI returns a non-zero exit code in `trim_clip_ffmpeg()`, the error is ignored (`video_trimmer.py:1903-1905`) and execution falls through to MoviePy, which explicitly skips `crop_config.mode == "9:16_blur"` (`video_trimmer.py:1920`), producing an uncropped clip without warning.
- Files: `video_trimmer.py:1903-1931`
- Trigger: Any `ffmpeg` CLI failure during a `9:16_blur` trim when `moviepy` is installed.
- Workaround: None.

## Security Considerations

**Docker Build Leaks Local `user_cookies.txt` and `.streamlit/secrets.toml` (`.dockerignore` vs `Dockerfile`):**
- Risk: `Dockerfile:23` runs `COPY --chown=user . .`, while `.dockerignore:1-10` does NOT exclude `user_cookies.txt`, `*cookies*.txt`, `.env*`, or `.streamlit/secrets.toml`. `user_cookies.txt` (`49,971` bytes) currently exists in the repository root. Building a Docker image from this directory bakes live YouTube session cookies into the container filesystem.
- Files: `.dockerignore`, `Dockerfile`, `user_cookies.txt`
- Current mitigation: `.gitignore` excludes `*cookies*.txt` and `.streamlit/secrets.toml` from Git commits, but `.dockerignore` does not.
- Recommendations: Add `user_cookies.txt`, `*cookies*`, `.env*`, `.streamlit/secrets.toml`, and `.planning/` to `.dockerignore`.

**Shared Cross-Session Cookie Persistence on Multi-User Deployments (`utils.py`):**
- Risk: `save_yt_cookies()` (`utils.py:271-280`) writes `user_cookies.txt` directly into the shared working directory, and `get_yt_cookies_file()` (`utils.py:312-325`) loads `Path("user_cookies.txt")` and `<tempdir>/user_cookies.txt` globally before checking `st.session_state`. On a shared Streamlit Cloud or Hugging Face Spaces instance, one user's pasted YouTube login cookies (`LOGIN_INFO`, `__Secure-1PSID`, `SAPISID`) become active for and visible inside the sidebar cookie textarea (`app.py:166-177`) for every other visitor. Additionally, `clear_yt_cookies()` (`utils.py:296`) deletes all `*cookies*.txt` in tempdir, disrupting concurrent sessions.
- Files: `utils.py:271-325`, `app.py:166-177`
- Current mitigation: Per-PID working copies (`yt_working_cookies_<pid>.txt`) prevent `yt-dlp` from mutating the master file, but do not isolate sessions.
- Recommendations: Scope user-pasted cookies strictly to `st.session_state` and per-session `workspace_dir` files instead of shared CWD or global tempdir paths.

**Unescaped HTML Interpolation (`app.py`):**
- Risk: `render_crop_framing_preview()` (`app.py:258-294`) and clip cards (`app.py:1099-1114`) interpolate `thumb_url`, `crop_cfg.mode`, `seg.time_range_str`, and `seg.source` into HTML rendered with `unsafe_allow_html=True` without `html.escape()`.
- Files: `app.py:258-294, 1099-1114`
- Current mitigation: Values come from YouTube metadata / internal presets rather than direct arbitrary HTML input.
- Recommendations: Escape interpolated strings with `html.escape()` and validate `thumb_url` scheme (`https://`).

## Performance Bottlenecks

**Repeated Subprocess Calls on Every Streamlit Rerun (`app.py`):**
- Problem: `ensure_ffmpeg()` (spawning `ffmpeg -version`) runs on every sidebar render (`app.py:506`), and `get_video_dimensions(Path(clip_file))` (spawning `ffmpeg -i`) runs inside the clip card loop for every trimmed clip on every UI interaction/rerun (`app.py:1091`).
- Files: `app.py:506, 1091`, `video_trimmer.py:194-215, 320-358`
- Cause: Streamlit re-executes the entire script on every widget interaction, and neither FFmpeg discovery nor clip dimensions are memoized in `@st.cache_resource` / `st.session_state`.
- Improvement path: Cache `ensure_ffmpeg()` with `@st.cache_resource` and store `(width, height)` per clip path in `st.session_state`.

**Full File Reads Into Memory for `st.download_button` (`app.py`):**
- Problem: Every trimmed clip download button (`app.py:1121-1129`) and the `.zip` bundle download button (`app.py:1066-1074`) reads entire MP4/ZIP binary contents into RAM (`f_mp4.read()`, `zf_bytes.read()`) on every rerun.
- Files: `app.py:1066-1074, 1121-1129`
- Cause: Passing raw `bytes` instead of file-like/cached handles or streaming on demand.
- Improvement path: On large videos/many clips, memory usage spikes per connected user; keep clip count/resolution bounded or pass open binary file objects where Streamlit supports them.

**Sequential Batch Trimming & Unbounded Disk Workspace (`video_trimmer.py`, `app.py`):**
- Problem: `download_and_trim_segments_direct()` (`video_trimmer.py:1823-1850`) processes segments sequentially, and `tempfile.mkdtemp(prefix="yt_trimmer_")` (`app.py:160`) is never automatically reaped when user sessions expire.
- Files: `video_trimmer.py:1823-1850`, `app.py:160`
- Cause: No session TTL cleanup or background reaper for abandoned `yt_trimmer_*` directories.
- Improvement path: Add age-based cleanup of stale `yt_trimmer_*` temp folders on startup.

## Fragile Areas

**Silent Exception Swallowing Across Multi-Tier Fallback Loops (`yt_processor.py`, `video_trimmer.py`, `app.py`):**
- Files: `yt_processor.py:127-129, 161-164`, `video_trimmer.py:1024-1025, 1362-1363, 1463-1464, 1795-1796`, `app.py:970, 1011`
- Why fragile: All tiers catch `Exception` and continue silently without logging why a profile or tier failed, making cloud-only regressions (e.g., YouTube client deprecation, TLS handshake errors, FFmpeg codec failures) opaque.
- Safe modification: Accumulate `(profile/tier, error_summary)` diagnostics so failures can be inspected in logs or the UI diagnostics panel.

**Thread-Shared `yt_dlp.YoutubeDL` Instance in Range Relay (`video_trimmer.py`):**
- Files: `video_trimmer.py:1136-1270, 1347-1361`
- Why fragile: `_ThreadedServer` spawns concurrent `_RelayHandler` threads for video and audio streams simultaneously; while `ydl_lock` serializes `ydl.urlopen()`, reading `resp` streams and mutating `ydl.cookiejar` happens across concurrent threads, and `run_http_range_relay_trim()` hardcodes a `15 MB` progress denominator (`line 1251`).
- Safe modification: Test concurrent `/v` + `/a` multi-stream requests under `test_http_range_relay_trim_end_to_end` when modifying relay logic.

**IPv4-Only Network Enforcement (`video_trimmer.py`, `yt_processor.py`):**
- Files: `video_trimmer.py:491-543, 586, 1482`, `yt_processor.py:107`
- Why fragile: `_create_ipv4_connection()` forces `socket.AF_INET` and `yt-dlp` sets `"source_address": "0.0.0.0"`, which breaks completely in IPv6-only cloud networks.
- Safe modification: Fall back to dual-stack/AF_UNSPEC if IPv4 address resolution fails.

**Cookie Format Heuristics (`utils.py`):**
- Files: `utils.py:144-257`
- Why fragile: `sanitize_cookie_content()` attempts 4 sequential heuristics (JSON, Netscape/DevTools, `Cookie:` header, 2-column/alternating lines) and silently discards lines that fail regex checks without reporting partial parse errors.
- Safe modification: Add test cases to `tests/test_processor.py` before altering regexes in `sanitize_cookie_content()`.

## Scaling Limits

**Server RAM & Temp Disk Under Concurrent Users:**
- Current capacity: Single-process Streamlit server storing full source downloads, raw clips, cropped clips, and ZIP files on local `/tmp` + loading final MP4s/ZIPs into Python memory for `st.download_button`.
- Limit: Container ephemeral storage and memory (e.g., 1 GB RAM on Streamlit Community Cloud free tier) can be exhausted by a few concurrent 1080p full-video fallbacks or multiple 10-clip ZIP bundles.
- Scaling path: Enforce per-workspace disk quotas, stream files without keeping uncropped + cropped + full source simultaneously, and reap expired session temp directories.

**YouTube IP Rate Limiting / Bot Detection:**
- Current capacity: Relies on `visionos`/`web_embedded`/`android` client rotation and optional user-provided cookies from a single egress IP.
- Limit: Datacenter IP bursts trigger HTTP 403 / `Sign in to confirm you're not a bot` across all cookieless profiles simultaneously.
- Scaling path: Encourage Direct Stream-Trim, cache extracted metadata per URL briefly, and support residential/proxy configuration if needed.

## Dependencies at Risk

**`yt-dlp` & `pytubefix` Against YouTube API Churn:**
- Risk: YouTube frequently rotates player clients (`visionos`, `web_embedded`, `android_vr`, `ios`), PO-token requirements, and `n`-challenge JS obfuscation; pinned minimum versions (`yt-dlp[default]>=2025.1.0`, `pytubefix>=8.0.0`) quickly become stale if environments don't upgrade.
- Impact: Metadata extraction (`heatmap`) and stream slicing fail with HTTP 403 or missing formats.
- Migration plan: Pin tested versions or upgrade `yt-dlp` regularly and rely on remote EJS solver updates (`ejs:github`).

**`moviepy>=1.0.3`:**
- Risk: MoviePy 1.x vs 2.x had breaking API changes (`moviepy.editor` vs `moviepy`, `subclip` vs `subclipped`, `crop` vs `cropped`), handled via duck-typing in `video_trimmer.py:1908-1944` with no automated test exercising the MoviePy fallback branch.
- Impact: Fallback trimming could silently break if MoviePy 2.x updates further or FFmpeg fails.
- Migration plan: Either test the MoviePy fallback path explicitly with a mocked FFmpeg failure or remove the fallback if FFmpeg (`imageio-ffmpeg`/`static-ffmpeg`) is mandatory.

## Missing Critical Features

- **Automated Temp Workspace Reaper:** No background or startup cleanup of orphaned `yt_trimmer_*` temporary directories (`app.py:160`).
- **URL Variant Support:** `is_valid_youtube_url()` (`utils.py:436-443`) rejects `music.youtube.com`, `youtube-nocookie.com`, and `youtube.com/clip/...` links.

## Test Coverage Gaps

**Untested Network & Download Orchestration (`yt_processor.py`, `video_trimmer.py`):**
- What's not tested: `fetch_video_metadata()`, `_try_python_hls_direct_slice()`, `_try_python_http_range_slice()`, `download_full_video()`, `download_single_clip_section()`, `download_and_trim_segments_direct()`, `_download_with_pytubefix()`, and the MoviePy fallback in `trim_clip_ffmpeg()`.
- Files: `yt_processor.py:49-168`, `video_trimmer.py:874-1027, 1273-1851, 1908-1945`
- Risk: Regressions in client-profile fallbacks or MoviePy API compatibility go undetected until runtime.
- Priority: High

**Untested Cookie Disk Lifecycle & Diagnostics (`utils.py`, `video_trimmer.py`):**
- What's not tested: `save_yt_cookies()`, `clear_yt_cookies()`, `get_yt_cookies_file()` resolution order, `get_cookie_stats()`, `get_ffmpeg_diagnostics()`, and `sanitize_filename()`.
- Files: `utils.py:271-386, 446-450`, `video_trimmer.py:361-388`
- Risk: Cookie path precedence bugs or `MozillaCookieJar` errors could break authentication or leak cookies across files.
- Priority: Medium

**Untested Streamlit UI & Session State (`app.py`, `streamlit_app.py`):**
- What's not tested: No Streamlit `AppTest` or smoke tests verify `app.py` rendering, demo mode, `raw_clips` re-cropping, or `streamlit_app.py` delegation.
- Files: `app.py`, `streamlit_app.py`
- Risk: Streamlit version upgrades (e.g., `use_container_width` deprecation, `st.session_state` widget key rules) can break the UI silently.
- Priority: Medium

---

*Concerns audit: 2026-11-11*

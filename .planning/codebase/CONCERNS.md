---
last_mapped_commit: 93439675fb29331f3aabaa6cefada3bd2967cb13
last_mapped_at: 2026-11-12
---
# Codebase Concerns

**Analysis Date:** 2026-11-12

## Tech Debt

**Monolithic Video Processing Module (`video_trimmer.py`):**
- Issue: `video_trimmer.py` is 2,002 lines and combines six distinct responsibilities in one file: crop filter generation (`CropConfig`, `build_crop_vf_filter`, `compute_crop_box_normalized`), FFmpeg discovery/shim setup (`get_ffmpeg_path`, `ensure_ffmpeg`, `get_ffmpeg_diagnostics`), IPv4 HTTP adapter plumbing (`_IPv4HTTPConnection`, `_IPv4HTTPSConnection`, `_fetch_bytes_ipv4`), HLS playlist slicing (`slice_hls_playlist_to_file`, `_try_python_hls_direct_slice`), an embedded localhost HTTP Range proxy (`run_http_range_relay_trim`, `_try_python_http_range_slice`), and yt-dlp/pytubefix/MoviePy download, trim, and ZIP packaging (`download_full_video`, `download_single_clip_section`, `trim_clip_ffmpeg`, `create_clips_zip`).
- Files: `video_trimmer.py`
- Impact: High cognitive load, tight coupling between network transport and media encoding, and difficult isolated unit testing.
- Fix approach: Split into focused modules (`cropper.py`, `ffmpeg_runtime.py`, `hls_slicer.py`, `http_range_relay.py`, `downloader.py`) behind small explicit interfaces.

**Monolithic Streamlit Entrypoint (`app.py`):**
- Issue: `app.py` is 1,155 lines mixing top-level UI rendering, CSS injection, session initialization (`init_session_state`), Plotly figure construction (`build_heatmap_figure`), HTML crop previews (`render_crop_framing_preview`), cookie management UIs (duplicated in sidebar lines 455–503 and main pane lines 553–574), synthetic demo video generation (`ensure_source_video` lines 858–877), and smart caching/trimming orchestration (`trim_single_segment_smart` lines 900–986). Also includes a `try/except ImportError` `importlib.reload(sys.modules["video_trimmer"])` workaround at lines 54–90.
- Files: `app.py`, `streamlit_app.py`
- Impact: Any UI change risks breaking trimming/caching state transitions; business logic in `trim_single_segment_smart` and `ensure_source_video` cannot be tested outside Streamlit.
- Fix approach: Move trimming orchestration, filename generation, and demo media creation into a backend service module and split UI panels into dedicated view functions.

**Import-Time Side Effects:**
- Issue: Importing core modules immediately mutates process state: `utils.py:133` runs `sanitize_environment()` (mutating `os.environ` and writing to `/tmp/env_cookies.txt`), `video_trimmer.py:42-44` runs `sanitize_environment()` and sets `YTDLP_JS_RUNTIMES`, `video_trimmer.py:392` runs `ensure_ffmpeg()` (spawning subprocesses and mutating `PATH`), and `app.py:14-15` + `streamlit_app.py:9-12` call `os.chdir(PROJECT_ROOT)`.
- Files: `utils.py`, `video_trimmer.py`, `app.py`, `streamlit_app.py`
- Impact: Tests, linters, or external callers importing a single helper unexpectedly modify environment variables, working directory, `PATH`, and `/tmp`.
- Fix approach: Move initialization into an explicit `bootstrap_runtime()` function invoked only by app entrypoints.

**Silent Broad Exception Swallowing:**
- Issue: Over 50 `except Exception:` blocks silently discard errors without logging (`video_trimmer.py` has 31 occurrences, `utils.py` 13, `app.py` 7, `yt_processor.py` 3). Critical fallback boundaries at `video_trimmer.py:1024-1025`, `video_trimmer.py:1362-1363`, `video_trimmer.py:1795-1796`, and `app.py:970-971, 1011-1012` drop root-cause exceptions completely. Additionally, `YdlCaptureLogger` (`utils.py:22-41`) collects warnings and errors in memory, but callers (`yt_processor.py:97`, `video_trimmer.py:581, 1480`) never read or log `ydl_logger.warnings` / `ydl_logger.errors`.
- Files: `video_trimmer.py`, `utils.py`, `yt_processor.py`, `app.py`
- Impact: Network blocks, cookie parse failures, and FFmpeg errors fail silently or fall back to slow full-video downloads with no diagnostic trail.
- Fix approach: Introduce Python `logging` across all modules, record per-tier failure reasons, and surface structured diagnostics when all tiers fail.

**Duplicated Client Profiles, Filename Logic, and Deployment Drift:**
- Issue: YouTube `player_client` profiles are defined separately in four places (`yt_processor.py:65-92`, `video_trimmer.py:404-485`, `video_trimmer.py:891-900`, `video_trimmer.py:1293-1307`), while `README.md:53` documents outdated client names (`tv`, `web_creator`, `web_safari`, `mweb`, `tv_embedded`). Clip filename formatting is duplicated in `app.py:912-917`, `video_trimmer.py:1818-1827`, and `video_trimmer.py:1963-1972`. Deployment configs also disagree on Python version (`runtime.txt` and `.python-version` specify `3.12`; `Dockerfile:1` uses `python:3.11-slim`) and Node.js (`packages.txt` installs `nodejs`, `Dockerfile` omits it).
- Files: `yt_processor.py`, `video_trimmer.py`, `app.py`, `README.md`, `Dockerfile`, `runtime.txt`, `.python-version`, `packages.txt`
- Impact: Client workarounds fixed in one tier are missed in others; containerized builds behave differently from Streamlit Cloud builds.
- Fix approach: Centralize client profile definitions and clip path builders in shared helpers, and align `Dockerfile` with Python 3.12 + `nodejs`.

## Known Bugs

**Stale Raw Clip Cache Ignores Resolution Changes (`raw_clips`):**
- Symptoms: Changing **Max Download Resolution** (e.g., from `360p` to `1080p`) and clicking **Re-Trim / Apply Crop** reuses the lower-resolution cached clip instead of downloading at the new resolution.
- Files: `app.py:909-930`
- Trigger: Trim a single segment at `360p` (which populates `st.session_state["raw_clips"][rank]`), then change `max_resolution` to `1080p` and click `🔄 Re-Trim / Apply Crop`.
- Workaround: Click **Clear Temporary Workspace** in the sidebar before re-trimming at a higher resolution.

**Clip Card Crop Badge Reflects Current Sidebar Mode Instead of Trimmed File Mode:**
- Symptoms: Changing the sidebar **Aspect Ratio / Crop Mode** immediately updates the `crop_pill` badge on already-trimmed clip cards to show the *new* sidebar mode rather than the crop actually encoded into the saved `.mp4` file.
- Files: `app.py:1093-1097`
- Trigger: Trim a clip with `9:16` crop, then switch the sidebar selectbox to `1:1` without clicking Re-Trim.
- Workaround: Re-trim the clip after changing sidebar crop settings.

**Trim-All Path Does Not Populate `raw_clips` Cache:**
- Symptoms: Using **Trim All Highlight Clips** in Direct Stream-Trim mode (`app.py:1001-1009`) never populates `st.session_state["raw_clips"]`, so subsequent single-clip re-crops must re-download from YouTube instead of cropping locally.
- Files: `app.py:989-1044`
- Trigger: Run **Trim All** in Direct Stream-Trim mode, then change crop settings and click **Re-Trim / Apply Crop** on an individual card.
- Workaround: Trim clips individually or use full-video download mode.

**Extra Duration Added When Trimming Cached Raw / Tier-3 Clips (`clip_dur + 5.0`):**
- Symptoms: Re-cropping a cached raw clip (`app.py:926, 963`) or cropping a Tier-3 `yt-dlp` section download (`video_trimmer.py:1739`) passes `end_time=clip_dur + 5.0` (or `target_seg.duration + 5.0`) to `trim_clip_ffmpeg`, allowing clips to exceed the requested duration by up to 5 seconds when `yt-dlp` includes surrounding GOP frames.
- Files: `app.py:926, 963`, `video_trimmer.py:1739`
- Trigger: Trim via Tier 3 or re-crop a raw clip sourced from a non-keyframe-aligned section download.
- Workaround: None; requires passing exact `target_seg.duration`.

**Custom Timestamp Range Expander Hidden When No Segments Exist and Does Not Trim on Click:**
- Symptoms: Clicking `"✂️ Add & Trim Custom Clip"` (`app.py:826-847`) appends the segment to `st.session_state["segments"]` and calls `st.rerun()` without actually trimming it. Additionally, the entire expander (`app.py:818`) is nested inside `if segments:` (`app.py:733`), making manual trimming unreachable if automatic extraction returns zero segments.
- Files: `app.py:733, 818-847`
- Trigger: Click **Add & Trim Custom Clip** (only adds row to table), or load a video with `duration == 0` / empty segments.
- Workaround: After clicking **Add & Trim Custom Clip**, click the newly added card's inline **Trim & Prepare Clip** button.

**MoviePy Fallback Silently Drops `9:16_blur` Cropping:**
- Symptoms: If FFmpeg CLI fails and `trim_clip_ffmpeg` falls back to MoviePy, `9:16_blur` mode is explicitly skipped (`crop_config.mode != "9:16_blur"`), producing an uncropped clip without warning.
- Files: `video_trimmer.py:1920`
- Trigger: Run `trim_clip_ffmpeg` with `CropConfig(mode="9:16_blur")` when FFmpeg subprocess fails or `ensure_ffmpeg()` returns `None`.
- Workaround: Ensure FFmpeg CLI is installed and runnable.

**Raw Master Cookie Path Returned on Read Error:**
- Symptoms: In `get_yt_cookies_file()`, if reading/sanitizing a persistent cookie file raises an exception, the `except Exception:` block returns `str(p.resolve())` directly (`utils.py:324-325`), allowing `yt-dlp` to mutate/overwrite the master cookie file in-place.
- Files: `utils.py:318-325`
- Trigger: Corrupt/non-UTF-8 `user_cookies.txt` on disk.
- Workaround: Re-save valid UTF-8 cookies or clear cookies.

## Security Considerations

**Cross-Session Cookie Leakage & Plaintext Disk Persistence:**
- Risk: `save_yt_cookies` (`utils.py:271-280`) writes user-pasted YouTube session cookies in plaintext to `user_cookies.txt` in the working directory and `/tmp/user_cookies.txt`. `get_yt_cookies_file()` (`utils.py:303-359`) reads from CWD `user_cookies.txt`, `/tmp/user_cookies.txt`, `/tmp/env_cookies.txt`, `st.secrets`, and `st.session_state` globally. On `app.py:166-177`, `init_session_state()` reads `user_cookies.txt` from disk and populates `st.session_state["cookie_textarea_box"]` — exposing one user's authenticated YouTube cookies in plaintext in the sidebar UI to every other visitor on a shared Streamlit deployment.
- Files: `utils.py:100-109, 260-360`, `app.py:166-177, 466-488`
- Current mitigation: `user_cookies.txt` and `*cookies*.txt` are listed in `.gitignore`.
- Recommendations: Never persist user-pasted cookies to shared CWD/`/tmp` files or pre-fill `cookie_textarea_box` from disk in multi-user web mode; keep pasted cookies strictly in `st.session_state`, create ephemeral `0o600` temp files per request, and unlink them in `finally` blocks.

**Cookies and Secrets Not Excluded from Docker Image Builds:**
- Risk: `.dockerignore` excludes `.git`, `downloads`, `trimmed_clips`, `*.mp4`, and `*.zip`, but does **not** exclude `user_cookies.txt`, `cookies.txt`, `*cookies*.txt`, `.env*`, or `.streamlit/secrets.toml`. Since `Dockerfile:23` executes `COPY --chown=user . .` and `user_cookies.txt` currently exists in the repo root, running `docker build` bakes active session cookies directly into the container image layer.
- Files: `.dockerignore`, `Dockerfile:23`, `user_cookies.txt` (present on disk)
- Current mitigation: `.gitignore` prevents committing `user_cookies.txt` to git, but provides no protection for `docker build`.
- Recommendations: Add `*cookies*`, `.env*`, `.streamlit/secrets.toml`, and `raw_clips_cache/` to `.dockerignore` immediately.

**TLS Certificate Verification Disabled Globally (`nocheckcertificate: True`):**
- Risk: Every `yt-dlp` option dictionary sets `"nocheckcertificate": True`, disabling HTTPS certificate verification during metadata extraction, format probing, and stream downloads (exposing authenticated requests and cookie headers to MitM interception).
- Files: `yt_processor.py:106`, `video_trimmer.py:585, 1484`
- Current mitigation: None.
- Recommendations: Remove `"nocheckcertificate": True` and rely on system/certifi CA bundles.

**Unescaped HTML Interpolation (`unsafe_allow_html=True`):**
- Risk: `render_crop_framing_preview` (`app.py:258-294`) interpolates `thumb_url` directly into an inline CSS `background-image: url('{thumb_url}')` string, and clip card rendering (`app.py:1099-1114`) interpolates `seg.time_range_str`, `seg.source`, and `active_crop_config.mode` into raw HTML. Crafted external metadata could break out of the style/HTML context.
- Files: `app.py:258-294, 1099-1114`
- Current mitigation: URLs are first validated as YouTube URLs (`utils.py:436-443`), though `is_valid_youtube_url` permits arbitrary suffixes (`.*$`) after the 11-character ID.
- Recommendations: HTML/URL-escape `thumb_url` and metadata fields using `html.escape()` / `urllib.parse.quote`, or use native Streamlit components (`st.image`, `st.badge`).

**Unauthenticated Ephemeral Localhost Relay & Permissive File Permissions:**
- Risk: `run_http_range_relay_trim` (`video_trimmer.py:1209-1212`) opens an unauthenticated HTTP proxy on `127.0.0.1:0` forwarding signed `googlevideo` requests during FFmpeg execution; any local process on a shared host can query `/v`, `/a`, or `/m` while active. Additionally, core Python files (`app.py`, `utils.py`, `yt_processor.py`, `video_trimmer.py`) have `0777` filesystem permissions on disk.
- Files: `video_trimmer.py:1136-1270`
- Current mitigation: Bound strictly to `127.0.0.1` and shut down in `finally`.
- Recommendations: Require a per-invocation random bearer path prefix or token header on the relay server, and normalize repository file permissions to `0644`.

## Performance Bottlenecks

**Repeated Redundant `yt-dlp.extract_info()` Probes Across Tiers & Clips:**
- Problem: Trimming `K` segments via `download_and_trim_segments_direct` (`video_trimmer.py:1823-1849`) calls `download_single_clip_section` `K` separate times. Each call independently runs `ytdl.extract_info(url, download=False)` across up to 5 profiles in Tier 1 (`video_trimmer.py:891-911`), up to 8 profiles in Tier 2 (`video_trimmer.py:1293-1317`), and up to 10 profiles in Tier 3 (`video_trimmer.py:1691-1719`) — ignoring the `info["formats"]` already fetched by `fetch_video_metadata` (`yt_processor.py:124`).
- Files: `video_trimmer.py:874-1027, 1273-1365, 1616-1851`, `yt_processor.py:49-130`
- Cause: No shared format/client-profile cache or reuse of resolved stream URLs across the `K` clips of the same video.
- Improvement path: Cache the working player client profile and resolved stream URLs per video session (respecting `expire=` timestamps) and reuse them across all `K` segments.

**Sequential Segment Downloads & Encodes:**
- Problem: `slice_hls_playlist_to_file` (`video_trimmer.py:788-797`) fetches every `.ts` segment one by one; `_try_python_hls_direct_slice` (`video_trimmer.py:963-979`) downloads video and audio playlists sequentially; `download_and_trim_segments_direct` and `trim_selected_segments` process all `K` clips sequentially.
- Files: `video_trimmer.py:788-797, 963-979, 1823-1849, 1968-1990`
- Cause: Single-threaded loops without `ThreadPoolExecutor` / bounded worker pools.
- Improvement path: Download HLS segments and independent A/V tracks concurrently with a small bounded thread pool (e.g., 3–4 workers).

**Full-File Memory Reads on Every Render for `st.download_button`:**
- Problem: `app.py:1066-1074` (`zf_bytes.read()`) and `app.py:1121-1129` (`f_mp4.read()`) read entire `.zip` and `.mp4` files into Python memory on every Streamlit rerun while clip cards are displayed.
- Files: `app.py:1066-1074, 1121-1129`
- Cause: Passing raw `bytes` rather than a file handle (`st.download_button(..., data=f_mp4)`) or streaming.
- Improvement path: Pass opened binary file objects or cache/limit previewed payloads to prevent memory spikes under Streamlit Cloud's 1 GB RAM cap.

**Unmemoized FFmpeg & Cookie Subprocess/Disk Work on Every Rerun:**
- Problem: Every Streamlit UI interaction triggers `get_cookie_stats()` (`app.py:455`, which re-reads, re-sanitizes, and re-writes `/tmp/yt_working_cookies_<pid>.txt`), `ensure_ffmpeg()` (`app.py:506`, spawning `ffmpeg -version`), `get_ffmpeg_diagnostics()` (`app.py:513`, unconditionally executing up to 4 `subprocess.run` checks even when the expander is collapsed), and `get_video_dimensions(Path(clip_file))` (`app.py:1091`, spawning `ffmpeg -version` + `ffmpeg -i` for every trimmed clip).
- Files: `app.py:455, 506, 512-513, 1091`, `video_trimmer.py:194-215, 221-399`, `utils.py:303-385`
- Cause: Lack of `@st.cache_resource` / `@st.cache_data` memoization and eager evaluation inside collapsed `st.expander` blocks.
- Improvement path: Memoize FFmpeg discovery and clip dimensions by file path/mtime, and compute diagnostics lazily only on explicit user button click.

## Fragile Areas

**Shared Per-PID Working Cookie File Race Condition:**
- Files: `utils.py:260-268, 303-359`
- Why fragile: `_write_working_cookie_copy` always writes to the fixed filename `yt_working_cookies_{os.getpid()}.txt` in `tempfile.gettempdir()`. Because Streamlit serves concurrent sessions/requests as threads inside one Python process (same PID), concurrent calls to `get_cookie_stats()` or `get_yt_cookies_file()` overwrite the same file while another thread's `yt-dlp` instance is reading or mutating it.
- Safe modification: Replace the fixed PID filename with `tempfile.NamedTemporaryFile(delete=False, suffix=".txt")` per operation and delete it in a `finally` block.
- Test coverage: None (`get_yt_cookies_file`, `save_yt_cookies`, `clear_yt_cookies`, and `get_cookie_stats` have no tests).

**Custom HLS Playlist Parser & Concatenation (`slice_hls_playlist_to_file`):**
- Files: `video_trimmer.py:680-799, 930-1017`
- Why fragile: Hand-rolled `.m3u8` parser does not support `#EXT-X-KEY` (encrypted segments), `#EXT-X-DISCONTINUITY` (timeline resets), or master playlists referencing separate `#EXT-X-MEDIA` audio renditions. Moreover, `local_offset = max(0.0, start_time - first_seg_start)` (`line 776`) assumes internal media PTS aligns linearly with cumulative `#EXTINF` durations, which can drift on segments with non-zero PTS start or variable keyframes.
- Safe modification: Parse `#EXT-X-KEY` / `#EXT-X-DISCONTINUITY` (aborting cleanly to Tier 2/3 when unsupported tags appear) and verify trimmed clip duration via `ffprobe`/stream metadata.
- Test coverage: Tested only against a simple local single-playlist HLS stream (`tests/test_processor.py:346-376`); master playlists, `#EXT-X-MAP`, and `#EXT-X-BYTERANGE` are untested.

**Regex Parsing of `ffmpeg -i` Stderr (`get_video_dimensions`):**
- Files: `video_trimmer.py:194-215`
- Why fragile: Relies on `re.search(r"Stream #\d+:\d+.*Video:.*?,\s*(\d{2,5})x(\d{2,5})(?:\s|\[|,)", proc.stderr)` against human-readable FFmpeg stderr text, which varies across FFmpeg builds and pixel/color-space strings.
- Safe modification: Prefer `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of json` with stderr regex only as fallback.
- Test coverage: Tested only on synthetic `640x360` H.264 testsrc MP4s (`tests/test_processor.py:310-344`).

**No Lifecycle Cleanup for Temporary Workspaces & Caches:**
- Files: `app.py:160, 515-526`, `utils.py:12-19, 266`
- Why fragile: Each Streamlit session creates `yt_trimmer_*` in `/tmp` (`app.py:160`), and `utils.py` / `video_trimmer.py` write `/tmp/yt_dlp_cache`, `/tmp/ffmpeg_bin`, and `/tmp/yt_working_cookies_*.txt`. If users close the browser tab without clicking **Clear Temporary Workspace**, these directories persist until host restart.
- Safe modification: Add startup/periodic TTL pruning for stale `yt_trimmer_*` directories and temporary cookie files older than e.g. 2 hours.
- Test coverage: None.

## Scaling Limits

**Single-Container Memory & Ephemeral Disk Ceilings:**
- Current capacity: Designed for single-user or low-concurrency usage on Streamlit Community Cloud (~1 GB RAM) or Hugging Face Spaces (`Dockerfile`, port 7860).
- Limit: Concurrent `libx264` re-encoding (especially `9:16_blur` with `boxblur=20:5` + `overlay` in `video_trimmer.py:124-131`), holding full `.mp4`/`.zip` buffers in `st.download_button` (`app.py:1069, 1124`), and accumulating full-video downloads (`download_full_video`) plus `raw_clips` and `trimmed_clips` in `workspace_dir` will exhaust RAM/disk at 2–3 concurrent users or long 1080p videos.
- Scaling path: Stream download responses without full-buffer `bytes` reads, enforce per-workspace disk quotas and TTL eviction, and offload FFmpeg transcoding to bounded background job queues if multi-user traffic grows.

**Sequential Multi-Clip Timeout Horizon:**
- Current capacity: `top_k` up to 10 clips (`app.py:306`), with Tier 2 HTTP relay timeout of `120s` per clip (`video_trimmer.py:1124`) and socket timeouts of `20–25s` across multiple fallback profiles.
- Limit: A blocked or slow video in **Trim All** can compound retries across 10 clips × 4 tiers × 5–10 profiles, exceeding Streamlit/proxy request timeouts (typically 60–300s).
- Scaling path: Reuse resolved stream URLs/working client across all clips in a batch and fail fast on non-recoverable HTTP errors.

## Dependencies at Risk

**Unpinned Upper Bounds & Redundant Media Packages (`requirements.txt`):**
- Risk: Every dependency uses `>=` without upper bounds (`streamlit>=1.35.0`, `yt-dlp[default]>=2025.1.0`, `deno>=2.3.0`, `pytubefix>=8.0.0`, `curl_cffi>=0.7.0`, `static-ffmpeg>=2.7`, `imageio-ffmpeg>=0.4.9`, `moviepy>=1.0.3`, `ffmpeg-python>=0.2.0`, `pandas>=2.0.0`, `numpy>=1.24.0`, `plotly>=5.18.0`).
- Impact: `moviepy` 2.x introduced breaking API changes (requiring runtime compatibility shims in `video_trimmer.py:1908-1944`), `ffmpeg-python>=0.2.0` is unmaintained and completely unused in the codebase, `pytubefix` emits Python 3.12 `SyntaxWarning`s (suppressed in `yt_processor.py:134` and `video_trimmer.py:1379`), and `static-ffmpeg` + `imageio-ffmpeg` duplicate system `ffmpeg`.
- Migration plan: Remove unused `ffmpeg-python`, consolidate FFmpeg provisioning, and pin tested version ranges (or commit a lockfile).

**Runtime Remote Code Fetch (`"remote_components": ["ejs:github"]`):**
- Risk: `yt_processor.py:110` and `video_trimmer.py:589, 1493` configure `yt-dlp` to download EJS solver components from GitHub at runtime if missing from `/tmp/yt_dlp_cache`.
- Impact: Air-gapped or GitHub-throttled environments fail signature/n-challenge solving, and runtime behavior depends on external GitHub releases.
- Migration plan: Pre-bundle or pin compatible `yt-dlp-ejs` components at image build time.

**Volatility of Undocumented YouTube Player Clients:**
- Risk: Extraction and trimming rely heavily on specific InnerTube clients (`visionos`, `web_embedded`, `tv_samsung`, `android`, `android_vr`, `ios`) and YouTube's unstandardized `heatmap` JSON structure.
- Impact: Any YouTube server-side deprecation of `visionos` or `web_embedded` stream formats immediately forces slower Tier 3/4 fallbacks or bot-wall failures.
- Migration plan: Keep `yt-dlp` regularly updated and maintain automated smoke checks (separate from unit tests) for active client profiles.

## Missing Critical Features

**Observability & Structured Failure Reporting:**
- Problem: No `logging` configuration exists anywhere in the repository; `YdlCaptureLogger` captures `yt-dlp` errors/warnings that are never inspected, and fallback tiers swallow exceptions silently.
- Blocks: Diagnosing production 403/PO-token failures, cookie expiration issues, or FFmpeg filter errors on cloud deployments.

**Workspace & Temp-File Retention / Eviction Policy:**
- Problem: No automatic cleanup of orphaned `yt_trimmer_*` directories, cached raw/trimmed MP4s, or temporary cookie files.
- Blocks: Safe long-running multi-user container deployments without manual **Clear Temporary Workspace** clicks or periodic container restarts.

**URL & Custom Timestamp Input Validation Edge Cases:**
- Problem: `is_valid_youtube_url` (`utils.py:436-443`) uses `.*$` after the 11-char ID without validating URL query/path boundaries, and `parse_timestamp` (`utils.py:424-433`) accepts negative numbers or out-of-range minutes/seconds (e.g., `"01:99"` or `"-5"`).
- Blocks: Rejecting malformed user inputs early before invoking `yt-dlp` or FFmpeg.

## Test Coverage Gaps

**Zero Coverage of Frontend & Orchestration (`app.py`, `streamlit_app.py`):**
- What's not tested: All 1,155 lines of `app.py` and 23 lines of `streamlit_app.py`, including `init_session_state`, `build_heatmap_figure`, `render_crop_framing_preview`, `ensure_source_video`, `trim_single_segment_smart`, and UI state transitions.
- Files: `app.py`, `streamlit_app.py`
- Risk: Regressions in caching keys (`raw_clips` vs `max_resolution`), cookie pre-fill behavior, and custom timestamp flows go undetected.
- Priority: High

**Zero Coverage of Network Extraction & 4-Tier Download Pipeline:**
- What's not tested: `fetch_video_metadata` (`yt_processor.py:49-168`), `_try_python_hls_direct_slice` (`video_trimmer.py:874-1027`), `_try_python_http_range_slice` (`video_trimmer.py:1273-1365`), `_download_with_pytubefix` (`video_trimmer.py:1368-1466`), `download_full_video` (`video_trimmer.py:1536-1613`), `download_single_clip_section` (`video_trimmer.py:1616-1800`), and `download_and_trim_segments_direct` (`video_trimmer.py:1803-1851`).
- Files: `yt_processor.py`, `video_trimmer.py`, `tests/test_processor.py`
- Risk: Broken tier handoffs, `yt-dlp` option regressions, and format selection edge cases are not caught before deployment.
- Priority: High

**Untested Cookie Persistence, Clearing, and Diagnostics (`utils.py`):**
- What's not tested: `save_yt_cookies`, `clear_yt_cookies`, `get_yt_cookies_file` (disk/`env_cookies.txt`/`st.secrets`/`st.session_state` precedence), and `get_cookie_stats`. (`test_get_yt_cookies_file_and_json_conversion` in `tests/test_processor.py:268-298` only tests `sanitize_cookie_content`, not `get_yt_cookies_file`.)
- Files: `utils.py:260-385`, `tests/test_processor.py:268-298`
- Risk: Cookie file locking/overwriting regressions or broken secret fallback paths go unnoticed.
- Priority: High

**Untested Fallback, HLS, and MoviePy Code Paths:**
- What's not tested: `extract_fallback_segments` description-timestamp regex and uniform fallback (`yt_processor.py:440-481`), `slice_hls_playlist_to_file` master playlist / `#EXT-X-MAP` / `#EXT-X-BYTERANGE` parsing (`video_trimmer.py:709-761`), `trim_clip_ffmpeg` `fast_copy=True` and MoviePy fallback (`video_trimmer.py:1874-1883, 1908-1945`), and `test_http_range_relay_trim_end_to_end` skips whenever loopback sockets are restricted (`tests/test_processor.py:530` — current run: 22 passed, 1 skipped).
- Files: `yt_processor.py`, `video_trimmer.py`, `tests/test_processor.py`
- Risk: Subtle breakage in fallback tiers and HLS byte-range/fMP4 parsing.
- Priority: Medium

---

*Concerns audit: 2026-11-12*

---
last_mapped_commit: 953c1cc506faa8731517922dfb9aaa818ca2b187
last_mapped_at: 2026-11-11
---
# Coding Conventions

**Analysis Date:** 2026-11-11

## Naming Patterns

**Files:**
- Use flat `snake_case.py` module filenames at repository root (`yt_processor.py`, `video_trimmer.py`, `streamlit_app.py`, `utils.py`, `app.py`)
- Test files follow `tests/test_<name>.py` (`tests/test_processor.py`)

**Functions:**
- Use `snake_case` for all public functions (`extract_most_viewed_segments`, `build_crop_vf_filter`, `sanitize_cookie_content`)
- Prefix internal/module-private helpers with a single leading underscore (`_build_client_profiles`, `_select_hls_formats`, `_write_working_cookie_copy`, `_parse_cookie_expiry`)
- Test methods begin with `test_` (`test_heatmap_peak_extraction`, `test_crop_config_and_vf_builder`)

**Variables:**
- Use `snake_case` for local variables, arguments, and dataclass fields (`start_time`, `max_height`, `crop_config`)
- Use `UPPER_SNAKE_CASE` for module-level constants and path-guard globals (`CROP_PRESETS`, `STRETCH_WIDTH`, `PROJECT_ROOT`, `ROOT_DIR`, `_IPV4_OPENER`, `_ROOT`)

**Types:**
- Use `PascalCase` for dataclasses, classes, and test suites (`HighlightSegment`, `CropConfig`, `YdlCaptureLogger`, `TestYouTubeMostViewedTrimmer`)
- Prefix private helper classes with `_` (`_IPv4HTTPConnection`, `_IPv4HTTPSConnection`, `_RelayHandler`, `_ThreadedServer`)

## Code Style

**Formatting:**
- 4-space indentation, double quotes for strings, trailing commas in multi-line collections and function signatures
- No formatter config file (no `pyproject.toml`, `.black`, or `setup.cfg`) is present; formatting follows PEP 8 conventions manually
- Section dividers in `app.py` and `video_trimmer.py` use `# ------------------------------------------------------------------------------` comment blocks

**Linting:**
- No linter configuration (no `.ruff.toml`, `.flake8`, or `.mypy.ini`) is present
- Type annotations use `typing` module generics (`List`, `Dict`, `Optional`, `Tuple`, `Callable`, `Any`) rather than Python 3.10+ union syntax (`|`) for backward compatibility with Python 3.9+

## Import Organization

**Order:**
1. Module docstring (`"""..."""`)
2. Standard library imports (`os`, `sys`, `re`, `tempfile`, `pathlib.Path`, `typing`, etc.)
3. `sys.path` root safeguard inserting `Path(__file__).resolve().parent` (`app.py:11-13`, `streamlit_app.py:6-8`, `yt_processor.py:10-12`, `video_trimmer.py:23-25`, `tests/test_processor.py:10`)
4. Third-party imports (`pandas`, `plotly.graph_objects`, `streamlit`) — heavy optional libraries (`yt_dlp`, `pytubefix`, `moviepy`, `imageio_ffmpeg`, `static_ffmpeg`, `deno`) are lazy-imported inside functions
5. Local sibling module imports (`from utils import ...`, `from yt_processor import ...`, `from video_trimmer import ...`)

**Path Aliases:**
- None; all local imports use direct sibling module names enabled by inserting `PROJECT_ROOT` / `_ROOT` onto `sys.path`

## Error Handling

**Patterns:**
- **Fallback cascades:** Network and tool discovery functions (`fetch_video_metadata()`, `get_ffmpeg_path()`, `download_single_clip_section()`, `download_full_video()`, `trim_clip_ffmpeg()`) iterate through prioritized profiles/engines inside `try/except Exception:`, preserving `last_exc` and re-raising only if every fallback fails
- **Non-fatal cleanup & side operations:** File unlinking, directory cleanup, and cookie persistence catch `Exception` with `pass` or `continue` (`utils.py:278-299`, `video_trimmer.py:1018-1023`)
- **UI error display:** Exceptions surfaced to Streamlit users are stripped of ANSI escape codes via `strip_ansi()` (`app.py:890-898`) and paired with actionable bot-bypass guidance when `403`, `bot`, or `sign in` appears
- **Input validation:** Public boundary helpers validate inputs explicitly and return safe defaults or raise `ValueError` (`parse_timestamp()` in `utils.py:424-433`, `slice_hls_playlist_to_file()` in `video_trimmer.py:762-763`)

## Logging

**Framework:** Custom quiet logger class (`YdlCaptureLogger` in `utils.py:22-41`) instead of stdlib `logging`

**Patterns:**
- Suppress `yt-dlp` stdout/stderr noise in production UI by passing `logger=YdlCaptureLogger()` and `"quiet": True`, `"no_warnings": True`, `"no_color": True`
- Capture warnings and errors into `self.warnings` / `self.errors` lists on the logger instance
- Diagnostic output for troubleshooting is exposed synchronously via `get_ffmpeg_diagnostics()` (`video_trimmer.py:361-388`) and rendered in the sidebar expander (`app.py:512-513`)

## Comments

**When to Comment:**
- Use inline comments to explain non-obvious platform constraints or workarounds (e.g., Linux `execve` `E2BIG` limit in `utils.py:92-94`, why `yt-dlp` `cookiefile` must be isolated per PID in `utils.py:261-265`, why cookieless clients are kept separate from cookie clients in `yt_processor.py:62-64`, Streamlit `>=1.40` button width compatibility in `app.py:92`)

**Docstrings:**
- Every file begins with a module-level docstring
- Public functions and dataclasses include descriptive triple-quoted docstrings documenting parameters, behavior, and return types (e.g., `extract_most_viewed_segments()` in `yt_processor.py:283-294`, `download_single_clip_section()` in `video_trimmer.py:1625-1638`)

## Function Design

**Size:** Utility and analysis functions are 10–120 lines; core streaming/UI functions (`fetch_video_metadata`, `sanitize_cookie_content`, `_try_python_hls_direct_slice`, `run_http_range_relay_trim`, `download_single_clip_section`) run 100–185 lines

**Parameters:**
- Use explicit type annotations with keyword defaults on configuration parameters (`top_k: int = 5`, `max_height: int = 720`, `crop_config: Optional[CropConfig] = None`)
- Pass progress reporting as an optional callback:
  - Single-operation functions take `progress_callback: Optional[Callable[[float, str], None]] = None` where `float` is in `[0.0, 1.0]`
  - Batch functions (`download_and_trim_segments_direct`, `trim_selected_segments`) take `progress_callback: Optional[Callable[[int, int, str], None]] = None` `(index, total, message)`
- Always pass `env=get_clean_env()` to `subprocess.run` / `subprocess.Popen` (`video_trimmer.py`) to avoid Linux `E2BIG` errors

**Return Values:**
- Return typed `Path`, `Dict[int, Path]`, `List[HighlightSegment]`, or `Optional[...]` (`None` indicates fallback miss in internal tier helpers `_try_*`)
- Dataclasses (`HighlightSegment`, `CropConfig`) expose derived `@property` attributes and a `to_dict()` serializer via `dataclasses.asdict`

## Module Design

**Exports:**
- Flat module namespaces with no `__all__` declarations; callers import explicit symbols (`from yt_processor import HighlightSegment, ...`)

**Barrel Files:**
- Not used (single-package flat script layout at repo root)

---

*Convention analysis: 2026-11-11*

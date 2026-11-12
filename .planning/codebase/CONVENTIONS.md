---
last_mapped_commit: 93439675fb29331f3aabaa6cefada3bd2967cb13
last_mapped_at: 2026-11-12
---
# Coding Conventions

**Analysis Date:** 2026-11-12

## Naming Patterns

**Files:**
- Use `snake_case.py` for all application and library modules at the repository root (`app.py`, `streamlit_app.py`, `yt_processor.py`, `video_trimmer.py`, `utils.py`).
- Prefix test files with `test_` inside `tests/` (`tests/test_processor.py`).

**Functions:**
- Use `snake_case` for all public functions (`extract_most_viewed_segments`, `build_crop_vf_filter`, `sanitize_cookie_content`, `format_timestamp`).
- Prefix internal/module-private helpers with a single leading underscore `_` (`_build_client_profiles`, `_select_hls_formats`, `_select_https_formats`, `_parse_cookie_expiry`, `_write_working_cookie_copy`).
- Prefix nested helper closures and callback functions with `_` (`_get_format_size`, `_v_rank`, `_on_prog`, `_clip_prog`, `_ydl_fetcher`, `_open_fn`, `_res_label`, `_on_trim_prog`).

**Variables & Constants:**
- Use `snake_case` for local variables, parameters, and dataclass fields (`max_height`, `crop_config`, `pan_x`, `active_cookies`).
- Use `UPPER_SNAKE_CASE` for module-level constants and configuration maps (`CROP_PRESETS` in `video_trimmer.py`, `PROJECT_ROOT` and `STRETCH_WIDTH` in `app.py`, `ROOT_DIR` in `streamlit_app.py`).
- Prefix private module-level constants and singletons with `_` (`_ROOT` in `yt_processor.py` and `video_trimmer.py`, `_IPV4_OPENER` in `video_trimmer.py`).

**Types & Classes:**
- Use `PascalCase` for public classes and `@dataclass` models (`HighlightSegment` in `yt_processor.py`, `CropConfig` in `video_trimmer.py`, `YdlCaptureLogger` in `utils.py`, `TestYouTubeMostViewedTrimmer` in `tests/test_processor.py`).
- Prefix internal helper classes with `_PascalCase` (`_IPv4HTTPConnection`, `_IPv4HTTPSConnection`, `_IPv4HTTPHandler`, `_IPv4HTTPSHandler`, `_RelayHandler`, `_ThreadedServer` in `video_trimmer.py`).

## Code Style

**Formatting:**
- No dedicated formatter config (`.prettierrc`, `pyproject.toml`, `ruff.toml`, or `biome.json`) is present; follow PEP 8 4-space indentation.
- Use double quotes `"` for strings and docstrings; use single quotes `'` only inside f-strings or HTML attributes where needed to avoid escaping.
- Use trailing commas on multi-line function parameters, argument lists, dicts, and lists:
```python
def trim_selected_segments(
    source_video: Path,
    segments: List[HighlightSegment],
    clips_dir: Path,
    video_title: str = "video",
    fast_copy: bool = False,
    crop_config: Optional[CropConfig] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Dict[int, Path]:
```
- Use `Path` from `pathlib` for filesystem paths rather than raw string concatenation (`Path(tempfile.gettempdir()) / "yt_dlp_cache"` in `utils.py`, `output_path.parent.mkdir(parents=True, exist_ok=True)` in `video_trimmer.py`).

**Linting & Type Checking:**
- No linter or static type checker config (`ruff`, `flake8`, `mypy`, `pylint`) is configured in the repository.
- Annotate function parameters, return types, and dataclass fields with `typing` generics (`Optional`, `Dict`, `List`, `Tuple`, `Callable`, `Any`) compatible with Python 3.12 (`runtime.txt` specifies `python-3.12`, `.python-version` specifies `3.12`).

## Import Organization

**Order:**
1. Module docstring (`"""..."""`) at line 1.
2. Python standard library imports (`os`, `sys`, `re`, `json`, `tempfile`, `subprocess`, `threading`, `dataclasses`, `pathlib`, `typing`).
3. Root `sys.path` guard before local imports so modules resolve under Streamlit Cloud and direct script runs:
```python
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
```
4. Third-party top-level imports (`pandas`, `plotly.graph_objects`, `streamlit` in `app.py`). Defer heavy or warning-emitting optional/runtime packages (`yt_dlp`, `pytubefix`, `moviepy`, `imageio_ffmpeg`, `static_ffmpeg`, `deno`) to local imports inside the functions that use them.
5. Local module imports (`from utils import ...`, `from yt_processor import ...`, `from video_trimmer import ...`).

**Path Aliases:**
- None. All imports resolve relative to the repository root via `sys.path.insert(0, str(_ROOT))` (in `yt_processor.py`, `video_trimmer.py`, `tests/test_processor.py`) or `PROJECT_ROOT` / `ROOT_DIR` (in `app.py`, `streamlit_app.py`).
- In `app.py` (`app.py:54-90`), wrap `from video_trimmer import ...` in `try/except ImportError` with `importlib.reload(sys.modules["video_trimmer"])` to survive Streamlit hot-reloads.

## Error Handling

**Patterns:**
- **Multi-tier fallback loops:** Network and extraction functions (`fetch_video_metadata` in `yt_processor.py:95-168`, `download_full_video` and `download_single_clip_section` in `video_trimmer.py:1569-1800`) iterate through prioritized profiles, capture `last_exc = exc` on `Exception`, try secondary engines (`pytubefix`), and re-raise `last_exc` or raise `RuntimeError`/`FileNotFoundError` only after every tier fails.
- **Best-effort cleanup and environment guards:** Non-critical operations (temp file unlinking, `resp.close()`, `os.chdir`, `sanitize_environment`, optional package probes in `get_js_runtimes` and `get_ffmpeg_path`) swallow `Exception` silently (`except Exception: pass` or `continue`).
- **Explicit `ValueError` / `OSError` handling:** Raise `ValueError` for invalid inputs or empty playlists (`parse_timestamp` in `utils.py:433`, `slice_hls_playlist_to_file` in `video_trimmer.py:763`). Check specific `errno` values where OS limits apply (e.g., `getattr(e, "errno", None) == 7` for `E2BIG` in `is_ffmpeg_executable` at `video_trimmer.py:243-245`).
- **Warning suppression for third-party libraries:** Wrap `pytubefix` imports in `warnings.catch_warnings()` with `warnings.simplefilter("ignore", SyntaxWarning)` (`yt_processor.py:133-135`, `video_trimmer.py:1378-1379`).
- **UI error surfacing:** In `app.py:890-898`, route trimming exceptions through `show_trim_error(prefix, exc)` which calls `strip_ansi(str(exc))`, displays `st.error(...)`, and renders contextual `st.warning(...)` bot-bypass instructions when `"403"`, `"bot"`, or `"sign in"` appears.

## Logging

**Framework:** Custom capture logger (`YdlCaptureLogger` in `utils.py:22-41`) instead of Python's `logging` module.

**Patterns:**
- Pass `YdlCaptureLogger()` as `ydl_opts["logger"]` alongside `"quiet": True` and `"no_warnings": True` so `yt-dlp` does not write raw ANSI output to stderr while warnings and errors remain accessible in `logger.warnings` and `logger.errors`.
- Surface runtime progress and status to the UI via callback parameters (`progress_callback: Optional[Callable[[float, str], None]]` for single-clip streams; `Optional[Callable[[int, int, str], None]]` for multi-clip batch functions) connected to `st.progress` and `st.spinner` in `app.py`.
- Suppress FFmpeg banner noise with `"-hide_banner"` / `"-loglevel", "error"` (`video_trimmer.py:203, 1218`) and capture stderr into a `TemporaryFile` or `subprocess.PIPE` (`video_trimmer.py:1236-1263, 1903`).
- Always pass `env=get_clean_env()` to `subprocess.run` and `subprocess.Popen` calls inside `video_trimmer.py` so oversized environment variables cannot trigger Linux `E2BIG`.

## Comments

**When to Comment:**
- Use dashed section banners (`# ------------------------------------------------------------------------------`) to separate major functional groups in `video_trimmer.py` and `app.py`.
- Use inline comments to explain *why* non-obvious constraints exist (e.g., why cookieless clients must not receive `cookiefile` in `yt_processor.py:62-64` and `video_trimmer.py:413`, why `yt-dlp` requires a working copy in `_write_working_cookie_copy` at `utils.py:260-268`, why `trunc(.../2)*2` enforces even dimensions for `yuv420p` in `video_trimmer.py:99-100`).
- Use numbered step comments (`# 1. ...`, `# 2. ...`) inside multi-branch parsing/fallback pipelines (`sanitize_cookie_content` in `utils.py`, `estimate_video_size` in `yt_processor.py`).

**Docstrings:**
- Start every module with a concise triple-quoted module docstring (`utils.py:1`, `yt_processor.py:1`, `video_trimmer.py:1`, `streamlit_app.py:1`, `app.py:1-4`, `tests/test_processor.py:1`).
- Provide triple-quoted docstrings on all public functions, dataclasses, and major private helpers describing inputs, outputs, return tuples, and numbered algorithm/tier steps with backticked symbol names.

## Function Design

**Size:**
- Keep pure helpers in `utils.py` small (5–50 lines).
- Complex orchestration and parsing functions (`extract_most_viewed_segments`, `sanitize_cookie_content`, `slice_hls_playlist_to_file`, `run_http_range_relay_trim`, `download_single_clip_section`) run 100–190 lines and encapsulate small local helpers (`_get_format_size`, `_fetch_bytes`, `_is_direct_http`, `_RelayHandler`) inside the function body rather than polluting module scope.

**Parameters:**
- Accept `pathlib.Path` for file/directory parameters and provide keyword defaults for configuration (`max_height: int = 720`, `top_k: int = 5`, `crop_config: Optional[CropConfig] = None`, `progress_callback: Optional[Callable[...]] = None`).
- Use dataclasses (`CropConfig`, `HighlightSegment`) to bundle multi-field state instead of passing loose parameter bags across modules.

**Return Values:**
- Return `Path` on successful file creation, raising an exception if no output is produced (`download_full_video`, `download_single_clip_section`, `trim_clip_ffmpeg`, `create_clips_zip`).
- Return `Optional[Path]` / `Optional[str]` for best-effort discovery or tier attempts (`ensure_ffmpeg`, `get_yt_cookies_file`, `_try_python_hls_direct_slice`, `_try_python_http_range_slice`).
- Return `Dict[int, Path]` mapping `segment.rank -> clip_path` from batch operations (`trim_selected_segments`, `download_and_trim_segments_direct`).
- Return fixed-shape tuples for multi-candidate format selectors (`(muxed_fmt, video_fmt, audio_fmt)` in `_select_hls_formats` and `_select_https_formats`) and normalized bounds (`(x0, y0, x1, y1)` in `compute_crop_box_normalized`).

## Module Design

**Exports:**
- Use flat root-level Python modules with no `__init__.py` or `__all__` export lists; callers import symbols explicitly by name.

**Barrel Files:**
- Not used. Import directly from `utils`, `yt_processor`, or `video_trimmer`.

**Import-Time Behavior:**
- `utils.py:133` calls `sanitize_environment()` on import.
- `video_trimmer.py:42-44, 392` runs `sanitize_environment()`, sets `os.environ.setdefault("YTDLP_JS_RUNTIMES", "node,deno")`, and calls `ensure_ffmpeg()` on import.
- `streamlit_app.py:22-23` acts as a thin Streamlit Community Cloud wrapper that prepares `sys.path`, `cwd`, and `sanitize_environment()`, then runs `app.py` via `runpy.run_path(str(app_path), run_name="__main__")`.

---

*Convention analysis: 2026-11-12*

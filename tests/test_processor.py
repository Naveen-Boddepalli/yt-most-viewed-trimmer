"""Unit and integration tests for YouTube heatmap extraction, cloud 403/cookie fixes, and video cropping."""

import json
import tempfile
import subprocess
from pathlib import Path
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils
from utils import (
    format_timestamp,
    parse_timestamp,
    is_valid_youtube_url,
    sanitize_filename,
    format_bytes,
    strip_ansi,
    get_yt_cookies_file,
    sanitize_cookie_content,
    sanitize_environment,
    get_clean_env,
    get_js_runtimes,
    get_ytdlp_cache_dir,
    YdlCaptureLogger,
)
from yt_processor import (
    extract_most_viewed_segments,
    extract_fallback_segments,
    estimate_video_size,
    estimate_clip_size,
)
from video_trimmer import (
    CROP_PRESETS,
    CropConfig,
    build_crop_vf_filter,
    compute_crop_box_normalized,
    has_ffmpeg,
    ensure_ffmpeg,
    get_ffmpeg_path,
    is_ffmpeg_executable,
    download_single_clip_section,
    download_and_trim_segments_direct,
    trim_clip_ffmpeg,
    trim_selected_segments,
    create_clips_zip,
    _build_client_profiles,
    _select_hls_formats,
    _select_https_formats,
    _wait_for_available_at,
    run_http_range_relay_trim,
    get_video_dimensions,
    slice_hls_playlist_to_file,
    mux_trim_hls_av,
)


class TestYouTubeMostViewedTrimmer(unittest.TestCase):
    def test_timestamp_helpers(self):
        self.assertEqual(format_timestamp(75), "01:15")
        self.assertEqual(format_timestamp(3661), "01:01:01")
        self.assertEqual(parse_timestamp("01:15"), 75.0)
        self.assertEqual(parse_timestamp("01:01:01"), 3661.0)

    def test_format_bytes(self):
        self.assertEqual(format_bytes(None), "Unknown")
        self.assertEqual(format_bytes(-10), "Unknown")
        self.assertEqual(format_bytes(500), "500 B")
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_bytes(150 * 1024), "150.0 KB")
        self.assertEqual(format_bytes(25 * 1024 * 1024), "25.0 MB")
        self.assertEqual(format_bytes(1500 * 1024 * 1024), "1.46 GB")

    def test_strip_ansi(self):
        self.assertEqual(strip_ansi("\x1b[0;31mERROR:\x1b[0m test"), "ERROR: test")
        self.assertEqual(strip_ansi("\x1b[0;32m 129.25KiB/s \x1b[0m"), " 129.25KiB/s ")
        self.assertEqual(strip_ansi("clean text"), "clean text")
        self.assertEqual(strip_ansi(""), "")

    def test_estimate_video_size(self):
        info_with_formats = {
            "duration": 60.0,
            "formats": [
                {"height": 720, "vcodec": "avc1.4d401f", "acodec": "none", "ext": "mp4", "filesize": 15_000_000},
                {"height": 1080, "vcodec": "avc1.640028", "acodec": "none", "ext": "mp4", "filesize": 35_000_000},
                {"height": None, "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "filesize": 2_000_000, "abr": 128},
            ],
        }
        sz_720 = estimate_video_size(info_with_formats, max_height=720)
        self.assertEqual(sz_720, 17_000_000)

        sz_1080 = estimate_video_size(info_with_formats, max_height=1080)
        self.assertEqual(sz_1080, 37_000_000)

        info_bitrate = {
            "duration": 10.0,
            "formats": [
                {"height": 720, "vcodec": "avc1", "acodec": "none", "ext": "mp4", "tbr": 2000},
                {"height": None, "vcodec": "none", "acodec": "mp4a", "ext": "m4a", "abr": 128},
            ],
        }
        sz_tbr = estimate_video_size(info_bitrate, max_height=720)
        self.assertEqual(sz_tbr, 2_660_000)

        info_fallback = {"duration": 100.0}
        sz_fb_720 = estimate_video_size(info_fallback, max_height=720)
        self.assertGreater(sz_fb_720, 10_000_000)
        sz_fb_360 = estimate_video_size(info_fallback, max_height=360)
        self.assertLess(sz_fb_360, sz_fb_720)

        self.assertEqual(estimate_video_size(None), 0)
        self.assertEqual(estimate_video_size({}), 0)

    def test_estimate_clip_size(self):
        c_sz = estimate_clip_size(clip_duration=15.0, total_duration=60.0, total_size_bytes=20_000_000)
        self.assertEqual(c_sz, 5_000_000)

    def test_url_validation(self):
        self.assertTrue(is_valid_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(is_valid_youtube_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertFalse(is_valid_youtube_url("https://example.com/video.mp4"))

    def test_heatmap_peak_extraction(self):
        heatmap = []
        for i in range(20):
            score = 0.15
            if i == 3:
                score = 0.95
            elif i == 14:
                score = 0.80
            heatmap.append({"start_time": i * 10.0, "end_time": (i + 1) * 10.0, "value": score})

        segs = extract_most_viewed_segments(
            heatmap=heatmap,
            video_duration=200.0,
            top_k=2,
            min_score_threshold=0.5,
            min_clip_duration=12.0,
            max_clip_duration=30.0,
            padding_seconds=2.0,
        )
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].rank, 1)
        self.assertAlmostEqual(segs[0].score, 0.95)
        self.assertTrue(segs[0].start_time <= 30.0 and segs[0].end_time >= 40.0)
        self.assertEqual(segs[1].rank, 2)
        self.assertAlmostEqual(segs[1].score, 0.80)

    def test_fallback_extraction(self):
        info = {
            "duration": 180.0,
            "chapters": [
                {"start_time": 0.0, "end_time": 25.0, "title": "Intro"},
                {"start_time": 25.0, "end_time": 60.0, "title": "Key Demo"},
            ],
        }
        segs = extract_fallback_segments(info, top_k=2, clip_duration=20.0)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0].source, "chapters")

    def test_ffmpeg_detection_and_shim(self):
        self.assertTrue(has_ffmpeg())
        path = ensure_ffmpeg()
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists())

    def test_crop_config_and_vf_builder(self):
        # 1. No crop
        cfg_none = CropConfig(mode="none")
        self.assertFalse(cfg_none.is_active)
        self.assertIsNone(build_crop_vf_filter(cfg_none))

        # 2. 9:16 vertical crop
        cfg_916 = CropConfig(mode="9:16", pan_x=0.25, pan_y=0.5)
        self.assertTrue(cfg_916.is_active)
        vf_916 = build_crop_vf_filter(cfg_916)
        self.assertIsNotNone(vf_916)
        self.assertIn("crop=w=", vf_916)
        self.assertIn("ih*9/16", vf_916)
        x0, y0, x1, y1 = compute_crop_box_normalized(cfg_916, source_aspect=16.0 / 9.0)
        self.assertGreater(x0, 0.0)
        self.assertLess(x1, 1.0)
        self.assertAlmostEqual(y0, 0.0)
        self.assertAlmostEqual(y1, 1.0)

        # 3. 9:16 blur pad mode
        cfg_blur = CropConfig(mode="9:16_blur")
        self.assertTrue(cfg_blur.is_active)
        vf_blur = build_crop_vf_filter(cfg_blur)
        self.assertIn("boxblur", vf_blur)

        # 4. Custom margin crop
        cfg_custom = CropConfig(mode="custom", left_pct=15.0, right_pct=15.0, top_pct=5.0, bottom_pct=5.0)
        self.assertTrue(cfg_custom.is_active)
        vf_custom = build_crop_vf_filter(cfg_custom)
        self.assertIn("crop=w=", vf_custom)
        cx0, cy0, cx1, cy1 = compute_crop_box_normalized(cfg_custom)
        self.assertAlmostEqual(cx0, 0.15)
        self.assertAlmostEqual(cx1, 0.85)

    def test_end_to_end_trim_crop_and_zip(self):
        if not has_ffmpeg():
            self.skipTest("FFmpeg not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_mp4 = tmp_path / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=8:size=640x360:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest",
                    str(src_mp4),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            heatmap = [
                {"start_time": 0.0, "end_time": 2.5, "value": 0.2},
                {"start_time": 2.5, "end_time": 5.5, "value": 0.92},
                {"start_time": 5.5, "end_time": 8.0, "value": 0.3},
            ]
            segs = extract_most_viewed_segments(
                heatmap=heatmap,
                video_duration=8.0,
                top_k=1,
                min_score_threshold=0.5,
                min_clip_duration=2.5,
                max_clip_duration=4.0,
                padding_seconds=0.2,
            )

            # Test trimming with 9:16 vertical crop, 1:1 square crop, 9:16_blur, and custom crop
            for mode in ["none", "9:16", "1:1", "9:16_blur", "custom"]:
                crop_cfg = CropConfig(
                    mode=mode,
                    pan_x=0.5,
                    left_pct=10.0 if mode == "custom" else 0.0,
                    right_pct=10.0 if mode == "custom" else 0.0,
                )
                clips_map = trim_selected_segments(
                    source_video=src_mp4,
                    segments=segs,
                    clips_dir=tmp_path / f"clips_{mode.replace(':', 'x')}",
                    video_title="Unit Test Video",
                    crop_config=crop_cfg,
                )
                self.assertIn(1, clips_map)
                self.assertTrue(clips_map[1].exists())
                self.assertGreater(clips_map[1].stat().st_size, 1000)

            zip_file = create_clips_zip(clips_map, tmp_path / "bundle.zip")
            self.assertTrue(zip_file.exists())

    def test_client_profiles_and_cookie_isolation(self):
        profiles = _build_client_profiles()
        self.assertGreaterEqual(len(profiles), 4)
        # Ensure visionos is prioritized first to bypass datacenter bot challenges
        self.assertEqual(profiles[0]["name"], "visionos_direct")
        self.assertEqual(profiles[0]["clients"], ["visionos"])
        # Ensure cookieless android_vr and ios profiles exist and are separate from web
        cookieless_clients = [p["clients"] for p in profiles if not p["use_cookies"]]
        self.assertIn(["visionos"], cookieless_clients)
        self.assertIn(["android_vr"], cookieless_clients)
        self.assertIn(["ios"], cookieless_clients)

    def test_get_yt_cookies_file_and_json_conversion(self):
        # 1. Test Netscape format + #HttpOnly_ prefix handling
        raw_cookies = (
            "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\ttest_sid_val\n"
            "posit.co\tFALSE\t/\tTRUE\t1797276424\tcmplz_consented_services\n"
            "broken_garbage_line\n"
        )
        sanitized = sanitize_cookie_content(raw_cookies)
        self.assertTrue(sanitized.startswith("# Netscape HTTP Cookie File"))
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\ttest_sid_val", sanitized)
        self.assertIn("posit.co\tFALSE\t/\tTRUE\t1797276424\tcmplz_consented_services\t", sanitized)
        self.assertNotIn("broken_garbage_line", sanitized)

        # 2. Test JSON cookie array automatic conversion to Netscape format
        json_cookies = json.dumps([
            {"domain": ".youtube.com", "path": "/", "secure": True, "expirationDate": 1999999999, "name": "LOGIN_INFO", "value": "abc123xyz"}
        ])
        sanitized_json = sanitize_cookie_content(json_cookies)
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tabc123xyz", sanitized_json)

        # 3. Test DevTools alternating lines (Name \n Value)
        devtools_paste = "ROLLOUT_TOKEN\nCMHqneCitbWR1AEQ2dGj6raRjwMYkeaQyoPeLwM%3D"
        sanitized_devtools = sanitize_cookie_content(devtools_paste)
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1999999999\tROLLOUT_TOKEN\tCMHqneCitbWR1AEQ2dGj6raRjwMYkeaQyoPeLwM%3D", sanitized_devtools)

        # 4. Test Cookie header string format
        cookie_hdr = "Cookie: LOGIN_INFO=afmm123; __Secure-1PSID=sid_val"
        sanitized_hdr = sanitize_cookie_content(cookie_hdr)
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tafmm123", sanitized_hdr)
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1999999999\t__Secure-1PSID\tsid_val", sanitized_hdr)

    def test_sanitize_environment_and_clean_env(self):
        import os
        from unittest import mock
        os.environ["TEST_HUGE_VAR"] = "X" * 10000
        os.environ["TEST_YOUTUBE_COOKIES"] = "# Netscape HTTP Cookie File\nexample.com\tTRUE\t/\tTRUE\t0\tk\tv"
        # Isolate tempdir so the test cookie never lands in the real env_cookies.txt
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tempfile, "tempdir", tmp):
            sanitize_environment()
            env_cf = utils._env_cookie_path()
            self.assertIn("\tk\tv", env_cf.read_text(encoding="utf-8"))
            self.assertEqual(env_cf.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("TEST_HUGE_VAR", os.environ)
        self.assertNotIn("TEST_YOUTUBE_COOKIES", os.environ)
        clean = get_clean_env()
        self.assertIn("PATH", clean)


    def test_video_dimensions_and_aspect_crops(self):
        if not has_ffmpeg():
            self.skipTest("FFmpeg not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_mp4 = tmp_path / "src_16x9.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=4:size=640x360:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest",
                    str(src_mp4),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual(get_video_dimensions(src_mp4), (640, 360))

            # 1. Crop 640x360 (16:9) -> 9:16 vertical -> should be 202x360 (even width!)
            out_916 = tmp_path / "out_9x16.mp4"
            trim_clip_ffmpeg(src_mp4, out_916, 0.5, 2.5, crop_config=CropConfig(mode="9:16", pan_x=0.5))
            self.assertEqual(get_video_dimensions(out_916), (202, 360))

            # 2. Crop 640x360 (16:9) -> 1:1 square -> should be 360x360
            out_1x1 = tmp_path / "out_1x1.mp4"
            trim_clip_ffmpeg(src_mp4, out_1x1, 0.5, 2.5, crop_config=CropConfig(mode="1:1", pan_x=0.5))
            self.assertEqual(get_video_dimensions(out_1x1), (360, 360))

            # 3. Crop 640x360 (16:9) -> 4:5 portrait -> should be 288x360
            out_4x5 = tmp_path / "out_4x5.mp4"
            trim_clip_ffmpeg(src_mp4, out_4x5, 0.5, 2.5, crop_config=CropConfig(mode="4:5", pan_x=0.2))
            self.assertEqual(get_video_dimensions(out_4x5), (288, 360))

    def test_python_native_hls_playlist_slicer(self):
        if not has_ffmpeg():
            self.skipTest("FFmpeg not installed")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            hls_dir = tmp_path / "hls"
            hls_dir.mkdir(parents=True, exist_ok=True)
            m3u8_file = hls_dir / "playlist.m3u8"
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=9:size=320x180:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=9",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-f", "hls", "-hls_time", "3", "-hls_list_size", "0",
                    str(m3u8_file),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            sliced_ts = tmp_path / "sliced.ts"
            out_path, local_offset = slice_hls_playlist_to_file(
                m3u8_url=m3u8_file.as_uri(),
                output_ts_path=sliced_ts,
                start_time=3.5,
                end_time=5.5,
            )
            self.assertTrue(out_path.exists())
            self.assertGreater(out_path.stat().st_size, 1000)
            self.assertGreaterEqual(local_offset, 0.0)

    def test_is_ffmpeg_executable_e2big_handling(self):
        from unittest.mock import patch
        import shutil
        import errno

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg not on PATH")

        # When subprocess.run raises OSError with errno 7 (E2BIG), it should return True because the file exists and is executable
        with patch("subprocess.run", side_effect=OSError(errno.E2BIG, "Argument list too long")):
            self.assertTrue(is_ffmpeg_executable(ffmpeg))

        # But for a non-existent or non-executable path, it returns False
        self.assertFalse(is_ffmpeg_executable("/nonexistent/binary/path"))

    def test_select_hls_formats_separate_av_and_cookieless_guard(self):
        # Verify visionos/android/android_vr/ios never get use_cookies=True
        profiles = _build_client_profiles()
        for prof in profiles:
            clients = prof.get("clients") or []
            if any(c in ("visionos", "android", "android_vr", "ios") for c in clients):
                self.assertFalse(prof["use_cookies"], f"{prof['name']} must not use cookies")

        # Verify separate video+audio HLS selection (visionos style) prefers avc1 H.264 <= max_height
        sample_formats = [
            {"format_id": "233", "protocol": "m3u8_native", "url": "https://ex/a233.m3u8", "vcodec": "none", "acodec": None, "ext": "mp4"},
            {"format_id": "234", "protocol": "m3u8_native", "url": "https://ex/a234.m3u8", "vcodec": "none", "acodec": None, "ext": "mp4"},
            {"format_id": "609", "protocol": "m3u8_native", "url": "https://ex/v609.m3u8", "vcodec": "vp09.00", "acodec": "none", "height": 720, "tbr": 1048},
            {"format_id": "232", "protocol": "m3u8_native", "url": "https://ex/v232.m3u8", "vcodec": "avc1.4D401F", "acodec": "none", "height": 720, "tbr": 1248},
            {"format_id": "270", "protocol": "m3u8_native", "url": "https://ex/v270.m3u8", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "tbr": 4688},
        ]
        muxed, v_fmt, a_fmt = _select_hls_formats(sample_formats, max_height=720)
        self.assertIsNone(muxed)
        self.assertEqual(v_fmt["format_id"], "232")
        self.assertEqual(a_fmt["format_id"], "234")

    def test_devtools_table_cookies_loadable_by_mozilla_cookie_jar(self):
        import http.cookiejar
        devtools_table = "LOGIN_INFO\tafmm123\t.youtube.com\t/\t2027-01-01T00:00:00.000Z\t42\t✓\t✓"
        sanitized = sanitize_cookie_content(devtools_table)
        self.assertIn(".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tafmm123", sanitized)
        with tempfile.TemporaryDirectory() as tmp:
            cf = Path(tmp) / "c.txt"
            cf.write_text(sanitized, encoding="utf-8")
            cj = http.cookiejar.MozillaCookieJar(str(cf))
            cj.load(ignore_discard=True, ignore_expires=True)
            self.assertEqual([c.name for c in cj], ["LOGIN_INFO"])

    def test_get_js_runtimes_structure(self):
        rt = get_js_runtimes()
        self.assertIn("node", rt)
        self.assertIn("deno", rt)

    def test_cache_dir_and_ydl_capture_logger(self):
        cdir = Path(get_ytdlp_cache_dir())
        self.assertTrue(cdir.exists() and cdir.is_dir())
        logger = YdlCaptureLogger()
        logger.debug("dbg")
        logger.warning("warn1")
        logger.error("err1")
        self.assertEqual(logger.warnings, ["warn1"])
        self.assertEqual(logger.errors, ["err1"])

    def test_select_https_formats_and_available_at_wait(self):
        import time
        sample_formats = [
            {"format_id": "sb0", "protocol": "mhtml", "url": "https://ex/sb0.jpg", "vcodec": "none", "acodec": "none"},
            {"format_id": "311", "protocol": "m3u8_native", "url": "https://ex/311.m3u8", "vcodec": "avc1", "acodec": "none", "height": 720},
            {"format_id": "18", "protocol": "https", "url": "https://ex/18.mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "ext": "mp4", "tbr": 400},
            {"format_id": "298", "protocol": "https", "url": "https://ex/298.mp4", "vcodec": "avc1.4D4020", "acodec": "none", "height": 720, "ext": "mp4", "tbr": 1200},
            {"format_id": "140-drc", "protocol": "https", "url": "https://ex/140drc.m4a", "vcodec": "none", "acodec": "mp4a", "ext": "m4a", "abr": 128},
            {"format_id": "140", "protocol": "https", "url": "https://ex/140.m4a", "vcodec": "none", "acodec": "mp4a", "ext": "m4a", "abr": 128},
        ]
        prog, v_fmt, a_fmt = _select_https_formats(sample_formats, max_height=720)
        self.assertIsNone(prog)
        self.assertEqual(v_fmt["format_id"], "298")
        self.assertEqual(a_fmt["format_id"], "140")

        prog_360, v_360, a_360 = _select_https_formats(sample_formats, max_height=360)
        self.assertEqual(prog_360["format_id"], "18")
        self.assertIsNone(v_360)
        self.assertIsNone(a_360)

        self.assertTrue(_wait_for_available_at([{"available_at": time.time() - 5}]))
        self.assertFalse(_wait_for_available_at([{"available_at": time.time() + 120}], max_wait=5.0))

    def test_http_range_relay_trim_end_to_end(self):
        if not has_ffmpeg():
            self.skipTest("FFmpeg not installed")
        import http.server
        import socketserver
        import threading
        import urllib.request

        ffmpeg_exe = ensure_ffmpeg()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src_mp4 = tmp_path / "src_relay.mp4"
            subprocess.run(
                [
                    ffmpeg_exe, "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", "-shortest",
                    str(src_mp4),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            data = src_mp4.read_bytes()

            class _RangeServerHandler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    rng = self.headers.get("Range")
                    if rng and rng.startswith("bytes="):
                        spec = rng[len("bytes="):]
                        start_s, end_s = spec.split("-", 1)
                        start_i = int(start_s) if start_s else 0
                        end_i = int(end_s) if end_s else len(data) - 1
                        end_i = min(end_i, len(data) - 1)
                        slice_bytes = data[start_i:end_i + 1]
                        self.send_response(206)
                        self.send_header("Content-Range", f"bytes {start_i}-{end_i}/{len(data)}")
                        self.send_header("Content-Length", str(len(slice_bytes)))
                        self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        self.wfile.write(slice_bytes)
                    else:
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Accept-Ranges", "bytes")
                        self.end_headers()
                        self.wfile.write(data)

            class _Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
                daemon_threads = True

            srv = _Srv(("127.0.0.1", 0), _RangeServerHandler)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                import socket
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=2):
                        pass
                except PermissionError:
                    self.skipTest("Loopback TCP sockets blocked by sandbox")

                out_clip = tmp_path / "relayed_clip.mp4"
                prog_events = []

                # While the relay runs, a request without its secret path prefix must be refused
                import re as _re
                from unittest import mock
                import video_trimmer as vt
                real_popen = vt.subprocess.Popen
                probe = {}

                def _popen_with_probe(cmd, *a, **kw):
                    relay_url = next(c for c in cmd if str(c).startswith("http://127.0.0.1:"))
                    relay_port = int(_re.match(r"http://127\.0\.0\.1:(\d+)/", relay_url).group(1))
                    try:
                        urllib.request.urlopen(f"http://127.0.0.1:{relay_port}/m", timeout=5)
                        probe["status"] = 200
                    except urllib.error.HTTPError as err:
                        probe["status"] = err.code
                    probe["token_len"] = len(relay_url.split("/")[3])
                    return real_popen(cmd, *a, **kw)

                def _open_fn(t_url, hdrs):
                    req = urllib.request.Request(t_url, headers=hdrs)
                    return urllib.request.urlopen(req, timeout=10)

                with mock.patch.object(vt.subprocess, "Popen", _popen_with_probe):
                    run_http_range_relay_trim(
                        ffmpeg_exe=ffmpeg_exe,
                        output_path=out_clip,
                        start_time=1.5,
                        end_time=4.0,
                        open_url_fn=_open_fn,
                        stream_specs={"/m": (f"http://127.0.0.1:{port}/video.mp4", {})},
                        crop_config=CropConfig(mode="1:1", pan_x=0.5),
                        progress_callback=lambda p, m: prog_events.append((p, m)),
                    )
                self.assertTrue(out_clip.exists())
                self.assertGreater(out_clip.stat().st_size, 1000)
                self.assertEqual(get_video_dimensions(out_clip), (360, 360))
                self.assertGreaterEqual(len(prog_events), 1)
                self.assertEqual(probe["status"], 404)
                self.assertGreaterEqual(probe["token_len"], 24)
            finally:
                srv.shutdown()
                srv.server_close()


def _first_video_pts_seconds(ffmpeg_exe: str, path: Path) -> float:
    """Presentation time of the first video packet (framemd5 uses the stream time base)."""
    out = subprocess.run(
        [ffmpeg_exe, "-v", "error", "-i", str(path), "-map", "0:v", "-c", "copy", "-f", "framemd5", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
    ).stdout
    tb_line = next(l for l in out.splitlines() if l.startswith("#tb"))
    num, den = tb_line.split(":")[1].strip().split("/")
    first = next(l for l in out.splitlines() if l and not l.startswith("#"))
    return int(first.split(",")[1]) * int(num) / int(den)


def _media_duration(ffmpeg_exe: str, path: Path) -> float:
    err = subprocess.run([ffmpeg_exe, "-hide_banner", "-i", str(path)], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True).stderr
    hms = err.split("Duration: ")[1].split(",")[0]
    h, m, sec = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


class TestHlsTsSeekAccuracy(unittest.TestCase):
    """YouTube HLS slices are .ts files with ~5s GOPs and non-zero start timestamps.
    Input-side -ss on them jumped to the next keyframe, so clips opened with seconds
    of audio but no video."""

    def setUp(self):
        if not has_ffmpeg():
            self.skipTest("FFmpeg not installed")
        self.ffmpeg = ensure_ffmpeg()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        common = ["-output_ts_offset", "500", "-f", "mpegts"]
        self.v_ts = self.tmp / "v.ts"
        self.a_ts = self.tmp / "a.ts"
        self.av_ts = self.tmp / "av.ts"
        gop = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
               "-g", "125", "-keyint_min", "125", "-sc_threshold", "0"]
        src_v = ["-f", "lavfi", "-i", "testsrc=duration=12:size=320x180:rate=25"]
        src_a = ["-f", "lavfi", "-i", "sine=duration=12"]
        run = lambda args: subprocess.run([self.ffmpeg, "-y", "-v", "error", *args],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        run([*src_v, *gop, *common, str(self.v_ts)])
        run([*src_a, "-c:a", "aac", *common, str(self.a_ts)])
        run([*src_v, *src_a, *gop, "-c:a", "aac", "-shortest", *common, str(self.av_ts)])

    def tearDown(self):
        self._tmp.cleanup()

    def assert_clip_starts_on_time(self, out: Path, expected_dur: float):
        self.assertLess(abs(_first_video_pts_seconds(self.ffmpeg, out)), 0.1)
        self.assertAlmostEqual(_media_duration(self.ffmpeg, out), expected_dur, delta=0.15)

    def test_separate_av_mux_keeps_video_from_offset(self):
        out = self.tmp / "out.mp4"
        self.assertTrue(mux_trim_hls_av(self.ffmpeg, self.v_ts, 1.39, self.a_ts, 1.39, 6.0, out))
        self.assert_clip_starts_on_time(out, 6.0)

    def test_separate_av_mux_with_blur_crop(self):
        out = self.tmp / "out_blur.mp4"
        self.assertTrue(mux_trim_hls_av(self.ffmpeg, self.v_ts, 1.39, self.a_ts, 1.39, 6.0, out,
                                        crop_config=CropConfig(mode="9:16_blur")))
        self.assert_clip_starts_on_time(out, 6.0)
        self.assertEqual(get_video_dimensions(out), (100, 180))

    def test_trim_clip_ffmpeg_on_muxed_ts(self):
        for crop in (None, CropConfig(mode="9:16")):
            out = self.tmp / f"out_muxed_{crop.mode if crop else 'none'}.mp4".replace(":", "x")
            trim_clip_ffmpeg(self.av_ts, out, 1.39, 7.39, crop_config=crop)
            self.assert_clip_starts_on_time(out, 6.0)


class TestDownloadFailureReporting(unittest.TestCase):
    """When every route fails, the user should learn *why* (e.g. YouTube's bot check on a
    cloud IP), not just the last client's "Requested format is not available"."""

    def _run_clip_download(self, reason: str):
        from unittest import mock
        import video_trimmer as vt

        def failing_tier(**kwargs):
            vt._record_failure("HLS slice (visionos)", reason)
            return None

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(vt, "_try_python_hls_direct_slice", side_effect=failing_tier), \
                mock.patch.object(vt, "_try_python_http_range_slice", side_effect=failing_tier), \
                mock.patch.object(vt, "_build_client_profiles", return_value=[]), \
                mock.patch.object(vt, "_download_with_pytubefix", return_value=None):
            vt.download_single_clip_section(
                url="https://youtu.be/bVbXU7fq0ak", output_path=Path(tmp) / "c.mp4",
                start_time=10.0, end_time=20.0,
            )

    def test_bot_check_raises_blocked_error_with_every_reason(self):
        import video_trimmer as vt
        with self.assertRaises(vt.YouTubeBlockedError) as ctx:
            self._run_clip_download("ERROR: [youtube] x: Sign in to confirm you're not a bot")
        self.assertEqual(len(ctx.exception.reasons), 2)
        self.assertTrue(all("not a bot" in r for r in ctx.exception.reasons))
        # A fresh call starts with an empty list instead of piling onto the last one
        self.assertIsNone(vt._FAILURES.get())

    def test_requested_format_unavailable_counts_as_blocked(self):
        import video_trimmer as vt
        with self.assertRaises(vt.YouTubeBlockedError):
            self._run_clip_download("ERROR: [youtube] x: Requested format is not available")

    def test_local_failure_is_not_reported_as_a_block(self):
        import video_trimmer as vt
        with self.assertRaises(RuntimeError) as ctx:
            self._run_clip_download("ffmpeg exited with code 1")
        self.assertNotIsInstance(ctx.exception, vt.YouTubeBlockedError)
        self.assertIn("ffmpeg exited", str(ctx.exception))

    def test_record_failure_includes_captured_ytdlp_warnings(self):
        import video_trimmer as vt
        log = utils.YdlCaptureLogger()
        log.warning("[youtube] x: The page needs to be reloaded.")
        token = vt._FAILURES.set([])
        try:
            vt._record_failure("Section download (tv)", None, log)
            self.assertIn("page needs to be reloaded", vt._FAILURES.get()[0])
        finally:
            vt._FAILURES.reset(token)


class TestMoviePyFallback(unittest.TestCase):
    def test_vertical_blur_survives_moviepy_fallback(self):
        if not has_ffmpeg():
            self.skipTest("FFmpeg not installed")
        from unittest import mock
        import video_trimmer as vt
        ffmpeg_exe = ensure_ffmpeg()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.mp4"
            subprocess.run(
                [ffmpeg_exe, "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=15",
                 "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-shortest", str(src)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
            )
            out = Path(tmp) / "out.mp4"
            # Force the MoviePy path by making the FFmpeg CLI unavailable
            with mock.patch.object(vt, "ensure_ffmpeg", return_value=None):
                trim_clip_ffmpeg(src, out, 0.5, 2.5, crop_config=CropConfig(mode="9:16_blur"))
            self.assertEqual(get_video_dimensions(out), (202, 360))


class TestHtmlEscaping(unittest.TestCase):
    def test_escape_html(self):
        self.assertEqual(utils.escape_html('<b onclick="x">&'), "&lt;b onclick=&quot;x&quot;&gt;&amp;")
        self.assertEqual(utils.escape_html(3), "3")

    def test_safe_css_url(self):
        ok = utils.safe_css_url("https://i.ytimg.com/vi/abc/maxresdefault.jpg")
        self.assertEqual(ok, "https://i.ytimg.com/vi/abc/maxresdefault.jpg")
        hostile = utils.safe_css_url("https://i.ytimg.com/a.jpg?x=');background:url(evil)\"><script>")
        for ch in ("'", '"', "(", ")", "<", ">", " "):
            self.assertNotIn(ch, hostile)
        self.assertIsNone(utils.safe_css_url("javascript:alert(1)"))
        self.assertIsNone(utils.safe_css_url("data:image/png;base64,AAAA"))
        self.assertIsNone(utils.safe_css_url(""))
        self.assertIsNone(utils.safe_css_url(None))


class TestCookieIsolation(unittest.TestCase):
    """Pasted cookies stay in one visitor's session; server cookies are never exposed."""

    SESSION_A = "Cookie: LOGIN_INFO=alice_login; SAPISID=alice_sapisid"
    SESSION_B = "Cookie: LOGIN_INFO=bob_login; SAPISID=bob_sapisid"
    SERVER = ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\toperator_login\n"

    def setUp(self):
        import os
        from unittest import mock
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.app_root = root / "app"
        self.app_root.mkdir()
        self.tmpdir = root / "tmp"
        self.tmpdir.mkdir()
        self._session = None
        self._patches = [
            mock.patch.object(tempfile, "tempdir", str(self.tmpdir)),
            mock.patch.object(utils, "APP_ROOT", self.app_root),
            mock.patch.object(utils, "_read_session_cookies", lambda: self._session),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in self._patches:
            p.start()
        os.environ.pop(utils.SINGLE_USER_ENV, None)

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _set_single_user(self, on: bool):
        import os
        if on:
            os.environ[utils.SINGLE_USER_ENV] = "1"
        else:
            os.environ.pop(utils.SINGLE_USER_ENV, None)

    def test_save_does_not_write_disk_in_multi_user_mode(self):
        sanitized = utils.save_yt_cookies(self.SESSION_A)
        self.assertIn("alice_login", sanitized)
        self.assertFalse((self.app_root / "user_cookies.txt").exists())
        self.assertFalse(any("alice_login" in p.read_text(errors="ignore")
                             for p in self.tmpdir.rglob("*") if p.is_file()))

    def test_server_cookies_never_prefilled_into_session(self):
        (self.app_root / "user_cookies.txt").write_text(self.SERVER, encoding="utf-8")
        self.assertEqual(utils.load_persisted_cookies(), "")
        stats = utils.get_cookie_stats()
        self.assertTrue(stats["active"])
        self.assertEqual(stats["source"], "server")

    def test_session_cookies_override_server_and_are_isolated(self):
        (self.app_root / "cookies.txt").write_text(self.SERVER, encoding="utf-8")
        self._session = utils.save_yt_cookies(self.SESSION_A)
        jar_a = Path(utils.get_yt_cookies_file())
        self.assertEqual(utils.get_cookie_stats()["source"], "session")
        self._session = utils.save_yt_cookies(self.SESSION_B)
        jar_b = Path(utils.get_yt_cookies_file())

        self.assertNotEqual(jar_a, jar_b)
        self.assertIn("alice_login", jar_a.read_text(encoding="utf-8"))
        self.assertNotIn("bob_login", jar_a.read_text(encoding="utf-8"))
        self.assertIn("bob_login", jar_b.read_text(encoding="utf-8"))
        for jar in (jar_a, jar_b):
            self.assertEqual(jar.stat().st_mode & 0o777, 0o600)
        self.assertEqual(jar_a.parent.stat().st_mode & 0o777, 0o700)

        self._session = None
        self.assertIn("operator_login", Path(utils.get_yt_cookies_file()).read_text(encoding="utf-8"))

    def test_clear_only_removes_own_cookies(self):
        server_file = self.app_root / "cookies.txt"
        server_file.write_text(self.SERVER, encoding="utf-8")
        self._session = utils.save_yt_cookies(self.SESSION_A)
        jar_a = Path(utils.get_yt_cookies_file())
        self._session = utils.save_yt_cookies(self.SESSION_B)
        jar_b = Path(utils.get_yt_cookies_file())

        utils.clear_yt_cookies(self._session)
        self.assertFalse(jar_b.exists())
        self.assertTrue(jar_a.exists())
        self.assertTrue(server_file.exists())

    def test_unreadable_server_file_is_not_handed_to_ytdlp(self):
        master = self.app_root / "user_cookies.txt"
        master.write_bytes(b"\xff\xfe\x00 not utf-8")
        self.assertIsNone(utils.get_yt_cookies_file())

    def test_single_user_mode_persists_restores_and_clears(self):
        self._set_single_user(True)
        persisted = self.app_root / "user_cookies.txt"
        sanitized = utils.save_yt_cookies(self.SESSION_A)
        self.assertTrue(persisted.exists())
        self.assertEqual(persisted.stat().st_mode & 0o777, 0o600)
        self.assertEqual(utils.load_persisted_cookies(), sanitized)
        utils.clear_yt_cookies(sanitized)
        self.assertFalse(persisted.exists())

    def test_dockerignore_excludes_secrets(self):
        patterns = (Path(__file__).resolve().parent.parent / ".dockerignore").read_text().split()
        for required in ("*cookies*", ".env", ".env.*", ".streamlit/secrets.toml"):
            self.assertIn(required, patterns)


if __name__ == "__main__":
    unittest.main()

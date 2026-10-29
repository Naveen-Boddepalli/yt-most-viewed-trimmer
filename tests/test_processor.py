"""Unit and integration tests for YouTube heatmap extraction, cloud 403/cookie fixes, and video cropping."""

import json
import tempfile
import subprocess
from pathlib import Path
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    get_video_dimensions,
    slice_hls_playlist_to_file,
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
        # Ensure cookieless android_vr and ios profiles exist and are separate from web
        cookieless_clients = [p["clients"] for p in profiles if not p["use_cookies"]]
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

    def test_sanitize_environment_and_clean_env(self):
        import os
        os.environ["TEST_HUGE_VAR"] = "X" * 10000
        os.environ["TEST_YOUTUBE_COOKIES"] = "# Netscape HTTP Cookie File\nexample.com\tTRUE\t/\tTRUE\t0\tk\tv"
        sanitize_environment()
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


if __name__ == "__main__":
    unittest.main()

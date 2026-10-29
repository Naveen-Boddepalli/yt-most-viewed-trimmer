# 🎬 YouTube Most-Viewed Timestamp Extractor, Trimmer & Cropper (Streamlit App)

A Python + Streamlit web application that loads a YouTube video from its link, extracts the **Most Replayed (Most Viewed)** timestamp peaks from YouTube's engagement telemetry (`heatmap`), trims and **crops** the video into top highlight segments (`9:16 Vertical Shorts/Reels`, `1:1 Square`, `4:5 Portrait`, `9:16 Blurred-Pad`, or `Custom Margins`), and displays them inline for previewing and downloading **either a particular clip (~10–15 MB) or all clips as a `.zip` bundle**.

---

## ⚡ Direct Stream-Trim (Cloud Optimized)
- **Zero Full-Video Download Required**: Directly streams and slices *only* the requested 30–60s highlight clip from YouTube's CDN using byte-range requests.
- **Ultra-lightweight**: Downloads only **10–15 MB** per clip instead of 1–2 GB source videos.
- **Fast**: Cuts and delivers clips in just 2–4 seconds.

---

## ✨ Key Features

1. **YouTube Heatmap ('Most Replayed') Peak Extraction (`yt_processor.py`)**
   - Uses `yt-dlp` to inspect the video's `heatmap` telemetry array (`start_time`, `end_time`, `value` in `[0.0, 1.0]`).
   - Detects local maxima, expands around contiguous high-intensity replay regions, applies configurable padding and min/max clip duration constraints, and merges overlapping windows.
   - **Automatic Multi-Tier Fallback**: If a video does not have YouTube's Most Replayed heatmap enabled, the extractor automatically falls back to **YouTube Chapters**, **Description Timestamps**, or **Uniform Highlight Sampling**.

2. **Video Cropping & Framing (`CropConfig` + Live Preview)**:
   - **7 Aspect Ratio & Crop Modes**:
     - `🖥️ Original (No Crop — 16:9)`
     - `📱 9:16 Vertical Crop (Shorts / Reels / TikTok)`
     - `✨ 9:16 Vertical + Blurred Background (Full Frame)`
     - `🟦 1:1 Square Crop (Instagram / Feed)`
     - `🖼️ 4:5 Portrait Crop (Social Feed)`
     - `📺 4:3 Classic Crop`
     - `📐 Custom Crop Margins (%)` (independent Top, Bottom, Left, Right % sliders)
   - **Subject Focus / Horizontal & Vertical Panning**: Anchor the crop window to `Center (50%)`, `Left Subject (20%)`, `Right Subject (80%)`, or any custom horizontal/vertical percentage (`0%–100%`) with a live visual framing preview over the video thumbnail.
   - **One-Click Re-Crop**: Change the crop settings in the sidebar and click **🔄 Re-Trim / Apply Crop** on any clip card.

3. **Interactive Engagement Curve (`app.py` + `plotly`)**
   - Plots the full 0–100% replay intensity timeline with shaded highlight windows marking each extracted segment (`#1`, `#2`, ...).

4. **Pre-Download File Size Visibility & Estimation (`yt_processor.py` + `utils.py`)**
   - **Full Video Download Size**: Estimates video download size for every resolution (360p, 480p, 720p, 1080p) before downloading using stream metadata, bitrates, and duration.
   - **Per-Segment Size Display**: Shows estimated clip size (e.g. `~3.5 MB`) in the segments table, trimming toolbar, and on clip cards before trimming, and exact trimmed file size before downloading.
   - **Download Buttons & ZIP Size**: Shows exact sizes on individual clip download buttons and the combined `.zip` bundle button.

5. **Flexible Video Trimming & Downloading (`video_trimmer.py`)**
   - **Trim a Particular Segment**: Pick any specific rank from the dropdown (or click the inline button on any segment card) to trim, preview (`st.video`), and download just that clip (`st.download_button`).
   - **Trim All Segments + `.zip` Bundle**: One-click batch trimming of all top $K$ segments plus a single button to download all trimmed `.mp4` files in a `.zip` archive.
   - **Custom Timestamp Range Trimmer**: Manually add and trim any `MM:SS - MM:SS` interval.
   - **Offline Sample Demo Mode**: Click **Load Sample Heatmap Demo** in the header to test the heatmap peak detector, interactive chart, and FFmpeg video trimming immediately without needing an external link.

---

## 🛠️ Cloud & Datacenter Resilience

1. **Fixed `ERROR: ffmpeg exited with code 8` & `HTTP Error 403: Forbidden`**:
   - **Removed Conflicting Desktop User-Agent Override**: Previously, forcing a desktop Windows Chrome `User-Agent` onto mobile/embedded `player_client` URLs caused `googlevideo.com` to reject requests with `HTTP 403 Forbidden` and caused `ffmpeg` to exit with `code 8`. Client-native headers are now used.
   - **Isolated Single-Client Profiles (`_build_client_profiles`)**: The trimmer tests isolated profiles in order: cookie-authenticated `tv`, `web_creator`, `web_safari`, and `mweb`, followed by cookieless `android_vr`, `ios` (HLS), and `tv_embedded`.
   - **Added `pytubefix` Secondary Fallback Engine**: If `yt-dlp` encounters a 403 block on a cloud IP, `video_trimmer.py` automatically falls back to `pytubefix` (`ANDROID_VR`, `IOS`, `TV`, `MWEB`) to fetch and merge the stream.
   - **Isolated Working Cookie Jar (`utils.py`)**: `yt-dlp` mutates/overwrites its `cookiefile` on disk after each run. `get_yt_cookies_file()` writes a fresh temporary working copy (`/tmp/yt_working_cookies_<pid>.txt`) before each request and supports both **Netscape** and **JSON** cookie exports (including `#HttpOnly_` lines and auto-padding 6-column lines).
   - **Streamlit Cloud Environment Configuration**: `runtime.txt` (`python-3.12`), `packages.txt` (`ffmpeg`, `nodejs`), and `requirements.txt` (`yt-dlp[default]`).

---

## ☁️ Deploying to Streamlit Community Cloud (100% Free)

This repository is pre-configured with `packages.txt` (installs FFmpeg + Node.js) and both `app.py` / `streamlit_app.py` entrypoints.

1. **Push** this repository to your GitHub account (`https://github.com/Naveen-Boddepalli/yt-most-viewed-trimmer`).
   - *Ensure the repository is set to **Public** so Streamlit Cloud can access it.*
2. Go to **[share.streamlit.io](https://share.streamlit.io)** and sign in with GitHub.
3. Click **Deploy an app** (or **New app**).
4. Fill in:
   - **Repository**: `naveen-boddepalli/yt-most-viewed-trimmer`
   - **Branch**: `main`
   - **Main file path**: `app.py` *(or `streamlit_app.py`)*
5. Click **Deploy!**

---

## 📁 Project Structure

```text
yt_most_viewed_trimmer/
├── .streamlit/
│   └── config.toml          # Streamlit dark theme & upload configuration
├── tests/
│   └── test_processor.py    # Unit & integration tests (heatmap peaks, cropping, cookies, FFmpeg)
├── app.py                   # Streamlit frontend UI with interactive cropping controls
├── streamlit_app.py         # Entrypoint wrapper for Streamlit Community Cloud
├── yt_processor.py          # YouTube metadata & Most-Replayed heatmap peak extractor
├── video_trimmer.py         # Direct stream trimming (HLS, yt-dlp, pytubefix) & FFmpeg cropper
├── utils.py                 # Timestamp parsing, formatting, cookie sanitization & size helpers
├── packages.txt             # System packages (ffmpeg, nodejs) for Streamlit Cloud & Linux
├── requirements.txt         # Python package dependencies
├── runtime.txt              # Python runtime version (python-3.12)
├── .python-version          # Python version marker (3.12)
├── .dockerignore            # Docker ignore file
├── Dockerfile               # Containerized deployment definition
├── .gitignore               # Ignored virtualenvs, temp media files, and user cookies
└── README.md                # Documentation
```

---

## 🚀 Local Installation & Run

### 1. Prerequisites
- **Python 3.9+** (Python 3.12 recommended)
- **FFmpeg** installed on your system (or auto-provisioned via `imageio-ffmpeg` / `static-ffmpeg`)

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the App
```bash
streamlit run app.py
```

### 4. Run Unit & Integration Tests
```bash
pytest -v
```

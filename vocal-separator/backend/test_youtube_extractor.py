"""Tests for YouTube URL validation, metadata lookup, and audio extraction.

Network-facing yt_dlp calls are mocked throughout, so this suite runs
offline and deterministically in CI - the real-network path is covered
separately by manual integration testing (see Feature 7 prompt, Task 7.8).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from . import youtube_extractor
from .youtube_extractor import (
    validate_youtube_url,
    fetch_video_metadata,
    download_audio,
    InvalidURLError,
    VideoTooLongError,
    VideoUnavailableError,
    YouTubeExtractionError,
)
from .config import YOUTUBE_MAX_DURATION_SECONDS


class TestURLValidation:
    """validate_youtube_url should accept only real YouTube watch/short/shorts links."""

    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abc123XYZ",
    ])
    def test_accepts_valid_urls(self, url):
        assert validate_youtube_url(url) is True

    @pytest.mark.parametrize("url", [
        "https://vimeo.com/12345",
        "https://www.dailymotion.com/video/x123",
        "not a url",
        "",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?list=PL123",  # playlist only, no video id
        "https://www.youtube.com/shorts/",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
    ])
    def test_rejects_invalid_urls(self, url):
        assert validate_youtube_url(url) is False


class TestMetadataFetch:
    """fetch_video_metadata should validate, then reject long/unavailable videos
    before any download is attempted."""

    def test_invalid_url_raises_without_network_call(self):
        with patch("yt_dlp.YoutubeDL.extract_info") as mock_extract:
            with pytest.raises(InvalidURLError):
                fetch_video_metadata("https://vimeo.com/12345")
            mock_extract.assert_not_called()

    def test_valid_short_video_returns_metadata(self):
        fake_info = {"title": "Test Song", "duration": 180, "uploader": "Test Channel"}
        with patch("yt_dlp.YoutubeDL.extract_info", return_value=fake_info):
            meta = fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")
        assert meta == {
            "title": "Test Song",
            "duration_seconds": 180,
            "uploader": "Test Channel",
        }

    def test_video_over_limit_is_rejected(self):
        fake_info = {"title": "Long Video", "duration": YOUTUBE_MAX_DURATION_SECONDS + 60, "uploader": "X"}
        with patch("yt_dlp.YoutubeDL.extract_info", return_value=fake_info):
            with pytest.raises(VideoTooLongError):
                fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

    def test_video_exactly_at_limit_is_accepted(self):
        fake_info = {"title": "Exact", "duration": YOUTUBE_MAX_DURATION_SECONDS, "uploader": "X"}
        with patch("yt_dlp.YoutubeDL.extract_info", return_value=fake_info):
            meta = fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")
        assert meta["duration_seconds"] == YOUTUBE_MAX_DURATION_SECONDS

    def test_video_one_second_over_limit_is_rejected(self):
        fake_info = {"title": "Over by 1s", "duration": YOUTUBE_MAX_DURATION_SECONDS + 1, "uploader": "X"}
        with patch("yt_dlp.YoutubeDL.extract_info", return_value=fake_info):
            with pytest.raises(VideoTooLongError):
                fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

    def test_unavailable_video_raises_clear_error(self):
        with patch(
            "yt_dlp.YoutubeDL.extract_info",
            side_effect=yt_dlp.utils.DownloadError("This video is unavailable"),
        ):
            with pytest.raises(VideoUnavailableError):
                fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

    def test_missing_duration_raises_unavailable(self):
        """Livestreams and some special content report duration=None."""
        fake_info = {"title": "Live", "duration": None, "uploader": "X"}
        with patch("yt_dlp.YoutubeDL.extract_info", return_value=fake_info):
            with pytest.raises(VideoUnavailableError):
                fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")


class TestDownloadAudio:
    """download_audio should call yt-dlp with audio-only format + MP3
    postprocessor, report progress, and locate the resulting file."""

    def test_download_success_returns_path_and_reports_progress(self, tmp_path):
        video_id = "abc12345678"
        expected_path = tmp_path / f"{video_id}.mp3"

        progress_events = []

        def fake_extract_info(self, url, download=True):
            # Simulate yt-dlp firing its progress hook mid-download, then
            # let the postprocessor "produce" the output file.
            for hook in self.params.get("progress_hooks", []):
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
            expected_path.write_bytes(b"fake mp3 data")
            return {"id": video_id}

        with patch.object(yt_dlp.YoutubeDL, "extract_info", fake_extract_info):
            result_path = download_audio(
                "https://www.youtube.com/watch?v=abc12345678",
                tmp_path,
                bitrate_kbps=320,
                on_progress=lambda f: progress_events.append(f),
            )

        assert result_path == expected_path
        assert result_path.is_file()
        assert progress_events == [0.5, 1.0]

    def test_download_uses_audio_only_format(self, tmp_path):
        """Never request the video stream - only audio, to save bandwidth/memory."""
        captured_opts = {}

        def fake_init(self, params=None, **kwargs):
            captured_opts.update(params or {})
            self.params = params or {}

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info", lambda self, url, download: {"id": "x"} ):
            (tmp_path / "x.mp3").write_bytes(b"fake")
            download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)

        assert captured_opts["format"] == "bestaudio/best"
        assert captured_opts["postprocessors"][0]["preferredcodec"] == "mp3"

    def test_download_failure_raises_video_unavailable(self, tmp_path):
        with patch.object(
            yt_dlp.YoutubeDL, "extract_info",
            side_effect=yt_dlp.utils.DownloadError("Connection timed out"),
        ):
            with pytest.raises(VideoUnavailableError):
                download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)

    def test_missing_output_file_raises_extraction_error(self, tmp_path):
        """If yt-dlp reports success but the expected file isn't on disk, fail loudly."""
        with patch.object(yt_dlp.YoutubeDL, "extract_info", return_value={"id": "nonexistent"}):
            with pytest.raises(YouTubeExtractionError):
                download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)


class TestCookieSupport:
    """cookiefile should be passed to yt-dlp only when the file configured
    via YOUTUBE_COOKIES_FILE actually exists on disk - otherwise requests
    go out exactly as before (no cookies)."""

    @staticmethod
    def _capture_opts(monkeypatch, cookies_path):
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_COOKIES_FILE", str(cookies_path))
        captured_opts = {}

        def fake_init(self, params=None, **kwargs):
            captured_opts.update(params or {})
            self.params = params or {}

        return captured_opts, fake_init

    def test_metadata_fetch_includes_cookiefile_when_present(self, tmp_path, monkeypatch):
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("# Netscape HTTP Cookie File\n")
        captured_opts, fake_init = self._capture_opts(monkeypatch, cookies_file)

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["cookiefile"] == str(cookies_file)

    def test_metadata_fetch_omits_cookiefile_when_absent(self, tmp_path, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, tmp_path / "missing.txt")

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert "cookiefile" not in captured_opts

    def test_download_includes_cookiefile_when_present(self, tmp_path, monkeypatch):
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("# Netscape HTTP Cookie File\n")
        captured_opts, fake_init = self._capture_opts(monkeypatch, cookies_file)

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info", lambda self, url, download: {"id": "x"}):
            (tmp_path / "x.mp3").write_bytes(b"fake")
            download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)

        assert captured_opts["cookiefile"] == str(cookies_file)


class TestBrowserCookieSupport:
    """cookiesfrombrowser should be used when YOUTUBE_COOKIES_BROWSER is set
    and its profile directory is mounted and non-empty, taking priority
    over a configured cookiefile."""

    @staticmethod
    def _capture_opts(monkeypatch, browser, profile_dir, keyring=""):
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_COOKIES_BROWSER", browser)
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_COOKIES_BROWSER_PROFILE_DIR", str(profile_dir))
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_COOKIES_KEYRING", keyring)
        captured_opts = {}

        def fake_init(self, params=None, **kwargs):
            captured_opts.update(params or {})
            self.params = params or {}

        return captured_opts, fake_init

    def test_uses_browser_profile_when_present_and_non_empty(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()
        (profile_dir / "cookies.sqlite").write_text("fake")
        captured_opts, fake_init = self._capture_opts(monkeypatch, "firefox", profile_dir)

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["cookiesfrombrowser"] == ("firefox", str(profile_dir), None, None)
        assert "cookiefile" not in captured_opts

    def test_passes_keyring_when_configured(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()
        (profile_dir / "Cookies").write_text("fake")
        captured_opts, fake_init = self._capture_opts(monkeypatch, "chrome", profile_dir, keyring="BASICTEXT")

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["cookiesfrombrowser"] == ("chrome", str(profile_dir), "BASICTEXT", None)

    def test_falls_back_to_cookiefile_when_profile_dir_empty(self, tmp_path, monkeypatch):
        profile_dir = tmp_path / "profile"
        profile_dir.mkdir()  # exists but empty
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("# Netscape HTTP Cookie File\n")
        captured_opts, fake_init = self._capture_opts(monkeypatch, "firefox", profile_dir)
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_COOKIES_FILE", str(cookies_file))

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert "cookiesfrombrowser" not in captured_opts
        assert captured_opts["cookiefile"] == str(cookies_file)

    def test_falls_back_to_cookiefile_when_browser_not_configured(self, tmp_path, monkeypatch):
        cookies_file = tmp_path / "cookies.txt"
        cookies_file.write_text("# Netscape HTTP Cookie File\n")
        captured_opts, fake_init = self._capture_opts(monkeypatch, "", tmp_path / "missing_profile")
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_COOKIES_FILE", str(cookies_file))

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert "cookiesfrombrowser" not in captured_opts
        assert captured_opts["cookiefile"] == str(cookies_file)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

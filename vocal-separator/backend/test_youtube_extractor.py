"""Tests for YouTube URL validation, metadata lookup, and audio extraction.

Network-facing yt_dlp calls are mocked throughout, so this suite runs
offline and deterministically in CI - the real-network path is covered
separately by manual integration testing (see Feature 7 prompt, Task 7.8).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
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


class TestPlayerClientSupport:
    """extractor_args.youtube.player_client should reflect YOUTUBE_PLAYER_CLIENTS,
    passed to yt-dlp so requests present as a mobile app client instead of
    the web client (the one YouTube's bot check targets)."""

    @staticmethod
    def _capture_opts(monkeypatch, clients):
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_PLAYER_CLIENTS", clients)
        # Isolated from the PO Token provider setting (covered separately
        # in TestPotProviderSupport) so assertions here only ever see
        # player_client in extractor_args.
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_POT_PROVIDER_BASE_URL", "")
        captured_opts = {}

        def fake_init(self, params=None, **kwargs):
            captured_opts.update(params or {})
            self.params = params or {}

        return captured_opts, fake_init

    def test_default_android_client_is_requested(self, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, ["android"])

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["extractor_args"] == {"youtube": {"player_client": ["android"]}}

    def test_empty_list_omits_extractor_args(self, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, [])

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert "extractor_args" not in captured_opts

    def test_multiple_clients_are_all_requested(self, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, ["android", "ios"])

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["extractor_args"] == {"youtube": {"player_client": ["android", "ios"]}}

    def test_download_requests_configured_client(self, tmp_path, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, ["android"])

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info", lambda self, url, download: {"id": "x"}):
            (tmp_path / "x.mp3").write_bytes(b"fake")
            download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)

        assert captured_opts["extractor_args"] == {"youtube": {"player_client": ["android"]}}


class TestPotProviderSupport:
    """extractor_args["youtubepot-bgutilhttp"].base_url should reflect
    YOUTUBE_POT_PROVIDER_BASE_URL, merged alongside (not replacing)
    player_client, since both live under the single extractor_args dict.
    Configuring a provider should also force youtube:fetch_pot, since
    yt-dlp's own "auto" heuristic can skip fetching even when a client's
    policy marks a token as required (see _extractor_args' docstring)."""

    @staticmethod
    def _capture_opts(monkeypatch, base_url, clients=None, fetch_policy="always"):
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_POT_PROVIDER_BASE_URL", base_url)
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_PLAYER_CLIENTS", clients or [])
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_POT_FETCH_POLICY", fetch_policy)
        captured_opts = {}

        def fake_init(self, params=None, **kwargs):
            captured_opts.update(params or {})
            self.params = params or {}

        return captured_opts, fake_init

    def test_base_url_is_passed_to_bgutil_provider(self, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, "http://bgutil-provider:4416")

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["extractor_args"] == {
            "youtube": {"fetch_pot": ["always"]},
            "youtubepot-bgutilhttp": {"base_url": ["http://bgutil-provider:4416"]},
        }

    def test_empty_base_url_omits_pot_provider_args(self, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, "")

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert "extractor_args" not in captured_opts

    def test_merges_with_player_client_instead_of_overwriting(self, monkeypatch):
        captured_opts, fake_init = self._capture_opts(
            monkeypatch, "http://bgutil-provider:4416", clients=["android"])

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["extractor_args"] == {
            "youtube": {"player_client": ["android"], "fetch_pot": ["always"]},
            "youtubepot-bgutilhttp": {"base_url": ["http://bgutil-provider:4416"]},
        }

    def test_fetch_policy_is_configurable(self, monkeypatch):
        captured_opts, fake_init = self._capture_opts(
            monkeypatch, "http://bgutil-provider:4416", fetch_policy="never")

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["extractor_args"]["youtube"]["fetch_pot"] == ["never"]

    def test_download_passes_base_url_too(self, tmp_path, monkeypatch):
        captured_opts, fake_init = self._capture_opts(monkeypatch, "http://bgutil-provider:4416")

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info", lambda self, url, download: {"id": "x"}):
            (tmp_path / "x.mp3").write_bytes(b"fake")
            download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)

        assert captured_opts["extractor_args"]["youtubepot-bgutilhttp"] == {
            "base_url": ["http://bgutil-provider:4416"],
        }


class TestYtDlpLogger:
    """_YtDlpLogger should forward yt-dlp's messages into this module's
    LOGGER, and both ydl_opts dicts should wire it up with verbose=True -
    otherwise the PO Token provider's own debug/warning output (the only
    real signal of whether a token was retrieved) never surfaces."""

    def test_forwards_to_module_logger(self, caplog):
        import logging
        caplog.set_level(logging.DEBUG, logger="backend.youtube_extractor")

        logger = youtube_extractor._YtDlpLogger()
        logger.debug("debug message")
        logger.warning("warning message")
        logger.error("error message")

        messages = [r.message for r in caplog.records]
        assert any("debug message" in m for m in messages)
        assert any("warning message" in m for m in messages)
        assert any("error message" in m for m in messages)

    def test_metadata_fetch_enables_verbose_logging(self, monkeypatch):
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_PLAYER_CLIENTS", [])
        monkeypatch.setattr(youtube_extractor, "YOUTUBE_POT_PROVIDER_BASE_URL", "")
        captured_opts = {}

        def fake_init(self, params=None, **kwargs):
            captured_opts.update(params or {})
            self.params = params or {}

        with patch.object(yt_dlp.YoutubeDL, "__init__", fake_init), \
             patch.object(yt_dlp.YoutubeDL, "__enter__", lambda self: self), \
             patch.object(yt_dlp.YoutubeDL, "__exit__", lambda self, *a: None), \
             patch.object(yt_dlp.YoutubeDL, "extract_info",
                          lambda self, url, download=False: {"title": "T", "duration": 10, "uploader": "U"}):
            fetch_video_metadata("https://www.youtube.com/watch?v=abc12345678")

        assert captured_opts["verbose"] is True
        assert isinstance(captured_opts["logger"], youtube_extractor._YtDlpLogger)


def _mock_post_response(json_data, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error", response=response)
    return response


def _mock_get_response(chunks, headers=None, status_code=200):
    """requests.get(..., stream=True) is used as a context manager."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.iter_content.return_value = iter(chunks)
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code} error", response=response)
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class TestCobaltDownload:
    """download_audio() should delegate to the Cobalt API when
    COBALT_API_URL is configured, and fall back to yt-dlp otherwise."""

    def test_dispatches_to_ytdlp_when_cobalt_not_configured(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "")
        with patch.object(youtube_extractor, "_download_via_ytdlp") as mock_ytdlp, \
             patch.object(youtube_extractor, "_download_via_cobalt") as mock_cobalt:
            mock_ytdlp.return_value = tmp_path / "x.mp3"
            download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)

        mock_ytdlp.assert_called_once()
        mock_cobalt.assert_not_called()

    def test_dispatches_to_cobalt_when_configured(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        with patch.object(youtube_extractor, "_download_via_ytdlp") as mock_ytdlp, \
             patch.object(youtube_extractor, "_download_via_cobalt") as mock_cobalt:
            mock_cobalt.return_value = tmp_path / "x.mp3"
            download_audio("https://www.youtube.com/watch?v=abc12345678", tmp_path)

        mock_cobalt.assert_called_once()
        mock_ytdlp.assert_not_called()

    def test_successful_tunnel_download_writes_file_and_reports_progress(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        monkeypatch.setattr(youtube_extractor, "COBALT_API_KEY", "")
        post_response = _mock_post_response({"status": "tunnel", "url": "https://cobalt.example.com/tunnel/abc", "filename": "song.mp3"})
        get_response = _mock_get_response([b"a" * 50, b"b" * 50], headers={"Content-Length": "100"})
        progress_events = []

        with patch.object(youtube_extractor.requests, "post", return_value=post_response) as mock_post, \
             patch.object(youtube_extractor.requests, "get", return_value=get_response):
            result_path = youtube_extractor._download_via_cobalt(
                "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128,
                lambda f: progress_events.append(f),
            )

        assert result_path == tmp_path / "song.mp3"
        assert result_path.read_bytes() == b"a" * 50 + b"b" * 50
        assert progress_events == [0.5, 1.0]

        # Verify the exact Cobalt request payload/headers contract.
        _, kwargs = mock_post.call_args
        assert kwargs["json"] == {
            "url": "https://www.youtube.com/watch?v=abc12345678",
            "audioFormat": "mp3",
            "audioBitrate": "128",
            "downloadMode": "audio",
        }
        assert kwargs["headers"]["Accept"] == "application/json"
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert "Authorization" not in kwargs["headers"]

    def test_includes_api_key_header_when_configured(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        monkeypatch.setattr(youtube_extractor, "COBALT_API_KEY", "secret-token")
        post_response = _mock_post_response({"status": "tunnel", "url": "https://cobalt.example.com/tunnel/abc", "filename": "song.mp3"})
        get_response = _mock_get_response([b"data"])

        with patch.object(youtube_extractor.requests, "post", return_value=post_response) as mock_post, \
             patch.object(youtube_extractor.requests, "get", return_value=get_response):
            youtube_extractor._download_via_cobalt(
                "https://www.youtube.com/watch?v=abc12345678", tmp_path, 320, None)

        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Api-Key secret-token"

    def test_snaps_bitrate_to_nearest_supported_value(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        post_response = _mock_post_response({"status": "tunnel", "url": "https://cobalt.example.com/x", "filename": "s.mp3"})
        get_response = _mock_get_response([b"data"])

        with patch.object(youtube_extractor.requests, "post", return_value=post_response) as mock_post, \
             patch.object(youtube_extractor.requests, "get", return_value=get_response):
            youtube_extractor._download_via_cobalt(
                "https://www.youtube.com/watch?v=abc12345678", tmp_path, 150, None)

        assert mock_post.call_args.kwargs["json"]["audioBitrate"] == "128"

    def test_sanitizes_path_traversal_in_filename(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        post_response = _mock_post_response({
            "status": "tunnel", "url": "https://cobalt.example.com/x", "filename": "../../etc/evil",
        })
        get_response = _mock_get_response([b"data"])

        with patch.object(youtube_extractor.requests, "post", return_value=post_response), \
             patch.object(youtube_extractor.requests, "get", return_value=get_response):
            result_path = youtube_extractor._download_via_cobalt(
                "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128, None)

        assert result_path.parent == tmp_path
        assert result_path.name == "evil.mp3"

    def test_error_status_raises_video_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        post_response = _mock_post_response({"status": "error", "error": {"code": "content.video.unavailable"}})

        with patch.object(youtube_extractor.requests, "post", return_value=post_response):
            with pytest.raises(VideoUnavailableError, match="content.video.unavailable"):
                youtube_extractor._download_via_cobalt(
                    "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128, None)

    def test_unexpected_status_raises_extraction_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        post_response = _mock_post_response({"status": "picker", "picker": []})

        with patch.object(youtube_extractor.requests, "post", return_value=post_response):
            with pytest.raises(YouTubeExtractionError, match="picker"):
                youtube_extractor._download_via_cobalt(
                    "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128, None)

    def test_missing_url_raises_extraction_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        post_response = _mock_post_response({"status": "tunnel", "filename": "s.mp3"})

        with patch.object(youtube_extractor.requests, "post", return_value=post_response):
            with pytest.raises(YouTubeExtractionError, match="download URL"):
                youtube_extractor._download_via_cobalt(
                    "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128, None)

    def test_post_network_failure_raises_video_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")

        with patch.object(youtube_extractor.requests, "post", side_effect=requests.ConnectionError("boom")):
            with pytest.raises(VideoUnavailableError, match="Cobalt API request failed"):
                youtube_extractor._download_via_cobalt(
                    "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128, None)

    def test_tunnel_download_failure_cleans_up_and_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        post_response = _mock_post_response({"status": "tunnel", "url": "https://cobalt.example.com/x", "filename": "song.mp3"})

        with patch.object(youtube_extractor.requests, "post", return_value=post_response), \
             patch.object(youtube_extractor.requests, "get", side_effect=requests.ConnectionError("dropped")):
            with pytest.raises(VideoUnavailableError, match="Cobalt tunnel"):
                youtube_extractor._download_via_cobalt(
                    "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128, None)

        assert not (tmp_path / "song.mp3").exists()

    def test_invalid_json_response_raises_extraction_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(youtube_extractor, "COBALT_API_URL", "https://cobalt.example.com")
        post_response = MagicMock()
        post_response.raise_for_status = MagicMock()
        post_response.json.side_effect = ValueError("not json")

        with patch.object(youtube_extractor.requests, "post", return_value=post_response):
            with pytest.raises(YouTubeExtractionError, match="invalid JSON"):
                youtube_extractor._download_via_cobalt(
                    "https://www.youtube.com/watch?v=abc12345678", tmp_path, 128, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

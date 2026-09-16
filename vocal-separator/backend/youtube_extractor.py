"""YouTube audio extraction: URL validation, metadata lookup, and download.

Downloads audio-only (never the video stream) directly to disk via yt-dlp,
then lets ffmpeg (through yt-dlp's postprocessor) encode straight to MP3 -
no full-resolution video or in-memory buffering involved.
"""

import logging
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp

from .config import (
	YOUTUBE_COOKIES_BROWSER,
	YOUTUBE_COOKIES_BROWSER_PROFILE_DIR,
	YOUTUBE_COOKIES_FILE,
	YOUTUBE_COOKIES_KEYRING,
	YOUTUBE_MAX_DURATION_SECONDS,
)

LOGGER = logging.getLogger(__name__)

YOUTUBE_DOMAINS = {
	"youtube.com",
	"www.youtube.com",
	"m.youtube.com",
	"youtu.be",
	"www.youtu.be",
}


class YouTubeExtractionError(Exception):
	"""Base error for all YouTube extraction failures."""


class InvalidURLError(YouTubeExtractionError):
	"""Raised when the given string is not a supported YouTube URL."""


class VideoTooLongError(YouTubeExtractionError):
	"""Raised when the video exceeds YOUTUBE_MAX_DURATION_SECONDS."""


class VideoUnavailableError(YouTubeExtractionError):
	"""Raised when yt-dlp cannot fetch or download the video (private,
	deleted, geo-restricted, age-restricted, etc.)."""


def _cookie_opts() -> dict:
	"""
	yt-dlp options to authenticate as a logged-in browser session, to work
	around YouTube's "Sign in to confirm you're not a bot" check (common on
	datacenter/server IPs). Two sources, checked in this order:

	1. A local browser profile (YOUTUBE_COOKIES_BROWSER + a non-empty
	   YOUTUBE_COOKIES_BROWSER_PROFILE_DIR mounted from the host) - reads
	   cookies live, no manual export needed. Reliable only for Firefox-based
	   browsers; see config.py for why Chromium-based ones usually can't be
	   decrypted from inside this container.
	2. A static YOUTUBE_COOKIES_FILE (cookies.txt export).

	Neither configured -> requests go out without cookies, exactly as
	before.
	"""
	profile_dir = Path(YOUTUBE_COOKIES_BROWSER_PROFILE_DIR)
	if YOUTUBE_COOKIES_BROWSER and profile_dir.is_dir() and any(profile_dir.iterdir()):
		return {
			"cookiesfrombrowser": (
				YOUTUBE_COOKIES_BROWSER,
				str(profile_dir),
				YOUTUBE_COOKIES_KEYRING or None,
				None,
			)
		}
	if Path(YOUTUBE_COOKIES_FILE).is_file():
		return {"cookiefile": YOUTUBE_COOKIES_FILE}
	return {}


def validate_youtube_url(url: str) -> bool:
	"""
	Check that `url` points at a YouTube watch page, short link, or Short -
	nothing else is accepted (no arbitrary sites, even if yt-dlp could
	technically handle them).
	"""
	try:
		parsed = urlparse(url)
	except ValueError:
		return False

	if parsed.scheme not in ("http", "https"):
		return False

	netloc = parsed.netloc.lower()
	if netloc not in YOUTUBE_DOMAINS:
		return False

	if netloc in ("youtu.be", "www.youtu.be"):
		return bool(parsed.path.strip("/"))

	if parsed.path == "/watch":
		return bool(parse_qs(parsed.query).get("v"))

	if parsed.path.startswith("/shorts/"):
		return bool(parsed.path[len("/shorts/"):].strip("/"))

	return False


def fetch_video_metadata(url: str) -> dict:
	"""
	Fetch title/duration/uploader without downloading anything.

	Raises:
		InvalidURLError: If `url` isn't a supported YouTube URL
		VideoUnavailableError: If yt-dlp can't read the video's info
		VideoTooLongError: If the video exceeds YOUTUBE_MAX_DURATION_SECONDS
	"""
	if not validate_youtube_url(url):
		raise InvalidURLError(f"Not a supported YouTube URL: {url}")

	ydl_opts = {
		"quiet": True,
		"no_warnings": True,
		"noplaylist": True,
		"skip_download": True,
		"socket_timeout": 30,
		**_cookie_opts(),
	}

	try:
		with yt_dlp.YoutubeDL(ydl_opts) as ydl:
			info = ydl.extract_info(url, download=False)
	except yt_dlp.utils.DownloadError as e:
		raise VideoUnavailableError(f"Could not read video info: {e}") from e

	duration = info.get("duration")
	if duration is None:
		raise VideoUnavailableError("Video has no determinable duration (livestream or unsupported).")

	if duration > YOUTUBE_MAX_DURATION_SECONDS:
		raise VideoTooLongError(
			f"Video is {duration / 60:.1f} min long. "
			f"Maximum allowed: {YOUTUBE_MAX_DURATION_SECONDS / 60:.0f} min."
		)

	return {
		"title": info.get("title") or "Unknown title",
		"duration_seconds": duration,
		"uploader": info.get("uploader") or "Unknown",
	}


def download_audio(
	url: str,
	output_dir: Path,
	bitrate_kbps: int = 320,
	on_progress: Callable[[float], None] | None = None,
) -> Path:
	"""
	Download the audio-only stream and encode it to MP3 at bitrate_kbps.

	Args:
		url: YouTube URL (should already be validated by the caller)
		output_dir: Directory to write the resulting MP3 into
		bitrate_kbps: Target MP3 bitrate
		on_progress: Optional callback(fraction: float 0.0-1.0) for download progress

	Returns:
		Path to the downloaded MP3 file

	Raises:
		VideoUnavailableError: If the download or extraction fails
		YouTubeExtractionError: If the expected output file is missing afterward
	"""
	output_dir.mkdir(parents=True, exist_ok=True)

	def _hook(d: dict) -> None:
		if on_progress and d.get("status") == "downloading":
			total = d.get("total_bytes") or d.get("total_bytes_estimate")
			downloaded = d.get("downloaded_bytes", 0)
			if total:
				on_progress(min(downloaded / total, 1.0))

	ydl_opts = {
		"format": "bestaudio/best",
		"outtmpl": str(output_dir / "%(id)s.%(ext)s"),
		"postprocessors": [
			{
				"key": "FFmpegExtractAudio",
				"preferredcodec": "mp3",
				"preferredquality": str(bitrate_kbps),
			}
		],
		"quiet": True,
		"no_warnings": True,
		"noplaylist": True,
		"socket_timeout": 30,
		"progress_hooks": [_hook],
		**_cookie_opts(),
	}

	try:
		with yt_dlp.YoutubeDL(ydl_opts) as ydl:
			info = ydl.extract_info(url, download=True)
	except yt_dlp.utils.DownloadError as e:
		raise VideoUnavailableError(f"Could not download audio: {e}") from e

	video_id = info.get("id")
	mp3_path = output_dir / f"{video_id}.mp3"
	if not mp3_path.is_file():
		raise YouTubeExtractionError(f"Expected output file not found after download: {mp3_path}")

	LOGGER.info("Downloaded YouTube audio: %s (%.1f MB)", mp3_path.name, mp3_path.stat().st_size / (1024 * 1024))
	return mp3_path

"""YouTube audio extraction: URL validation, metadata lookup, and download.

Downloads audio-only (never the video stream) directly to disk via yt-dlp,
then lets ffmpeg (through yt-dlp's postprocessor) encode straight to MP3 -
no full-resolution video or in-memory buffering involved.
"""

import logging
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import requests
import yt_dlp

from .config import (
	COBALT_API_KEY,
	COBALT_API_URL,
	COBALT_DOWNLOAD_TIMEOUT_SECONDS,
	COBALT_REQUEST_TIMEOUT_SECONDS,
	YOUTUBE_MAX_DURATION_SECONDS,
	YOUTUBE_PLAYER_CLIENTS,
	YOUTUBE_POT_FETCH_POLICY,
	YOUTUBE_POT_PROVIDER_BASE_URL,
)

# Cobalt only accepts these exact bitrate strings (docs/api.md#api-schema);
# an arbitrary bitrate_kbps is snapped to the nearest one.
_COBALT_AUDIO_BITRATES = (320, 256, 128, 96, 64, 8)

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


class _YtDlpLogger:
	"""
	Routes yt-dlp's internal messages (including the PO Token provider
	plugin's own diagnostics - request attempts, generated tokens, server
	errors) into this module's logger instead of stdout/stderr, so PO
	Token retrieval can actually be observed in production logs rather
	than silently succeeding or failing under `quiet`/`no_warnings`.
	Matches yt-dlp's documented custom-logger duck type: debug/warning/
	error, each taking one message string. Warnings/errors always reach
	this (yt-dlp routes them here unconditionally once a logger is set);
	debug-level messages - including the provider's token trace - only do
	so because `verbose: True` is also set below. Actual visibility is
	still gated centrally by LOG_LEVEL (config.py): quiet in production,
	verbose the moment you set LOG_LEVEL=DEBUG to investigate a failure.
	"""

	def debug(self, msg):
		LOGGER.debug("[yt-dlp] %s", msg)

	def warning(self, msg):
		LOGGER.warning("[yt-dlp] %s", msg)

	def error(self, msg):
		LOGGER.error("[yt-dlp] %s", msg)


def _extractor_args() -> dict:
	"""
	Builds yt-dlp's extractor_args, combining two independent anti-bot
	workarounds. Both live under the single "extractor_args" ydl_opts key
	(one per provider namespace), so they're merged here rather than in
	separate dicts - naively unpacking two dicts that both set
	"extractor_args" would silently drop one instead of merging them.

	1. YOUTUBE_PLAYER_CLIENTS: request mobile app player clients (e.g.
	   android) instead of the web client, which gets YouTube's strictest
	   anti-bot check.
	2. YOUTUBE_POT_PROVIDER_BASE_URL: points the bgutil-ytdlp-pot-provider
	   plugin (pip-installed, auto-registered - see requirements.txt) at
	   the companion PO Token server, since it runs in a separate
	   container (not yt-dlp's localhost default) - see docker-compose.yml's
	   "bgutil-provider" service. If that server is unreachable, the
	   plugin logs a warning via _YtDlpLogger and yt-dlp proceeds without
	   a token rather than failing the whole request.
	3. YOUTUBE_POT_FETCH_POLICY: forces yt-dlp to actually call the
	   provider (youtube:fetch_pot=always) instead of only doing so when
	   its own internal policy heuristic decides a token is strictly
	   required - which, for metadata-only lookups, it doesn't always do
	   even for clients (android included) whose policy says required=True.
	   Only set when a provider is actually configured, since forcing a
	   fetch with nothing to serve it is pointless.

	Neither configured -> no extractor_args, exactly as before either
	workaround existed.
	"""
	args = {}
	if YOUTUBE_PLAYER_CLIENTS:
		args["youtube"] = {"player_client": YOUTUBE_PLAYER_CLIENTS}
	if YOUTUBE_POT_PROVIDER_BASE_URL:
		args.setdefault("youtube", {})["fetch_pot"] = [YOUTUBE_POT_FETCH_POLICY]
		args["youtubepot-bgutilhttp"] = {"base_url": [YOUTUBE_POT_PROVIDER_BASE_URL]}
	return {"extractor_args": args} if args else {}


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
		"verbose": True,
		"logger": _YtDlpLogger(),
		"noplaylist": True,
		"skip_download": True,
		"socket_timeout": 30,
		**_extractor_args(),
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


def _download_via_cobalt(
	url: str,
	output_dir: Path,
	bitrate_kbps: int,
	on_progress: Callable[[float], None] | None,
) -> Path:
	"""
	Downloads audio through a self-hosted Cobalt instance (COBALT_API_URL)
	instead of yt-dlp. Cobalt's own server - hosted elsewhere, e.g. Render -
	makes the actual request to YouTube from its own (non-datacenter) IP,
	then hands back a URL to the already-processed MP3 for us to fetch
	over a plain HTTP GET. Used when this server's IP is blocked outright
	and player-client/PO-Token workarounds aren't enough on their own.

	Raises:
		VideoUnavailableError: If the Cobalt request or file download fails
		YouTubeExtractionError: If Cobalt's response is malformed/unexpected
	"""
	bitrate = min(_COBALT_AUDIO_BITRATES, key=lambda b: abs(b - bitrate_kbps))
	headers = {"Accept": "application/json", "Content-Type": "application/json"}
	if COBALT_API_KEY:
		headers["Authorization"] = f"Api-Key {COBALT_API_KEY}"

	try:
		response = requests.post(
			COBALT_API_URL,
			json={
				"url": url,
				"audioFormat": "mp3",
				"audioBitrate": str(bitrate),
				"downloadMode": "audio",
			},
			headers=headers,
			timeout=COBALT_REQUEST_TIMEOUT_SECONDS,
		)
		response.raise_for_status()
		result = response.json()
	except requests.RequestException as e:
		raise VideoUnavailableError(f"Cobalt API request failed: {e}") from e
	except ValueError as e:
		raise YouTubeExtractionError(f"Cobalt API returned invalid JSON: {e}") from e

	status = result.get("status")
	if status == "error":
		error = result.get("error") or {}
		raise VideoUnavailableError(f"Cobalt could not process this video: {error.get('code', 'unknown_error')}")
	if status not in ("tunnel", "redirect"):
		raise YouTubeExtractionError(f"Unexpected Cobalt response status: {status!r}")

	download_url = result.get("url")
	if not download_url:
		raise YouTubeExtractionError("Cobalt response did not include a download URL")

	output_dir.mkdir(parents=True, exist_ok=True)
	# .name strips any path components Cobalt's response might contain -
	# it's an external API response, not to be trusted as a safe path.
	filename = Path(result.get("filename") or "").name
	if not filename or filename in (".", ".."):
		filename = f"{uuid4()}.mp3"
	mp3_path = (output_dir / filename).with_suffix(".mp3")

	try:
		with requests.get(download_url, stream=True, timeout=COBALT_DOWNLOAD_TIMEOUT_SECONDS) as file_response:
			file_response.raise_for_status()
			total = int(file_response.headers.get("Content-Length") or 0)
			downloaded = 0
			with mp3_path.open("wb") as f:
				for chunk in file_response.iter_content(chunk_size=1024 * 1024):
					f.write(chunk)
					downloaded += len(chunk)
					if on_progress and total:
						on_progress(min(downloaded / total, 1.0))
	except requests.RequestException as e:
		mp3_path.unlink(missing_ok=True)
		raise VideoUnavailableError(f"Could not download file from Cobalt tunnel: {e}") from e

	if not mp3_path.is_file() or mp3_path.stat().st_size == 0:
		mp3_path.unlink(missing_ok=True)
		raise YouTubeExtractionError("Cobalt tunnel download produced an empty or missing file")

	LOGGER.info(
		"Downloaded YouTube audio via Cobalt: %s (%.1f MB)", mp3_path.name, mp3_path.stat().st_size / (1024 * 1024)
	)
	return mp3_path


def _download_via_ytdlp(
	url: str,
	output_dir: Path,
	bitrate_kbps: int,
	on_progress: Callable[[float], None] | None,
) -> Path:
	"""Original download path - see download_audio() for the public contract."""
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
		"verbose": True,
		"logger": _YtDlpLogger(),
		"noplaylist": True,
		"socket_timeout": 30,
		"progress_hooks": [_hook],
		**_extractor_args(),
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


def download_audio(
	url: str,
	output_dir: Path,
	bitrate_kbps: int = 320,
	on_progress: Callable[[float], None] | None = None,
) -> Path:
	"""
	Download the audio-only stream and encode it to MP3 at bitrate_kbps.
	Uses a self-hosted Cobalt instance when COBALT_API_URL is configured
	(see config.py), otherwise yt-dlp directly - same public contract
	either way, so callers (tasks.py) never need to know which one ran.

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
	if COBALT_API_URL:
		return _download_via_cobalt(url, output_dir, bitrate_kbps, on_progress)
	return _download_via_ytdlp(url, output_dir, bitrate_kbps, on_progress)

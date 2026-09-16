"""Application configuration for the Vocal Separator backend."""

import logging
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv


# Load environment variables from .env file if it exists, otherwise use environment vars from docker-compose
BASE_DIR = Path(__file__).resolve().parent
_env_path = BASE_DIR / ".env"
if _env_path.exists():
	load_dotenv(_env_path)
else:
	# In Docker, rely on docker-compose environment: section (set via compose.yml)
	load_dotenv()


# Redis connection settings used by Celery.
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

if REDIS_PASSWORD:
	_redis_credentials = f":{quote(REDIS_PASSWORD, safe='')}@"
else:
	_redis_credentials = ""

REDIS_URL = (
	f"redis://{_redis_credentials}{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
)


# Celery settings for background separation tasks.
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_SERIALIZER = os.getenv("CELERY_TASK_SERIALIZER", "json")
CELERY_RESULT_SERIALIZER = os.getenv("CELERY_RESULT_SERIALIZER", "json")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "3600"))
CELERY_TASK_SOFT_TIME_LIMIT = int(
	os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "3300")
)


# Persistent directories for uploaded files and separated audio tracks.
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", BASE_DIR / "uploads"))
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", BASE_DIR / "outputs"))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


# Uploads and separated stems are deleted once they are older than this.
RETENTION_HOURS = int(os.getenv("RETENTION_HOURS", "1"))
CLEANUP_INTERVAL_MINUTES = int(os.getenv("CLEANUP_INTERVAL_MINUTES", "15"))


# Development frontend origins. Override with a comma-separated .env value.
_cors_origins = os.getenv(
	"CORS_ORIGINS",
	"http://localhost:3000,http://127.0.0.1:3000",
)
CORS_ORIGINS = [origin.strip() for origin in _cors_origins.split(",") if origin.strip()]


# Demucs model used to split a track into vocals and instrumental stems.
# htdemucs is the v4 hybrid transformer model; htdemucs_ft is slower but cleaner.
DEMUCS_MODEL = os.getenv("DEMUCS_MODEL", "htdemucs")

# Higher overlap means better quality and slower processing (Demucs default 0.25).
DEMUCS_OVERLAP = float(os.getenv("DEMUCS_OVERLAP", "0.25"))

# Cores used for separation. Defaults to 60% of them so the machine stays cool
# and responsive; set DEMUCS_THREADS to override.
_default_threads = max(1, int((os.cpu_count() or 1) * 0.6))
DEMUCS_THREADS = max(1, int(os.getenv("DEMUCS_THREADS", _default_threads)))

# Measured CPU cost per second of audio, used only to estimate progress.
DEMUCS_SECONDS_PER_AUDIO_SECOND = float(
	os.getenv("DEMUCS_SECONDS_PER_AUDIO_SECOND", "1.2")
)


# Accepted audio formats and maximum upload size (500 MB by default).
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a"}
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "500"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


# Audio output format and compression settings (optimization).
AUDIO_OUTPUT_FORMAT = os.getenv("AUDIO_OUTPUT_FORMAT", "mp3").lower()
MP3_BITRATE = os.getenv("MP3_BITRATE", "128k")

# Model idle timeout: unload from RAM if idle > N seconds to save memory.
MODEL_IDLE_TIMEOUT_SECONDS = int(os.getenv("MODEL_IDLE_TIMEOUT_SECONDS", "3600"))


# YouTube audio extraction (Feature 7). Duration is checked from metadata
# before any download starts, to reject long videos cheaply.
YOUTUBE_MAX_DURATION_SECONDS = int(os.getenv("YOUTUBE_MAX_DURATION_SECONDS", "900"))
YOUTUBE_AUDIO_BITRATE_KBPS = int(os.getenv("YOUTUBE_AUDIO_BITRATE_KBPS", "320"))
YOUTUBE_DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("YOUTUBE_DOWNLOAD_TIMEOUT_SECONDS", "300"))

# Netscape-format cookies.txt exported from a logged-in YouTube session, used
# to get past YouTube's "Sign in to confirm you're not a bot" anti-bot check
# (common when requests come from a datacenter/server IP). Optional: if the
# file doesn't exist, yt-dlp runs without cookies as before.
YOUTUBE_COOKIES_FILE = os.getenv(
	"YOUTUBE_COOKIES_FILE", str(BASE_DIR / "cookies" / "youtube_cookies.txt")
)

# Alternative to YOUTUBE_COOKIES_FILE: read cookies live from a local
# browser's profile instead of a static export. Set to a browser yt-dlp
# supports (firefox, chrome, chromium, edge, brave, vivaldi, opera, safari,
# whale) and bind-mount that browser's profile directory into the container
# at YOUTUBE_COOKIES_BROWSER_PROFILE_DIR - see docker-compose.yml's
# YOUTUBE_BROWSER_PROFILE_HOST_PATH. Takes priority over YOUTUBE_COOKIES_FILE
# when both are configured.
#
# Only reliable for Firefox-based browsers: Firefox stores cookies
# unencrypted on disk, but Chromium-based browsers (Chrome, Edge, Brave,
# Vivaldi, Opera, Whale) encrypt them with a key held by the host OS
# keychain (macOS Keychain, Windows DPAPI) - a Linux container has no way to
# reach that, so decryption fails there. On a Linux host, Chromium cookies
# may still be reachable via YOUTUBE_COOKIES_KEYRING below.
YOUTUBE_COOKIES_BROWSER = os.getenv("YOUTUBE_COOKIES_BROWSER", "")

# Container-side path where the host browser profile is mounted. Matches
# docker-compose.yml's mount target by default - only override if you
# changed that.
YOUTUBE_COOKIES_BROWSER_PROFILE_DIR = os.getenv(
	"YOUTUBE_COOKIES_BROWSER_PROFILE_DIR", str(BASE_DIR / "browser_profile")
)

# Linux-only keyring backend for decrypting Chromium-based browser cookies:
# BASICTEXT, GNOMEKEYRING, KWALLET, KWALLET5, or KWALLET6. Leave blank for
# Firefox, which doesn't need one.
YOUTUBE_COOKIES_KEYRING = os.getenv("YOUTUBE_COOKIES_KEYRING", "")


# Basic application logging; LOG_LEVEL can be changed through .env.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
	level=getattr(logging, LOG_LEVEL, logging.INFO),
	format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOGGER = logging.getLogger("vocal_separator")

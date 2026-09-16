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

# yt-dlp player clients to request, comma-separated (e.g. "android,ios").
# The mobile app clients get YouTube's lighter anti-bot check (unlike the
# web client), which is what gets past "Sign in to confirm you're not a
# bot" - no account, cookies, or login needed. Empty disables the override
# (falls back to yt-dlp's own default clients).
YOUTUBE_PLAYER_CLIENTS = [
	c.strip() for c in os.getenv("YOUTUBE_PLAYER_CLIENTS", "android").split(",") if c.strip()
]

# Base URL of the companion PO Token (Proof of Origin) provider server -
# see docker-compose.yml's "bgutil-provider" service and
# https://github.com/Brainicism/bgutil-ytdlp-pot-provider. Generates the
# token YouTube's anti-bot checks increasingly require, without cookies or
# a logged-in account. Consumed by the bgutil-ytdlp-pot-provider yt-dlp
# plugin (pip-installed, auto-registers - see requirements.txt), which
# degrades gracefully (logs a warning, no token) if this server is
# unreachable, rather than failing the whole extraction. Empty disables it.
YOUTUBE_POT_PROVIDER_BASE_URL = os.getenv(
	"YOUTUBE_POT_PROVIDER_BASE_URL", "http://bgutil-provider:4416"
)

# yt-dlp's youtube:fetch_pot extractor arg: never / auto / always. "auto"
# (yt-dlp's own default) only fetches a PO Token when yt-dlp's internal
# per-client/per-format policy marks it strictly "required" at the exact
# call site that builds stream URLs - which the metadata-only lookup path
# (fetch_video_metadata's skip_download) doesn't always reach, even for
# clients whose policy says required=True (android included). "always"
# forces the bgutil provider to be called on every request regardless, so
# the token is actually generated and observable in logs instead of being
# silently skipped by that heuristic.
YOUTUBE_POT_FETCH_POLICY = os.getenv("YOUTUBE_POT_FETCH_POLICY", "always")

# Base URL of a self-hosted Cobalt instance (https://github.com/imputnet/cobalt)
# used as an alternative to yt-dlp for the actual audio *download* step
# (fetch_video_metadata still uses yt-dlp - Cobalt's response doesn't
# include duration/title). Cobalt's own server runs elsewhere (e.g.
# Render) with a clean, non-datacenter IP, so it's the one YouTube sees
# making the request, sidestepping this server's IP reputation entirely.
# Empty (default) disables this path - download_audio() then uses yt-dlp
# as before. Never defaults to a public/shared Cobalt instance: cobalt's
# own docs explicitly ask that hosted instances like api.cobalt.tools not
# be used by other projects without permission - this must be your own
# self-hosted instance.
COBALT_API_URL = os.getenv("COBALT_API_URL", "")

# Only needed if your Cobalt instance is configured to require API-key
# authentication. Sent as "Authorization: Api-Key <token>".
COBALT_API_KEY = os.getenv("COBALT_API_KEY", "")

# Timeouts for talking to the Cobalt instance: the initial POST that starts
# processing, and the streamed download of the resulting file it hands back.
COBALT_REQUEST_TIMEOUT_SECONDS = int(os.getenv("COBALT_REQUEST_TIMEOUT_SECONDS", "30"))
COBALT_DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("COBALT_DOWNLOAD_TIMEOUT_SECONDS", "300"))


# Basic application logging; LOG_LEVEL can be changed through .env.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
	level=getattr(logging, LOG_LEVEL, logging.INFO),
	format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
LOGGER = logging.getLogger("vocal_separator")

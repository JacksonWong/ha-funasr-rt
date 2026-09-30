"""Constants for the FunASR Realtime STT integration."""

DOMAIN = "funasr_rt"

# Config entry data
CONF_BACKEND = "backend"
CONF_API_KEY = "***"
CONF_WORKSPACE = "workspace"

# Config entry options
CONF_MODEL = "model"
CONF_HOTWORDS = "hotwords"
CONF_AUTO_HOTWORDS = "auto_hotwords"
CONF_VOCAB_PREFIX = "vocab_prefix"
CONF_FINAL_TIMEOUT = "final_timeout"

BACKEND_CLOUD = "cloud"
BACKEND_LOCAL = "local"  # 预留：本地 FunASR runtime (WebSocket) 后端

DEFAULT_MODEL = "fun-asr-realtime"
DEFAULT_VOCAB_PREFIX = "hafunasr"
DEFAULT_FINAL_TIMEOUT = 15

CLOUD_VALIDATE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/models"

MAX_HOTWORDS = 500
MAX_WORD_LEN = 30
HOTWORD_WEIGHT = 4

VOCAB_CACHE_KEY = "***"

"""Constants for the FunASR Realtime STT integration."""

DOMAIN = "funasr_rt"

# Config entry data
CONF_BACKEND = "backend"
CONF_API_KEY = "***"
CONF_WORKSPACE = "workspace"
CONF_SERVER_URL = "server_url"  # local backend: FunASR runtime ws:// URL

# Config entry options
CONF_MODEL = "model"
CONF_HOTWORDS = "hotwords"
CONF_AUTO_HOTWORDS = "auto_hotwords"
CONF_VOCAB_PREFIX = "vocab_prefix"
CONF_FINAL_TIMEOUT = "final_timeout"

BACKEND_CLOUD = "cloud"
BACKEND_LOCAL = "local"  # 本地 FunASR runtime (WebSocket 2pass) 后端，v1.1.0 实现

DEFAULT_MODEL = "fun-asr-realtime"
DEFAULT_VOCAB_PREFIX = "hafunasr"
DEFAULT_FINAL_TIMEOUT = 15

CLOUD_VALIDATE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/models"

MAX_HOTWORDS = 500
HOTWORD_WEIGHT = 4

# 本地后端 (FunASR runtime)
LOCAL_HOTWORD_SCORE = 20    # runtime 热词权重（官方示例惯例值）
LOCAL_CONNECT_TIMEOUT = 10  # ws 连接/接收超时（秒）

# DashScope 热词格式硬规则（2026-09-30 实测探针验证）
MAX_NONASCII_LEN = 15     # 含非 ASCII 的词：总长度上限
MAX_ASCII_SEGMENTS = 7    # 纯 ASCII 词：空格分段数上限
VOCAB_FAIL_BACKOFF = 600  # 热词表失败重试退避（秒）

VOCAB_CACHE_KEY = "***"

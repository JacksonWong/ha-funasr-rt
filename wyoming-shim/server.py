"""Wyoming STT shim -> Aliyun Bailian FunASR (fun-asr-realtime).

Phase 2: real DashScope streaming backend + hotword vocabulary + errors.

Per wyoming connection (= one HA transcription session):
  Transcribe  -> capture language + hotwords (HA pipeline vocabulary)
  AudioStart  -> ensure hotword vocabulary, open DashScope WS session
  AudioChunk+ -> normalize to 16k/16bit/mono, stream frames to DashScope
  AudioStop   -> await final transcript (bounded), reply Transcript | Error

Run:
    python server.py --uri tcp://0.0.0.0:10300
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path

import dashscope
from dashscope.audio.asr import (
    Recognition,
    RecognitionCallback,
    RecognitionResult,
    VocabularyService,
)
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Describe, Info
from wyoming.ping import Ping, Pong
from wyoming.server import AsyncEventHandler, AsyncServer

from envutil import load_project_env

_LOGGER = logging.getLogger("wyoming-funasr-shim")

PROGRAM_NAME = "bailian-fun-asr"
MODEL_NAME_DEFAULT = "fun-asr-realtime"
MODEL_LANGUAGES = ["zh-CN", "zh", "en-US", "en"]
ATTRIBUTION = Attribution(
    name="Aliyun Bailian (DashScope)",
    url="https://bailian.console.aliyun.com/",
)
VERSION = "0.2.0"

TARGET_RATE, TARGET_WIDTH, TARGET_CHANNELS = 16000, 2, 1
MAX_HOTWORDS = 500
MAX_WORD_LEN = 30
DUMMY_TEXT = "测试成功"


class Config:
    def __init__(self, args: argparse.Namespace) -> None:
        self.backend = args.backend
        self.model = (
            args.model or os.environ.get("FUNASR_MODEL") or MODEL_NAME_DEFAULT
        )
        self.workspace = (
            args.workspace or os.environ.get("DASHSCOPE_WORKSPACE") or None
        )
        self.vocab_prefix = (
            args.vocab_prefix or os.environ.get("FUNASR_VOCAB_PREFIX") or "wyshim"
        )
        self.final_timeout = float(
            args.final_timeout
            or os.environ.get("FUNASR_FINAL_TIMEOUT")
            or "15"
        )
        env_words = os.environ.get("FUNASR_HOTWORDS", "")
        self.static_hotwords = [w.strip() for w in env_words.split(",") if w.strip()]
        self.state_path = Path(
            os.environ.get("FUNASR_STATE_FILE")
            or Path(__file__).resolve().parent / ".vocab_state.json"
        )


def build_info(cfg: Config) -> Info:
    return Info(
        asr=[
            AsrProgram(
                name=PROGRAM_NAME,
                description="Aliyun Bailian FunASR realtime STT (Wyoming shim)",
                attribution=ATTRIBUTION,
                installed=True,
                version=VERSION,
                models=[
                    AsrModel(
                        name=cfg.model,
                        description=f"{cfg.model} via DashScope WebSocket",
                        attribution=ATTRIBUTION,
                        installed=True,
                        version=VERSION,
                        languages=MODEL_LANGUAGES,
                    )
                ],
                requires_external_vad=True,
                supports_transcript_streaming=False,
            )
        ]
    )


# --------------------------------------------------------------------------
# Hotword vocabulary management (shared across sessions)
# --------------------------------------------------------------------------


class VocabularyManager:
    """Maintains ONE DashScope hotword vocabulary for the shim.

    - words = HA pipeline vocabulary (transcript_names/terms) + FUNASR_HOTWORDS
    - content hash cached in .vocab_state.json; network call only on change
    - adopts an existing prefix-matched vocabulary on first run
    - any failure degrades to "no hotwords", never kills a session
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._state: dict = {}
        if cfg.state_path.exists():
            try:
                self._state = json.loads(cfg.state_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._state = {}

    def _save(self) -> None:
        try:
            self._cfg.state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("vocab state save failed: %s", exc)

    @staticmethod
    def _payload(words: list[str]) -> list[dict]:
        clean = []
        seen = set()
        for w in words:
            w = w.strip()
            if not w or len(w) > MAX_WORD_LEN or w in seen:
                continue
            seen.add(w)
            clean.append(
                {"text": w, "weight": 4, "lang": "en" if w.isascii() else "zh"}
            )
        return clean[:MAX_HOTWORDS]

    async def ensure(self, words: list[str]) -> str | None:
        """Return vocabulary_id to use (may be None). Updates only on change."""
        payload = self._payload(sorted(set(words)))
        async with self._lock:
            vid = self._state.get("vocabulary_id")
            if not payload:
                return vid
            digest = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:16]
            if vid and self._state.get("hash") == digest:
                return vid

            loop = asyncio.get_running_loop()
            svc = VocabularyService()
            try:
                if vid:
                    await loop.run_in_executor(
                        None, svc.update_vocabulary, vid, payload
                    )
                else:
                    existing = await loop.run_in_executor(
                        None,
                        lambda: svc.list_vocabularies(
                            prefix=self._cfg.vocab_prefix, page_size=10
                        ),
                    )
                    if existing:
                        vid = existing[0]["vocabulary_id"]
                        await loop.run_in_executor(
                            None, svc.update_vocabulary, vid, payload
                        )
                    else:
                        vid = await loop.run_in_executor(
                            None,
                            lambda: svc.create_vocabulary(
                                target_model=self._cfg.model,
                                prefix=self._cfg.vocab_prefix,
                                vocabulary=payload,
                            ),
                        )
                self._state = {
                    "vocabulary_id": vid,
                    "hash": digest,
                    "words": [e["text"] for e in payload],
                    "updated_at": int(time.time()),
                }
                self._save()
                _LOGGER.info(
                    "hotword vocabulary ready: %s (%d words)", vid, len(payload)
                )
                return vid
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "vocabulary update failed (%s); continuing with cached=%s",
                    exc,
                    vid,
                )
                return vid


# --------------------------------------------------------------------------
# Per-session DashScope backend
# --------------------------------------------------------------------------


class _Callback(RecognitionCallback):
    """Fires on SDK threads; only touch thread-safe state here."""

    def __init__(self, session: "BackendSession") -> None:
        self._s = session

    def on_open(self) -> None:
        self._s.opened = True

    def on_event(self, *args) -> None:
        result = args[-1]
        try:
            sentence = result.get_sentence()
        except Exception:  # noqa: BLE001
            return
        items = (
            sentence if isinstance(sentence, list) else [sentence] if sentence else []
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not text:
                continue
            try:
                is_end = bool(RecognitionResult.is_sentence_end(item))
            except Exception:  # noqa: BLE001
                is_end = True
            if is_end:
                self._s.finals.append(text)

    def on_error(self, message) -> None:
        # dashscope 1.27.7 bug: str(RecognitionResult) crashes (missing
        # 'headers'); extract fields defensively instead.
        fields = []
        for attr in ("status_code", "code", "message", "request_id"):
            value = getattr(message, attr, None)
            if value is None and isinstance(message, dict):
                value = message.get(attr)
            if value is not None:
                fields.append(f"{attr}={value}")
        self._s.error = "; ".join(fields) or "unknown DashScope error"

    def on_complete(self) -> None:
        self._s.completed = True

    def on_close(self) -> None:
        self._s.closed = True


class BackendSession:
    """One DashScope Recognition stream bound to one wyoming session."""

    def __init__(self, cfg: Config, vocabulary_id: str | None) -> None:
        self.cfg = cfg
        self.finals: list[str] = []
        self.error: str | None = None
        self.opened = False
        self.completed = False
        self.closed = False
        self.started = False
        self.finished = False
        kwargs = {}
        if vocabulary_id:
            kwargs["vocabulary_id"] = vocabulary_id
        self.vocabulary_id = vocabulary_id
        self._cb = _Callback(self)
        self._rec = Recognition(
            model=cfg.model,
            format="pcm",
            sample_rate=TARGET_RATE,
            callback=self._cb,
            workspace=cfg.workspace,
            **kwargs,
        )
        self._loop = asyncio.get_running_loop()

    async def start(self) -> None:
        await self._loop.run_in_executor(None, self._rec.start)
        self.started = True

    def feed(self, data: bytes) -> None:
        """Non-blocking queue put; raises if the task already stopped."""
        self._rec.send_audio_frame(data)

    async def finish(self, timeout: float) -> tuple[str | None, str | None]:
        """Stop and wait for the final result. Returns (text, error)."""
        self.finished = True
        if self.error:
            return None, self.error
        try:
            await asyncio.wait_for(
                self._loop.run_in_executor(None, self._rec.stop), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None, f"timeout: no final result within {timeout}s"
        except Exception as exc:  # noqa: BLE001
            if not self.error:
                return None, f"stop failed: {type(exc).__name__}: {exc}"
        if self.error:
            return None, self.error
        return "".join(self.finals), None

    async def cleanup(self) -> None:
        """Best-effort teardown when the client vanished mid-session."""
        if not self.started or self.finished:
            return
        try:
            await asyncio.wait_for(
                self._loop.run_in_executor(None, self._rec.stop), timeout=5
            )
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# Wyoming handler
# --------------------------------------------------------------------------


class SttHandler(AsyncEventHandler):
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        cfg: Config,
        vocab: VocabularyManager,
    ) -> None:
        super().__init__(reader, writer)
        self.cfg = cfg
        self.vocab = vocab
        self.language: str | None = None
        self.hotwords: list[str] = []
        self.converter = AudioChunkConverter(
            rate=TARGET_RATE, width=TARGET_WIDTH, channels=TARGET_CHANNELS
        )
        self.session: BackendSession | None = None
        self.failed: str | None = None
        self.audio_bytes = 0
        self.chunks = 0
        self.t_start: float | None = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            peer = self.writer.get_extra_info("peername")
            _LOGGER.info("describe request from %s", peer)
            await self.write_event(build_info(self.cfg).event())
            return True

        if Ping.is_type(event.type):
            ping = Ping.from_event(event)
            await self.write_event(Pong(text=ping.text).event())
            return True

        if Transcribe.is_type(event.type):
            transcribe = Transcribe.from_event(event)
            self.language = transcribe.language
            self.hotwords = list(transcribe.transcript_names or []) + list(
                transcribe.transcript_terms or []
            )
            self.t_start = time.monotonic()
            _LOGGER.info(
                "session start: language=%s ha_hotwords=%d static_hotwords=%d backend=%s",
                self.language,
                len(self.hotwords),
                len(self.cfg.static_hotwords),
                self.cfg.backend,
            )
            return True

        if AudioStart.is_type(event.type):
            start = AudioStart.from_event(event)
            self.audio_bytes = 0
            self.chunks = 0
            _LOGGER.info(
                "audio start: rate=%s width=%s channels=%s",
                start.rate,
                start.width,
                start.channels,
            )
            if self.cfg.backend == "dummy":
                return True
            try:
                vid = await self.vocab.ensure(
                    self.hotwords + self.cfg.static_hotwords
                )
                self.session = BackendSession(self.cfg, vid)
                await self.session.start()
            except Exception as exc:  # noqa: BLE001
                self.failed = f"backend start failed: {type(exc).__name__}: {exc}"
                _LOGGER.error(self.failed)
            return True

        if AudioChunk.is_type(event.type):
            if self.cfg.backend == "dummy":
                self.audio_bytes += len(event.payload or b"")
                self.chunks += 1
                return True
            if self.session is None or self.failed:
                return True  # drain
            try:
                chunk = self.converter.convert(AudioChunk.from_event(event))
                self.session.feed(chunk.audio)
                self.audio_bytes += len(chunk.audio)
                self.chunks += 1
            except Exception as exc:  # noqa: BLE001
                if not self.failed:
                    self.failed = f"feed failed: {type(exc).__name__}: {exc}"
                    _LOGGER.error(self.failed)
            return True

        if AudioStop.is_type(event.type):
            await self._finish_session()
            return False  # one session per connection

        _LOGGER.debug("unhandled event type: %s", event.type)
        return True

    async def _finish_session(self) -> None:
        audio_seconds = self.audio_bytes / (TARGET_RATE * TARGET_WIDTH * TARGET_CHANNELS)

        if self.cfg.backend == "dummy":
            _LOGGER.info("audio stop (dummy): %.2fs in %d chunks", audio_seconds, self.chunks)
            await self.write_event(
                Transcript(text=DUMMY_TEXT, language=self.language).event()
            )
            return

        text: str | None = None
        err = self.failed
        if self.session is not None:
            text, backend_err = await self.session.finish(self.cfg.final_timeout)
            # 后端错误（如 401）是根因，优先于 feed 失败提示
            err = backend_err or err

        wall = time.monotonic() - self.t_start if self.t_start else 0.0

        if err:
            _LOGGER.error(
                "session failed: %.2fs audio, %.2fs wall, error: %s",
                audio_seconds,
                wall,
                err,
            )
            await self.write_event(Error(text=err, code="stt-backend-error").event())
            return

        text = text or ""
        if not text:
            _LOGGER.warning(
                "empty transcript: %.2fs audio in %d chunks, %.2fs wall",
                audio_seconds,
                self.chunks,
                wall,
            )
        else:
            _LOGGER.info(
                "final: %r (%.2fs audio, %.2fs wall)", text, audio_seconds, wall
            )
        await self.write_event(
            Transcript(text=text, language=self.language).event()
        )

    async def disconnect(self) -> None:
        if self.session is not None:
            await self.session.cleanup()


async def amain(cfg: Config, uri: str) -> None:
    if cfg.backend == "dashscope":
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise SystemExit("DASHSCOPE_API_KEY 未设置（环境变量或项目 .env）")
        dashscope.api_key = api_key
    vocab = VocabularyManager(cfg)
    server = AsyncServer.from_uri(uri)
    _LOGGER.info(
        "wyoming-funasr-shim v%s listening on %s (backend=%s model=%s vocab_prefix=%s)",
        VERSION,
        uri,
        cfg.backend,
        cfg.model,
        cfg.vocab_prefix,
    )
    await server.run(lambda r, w: SttHandler(r, w, cfg, vocab))


def main() -> None:
    load_project_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default="tcp://0.0.0.0:10300")
    parser.add_argument(
        "--backend",
        choices=("dashscope", "dummy"),
        default=os.environ.get("FUNASR_BACKEND", "dashscope"),
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--vocab-prefix", default=None)
    parser.add_argument("--final-timeout", default=None)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(amain(Config(args), args.uri))


if __name__ == "__main__":
    main()

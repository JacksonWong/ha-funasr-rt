"""STT platform for FunASR Realtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
import hashlib
import json
import logging

from homeassistant.components.stt import (
    AudioBitRates,
    AudioChannels,
    AudioCodecs,
    AudioFormats,
    AudioSampleRates,
    SpeechMetadata,
    SpeechResult,
    SpeechResultState,
    SpeechToTextEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_FRIENDLY_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_API_KEY,
    CONF_AUTO_HOTWORDS,
    CONF_FINAL_TIMEOUT,
    CONF_HOTWORDS,
    CONF_MODEL,
    CONF_VOCAB_PREFIX,
    CONF_WORKSPACE,
    DEFAULT_FINAL_TIMEOUT,
    DEFAULT_MODEL,
    DEFAULT_VOCAB_PREFIX,
    DOMAIN,
    HOTWORD_WEIGHT,
    MAX_HOTWORDS,
    MAX_WORD_LEN,
    VOCAB_CACHE_KEY,
)

_LOGGER = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ["zh", "zh-CN", "en", "en-US"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the FunASR RT STT platform from a config entry."""
    async_add_entities([FunAsrRtSpeechEntity(entry)])


class FunAsrRtSpeechEntity(SpeechToTextEntity):
    """Speech-to-text entity backed by FunASR realtime models (cloud: Bailian)."""

    _attr_icon = "mdi:message-processing-outline"
    _attr_name = "FunASR Realtime"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_stt"

    @property
    def supported_languages(self) -> list[str]:
        """Return a list of supported languages."""
        return SUPPORTED_LANGUAGES

    @property
    def supported_formats(self) -> list[AudioFormats]:
        """Return a list of supported formats."""
        return [AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[AudioCodecs]:
        """Return a list of supported codecs."""
        return [AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[AudioBitRates]:
        """Return a list of supported bit rates."""
        return [AudioBitRates.BIT_16]

    @property
    def supported_sample_rates(self) -> list[AudioSampleRates]:
        """Return a list of supported sample rates."""
        return [AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[AudioChannels]:
        """Return a list of supported channels."""
        return [AudioChannels.CHANNEL_1]

    # ------------------------------------------------------------------
    # Hotwords
    # ------------------------------------------------------------------
    def _collect_words(self) -> list[str]:
        """Manual hotwords + optional auto-collected device/area names."""
        opts = self._entry.options
        words: set[str] = set()
        manual = opts.get(CONF_HOTWORDS, "") or ""
        for word in manual.replace("，", ",").replace("\n", ",").split(","):
            word = word.strip()
            if word and len(word) <= MAX_WORD_LEN:
                words.add(word)
        if opts.get(CONF_AUTO_HOTWORDS, True) and self.hass is not None:
            for state in self.hass.states.async_all():
                name = state.attributes.get(ATTR_FRIENDLY_NAME)
                if isinstance(name, str) and name and len(name) <= MAX_WORD_LEN:
                    words.add(name.strip())
            try:
                for area in ar.async_get(self.hass).async_list_areas():
                    if area.name and len(area.name) <= MAX_WORD_LEN:
                        words.add(area.name.strip())
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Area registry unavailable for hotwords", exc_info=True)
        return sorted(words)[:MAX_HOTWORDS]

    @staticmethod
    def _ensure_vocabulary_sync(words, api_key, workspace, prefix, model, cache):
        """Blocking vocabulary create/update; run in executor.

        Returns the vocabulary_id to use (may be a stale cached id on failure,
        or None). Never raises for expected API errors is NOT guaranteed —
        callers wrap in try/except and degrade to no hotwords.
        """
        if not words:
            return cache.get("id")
        payload = [
            {"text": w, "weight": HOTWORD_WEIGHT, "lang": "en" if w.isascii() else "zh"}
            for w in words
        ]
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        vid = cache.get("id")
        if vid and cache.get("hash") == digest:
            return vid

        import dashscope  # noqa: PLC0415 (installed from manifest requirements)
        from dashscope.audio.asr import VocabularyService  # noqa: PLC0415

        dashscope.api_key = api_key
        svc = VocabularyService(api_key=api_key, workspace=workspace)
        if vid:
            svc.update_vocabulary(vid, payload)
        else:
            existing = svc.list_vocabularies(prefix=prefix, page_size=10)
            if existing:
                vid = existing[0]["vocabulary_id"]
                svc.update_vocabulary(vid, payload)
            else:
                vid = svc.create_vocabulary(
                    target_model=model, prefix=prefix, vocabulary=payload
                )
        cache["id"] = vid
        cache["hash"] = digest
        cache["words"] = len(payload)
        _LOGGER.info("Hotword vocabulary %s ready (%d words)", vid, len(payload))
        return vid

    # ------------------------------------------------------------------
    # Streaming STT
    # ------------------------------------------------------------------
    async def async_process_audio_stream(
        self, metadata: SpeechMetadata, stream: AsyncIterable[bytes]
    ) -> SpeechResult:
        """Convert an audio stream to text via DashScope realtime ASR."""
        import dashscope  # noqa: PLC0415
        from dashscope.audio.asr import (  # noqa: PLC0415
            Recognition,
            RecognitionCallback,
            RecognitionResult,
        )

        opts = self._entry.options
        api_key = self._entry.data[CONF_API_KEY]
        workspace = self._entry.data.get(CONF_WORKSPACE) or None
        model = opts.get(CONF_MODEL, DEFAULT_MODEL)
        prefix = opts.get(CONF_VOCAB_PREFIX, DEFAULT_VOCAB_PREFIX)
        timeout = float(opts.get(CONF_FINAL_TIMEOUT, DEFAULT_FINAL_TIMEOUT))

        # Hotword vocabulary (best-effort; failures degrade to no hotwords)
        vocabulary_id = None
        words = self._collect_words()
        if words:
            cache = (
                self.hass.data.setdefault(DOMAIN, {})
                .setdefault(self._entry.entry_id, {})
                .setdefault(VOCAB_CACHE_KEY, {})
            )
            try:
                vocabulary_id = await self.hass.async_add_executor_job(
                    self._ensure_vocabulary_sync,
                    words,
                    api_key,
                    workspace,
                    prefix,
                    model,
                    cache,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Hotword vocabulary update failed (%s); continuing without", err
                )

        finals: list[str] = []
        errors: list[str] = []

        class _Callback(RecognitionCallback):
            """Fires on SDK threads; only touch thread-safe locals here."""

            def on_event(self, *args):
                result = args[-1]
                try:
                    sentence = result.get_sentence()
                except Exception:  # noqa: BLE001
                    return
                items = (
                    sentence
                    if isinstance(sentence, list)
                    else [sentence]
                    if sentence
                    else []
                )
                for item in items:
                    if not isinstance(item, dict) or not item.get("text"):
                        continue
                    try:
                        is_end = bool(RecognitionResult.is_sentence_end(item))
                    except Exception:  # noqa: BLE001
                        is_end = True
                    if is_end:
                        finals.append(item["text"])

            def on_error(self, message):
                # dashscope 1.27.7 bug: str(RecognitionResult) crashes
                # (missing 'headers'); extract fields defensively.
                fields = []
                for attr in ("status_code", "code", "message"):
                    value = getattr(message, attr, None)
                    if value is None and isinstance(message, dict):
                        value = message.get(attr)
                    if value is not None:
                        fields.append(f"{attr}={value}")
                errors.append("; ".join(fields) or "unknown DashScope error")

        def _start() -> Recognition:
            dashscope.api_key = api_key
            kwargs = {}
            if vocabulary_id:
                kwargs["vocabulary_id"] = vocabulary_id
            rec = Recognition(
                model=model,
                format="pcm",
                sample_rate=int(metadata.sample_rate),
                callback=_Callback(),
                workspace=workspace,
                **kwargs,
            )
            rec.start()
            return rec

        try:
            rec = await self.hass.async_add_executor_job(_start)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("FunASR session start failed: %s", err)
            return SpeechResult(None, SpeechResultState.ERROR)

        # Forward audio chunks (send_audio_frame is a fast thread-safe queue put)
        try:
            async for chunk in stream:
                if chunk:
                    rec.send_audio_frame(chunk)
        except Exception as err:  # noqa: BLE001
            if not errors:
                _LOGGER.warning("Audio feed interrupted: %s", err)

        # Stop and wait for the final result (bounded)
        try:
            await asyncio.wait_for(
                self.hass.async_add_executor_job(rec.stop), timeout=timeout
            )
        except TimeoutError:
            _LOGGER.error("FunASR: no final result within %ss", timeout)
            return SpeechResult(None, SpeechResultState.ERROR)
        except Exception as err:  # noqa: BLE001
            if not errors:
                errors.append(f"stop failed: {err}")

        if errors:
            _LOGGER.error("FunASR session error: %s", errors[0])
            return SpeechResult(None, SpeechResultState.ERROR)

        text = "".join(finals)
        if text:
            _LOGGER.debug("FunASR transcript: %s", text)
        else:
            _LOGGER.info("FunASR: empty transcript (silence?)")
        return SpeechResult(text, SpeechResultState.SUCCESS)

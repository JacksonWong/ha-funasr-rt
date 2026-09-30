"""Phase 1: DashScope fun-asr-realtime 独立验证。

把本地 WAV（16kHz/16bit/mono）按 ~100ms 分块、以实时节奏喂给
fun-asr-realtime（WebSocket 流式），打印中间/最终识别结果和延迟指标。
这一步不经过 HA / wyoming，只验证百炼后端本身。

Env:  DASHSCOPE_API_KEY
Run:  .venv/bin/python test_local.py [--wav samples/hello_world.wav] [--fast]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from pathlib import Path

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

RATE = 16000
WIDTH = 2
CHANNELS = 1
CHUNK_BYTES = RATE * WIDTH * CHANNELS // 10  # 100ms = 3200 bytes


class TimedCallback(RecognitionCallback):
    """记录各阶段时间戳 + 打印识别事件。"""

    def __init__(self) -> None:
        self.t_open: float | None = None
        self.t_first_text: float | None = None
        self.t_complete: float | None = None
        self.sentences: list[str] = []
        self.error: str | None = None

    def on_open(self) -> None:
        self.t_open = time.monotonic()
        print("[ws] opened")

    def on_event(self, *args) -> None:
        # 兼容不同 SDK 版本的签名 (result) / (request_id, result)
        result = args[-1]
        try:
            sentence = result.get_sentence()
        except Exception:  # noqa: BLE001
            sentence = None
        items = (
            sentence
            if isinstance(sentence, list)
            else [sentence]
            if sentence
            else []
        )
        for item in items:
            text = item.get("text") if isinstance(item, dict) else str(item)
            if not text:
                continue
            try:
                is_end = (
                    bool(RecognitionResult.is_sentence_end(item))
                    if isinstance(item, dict)
                    else True
                )
            except Exception:  # noqa: BLE001
                is_end = True
            if self.t_first_text is None:
                self.t_first_text = time.monotonic()
            if is_end:
                self.sentences.append(text)
                print(f"[asr:final] {text}")
            elif not self.sentences or self.sentences[-1] != text:
                print(f"[asr:partial] {text}")

    def on_complete(self) -> None:
        self.t_complete = time.monotonic()
        print("[ws] complete")

    def on_error(self, message) -> None:
        # dashscope 1.27.7 bug: RecognitionResult.__str__ 会因缺 headers 属性崩溃，
        # 只取属性/字典字段，绝不对它调 str()
        fields = []
        for attr in ("status_code", "code", "message", "request_id"):
            value = getattr(message, attr, None)
            if value is None and isinstance(message, dict):
                value = message.get(attr)
            if value is not None:
                fields.append(f"{attr}={value}")
        self.error = "; ".join(fields) or repr(message)[:300]
        print(f"[ws] ERROR: {self.error}", file=sys.stderr)

    def on_close(self) -> None:
        print("[ws] closed")


def load_env_file(path: Path) -> None:
    """从 .env 文件加载 KEY=VALUE（无第三方依赖，不覆盖已有环境变量）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def read_wav_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        got = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        if got != (CHANNELS, WIDTH, RATE):
            sys.exit(
                f"需要 16kHz/16bit/mono WAV，实际 ch={got[0]} width={got[1]} rate={got[2]}"
            )
        return w.readframes(w.getnframes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="samples/hello_world.wav")
    ap.add_argument("--model", default="fun-asr-realtime")
    ap.add_argument(
        "--workspace",
        default=os.environ.get("DASHSCOPE_WORKSPACE") or None,
        help="百炼业务空间ID（子空间key必填，llm-xxx 格式）",
    )
    ap.add_argument(
        "--fast", action="store_true", help="不按实时节奏，尽快喂完音频"
    )
    args = ap.parse_args()

    load_env_file(Path(__file__).resolve().parent / ".env")
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        sys.exit("DASHSCOPE_API_KEY 未设置（环境变量或项目 .env 文件）")
    dashscope.api_key = api_key

    pcm = read_wav_pcm(args.wav)
    audio_seconds = len(pcm) / (RATE * WIDTH * CHANNELS)

    cb = TimedCallback()
    rec = Recognition(
        model=args.model,
        format="pcm",
        sample_rate=RATE,
        callback=cb,
        workspace=args.workspace,
    )

    print(
        f"model={args.model} wav={args.wav} audio={audio_seconds:.2f}s "
        f"fast={args.fast} workspace={args.workspace or '-'}"
    )
    t0 = time.monotonic()
    rec.start()
    try:
        for i in range(0, len(pcm), CHUNK_BYTES):
            rec.send_audio_frame(pcm[i : i + CHUNK_BYTES])
            if not args.fast:
                time.sleep(0.1)
    except Exception as exc:  # noqa: BLE001
        # 服务端提前报错会导致识别器停止；真正原因以 on_error 为准
        if cb.error is None:
            cb.error = f"{type(exc).__name__}: {exc}"
        print(f"[feed] interrupted: {type(exc).__name__}: {exc}", file=sys.stderr)
    t_feed_end = time.monotonic()
    try:
        rec.stop()
    except Exception:  # noqa: BLE001
        pass
    t_stop = time.monotonic()

    # 兜底等待 on_complete
    deadline = time.monotonic() + 10
    while cb.t_complete is None and cb.error is None and time.monotonic() < deadline:
        time.sleep(0.1)

    print("\n===== metrics =====")
    print(f"dashscope sdk   : {dashscope.__version__}")
    print(f"audio duration  : {audio_seconds:.2f}s")
    print(f"feed wall time  : {t_feed_end - t0:.2f}s")
    print(f"tail latency    : {t_stop - t_feed_end:.2f}s  (最后一帧→stop返回)")
    for label, fn in (
        ("first pkg delay", rec.get_first_package_delay),
        ("last pkg delay", rec.get_last_package_delay),
    ):
        try:
            value = fn()
            if isinstance(value, (int, float)) and value >= 0:
                print(f"{label:16s}: {value:.0f} ms")
        except Exception:  # noqa: BLE001
            pass
    if cb.t_first_text is not None:
        print(f"first text at   : {cb.t_first_text - t0:.2f}s (from start)")
    if cb.t_complete is not None:
        print(f"total wall      : {cb.t_complete - t0:.2f}s")
        print(f"RTF             : {(cb.t_complete - t0) / audio_seconds:.3f}")

    if cb.error:
        sys.exit(f"FAILED: {cb.error}")
    if not cb.sentences:
        sys.exit("FAILED: 没有收到任何识别文本")
    print("\n===== transcript =====")
    for s in cb.sentences:
        print(s)
    print("PASS")


if __name__ == "__main__":
    main()

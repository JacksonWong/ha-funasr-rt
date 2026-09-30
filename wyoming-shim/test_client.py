"""Test client for wyoming-funasr-shim.

模式:
  (默认)         1s 静音 -> 期望收到 Transcript（dummy 后端固定"测试成功"）
  --wav PATH     喂真实 16k/mono/16bit WAV（实时节奏）
  --expect SUB   转写文本必须包含 SUB
  --concurrent N N 路并发会话（隔离性测试）
  --hotwords a,b 通过 transcript_names 下发热词
  --expect-error 期望收到 Error 事件（错误路径测试）

Run:
    .venv/bin/python test_client.py --uri tcp://127.0.0.1:10300 --wav samples/hello_world.wav --expect 阿里巴巴
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.error import Error
from wyoming.info import Describe, Info

RATE, WIDTH, CHANNELS = 16000, 2, 1
CHUNK_BYTES = RATE * WIDTH * CHANNELS // 10  # 100ms


def read_wav(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        got = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        if got != (CHANNELS, WIDTH, RATE):
            sys.exit(
                f"需要 16kHz/16bit/mono WAV，实际 (ch,width,rate)={got}\n"
                "转换: ffmpeg -i 输入 -ar 16000 -ac 1 -c:a pcm_s16le 输出.wav"
            )
        return w.readframes(w.getnframes())


async def check_describe(uri: str) -> None:
    async with AsyncClient.from_uri(uri, connect_timeout=5, read_timeout=10) as client:
        await client.write_event(Describe().event())
        event = await client.read_event()
        if event is None or not Info.is_type(event.type):
            raise RuntimeError(f"describe failed: {event}")
        info = Info.from_event(event)
        program = info.asr[0]
        model = program.models[0]
        print(f"[describe] program={program.name} model={model.name} langs={model.languages}")


async def run_session(
    uri: str, audio: bytes, hotwords: list[str], tag: str
) -> tuple[str, str, float]:
    """Returns (kind, text, wall_seconds); kind: transcript|error|closed."""
    t0 = time.monotonic()
    async with AsyncClient.from_uri(uri, connect_timeout=5, read_timeout=60) as client:
        await client.write_event(
            Transcribe(
                language="zh-CN",
                transcript_names=hotwords or None,
            ).event()
        )
        await client.write_event(
            AudioStart(rate=RATE, width=WIDTH, channels=CHANNELS).event()
        )
        for i in range(0, len(audio), CHUNK_BYTES):
            await client.write_event(
                AudioChunk(
                    rate=RATE,
                    width=WIDTH,
                    channels=CHANNELS,
                    audio=audio[i : i + CHUNK_BYTES],
                ).event()
            )
            await asyncio.sleep(0.1)  # 实时节奏，模拟卫星推流
        await client.write_event(AudioStop().event())

        while True:
            event = await client.read_event()
            if event is None:
                print(f"[{tag}] connection closed without result")
                return ("closed", "", time.monotonic() - t0)
            if Transcript.is_type(event.type):
                t = Transcript.from_event(event)
                print(f"[{tag}] transcript ({time.monotonic() - t0:.2f}s): {t.text!r}")
                return ("transcript", t.text, time.monotonic() - t0)
            if Error.is_type(event.type):
                e = Error.from_event(event)
                print(f"[{tag}] ERROR ({time.monotonic() - t0:.2f}s): code={e.code} text={e.text}")
                return ("error", e.text or "", time.monotonic() - t0)
            # 其他事件忽略


async def amain(args: argparse.Namespace) -> int:
    await check_describe(args.uri)

    if args.wav:
        audio = read_wav(args.wav)
        print(f"wav: {args.wav} ({len(audio) / 32000:.2f}s)")
    else:
        audio = b"\x00" * CHUNK_BYTES * 10  # 1s 静音
        print("wav: (1s silence)")

    hotwords = [w.strip() for w in (args.hotwords or "").split(",") if w.strip()]
    n = max(1, args.concurrent)
    print(f"sessions: {n}  hotwords: {hotwords or '-'}  expect_error: {args.expect_error}")

    results = await asyncio.gather(
        *[run_session(args.uri, audio, hotwords, f"s{i + 1}") for i in range(n)],
        return_exceptions=True,
    )

    ok = True
    for i, r in enumerate(results):
        tag = f"s{i + 1}"
        if isinstance(r, BaseException):
            print(f"[{tag}] EXCEPTION: {type(r).__name__}: {r}")
            ok = False
            continue
        kind, text, _wall = r
        if args.expect_error:
            if kind != "error":
                print(f"[{tag}] FAIL: expected Error event, got {kind}")
                ok = False
        elif kind != "transcript":
            print(f"[{tag}] FAIL: expected Transcript, got {kind}")
            ok = False
        elif args.expect and args.expect not in text:
            print(f"[{tag}] FAIL: {args.expect!r} not in transcript")
            ok = False

    print(f"{'PASS' if ok else 'FAIL'} ({n} session(s))")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="tcp://127.0.0.1:10300")
    ap.add_argument("--wav", default=None)
    ap.add_argument("--expect", default=None, help="转写须包含的子串")
    ap.add_argument("--concurrent", type=int, default=1)
    ap.add_argument("--hotwords", default=None, help="逗号分隔")
    ap.add_argument("--expect-error", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()

import asyncio
import shutil
import subprocess

import pytest

from src.audio.mp3 import Mp3StreamDecoder

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg requis pour fabriquer le mp3 de test")


async def test_stream_decode_gives_expected_duration():
    mp3 = await asyncio.to_thread(lambda: subprocess.run(
        ["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0",
         "-ar", "32000", "-ac", "1", "-f", "mp3", "pipe:1"], capture_output=True, check=True).stdout)
    dec = Mp3StreamDecoder(24000)
    for i in range(0, len(mp3), 700):  # arrivée par petits morceaux
        dec.feed(mp3[i:i + 700])
    dec.finish()
    pcm = b"".join([c async for c in dec])
    seconds = len(pcm) / (2 * 24000)
    assert 0.9 < seconds < 1.15

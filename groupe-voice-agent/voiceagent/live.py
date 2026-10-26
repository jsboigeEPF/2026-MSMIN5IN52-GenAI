"""Live input keeps consuming frames while a response is generated."""

import asyncio
import contextlib

from voiceagent.audio.capture import Microphone
from voiceagent.domain import State
from voiceagent.vad import TurnDetector, rms
from voiceagent.workers import SerialWorker


async def handle_frame(agent, detector, frame, probability):
    detections = detector.feed(
        frame,
        probability,
        busy=agent.busy,
        reference_rms=agent.player.reference_rms,
        half_duplex=agent.config.audio.half_duplex,
    )
    for detection in detections:
        if detection.kind in {"speech_start", "barge_start"}:
            if detection.kind == "barge_start":
                await agent.interrupt(
                    probability=probability,
                    mic_rms=rms(frame.samples),
                    reference_rms=agent.player.reference_rms,
                    speech_onset_ns=detection.t_ns,
                )
            agent.begin_speech(detection.t_ns, probability)
        elif detection.kind == "endpoint":
            agent.submit_audio(detection.utterance)
        elif detection.kind == "discarded":
            agent.events.emit("speech_discarded", agent.turn)
            agent.set_state(State.LISTENING)


async def voice_conversation(agent):
    from voiceagent.backends.silero import Silero

    vad = await asyncio.to_thread(Silero)
    detector = TurnDetector(agent.config.vad)
    worker = SerialWorker("vad")
    agent.events.emit("listening_ready")
    print("À l'écoute. Ctrl+C pour quitter.")
    try:
        async with Microphone(agent.config.audio, agent.events) as microphone:
            async for frame in microphone.frames():
                probability = await worker.run(vad.probability, frame.samples)
                await handle_frame(agent, detector, frame, probability)
    finally:
        worker.close()


async def stdin_lines():
    # Linux pipe reader: avoids a thread blocked forever in input() on Ctrl+C.
    import sys

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    try:
        while line := await reader.readline():
            yield line.decode("utf-8").strip()
    finally:
        transport.close()


async def text_conversation(agent):
    print("Mode texte. /stop interrompt, /reset efface l'historique, /quit quitte.")
    async with contextlib.aclosing(stdin_lines()) as lines:
        async for text in lines:
            if text == "/quit":
                break
            if text == "/stop":
                await agent.interrupt(reason="text_command")
            elif text == "/reset":
                await agent.interrupt(reason="reset")
                agent.history.clear()
            elif text:
                await agent.submit_text(text)
    await agent.wait_idle()

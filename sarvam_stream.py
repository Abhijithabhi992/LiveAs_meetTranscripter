"""
Opens one persistent Sarvam streaming Speech-to-Text WebSocket per audio
source ("You" and "Client"), continuously feeds audio from an asyncio.Queue,
and writes transcription results into a shared TranscriptStore.

Audio is collected into approximately 1-second buffers before being sent
to Sarvam. This gives the speech recognizer more audio context than sending
many extremely small chunks.
"""

import asyncio
import base64
import io
import wave

from sarvamai import AsyncSarvamAI

from config import (
    SARVAM_API_KEY,
    STT_MODEL,
    STT_MODE,
    LANGUAGE_CODE,
    SAMPLE_RATE,
    CHANNELS,
)


def _pcm_to_wav_b64(pcm_bytes: bytes) -> str:
    """
    Convert 16-bit PCM audio into a WAV file and then Base64 encode it.
    """

    buf = io.BytesIO()

    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_bytes)

    return base64.b64encode(
        buf.getvalue()
    ).decode("utf-8")


def _extract_text(message) -> str:
    """
    Extract transcript text from a Sarvam streaming response.

    Depending on the installed SDK version, the response may be:
    - a typed object
    - an object containing .data
    - a dictionary
    """

    data = getattr(message, "data", message)

    # Typed object response
    for attr in ("transcript", "text"):
        value = getattr(data, attr, None)

        if value:
            return str(value)

    # Dictionary response
    if isinstance(data, dict):
        return (
            data.get("transcript")
            or data.get("text")
            or ""
        )

    return ""


async def stream_source_to_sarvam(
    label: str,
    audio_queue: asyncio.Queue,
    transcript_store,
    stop_event: asyncio.Event,
    caption_overlay=None,
):
    """
    Stream one audio source to Sarvam.

    label:
        "You" or "Client"

    audio_queue:
        Queue containing PCM audio chunks.

    transcript_store:
        Shared TranscriptStore instance.

    stop_event:
        Event used to stop the stream.
    """

    if not SARVAM_API_KEY:
        raise RuntimeError(
            "SARVAM_API_KEY is not set. "
            "Check your .env file."
        )

    # IMPORTANT:
    # The API key must come from .env/config.py.
    # Do NOT hard-code your Sarvam API key here.
    client = AsyncSarvamAI(
        api_subscription_key=SARVAM_API_KEY
    )

    async with client.speech_to_text_streaming.connect(
        model=STT_MODEL,
        mode=STT_MODE,
        language_code=LANGUAGE_CODE,
        high_vad_sensitivity=True,
        vad_signals=True,
    ) as ws:

        async def sender():
            """
            Collect several small PCM chunks into a larger audio buffer
            before sending them to Sarvam.
            """

            audio_buffer = bytearray()

            # 1 second of 16-bit mono 16 kHz audio.
            #
            # 16000 samples/second
            # × 2 bytes/sample
            # = 32000 bytes
            TARGET_BYTES = SAMPLE_RATE * 2

            while not (
                stop_event.is_set()
                and audio_queue.empty()
            ):

                try:
                    chunk = await asyncio.wait_for(
                        audio_queue.get(),
                        timeout=0.5,
                    )

                except asyncio.TimeoutError:
                    continue

                if not chunk:
                    continue

                audio_buffer.extend(chunk)

                # Send approximately 1 second of audio.
                if len(audio_buffer) >= TARGET_BYTES:

                    audio_b64 = _pcm_to_wav_b64(
                        bytes(audio_buffer)
                    )

                    await ws.transcribe(
                        audio=audio_b64
                    )

                    audio_buffer.clear()

            # --------------------------------------------------
            # Send any remaining audio after stopping.
            # --------------------------------------------------

            if audio_buffer:

                audio_b64 = _pcm_to_wav_b64(
                    bytes(audio_buffer)
                )

                await ws.transcribe(
                    audio=audio_b64
                )

            # --------------------------------------------------
            # Tell Sarvam to finalize the remaining speech.
            # --------------------------------------------------

            flush = getattr(
                ws,
                "flush",
                None,
            )

            if flush is not None:
                await flush()

        async def receiver():
            """
            Receive transcription results from Sarvam.
            """

            try:

                async for message in ws:

                    text = _extract_text(
                        message
                    )

                    if text:

                        transcript_store.add(
                            label,
                            text,
                        )
                        if caption_overlay is not None:
                            caption_overlay.update_caption(
                                label,
                                text,
                            )

            except asyncio.CancelledError:
                # Normal shutdown.
                raise

            except Exception as exc:

                print(
                    f"[{label}] receiver stopped: {exc}"
                )

        # ------------------------------------------------------
        # Run sender and receiver simultaneously.
        # ------------------------------------------------------

        sender_task = asyncio.create_task(
            sender()
        )

        receiver_task = asyncio.create_task(
            receiver()
        )

        try:

            # Wait until all queued audio has been sent.
            await sender_task

            # Give Sarvam time to return the final flushed result.
            await asyncio.sleep(1.5)

        finally:

            # Stop receiver after the final result.
            if not receiver_task.done():

                receiver_task.cancel()

                try:
                    await receiver_task

                except asyncio.CancelledError:
                    pass
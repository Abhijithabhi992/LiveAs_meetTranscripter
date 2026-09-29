import sys
import asyncio
import threading
import signal
import time
from datetime import datetime
from pathlib import Path

from audio_capture import (
    create_default_sources,
    list_audio_devices,
)

from sarvam_stream import (
    stream_source_to_sarvam,
)

from transcript_store import (
    TranscriptStore,
)

from proposal_writer import (
    generate_proposal,
)

from caption_overlay import (
    CaptionOverlay,
)


# ============================================================
# GLOBAL SESSION STATE
# ============================================================

shutdown_event = threading.Event()
_session_lock = threading.RLock()

_worker_thread = None
_transcript_store = None

_session_status = "idle"
_session_client_name = None
_session_started_at = None
_session_error = None
_session_proposal_path = None


# ============================================================
# SESSION HELPERS
# ============================================================

def _set_session_status(status, error=None):
    global _session_status
    global _session_error

    with _session_lock:
        _session_status = status
        _session_error = error


def get_api_meeting_status():
    """Return the current meeting status for desktop_api.py."""

    with _session_lock:
        elapsed_seconds = None

        if _session_started_at is not None:
            elapsed_seconds = max(
                0,
                int(time.time() - _session_started_at),
            )

        return {
            "status": _session_status,
            "client_name": _session_client_name,
            "elapsed_seconds": elapsed_seconds,
            "error": _session_error,
            "proposal_path": _session_proposal_path,
        }


def get_api_transcript():
    """Return the current transcript as plain text."""

    with _session_lock:
        store = _transcript_store

    if store is None:
        return ""

    return store.merged_text()

def get_api_suggestions():
    """Return suggestion alerts for the current meeting."""

    with _session_lock:
        store = _transcript_store

    if store is None:
        return []

    return store.suggestions()


def start_api_meeting(client_name, client_folder_path):
    """
    Start a meeting without creating a Tkinter caption overlay.

    Called by desktop_api.py.
    """

    global _worker_thread
    global _session_client_name
    global _session_started_at
    global _session_proposal_path
    global _session_status
    global _session_error

    client_name = (client_name or "").strip()

    if not client_name:
        raise ValueError("Client name cannot be empty.")

    if not client_folder_path:
        raise ValueError("Client folder path is required.")

    with _session_lock:
        if _worker_thread and _worker_thread.is_alive():
            raise RuntimeError("A meeting is already running.")

        shutdown_event.clear()

        _session_client_name = client_name
        _session_started_at = None
        _session_proposal_path = None
        _session_status = "starting"
        _session_error = None

        _worker_thread = threading.Thread(
            target=start_transcription_worker,
            args=(
                None,  # Default microphone device
                None,  # Default system audio device
                client_name,
                None,  # No Tkinter overlay in API mode
                client_folder_path,
            ),
            daemon=True,
        )

        _worker_thread.start()

    return get_api_meeting_status()


def stop_api_meeting():
    """
    Request the current meeting to stop.

    The transcription worker will stop audio capture,
    finish the Sarvam streams, and generate the proposal.
    """

    global _session_status

    with _session_lock:
        if not _worker_thread or not _worker_thread.is_alive():
            return get_api_meeting_status()

        _session_status = "stopping"
        shutdown_event.set()

    return get_api_meeting_status()


# ============================================================
# CTRL+C HANDLER
# ============================================================

def handle_ctrl_c(signum, frame):
    print()
    print("=" * 60)
    print("Stopping meeting transcription...")
    print("=" * 60)

    shutdown_event.set()


# ============================================================
# BACKGROUND ASYNC TRANSCRIPTION WORKER
# ============================================================

async def transcription_worker(
    mic_device_index,
    loopback_device_index,
    client_name,
    caption_overlay,
    client_folder_path=None,
):
    """
    Runs audio capture and Sarvam transcription.

    caption_overlay can be None when controlled by the API.
    """

    global _transcript_store
    global _session_started_at
    global _session_proposal_path
    global _session_status

    loop = asyncio.get_running_loop()

    # --------------------------------------------------------
    # Audio queues
    # --------------------------------------------------------

    mic_queue = asyncio.Queue()
    client_queue = asyncio.Queue()

    # --------------------------------------------------------
    # Create a unique meeting folder and transcript store
    # --------------------------------------------------------

    meeting_dir = None

    if client_folder_path:
        meeting_timestamp = datetime.now().strftime(
            "%Y-%m-%d_%H-%M-%S"
        )

        meeting_dir = (
            Path(client_folder_path)
            / "meetings"
            / meeting_timestamp
        )

        meeting_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    transcript_store = TranscriptStore(
        meeting_dir=meeting_dir
    )

    with _session_lock:
        _transcript_store = transcript_store

    # --------------------------------------------------------
    # Async stop event
    # --------------------------------------------------------

    stop_event = asyncio.Event()

    # --------------------------------------------------------
    # Create audio sources
    # --------------------------------------------------------

    mic_source, client_source = create_default_sources(
        loop,
        mic_queue,
        client_queue,
        mic_device_index,
        loopback_device_index,
    )

    # --------------------------------------------------------
    # Start audio capture
    # --------------------------------------------------------

    mic_source.start()
    client_source.start()

    with _session_lock:
        _session_started_at = time.time()
        _session_status = "recording"

    print()
    print("=" * 60)
    print("Recording...")
    print(f"Client: {client_name}")

    if meeting_dir:
        print(f"Meeting folder: {meeting_dir}")

    print("Press Ctrl+C to stop.")
    print("=" * 60)
    print()

    if caption_overlay is not None:
        caption_overlay.update_status("Recording meeting...")

    # --------------------------------------------------------
    # Start Sarvam streams
    # --------------------------------------------------------

    tasks = [
        asyncio.create_task(
            stream_source_to_sarvam(
                "You",
                mic_queue,
                transcript_store,
                stop_event,
                caption_overlay,
            )
        ),
        asyncio.create_task(
            stream_source_to_sarvam(
                "Client",
                client_queue,
                transcript_store,
                stop_event,
                caption_overlay,
            )
        ),
    ]

    # --------------------------------------------------------
    # Wait for stop request
    # --------------------------------------------------------

    try:
        while not shutdown_event.is_set():
            await asyncio.sleep(0.2)

    finally:
        print()
        print("Stopping audio capture...")

        _set_session_status("stopping")

        # Tell Sarvam streams to stop.
        stop_event.set()

        # Stop audio devices.
        try:
            mic_source.stop()
        except Exception as exc:
            print(f"Microphone stop warning: {exc}")

        try:
            client_source.stop()
        except Exception as exc:
            print(f"System audio stop warning: {exc}")

        # Allow streams to finish.
        await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

    # --------------------------------------------------------
    # Get final transcript
    # --------------------------------------------------------

    if transcript_store.is_empty():
        print("No speech was transcribed.")

        if caption_overlay is not None:
            caption_overlay.update_status(
                "No speech detected. Closing captions..."
            )
            caption_overlay.close_after(2000)

        _set_session_status("completed")
        return

    print()
    print("=" * 60)
    print("Meeting transcription completed.")
    print("=" * 60)

    transcript_text = transcript_store.merged_text()

    # --------------------------------------------------------
    # Generate proposal
    # --------------------------------------------------------

    print()
    print("Generating English proposal...")

    _set_session_status("generating_proposal")

    if caption_overlay is not None:
        caption_overlay.update_status(
            "Meeting ended • Generating proposal..."
        )

    try:
        output_path = generate_proposal(
            transcript_text,
            client_name=client_name,
        )

        with _session_lock:
            _session_proposal_path = str(output_path)

        print()
        print("=" * 60)
        print("Proposal generated successfully.")
        print("=" * 60)
        print()
        print(f"Proposal saved to:\n{output_path}")
        print()

        if caption_overlay is not None:
            caption_overlay.update_status(
                "Proposal generated successfully. Closing captions..."
            )
            caption_overlay.close_after(2000)

        _set_session_status("completed")

    except Exception as exc:
        print()
        print(f"Proposal generation failed: {exc}")

        if caption_overlay is not None:
            caption_overlay.update_status(
                "Proposal generation failed. Closing captions..."
            )
            caption_overlay.close_after(3000)

        _set_session_status(
            "error",
            error=f"Proposal generation failed: {exc}",
        )


# ============================================================
# BACKGROUND THREAD ENTRY POINT
# ============================================================

def start_transcription_worker(
    mic_device_index,
    loopback_device_index,
    client_name,
    caption_overlay,
    client_folder_path=None,
):
    """Starts asyncio in a background thread."""

    try:
        asyncio.run(
            transcription_worker(
                mic_device_index,
                loopback_device_index,
                client_name,
                caption_overlay,
                client_folder_path,
            )
        )

    except Exception as exc:
        print()
        print(f"Transcription worker error: {exc}")

        if caption_overlay is not None:
            caption_overlay.update_status(
                "Transcription stopped because of an error."
            )
            caption_overlay.close_after(3000)

        _set_session_status(
            "error",
            error=str(exc),
        )


# ============================================================
# COMMAND-LINE MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # List devices
    # --------------------------------------------------------

    if "--list-devices" in sys.argv:
        list_audio_devices()
        return

    # --------------------------------------------------------
    # Client name
    # --------------------------------------------------------

    client_name = "Client"

    for i, arg in enumerate(sys.argv):
        if arg == "--client" and i + 1 < len(sys.argv):
            client_name = sys.argv[i + 1]

    # --------------------------------------------------------
    # Ctrl+C handler
    # --------------------------------------------------------

    signal.signal(
        signal.SIGINT,
        handle_ctrl_c,
    )

    # --------------------------------------------------------
    # Create caption overlay on main thread
    # --------------------------------------------------------

    caption = CaptionOverlay()

    # --------------------------------------------------------
    # Start transcription thread
    # --------------------------------------------------------

    worker_thread = threading.Thread(
        target=start_transcription_worker,
        args=(
            None,
            None,
            client_name,
            caption,
        ),
        daemon=True,
    )

    worker_thread.start()

    # --------------------------------------------------------
    # Run Tkinter on main thread
    # --------------------------------------------------------

    try:
        caption.run()

    except KeyboardInterrupt:
        shutdown_event.set()

    finally:
        shutdown_event.set()

        if worker_thread.is_alive():
            worker_thread.join(timeout=10)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
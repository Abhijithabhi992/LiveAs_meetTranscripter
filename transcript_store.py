import json
import threading
import time
from pathlib import Path

from suggestion_engine import SuggestionEngine


class TranscriptStore:
    """
    Thread-safe transcript storage.

    Saves each transcript segment immediately to:
    - transcript.jsonl
    - transcript.txt

    Also checks client speech against the suggestion matrix.
    """

    def __init__(
        self,
        meeting_dir=None,
        suggestion_engine=None,
    ):
        self.lock = threading.Lock()
        self.entries = []
        self.start_time = time.time()
        self.sequence = 0

        self.meeting_dir = (
            Path(meeting_dir) if meeting_dir else None
        )

        self.transcript_jsonl = None
        self.transcript_txt = None

        if self.meeting_dir:
            self.meeting_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.transcript_jsonl = (
                self.meeting_dir / "transcript.jsonl"
            )

            self.transcript_txt = (
                self.meeting_dir / "transcript.txt"
            )

            self.transcript_jsonl.touch(exist_ok=True)
            self.transcript_txt.touch(exist_ok=True)

        self.suggestion_engine = (
            suggestion_engine or SuggestionEngine()
        )

    def add(self, speaker: str, text: str):
        text = str(text or "").strip()

        if not text:
            return

        with self.lock:
            # Prevent an identical consecutive segment
            # from being saved twice.
            if self.entries:
                _, _, last_speaker, last_text = self.entries[-1]

                if (
                    last_speaker == speaker
                    and last_text == text
                ):
                    return

            elapsed = max(
                0.0,
                time.time() - self.start_time,
            )

            self.sequence += 1

            self.entries.append(
                (
                    elapsed,
                    self.sequence,
                    speaker,
                    text,
                )
            )

            minutes, seconds = divmod(
                int(elapsed),
                60,
            )

            timestamp = (
                f"{minutes:02d}:{seconds:02d}"
            )

            line = (
                f"[{timestamp}] {speaker}: {text}"
            )

            # Save the segment immediately as JSONL.
            if self.transcript_jsonl:
                record = {
                    "elapsed_seconds": round(elapsed, 2),
                    "timestamp": timestamp,
                    "speaker": speaker,
                    "text": text,
                }

                with self.transcript_jsonl.open(
                    "a",
                    encoding="utf-8",
                ) as file:
                    file.write(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

            # Save a readable transcript line.
            if self.transcript_txt:
                with self.transcript_txt.open(
                    "a",
                    encoding="utf-8",
                ) as file:
                    file.write(line + "\n")

        # Check suggestions after the transcript is saved.
        self.suggestion_engine.process(
            speaker,
            text,
            elapsed,
        )

        print(line)

    def merged_text(self) -> str:
        with self.lock:
            entries = sorted(
                self.entries,
                key=lambda entry: (
                    entry[0],
                    entry[1],
                ),
            )

        lines = []

        for elapsed, _, speaker, text in entries:
            minutes, seconds = divmod(
                int(elapsed),
                60,
            )

            timestamp = (
                f"{minutes:02d}:{seconds:02d}"
            )

            lines.append(
                f"[{timestamp}] {speaker}: {text}"
            )

        return "\n".join(lines)

    def suggestions(self):
        return self.suggestion_engine.all()

    def is_empty(self) -> bool:
        with self.lock:
            return not self.entries
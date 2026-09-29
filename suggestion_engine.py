import json
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
MATRIX_PATH = BASE_DIR / "config" / "suggestion_matrix.json"


def normalize_text(text):
    text = str(text or "").casefold()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class SuggestionEngine:
    """
    Loads generic matrix triggers and returns each topic
    at most once per meeting.
    """

    def __init__(self, matrix_path=MATRIX_PATH):
        self.matrix_path = Path(matrix_path)
        self.topics = self.load_topics()
        self.shown_topics = set()
        self.suggestions = []

    def load_topics(self):
        try:
            data = json.loads(
                self.matrix_path.read_text(encoding="utf-8")
            )
            return data.get("topics", [])

        except (OSError, json.JSONDecodeError) as error:
            print(
                f"[Suggestions] Could not load matrix: {error}"
            )
            return []

    def process(self, speaker, text, elapsed_seconds):
        # Only client speech should trigger suggestions.
        if str(speaker).casefold() != "client":
            return []

        normalized = normalize_text(text)

        if not normalized:
            return []

        found_suggestions = []

        for topic in self.topics:
            topic_id = topic.get("id")

            if not topic_id:
                continue

            if topic_id in self.shown_topics:
                continue

            matched_phrase = None

            for phrase in topic.get("trigger_phrases", []):
                normalized_phrase = normalize_text(phrase)

                if not normalized_phrase:
                    continue

                pattern = (
                    rf"(?<![a-z0-9])"
                    rf"{re.escape(normalized_phrase)}"
                    rf"(?![a-z0-9])"
                )

                if re.search(pattern, normalized):
                    matched_phrase = phrase
                    break

            if not matched_phrase:
                continue

            self.shown_topics.add(topic_id)

            suggestion = {
                "id": topic_id,
                "topic": topic.get("name", topic_id),
                "title": topic.get("name", "Matrix suggestion"),
                "trigger": matched_phrase,
                "discovery_points": topic.get(
                    "discovery_points", []
                ),
                "elapsed_seconds": max(
                    0, int(elapsed_seconds)
                ),
            }

            self.suggestions.append(suggestion)
            found_suggestions.append(suggestion)

        return found_suggestions

    def all(self):
        return list(self.suggestions)  
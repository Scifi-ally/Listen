import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from listen_app.core import (
    EnergyVAD,
    HeuristicOnlyExtractor,
    KeyPointExtractor,
    LectureRunner,
    SessionStore,
    create_vad,
    local_readiness,
)


class EnergyVADTests(unittest.TestCase):
    def test_emits_after_pause(self):
        vad = EnergyVAD(
            sample_rate=1000,
            frame_ms=100,
            threshold=0.01,
            speech_start_frames=2,
            silence_end_frames=3,
            min_segment_seconds=0.1,
        )
        speech = [0.1] * 600
        silence = [0.0] * 300
        emitted = vad.feed(speech + silence)
        self.assertEqual(len(emitted), 1)
        self.assertGreater(len(emitted[0].samples), 0)
        self.assertFalse(vad.finalize())

    def test_does_not_emit_short_noise(self):
        vad = EnergyVAD(sample_rate=1000, frame_ms=100, threshold=0.05, min_segment_seconds=0.5)
        self.assertEqual(vad.feed([0.1] * 100), [])
        self.assertEqual(vad.finalize(), [])

    def test_factory_falls_back_without_optional_webrtc_package(self):
        self.assertIsInstance(create_vad(), EnergyVAD)


class NoteExtractionTests(unittest.TestCase):
    def test_heuristic_notes_are_incremental_and_bilingual_safe(self):
        extractor = KeyPointExtractor(host="http://127.0.0.1:11434")
        transcript = "Photosynthesis means plants make food using light. Example: a leaf uses chlorophyll."
        first = extractor._heuristic_extract(transcript, [])
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0].category, "definition")
        second = extractor._heuristic_extract(transcript, [first[0].__dict__])
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].category, "example")

    def test_non_loopback_ollama_is_rejected(self):
        with self.assertRaises(ValueError):
            KeyPointExtractor(host="https://example.com")

    def test_ollama_json_is_parsed_and_categories_are_normalized(self):
        extractor = KeyPointExtractor(host="http://127.0.0.1:11434")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({"response": json.dumps([{"text": "Local fact", "category": "invented"}])}).encode()

        with patch("listen_app.core.urllib.request.urlopen", return_value=FakeResponse()):
            result = extractor._ollama_extract("Local fact.", [], [])
        self.assertEqual(result[0].text, "Local fact")
        self.assertEqual(result[0].category, "key point")


class SessionStoreTests(unittest.TestCase):
    def test_json_and_markdown_are_written(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            session = store.create("Bilingual Biology")
            session["transcript"].append({"text": "यह photosynthesis है", "source": "manual"})
            session["notes"].append({"text": "Photosynthesis uses light", "category": "definition"})
            store.save(session)
            self.assertTrue((Path(directory) / f"{session['id']}.json").exists())
            markdown = (Path(directory) / f"{session['id']}.md").read_text(encoding="utf-8")
            self.assertIn("Bilingual Biology", markdown)
            self.assertIn("यह photosynthesis है", markdown)
            self.assertIn("Photosynthesis uses light", markdown)

    def test_invalid_ids_cannot_escape_session_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            self.assertIsNone(store.get("../../outside"))
            self.assertFalse(store.is_valid_id("../../outside"))


class RunnerLifecycleTests(unittest.TestCase):
    def test_manual_transcript_is_extracted_on_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            session = store.create("Lifecycle")
            events = []
            runner = LectureRunner(session, events.append, note_interval_seconds=30, store=store)
            runner.extractor = HeuristicOnlyExtractor("test")
            runner.start()
            item = runner.add_manual_transcript("Photosynthesis means plants make food using light.")
            runner.stop()
            saved = store.get(session["id"])
            self.assertEqual(item["source"], "manual")
            self.assertIsNotNone(saved["ended_at"])
            self.assertEqual(len(saved["notes"]), 1)
            self.assertTrue(any(event.get("type") == "session_stopped" for event in events))


class ReadinessTests(unittest.TestCase):
    def test_readiness_is_local_and_structured(self):
        status = local_readiness(Path(tempfile.mkdtemp()))
        self.assertIn("audio_dependency", status)
        self.assertIn("asr_dependency", status)
        self.assertEqual(status["ollama_host"].split("://", 1)[0], "http")


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from listen_app.core import EnergyVAD, KeyPointExtractor, SessionStore


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


if __name__ == "__main__":
    unittest.main()

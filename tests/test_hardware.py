import os
import unittest
from unittest.mock import patch

from listen_app.hardware import detect_hardware


class HardwareProfileTests(unittest.TestCase):
    def test_real_profile_is_safe(self):
        profile = detect_hardware()
        self.assertIn(profile.tier, {"light", "balanced", "performance"})
        self.assertIn(profile.asr_device, {"cpu", "cuda"})
        self.assertTrue(profile.whisper_model.startswith("whisper-"))
        self.assertTrue(profile.note_model.startswith("qwen2.5:"))

    def test_light_profile_uses_small_models_and_longer_cadence(self):
        with patch.dict(os.environ, {"LISTEN_PROFILE": "auto", "LISTEN_ASR_DEVICE": "auto"}, clear=False), patch("listen_app.hardware._memory_gb", return_value=4.0), patch("listen_app.hardware._nvidia_gpu", return_value=(None, 0.0)), patch("listen_app.hardware._cuda_available", return_value=False):
            profile = detect_hardware()
        self.assertEqual(profile.tier, "light")
        self.assertEqual(profile.whisper_model, "whisper-tiny-int8")
        self.assertEqual(profile.note_model, "qwen2.5:1.5b")
        self.assertEqual(profile.asr_device, "cpu")

    def test_performance_profile_uses_medium_model(self):
        with patch.dict(os.environ, {"LISTEN_PROFILE": "auto", "LISTEN_ASR_DEVICE": "auto"}, clear=False), patch("listen_app.hardware._memory_gb", return_value=16.0), patch("listen_app.hardware._nvidia_gpu", return_value=("Test RTX", 8.0)), patch("listen_app.hardware._cuda_available", return_value=True):
            profile = detect_hardware()
        self.assertEqual(profile.tier, "performance")
        self.assertEqual(profile.whisper_model, "whisper-medium-int8")
        self.assertEqual(profile.asr_device, "cuda")
        self.assertEqual(profile.whisper_beam_size, 5)

    def test_explicit_profile_override_is_respected(self):
        with patch.dict(os.environ, {"LISTEN_PROFILE": "light", "LISTEN_ASR_DEVICE": "cpu"}, clear=False), patch("listen_app.hardware._memory_gb", return_value=32.0), patch("listen_app.hardware._nvidia_gpu", return_value=("Test RTX", 12.0)), patch("listen_app.hardware._cuda_available", return_value=True):
            profile = detect_hardware()
        self.assertEqual(profile.tier, "light")
        self.assertEqual(profile.asr_device, "cpu")


if __name__ == "__main__":
    unittest.main()

import os
import shutil
import subprocess
import tempfile
import unittest

from app.pipeline import probe_mean_db


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class RealAudioProbeTests(unittest.TestCase):
    """Uses real audio so 'capture confirmed' is tested against an actual decoder, not a stub."""

    def make(self, source):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "x.webm")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", source, "-t", "1", "-c:a", "libopus", path], check=True)
        return path

    def test_tone_is_audible_and_silence_is_not(self):
        tone = probe_mean_db(self.make("sine=frequency=440:duration=1"))
        silence = probe_mean_db(self.make("anullsrc=r=48000:cl=mono"))
        self.assertGreater(tone, -40)        # well above the -55 dB default threshold
        self.assertLess(silence, -55)        # digital silence is rejected

    def test_undecodable_file_returns_none(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        bad = os.path.join(d, "bad.webm")
        with open(bad, "wb") as f:
            f.write(b"definitely not audio")
        self.assertIsNone(probe_mean_db(bad))


if __name__ == "__main__":
    unittest.main()

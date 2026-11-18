"""Offline pin-pair regression; the Linux image build is the compatibility gate."""
from pathlib import Path
import re
import unittest


class ToolchainPins(unittest.TestCase):
    def test_verified_base_snapshot_pair(self):
        recipe = (Path(__file__).resolve().parents[1] / "toolchain" / "Dockerfile").read_text()
        # Update together only after signed apt metadata, dependency resolution,
        # and an amd64 build/test run establish a new compatible pair (README).
        self.assertEqual(
            re.findall(r"^FROM (\S+)$", recipe, re.MULTILINE),
            ["debian:bookworm-slim@sha256:"
             "734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc"],
        )
        self.assertEqual(
            re.findall(r"http://snapshot\.debian\.org/archive/([^/]+)/([0-9TZ]+) (\S+) main", recipe),
            [("debian", "20261104T212339Z", "bookworm"),
             ("debian-security", "20261104T212339Z", "bookworm-security")],
        )
        for bypass in ("--allow-downgrades", "--allow-unauthenticated", "trusted=yes",
                       "--force-depends", "--allow-remove-essential"):
            self.assertNotIn(bypass, recipe)


if __name__ == "__main__":
    unittest.main()

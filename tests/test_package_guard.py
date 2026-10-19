import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("guard", Path(__file__).resolve().parents[1] / "scripts/verify-package.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class DigestGuardTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".lazycat-action").mkdir()
        self.proof = {
            "source_version": "1.37.4",
            "application_version": "1.37.4",
            "digest": "sha256:" + "a" * 64,
            "checks": ["login", "decrypt", "attachment", "config", "sqlite", "restart"],
        }
        (self.root / "package.yml").write_text("version: 1.37.4\n")
        (self.root / "lzc-manifest.yml").write_text(
            "services:\n  web:\n    # upstream: docker.io/vaultwarden/server:1.37.4@sha256:" + "a" * 64 +
            "\n    image: registry.lazycat.cloud/peter/vaultwarden/server:test\n")
        self.save()

    def save(self):
        (self.root / ".lazycat-action/upgrade-proof.json").write_text(json.dumps(self.proof))

    def test_tested_digest_is_accepted(self):
        guard.verify(self.root)

    def test_moved_image_tag_is_rejected(self):
        self.proof["digest"] = "sha256:" + "b" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "digest differs"):
            guard.verify(self.root)

    def test_different_candidate_is_rejected(self):
        self.proof["application_version"] = "1.37.5"
        self.save()
        with self.assertRaisesRegex(ValueError, "version differs"):
            guard.verify(self.root)

    def test_incomplete_checks_are_rejected(self):
        self.proof["checks"].remove("decrypt")
        self.save()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            guard.verify(self.root)

    def test_missing_proof_is_rejected(self):
        (self.root / ".lazycat-action/upgrade-proof.json").unlink()
        with self.assertRaises(FileNotFoundError):
            guard.verify(self.root)

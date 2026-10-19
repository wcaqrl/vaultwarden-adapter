"""Require proof that the delivered digest passed the adapter upgrade test."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def verify(root: Path) -> None:
    proof = json.loads((root / ".lazycat-action/upgrade-proof.json").read_text())
    manifest = (root / "lzc-manifest.yml").read_text()
    package = (root / "package.yml").read_text()
    expected = f"docker.io/vaultwarden/server:{proof['source_version']}@{proof['digest']}"
    upstream = re.findall(r"^\s*# upstream: (\S+)\s*$", manifest, re.MULTILINE)
    if upstream != [expected]:
        raise ValueError("Delivered source digest differs from the successfully tested image")
    version = re.search(r"^version:\s*(\S+)\s*$", package, re.MULTILINE)
    if not version or version[1] != proof["application_version"]:
        raise ValueError("Package version differs from the tested candidate")
    if not re.search(r"^\s+image: registry\.lazycat\.cloud/\S+\s*$", manifest, re.MULTILINE):
        raise ValueError("The package must use the completed official image copy")
    if proof.get("checks") != ["login", "decrypt", "attachment", "config", "sqlite", "restart"]:
        raise ValueError("Upgrade test proof is incomplete")


if __name__ == "__main__":
    verify(ROOT)
    print("Verified: the packaged image is exactly the digest that passed upgrade tests.")

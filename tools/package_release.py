"""Build a source distribution without caches or user run data."""
from pathlib import Path
import hashlib
import zipfile

root = Path(__file__).resolve().parents[1]
version = (root / "preprocess/VERSION").read_text().strip()
out = root / "dist"
out.mkdir(exist_ok=True)
archive = out / f"SCRing-{version}.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(x in {".git", "dist", "__pycache__", "_scratch", ".venv"} for x in rel.parts):
            continue
        if path.suffix in {".pyc", ".pyo"} or path.name == ".env":
            continue
        if len(rel.parts) > 2 and rel.parts[0] == "preprocess" and rel.parts[1] in {"runs", "cuts"}:
            if rel.parts[2:] != ("index.json",):
                continue
            if __import__("json").loads(path.read_text())[rel.parts[1]]:
                raise RuntimeError("Release requires empty run/cut history")
        z.write(path, f"SCRing-{version}/{rel.as_posix()}")
(out / "SHA256SUMS.txt").write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
print(archive)

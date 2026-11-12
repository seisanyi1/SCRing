"""Export wellprep results as a YOLO detection/segmentation dataset.

The circle fitted by wellprep is converted to either a tight bounding box
(YOLO detect) or a 72-point polygon (YOLO segment).  This makes it possible to
train a YOLO model later without any manual annotation, or to correct a small
subset and fine-tune.

Example:
    python preprocess/tools/yolo_export.py \
        --csv preprocess/out/tables/wells.csv \
        --out preprocess/yolo_dataset --mode seg
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np

POLY_POINTS = 72
CLASS_NAME = "green_well"


def _polygon(cx: float, cy: float, r: float, w: int, h: int) -> list[tuple[float, float]]:
    t = np.linspace(0.0, 2.0 * np.pi, POLY_POINTS, endpoint=False)
    xs = np.clip((cx + r * np.cos(t)) / max(1, w), 0.0, 1.0)
    ys = np.clip((cy + r * np.sin(t)) / max(1, h), 0.0, 1.0)
    return list(zip(xs.tolist(), ys.tolist()))


def _box(cx: float, cy: float, r: float, w: int, h: int) -> tuple[float, float, float, float]:
    x0 = max(0.0, (cx - r) / max(1, w))
    y0 = max(0.0, (cy - r) / max(1, h))
    x1 = min(1.0, (cx + r) / max(1, w))
    y1 = min(1.0, (cy + r) / max(1, h))
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0, y1 - y0)


def _flat_name(rel_path: str) -> str:
    raw = rel_path.replace("\\", "/").replace("/", "__")
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in raw)
    if not safe:
        safe = "image"
    return safe


def _split(rel_path: str, val_pct: float) -> str:
    digest = hashlib.md5(rel_path.encode("utf-8")).hexdigest()
    return "val" if int(digest[:8], 16) % 100 < val_pct * 100 else "train"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export wellprep CSV as YOLO dataset")
    ap.add_argument("--csv", required=True, help="wells.csv produced by run_preprocess.py")
    ap.add_argument("--out", required=True, help="output dataset directory")
    ap.add_argument("--images-root", help="root used to resolve relative image paths (optional)")
    ap.add_argument("--mode", choices=["seg", "det"], default="seg")
    ap.add_argument("--val-pct", type=float, default=0.15)
    ap.add_argument("--include-review", action="store_true", help="also export rows with status=review")
    ap.add_argument("--copy", action="store_true", help="copy instead of hard-link (default: link/fallback copy)")
    ap.add_argument("--limit", type=int, help="export at most N images")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 2
    out = Path(args.out).resolve()
    images_root = Path(args.images_root).resolve() if args.images_root else None

    allowed = {"ok"} | ({"review"} if args.include_review else set())
    groups: dict[str, list[dict]] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("status")) not in allowed:
                continue
            if not row.get("radius") or not row.get("cx") or not row.get("cy"):
                continue
            groups.setdefault(str(row.get("file")), []).append(row)

    if args.limit:
        groups = dict(list(groups.items())[: args.limit])
    if not groups:
        print("没有可导出的圆域（检查 status / 半径字段）", file=sys.stderr)
        return 1

    for split in ("train", "val"):
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    exported = {"train": 0, "val": 0}
    objects = {"train": 0, "val": 0}
    missing: list[str] = []
    used_names: set[str] = set()

    for file, rows in groups.items():
        img_path = Path(file)
        if not img_path.is_absolute() or not img_path.exists():
            rel = rows[0].get("rel_path", img_path.name)
            candidate = (images_root / rel) if images_root else None
            if candidate and candidate.exists():
                img_path = candidate
            elif not img_path.exists():
                missing.append(str(file))
                continue
        split = _split(rows[0].get("rel_path", img_path.name), args.val_pct)
        base = _flat_name(rows[0].get("rel_path", img_path.name))
        name = base
        if name in used_names:
            name = f"{Path(base).stem}__{len(used_names)}{Path(base).suffix}"
        used_names.add(name)
        dst_img = out / "images" / split / name
        if args.copy:
            shutil.copy2(img_path, dst_img)
        else:
            try:
                if dst_img.exists():
                    dst_img.unlink()
                dst_img.hardlink_to(img_path)
            except OSError:
                shutil.copy2(img_path, dst_img)
        label_lines = []
        w = int(float(rows[0].get("width") or 0))
        h = int(float(rows[0].get("height") or 0))
        if w <= 0 or h <= 0:
            from PIL import Image

            with Image.open(img_path) as im:
                w, h = im.size
        for row in rows:
            cx = float(row["cx"])
            cy = float(row["cy"])
            r = float(row["radius"])
            if args.mode == "seg":
                coords = " ".join(f"{x:.6f} {y:.6f}" for x, y in _polygon(cx, cy, r, w, h))
                label_lines.append(f"0 {coords}")
            else:
                xc, yc, bw, bh = _box(cx, cy, r, w, h)
                label_lines.append(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}")
        (out / "labels" / split / f"{Path(name).stem}.txt").write_text(
            "\n".join(label_lines) + "\n", encoding="utf-8"
        )
        exported[split] += 1
        objects[split] += len(label_lines)

    if not exported["val"] and exported["train"] > 2:
        # keep at least one validation image for a runnable data.yaml
        first = next((out / "images" / "train").iterdir())
        (out / "images" / "val").mkdir(parents=True, exist_ok=True)
        (out / "labels" / "val").mkdir(parents=True, exist_ok=True)
        shutil.move(str(first), out / "images" / "val" / first.name)
        lbl = out / "labels" / "train" / f"{first.stem}.txt"
        if lbl.exists():
            shutil.move(str(lbl), out / "labels" / "val" / lbl.name)
        exported["train"] -= 1
        exported["val"] += 1

    yaml = (
        f"path: {out.as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        f"  0: {CLASS_NAME}\n"
    )
    (out / "data.yaml").write_text(yaml, encoding="utf-8")
    stats = {
        "images": exported,
        "objects": objects,
        "mode": args.mode,
        "missing": missing,
        "source_csv": str(csv_path.resolve()),
    }
    (out / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"YOLO-{args.mode} dataset: {out}")
    print(f"  train {exported['train']} images / {objects['train']} objects")
    print(f"  val   {exported['val']} images / {objects['val']} objects")
    if missing:
        print(f"  skipped missing images: {len(missing)}")
    print(f"  data.yaml: {out / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

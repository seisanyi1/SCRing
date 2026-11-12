"""Storage for circular-cut jobs (history, metadata and files)."""

from __future__ import annotations

import csv
import json
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from .cutter import make_cut
from .output import create_run_output

CUT_FIELDS = [
    "index", "start", "end", "center", "pixels", "area_frac",
    "green_mean", "green_median", "green_p10", "green_p90", "gray_mean", "file",
]


class CutStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root / "index.json"
        self._items: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.index_file.exists():
            return
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            for item in data.get("cuts", []):
                if item.get("id"):
                    self._items[item["id"]] = item
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        payload = {"cuts": sorted(self._items.values(), key=lambda x: x.get("created_at", ""), reverse=True)}
        tmp = self.index_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.index_file)

    def list(self) -> list[dict]:
        return [dict(item) for item in sorted(self._items.values(), key=lambda x: x.get("created_at", ""), reverse=True)]

    def get(self, cut_id: str) -> dict | None:
        item = self._items.get(cut_id)
        if not item:
            return None
        out = dict(item)
        csv_path = Path(out["output"]) / "sectors.csv"
        sectors = []
        if csv_path.exists():
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    for key in ("index", "pixels"):
                        try:
                            row[key] = int(float(row[key]))
                        except (TypeError, ValueError):
                            row[key] = 0
                    for key in ("start", "end", "center", "area_frac", "green_mean", "green_median", "green_p10", "green_p90", "gray_mean"):
                        try:
                            row[key] = float(row[key])
                        except (TypeError, ValueError):
                            row[key] = None
                    sectors.append(row)
        out["sector_rows"] = sectors
        return out

    def create(self, run_meta: dict, row: dict, params: dict) -> dict:
        angle = float(params.get("angle_step") or 15)
        if not (1 <= angle <= 180):
            raise ValueError("圆心角必须在 1–180 度之间")
        size = int(params.get("out_size") or 512)
        if not (128 <= size <= 2048):
            raise ValueError("输出尺寸必须在 128–2048 之间")
        mode = str(params.get("center_mode") or "geometric")
        if mode not in ("geometric", "auto"):
            raise ValueError("center_mode 必须是 geometric 或 auto")

        cut_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        if not run_meta.get("output"):
            raise ValueError("提取记录缺少输出目录，请重新运行圆域提取")
        run_output = Path(run_meta["output"]).resolve()
        if not run_output.is_dir():
            raise ValueError(f"提取结果目录不存在：{run_output}")
        _, out_dir = create_run_output(str(run_output / "cuts"), self.root, cut_id)
        meta = {
            "id": cut_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "run_id": run_meta.get("id", ""),
            "run_name": run_meta.get("name", ""),
            "run_output": str(run_output),
            "file": row.get("file", ""),
            "rel_path": row.get("rel_path", ""),
            "source_name": Path(str(row.get("file", ""))).name,
        }
        try:
            result = make_cut(
                row["file"],
                float(row["cx"]),
                float(row["cy"]),
                float(row["radius"]),
                angle,
                size,
                mode,
                out_dir,
                meta,
            )
        except BaseException:
            if out_dir.resolve() == (run_output / "cuts" / cut_id).resolve():
                shutil.rmtree(out_dir, ignore_errors=True)
            raise
        info = result["info"]
        info["output"] = str(out_dir)
        (out_dir / "cut.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        self._items[cut_id] = info
        self._save()
        return self.get(cut_id) or info

    def zip_sectors(self, cut_id: str) -> Path:
        item = self._items.get(cut_id)
        if not item:
            raise KeyError(cut_id)
        out = Path(str(item.get("output", "")))
        sectors = sorted((out / "sectors").glob("*.png"))
        if not sectors:
            raise FileNotFoundError("没有扇形图片")
        target = out / "sectors.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for image in sectors:
                zf.write(image, arcname=image.name)
            for image in sorted((out / "sectors_aligned").glob("*.png")):
                zf.write(image, arcname="sectors_aligned/" + image.name)
            for extra in ("sectors.csv", "cut.json", "aligned_contact_sheet.png"):
                path = out / extra
                if path.exists():
                    zf.write(path, arcname=extra)
        return target

    def delete(self, cut_id: str) -> dict | None:
        item = self._items.get(cut_id)
        if not item:
            return None
        out = Path(str(item.get("output", ""))).resolve()
        expected = ((Path(item["run_output"]).resolve() / "cuts" / cut_id).resolve()
                    if item.get("run_output") else (self.root / cut_id).resolve())
        if out != expected or out.name != cut_id:
            raise ValueError("切割输出路径与记录不一致，拒绝删除")
        if out.exists():
            shutil.rmtree(out)
        self._items.pop(cut_id)
        self._save()
        return item

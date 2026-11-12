"""Local web application: run history, control panel and interactive results."""

from __future__ import annotations

import csv
import json
import math
import mimetypes
import os
import re
import shutil
import string
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import webbrowser
from dataclasses import fields
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import Config
from .cuts import CutStore
from .imaging import IMAGE_EXTS, load_rgb, save_rgb, stretch_for_display
from .pipeline import analyze, collect_images
from .render import downscale, draw_overlay, warp_circle
from .report import write_report
from .runner import count_status, run_batch, write_csv

APP_ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = APP_ROOT / "web"
RUNS_ROOT = Path(os.environ.get("WELLPREP_RUNS", str(APP_ROOT / "runs"))).resolve()
VERSION = "2.0.0"

STATUS_LABELS = {
    "queued": "排队中",
    "running": "运行中",
    "done": "已完成",
    "cancelled": "已取消",
    "error": "失败",
    "interrupted": "已中断",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _file_url(run_id: str, rel: str) -> str:
    if not rel:
        return ""
    return f"/runs/{quote(run_id)}/files/{quote(rel.replace(os.sep, '/'), safe='/')}"


def _num(value, default=None):
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_finite(value, digits: int = 2):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _read_tail(path: Path, n: int = 120) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except OSError:
        return []


class RunManager:
    def __init__(self, runs_root: Path = RUNS_ROOT) -> None:
        self.runs_root = Path(runs_root).resolve()
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.previews_root = self.runs_root / ".previews"
        self.previews_root.mkdir(parents=True, exist_ok=True)
        self.index_file = self.runs_root / "index.json"
        self._lock = threading.RLock()
        self._runs: dict[str, dict] = {}
        self._active: str | None = None
        self._cancel: threading.Event | None = None
        self._clean_previews()
        self._load()

    def _clean_previews(self) -> None:
        cutoff = time.time() - 6 * 3600
        for entry in self.previews_root.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        loaded: list[dict] = []
        if self.index_file.exists():
            try:
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
                loaded = data.get("runs", data) if isinstance(data, dict) else data
            except (OSError, ValueError):
                loaded = []
        if not loaded:
            for meta_file in self.runs_root.glob("*/run.json"):
                try:
                    loaded.append(json.loads(meta_file.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        for meta in loaded:
            rid = str(meta.get("id") or "")
            if not rid:
                continue
            if meta.get("status") in ("queued", "running"):
                meta["status"] = "interrupted"
                meta["finished_at"] = _now()
                meta["note"] = "服务重启，运行被中断"
            meta.setdefault("counts", {"ok": 0, "review": 0, "not_found": 0, "error": 0})
            meta.setdefault("records", 0)
            meta.setdefault("done", 0)
            meta.setdefault("total", 0)
            meta.setdefault("elapsed", 0)
            meta.setdefault("inputs", [])
            meta.setdefault("note", "")
            meta.setdefault("output_parent", str(Path(str(meta.get("output", ""))).parent))
            meta.setdefault("exclude", [])
            self._runs[rid] = meta
        self._save_locked()

    def _save_locked(self) -> None:
        payload = {"version": VERSION, "runs": sorted(self._runs.values(), key=lambda m: m.get("created_at", ""), reverse=True)}
        tmp = self.index_file.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.index_file)
        except OSError:
            pass
        for meta in self._runs.values():
            out = Path(str(meta.get("output", "")))
            if not out.is_dir():
                continue
            try:
                (out / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass

    # ------------------------------------------------------------------ helpers
    def list(self) -> list[dict]:
        with self._lock:
            return [
                dict(meta, active=(meta["id"] == self._active))
                for meta in sorted(self._runs.values(), key=lambda m: m.get("created_at", ""), reverse=True)
            ]

    def get(self, run_id: str) -> dict | None:
        with self._lock:
            meta = self._runs.get(run_id)
            return dict(meta) if meta else None

    def active_id(self) -> str | None:
        with self._lock:
            return self._active

    def status(self, run_id: str) -> dict | None:
        with self._lock:
            meta = self._runs.get(run_id)
            if not meta:
                return None
            out = dict(meta)
            out["active"] = run_id == self._active
        log = _read_tail(Path(out["output"]) / "run.log", 160)
        return {"run": out, "log": log}

    # -------------------------------------------------------------------- start
    def _config_from_payload(self, data: dict | None) -> Config:
        cfg = Config()
        field_defaults = {f.name: f.default for f in fields(Config)}
        for key, value in (data or {}).items():
            if key not in field_defaults:
                continue
            default = field_defaults[key]
            try:
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(value)
                elif isinstance(default, float):
                    value = float(value)
                else:
                    value = str(value)
            except (TypeError, ValueError):
                raise ValueError(f"参数 {key} 的值无效：{value!r}")
            setattr(cfg, key, value)
        cfg.workers = max(1, min(16, int(cfg.workers)))
        cfg.validate()
        return cfg

    def start(self, payload: dict) -> dict:
        inputs: list[str] = []
        for raw in payload.get("inputs") or []:
            if not str(raw).strip():
                continue
            p = Path(str(raw)).expanduser()
            if not p.exists():
                raise ValueError(f"路径不存在：{p}")
            inputs.append(str(p.resolve()))
        if not inputs:
            raise ValueError("请至少添加一个图片文件或文件夹")

        cfg = self._config_from_payload(payload.get("config"))

        exclude: list[str] = []
        for raw in payload.get("exclude") or []:
            try:
                exclude.append(str(Path(str(raw)).expanduser().resolve()))
            except (OSError, RuntimeError):
                continue

        with self._lock:
            if self._active:
                active = self._runs.get(self._active, {})
                raise RuntimeError(f"已有运行正在进行：{active.get('name', self._active)}，请等待完成或取消")
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
            output_root = str(payload.get("output_root") or "").strip()
            if output_root:
                parent = Path(output_root).expanduser()
                if not parent.is_absolute():
                    parent = Path.cwd() / parent
                parent = parent.resolve()
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                    if not parent.is_dir():
                        raise NotADirectoryError(str(parent))
                    out = parent / run_id
                    out.mkdir()
                except OSError as exc:
                    raise ValueError(f"无法创建输出目录 {parent}：{exc}") from exc
            else:
                parent = self.runs_root
                out = parent / run_id
                out.mkdir(parents=True, exist_ok=True)
            name = str(payload.get("name") or "").strip() or f"运行 {datetime.now().strftime('%m-%d %H:%M')}"
            meta = {
                "id": run_id,
                "name": name,
                "status": "queued",
                "created_at": _now(),
                "started_at": "",
                "finished_at": "",
                "inputs": inputs,
                "output": str(out),
                "output_parent": str(parent),
                "exclude": exclude,
                "config": cfg.to_dict(),
                "total": 0,
                "done": 0,
                "records": 0,
                "counts": {"ok": 0, "review": 0, "not_found": 0, "error": 0},
                "elapsed": 0,
                "current": "",
                "error": "",
                "note": "",
                "images": 0,
                "radius_median": None,
            }
            self._runs[run_id] = meta
            self._active = run_id
            self._cancel = threading.Event()
            self._save_locked()
            thread = threading.Thread(target=self._execute, args=(meta, cfg), name=f"wellprep-{run_id}", daemon=True)
            thread.start()
            return dict(meta)

    def _execute(self, meta: dict, cfg: Config) -> None:
        out = Path(meta["output"])
        log_path = out / "run.log"
        last_save = [0.0]
        fh = None
        try:
            fh = open(log_path, "a", encoding="utf-8")

            def log(message: str) -> None:
                try:
                    fh.write(f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
                    fh.flush()
                except OSError:
                    pass

            with self._lock:
                meta["status"] = "running"
                meta["started_at"] = _now()
                self._save_locked()
            log("参数：" + json.dumps(cfg.to_dict(), ensure_ascii=False))
            log("输入：" + " | ".join(meta["inputs"]))

            def progress(p: dict) -> None:
                with self._lock:
                    meta.update(
                        done=p["done"],
                        total=p["total"],
                        current=p["current"],
                        records=p["records"],
                        counts=p["counts"],
                        elapsed=p["elapsed"],
                    )
                    now = time.time()
                    if now - last_save[0] > 1.5:
                        self._save_locked()
                        last_save[0] = now

            result = run_batch(
                meta["inputs"],
                cfg,
                out,
                on_progress=progress,
                on_log=log,
                cancel=self._cancel,
                exclude_paths=meta.get("exclude") or [],
            )
            radii = []
            for row in result.records:
                if row.get("status") in ("ok", "review"):
                    value = _num(row.get("radius"))
                    if value:
                        radii.append(value)
            with self._lock:
                meta.update(
                    status="cancelled" if result.cancelled else "done",
                    finished_at=_now(),
                    total=result.total,
                    done=result.done,
                    records=len(result.records),
                    counts=result.counts,
                    elapsed=round(result.elapsed, 2),
                    current="",
                    images=result.images,
                    radius_median=round(float(sorted(radii)[len(radii) // 2]), 2) if radii else None,
                    note="用户取消，结果为已完成部分" if result.cancelled else "",
                )
                self._save_locked()
            log("运行结束：" + ("已取消" if result.cancelled else "完成"))
        except BaseException as exc:  # noqa: BLE001
            with self._lock:
                meta["status"] = "error"
                meta["finished_at"] = _now()
                meta["error"] = f"{type(exc).__name__}: {exc}"
                meta["note"] = meta["error"]
                self._save_locked()
            try:
                if fh:
                    fh.write(f"[{datetime.now().strftime('%H:%M:%S')}] ERROR {traceback.format_exc()}\n")
                    fh.flush()
            except OSError:
                pass
        finally:
            try:
                if fh:
                    fh.close()
            except OSError:
                pass
            with self._lock:
                if self._active == meta["id"]:
                    self._active = None
                    self._cancel = None
                self._save_locked()

    # ------------------------------------------------------------------- cancel
    def cancel(self, run_id: str) -> bool:
        with self._lock:
            if self._active != run_id or self._cancel is None:
                return False
            meta = self._runs.get(run_id)
            if meta:
                meta["note"] = "正在取消…"
            event = self._cancel
        event.set()
        return True

    # ------------------------------------------------------------------- delete
    def _delete_output_files(self, meta: dict) -> bool:
        out = Path(str(meta.get("output", ""))).resolve()
        # Only the dedicated run folder (…/<run-id>) may be removed, never a
        # user-selected parent, a drive root, the project or the home folder.
        if out.name != str(meta.get("id")):
            return False
        if out.parent == out or out == self.runs_root or out == self.runs_root.parent:
            return False
        if out in (Path.home().resolve(), APP_ROOT.resolve()):
            return False
        marker = out / "run.json"
        if marker.exists():
            try:
                if json.loads(marker.read_text(encoding="utf-8")).get("id") != meta.get("id"):
                    return False
            except (OSError, ValueError):
                return False
        if not out.is_dir():
            return False
        shutil.rmtree(out, ignore_errors=True)
        return not out.exists()

    def delete(self, run_id: str, delete_files: bool = False) -> dict | None:
        with self._lock:
            if self._active == run_id:
                raise RuntimeError("运行尚未结束，不能删除")
            meta = self._runs.pop(run_id, None)
            if not meta:
                return None
            if delete_files:
                self._delete_output_files(meta)
            self._save_locked()
            return meta

    # ------------------------------------------------------------------ results
    def results(self, run_id: str, query: dict) -> dict:
        meta = self.get(run_id)
        if not meta:
            raise KeyError(run_id)
        csv_path = Path(meta["output"]) / "tables" / "wells.csv"
        rows: list[dict] = []
        if csv_path.exists():
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
                for raw in csv.DictReader(fh):
                    row = {
                        "file": raw.get("file", ""),
                        "rel_path": raw.get("rel_path", ""),
                        "status": raw.get("status", "error"),
                        "note": raw.get("note", ""),
                        "object_index": int(_num(raw.get("object_index"), 0) or 0),
                        "width": _num(raw.get("width")),
                        "height": _num(raw.get("height")),
                        "cx": _num(raw.get("cx")),
                        "cy": _num(raw.get("cy")),
                        "radius": _num(raw.get("radius")),
                        "coverage": _num(raw.get("coverage")),
                        "snr": _num(raw.get("snr")),
                        "radial_cv": _num(raw.get("radial_cv")),
                        "edge_width": _num(raw.get("edge_width")),
                        "residual_rms": _num(raw.get("residual_rms")),
                        "saturation": _num(raw.get("saturation")),
                        "contrast": _num(raw.get("contrast")),
                        "threshold": _num(raw.get("threshold")),
                        "touches_border": raw.get("touches_border", ""),
                        "roi_url": _file_url(run_id, raw.get("roi_norm", "")),
                        "roi_raw_url": _file_url(run_id, raw.get("roi_raw", "")),
                        "mask_url": _file_url(run_id, raw.get("mask", "")),
                        "polar_url": _file_url(run_id, raw.get("polar", "")),
                        "overlay_url": _file_url(run_id, raw.get("overlay", "")),
                        "thumb_url": _file_url(run_id, raw.get("thumb", "")),
                    }
                    rows.append(row)

        counts = count_status(rows)
        radii = [r["radius"] for r in rows if r["status"] in ("ok", "review") and r["radius"]]
        summary = {
            "counts": counts,
            "images": len({r["file"] for r in rows}),
            "records": len(rows),
            "radius_median": round(float(sorted(radii)[len(radii) // 2]), 2) if radii else None,
            "radius_min": round(min(radii), 2) if radii else None,
            "radius_max": round(max(radii), 2) if radii else None,
        }

        status = str(query.get("status", "all"))
        q = str(query.get("q", "")).strip().lower()
        sort = str(query.get("sort", "path"))
        page = max(1, int(_num(query.get("page"), 1) or 1))
        page_size = int(_num(query.get("page_size"), 48) or 48)
        page_size = max(12, min(200, page_size))

        filtered = rows
        if status in ("ok", "review", "not_found", "error"):
            filtered = [r for r in filtered if r["status"] == status]
        if q:
            filtered = [
                r for r in filtered
                if q in str(r["rel_path"]).lower() or q in str(r["note"]).lower()
            ]
        if sort == "radius_desc":
            filtered = sorted(filtered, key=lambda r: r["radius"] or -1, reverse=True)
        elif sort == "radius_asc":
            filtered = sorted(filtered, key=lambda r: r["radius"] or 1e18)
        elif sort == "coverage_asc":
            filtered = sorted(filtered, key=lambda r: r["coverage"] if r["coverage"] is not None else 1e18)
        elif sort == "status":
            order = {"review": 0, "not_found": 1, "error": 2, "ok": 3}
            filtered = sorted(filtered, key=lambda r: (order.get(r["status"], 9), r["rel_path"]))
        else:
            filtered = sorted(filtered, key=lambda r: (r["rel_path"].lower(), r["object_index"]))

        total = len(filtered)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return {
            "run": meta,
            "summary": summary,
            "rows": filtered[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "total": total,
        }

    def _source_allowed(self, path: Path, roots: list[str]) -> bool:
        if path.suffix.lower() not in IMAGE_EXTS or not path.is_file():
            return False
        target = os.path.normcase(str(path.resolve()))
        for raw in roots:
            root = Path(str(raw)).expanduser().resolve()
            root_s = os.path.normcase(str(root)).rstrip(os.sep)
            if target == root_s:
                return True
            if root.is_dir() and target.startswith(root_s + os.sep):
                return True
        return False

    @staticmethod
    def _unlink_file(path: Path) -> None:
        try:
            path.chmod(0o666)
        except OSError:
            pass
        path.unlink()

    def delete_image(self, run_id: str, payload: dict) -> dict:
        with self._lock:
            if self._active == run_id:
                raise RuntimeError("运行尚未结束，不能删除图片结果")
            meta = self._runs.get(run_id)
            if not meta:
                raise KeyError(run_id)
            meta = dict(meta)

        csv_path = Path(meta["output"]) / "tables" / "wells.csv"
        if not csv_path.exists():
            raise ValueError("该运行还没有结果 CSV")
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))

        target_file = str(payload.get("file") or "").strip()
        target_rel = str(payload.get("rel_path") or "").strip()
        target_norm = ""
        if target_file:
            try:
                target_norm = os.path.normcase(str(Path(target_file).expanduser().resolve()))
            except (OSError, RuntimeError):
                target_norm = os.path.normcase(target_file)

        remove: list[dict] = []
        keep: list[dict] = []
        for row in rows:
            row_norm = ""
            if row.get("file"):
                try:
                    row_norm = os.path.normcase(str(Path(row["file"]).expanduser().resolve()))
                except (OSError, RuntimeError):
                    row_norm = os.path.normcase(row["file"])
            same = (
                (target_norm and row_norm == target_norm)
                or (target_rel and row.get("rel_path") == target_rel)
            )
            (remove if same else keep).append(row)
        if not remove:
            raise ValueError("没有找到这张图片的结果")

        source_deleted = False
        source_error = ""
        if payload.get("delete_source") and target_norm:
            source_path = Path(str(payload["file"])).expanduser().resolve()
            try:
                if self._source_allowed(source_path, meta.get("inputs") or []):
                    self._unlink_file(source_path)
                    source_deleted = True
                else:
                    source_error = "源文件不在该次运行的输入范围内"
            except OSError as exc:
                source_error = str(exc)

        write_csv(csv_path, keep)

        out_root = Path(meta["output"]).resolve()
        outputs_deleted = 0
        if payload.get("delete_outputs", True):
            for row in remove:
                for key in ("roi_norm", "roi_raw", "mask", "polar", "overlay", "thumb"):
                    rel = row.get(key) or ""
                    if not rel:
                        continue
                    target = (out_root / rel).resolve()
                    if target.is_file() and target.is_relative_to(out_root):
                        try:
                            self._unlink_file(target)
                            outputs_deleted += 1
                        except OSError:
                            pass

        try:
            cfg = Config.from_dict(meta.get("config") or {})
            write_report(keep, out_root, cfg)
        except BaseException:
            pass

        radii = []
        for row in keep:
            if row.get("status") in ("ok", "review"):
                value = _num(row.get("radius"))
                if value:
                    radii.append(value)
        counts = count_status(keep)
        meta.update(
            records=len(keep),
            counts=counts,
            images=len({row.get("file") for row in keep}),
            radius_median=round(float(sorted(radii)[len(radii) // 2]), 2) if radii else None,
        )
        with self._lock:
            self._runs[run_id] = meta
            self._save_locked()
        return {
            "ok": True,
            "deleted": len(remove),
            "outputs_deleted": outputs_deleted,
            "source_deleted": source_deleted,
            "source_error": source_error,
            "run": dict(meta),
            "summary": {
                "counts": counts,
                "records": len(keep),
                "images": meta["images"],
                "radius_median": meta["radius_median"],
            },
        }

    # ------------------------------------------------------------------ preview
    def preview(self, payload: dict) -> dict:
        raw = str(payload.get("input") or "").strip()
        if not raw:
            raise ValueError("请先添加图片路径")
        p = Path(raw).expanduser()
        if not p.exists():
            raise ValueError(f"路径不存在：{p}")
        items = collect_images([p], recursive=True, exclude=self.runs_root)
        if not items:
            raise ValueError("没有找到可预览的图片")
        items.sort(key=lambda item: str(item[0]).lower())
        sample = items[0][0]
        cfg = self._config_from_payload(payload.get("config"))
        cfg.workers = 1

        token = str(payload.get("preview_id") or "")
        if not re.fullmatch(r"[0-9a-f]{12}", token):
            token = uuid.uuid4().hex[:12]
        out = self.previews_root / token
        out.mkdir(parents=True, exist_ok=True)

        t0 = time.perf_counter()
        rgb, _ = load_rgb(sample)
        analysis = analyze(rgb, cfg)
        result = {
            "preview_id": token,
            "sample": str(sample),
            "sample_name": sample.name,
            "elapsed": None,
            "fit": None,
            "status": "not_found",
            "n_objects": len(analysis.fits),
            "overlay_url": "",
            "roi_url": "",
            "message": "未检测到绿色亮圆，请检查输入图或降低最小对比度",
        }
        if analysis.fits:
            fit = analysis.fits[0][0]
            overlay = draw_overlay(rgb, fit.cx, fit.cy, fit.r, 1200)
            overlay_path = out / "overlay.jpg"
            save_rgb(overlay_path, overlay, cfg.jpeg_quality)

            roi = warp_circle(rgb, fit.cx, fit.cy, fit.r, cfg.margin, cfg.out_size)
            roi = stretch_for_display(roi, cfg.stretch)
            roi_path = out / "roi.png"
            save_rgb(roi_path, roi, cfg.jpeg_quality)

            issues = []
            if fit.method != "radial":
                issues.append("edge_fit_failed")
            if fit.coverage < cfg.min_coverage:
                issues.append("low_coverage")
            if fit.snr < cfg.min_snr:
                issues.append("low_snr")
            if fit.residual_rms / max(fit.r, 1e-6) > cfg.max_residual_rel:
                issues.append("irregular_edge")
            if fit.edge_width / max(fit.r, 1e-6) > cfg.max_edge_width_frac:
                issues.append("blurred_edge")
            if fit.touches_border:
                issues.append("touches_border")
            result.update(
                status="ok" if not issues else "review",
                overlay_url=f"/previews/{token}/overlay.jpg",
                roi_url=f"/previews/{token}/roi.png",
                message="margin 越大留边越多；边缘电平越低，圆域越往外包",
                fit={
                    "cx": _round_finite(fit.cx, 2),
                    "cy": _round_finite(fit.cy, 2),
                    "r": _round_finite(fit.r, 2),
                    "diameter": _round_finite(2 * fit.r, 2),
                    "coverage": _round_finite(fit.coverage, 4),
                    "snr": _round_finite(fit.snr, 1),
                    "radial_cv": _round_finite(fit.radial_cv, 4),
                    "edge_width": _round_finite(fit.edge_width, 1),
                    "residual_rms": _round_finite(fit.residual_rms, 2),
                    "saturation": _round_finite(analysis.saturation[0], 4) if analysis.saturation else None,
                    "touches_border": bool(fit.touches_border),
                    "issues": issues,
                },
            )
        else:
            overlay_path = out / "overlay.jpg"
            save_rgb(overlay_path, downscale(rgb, 1200), cfg.jpeg_quality)
            result["overlay_url"] = f"/previews/{token}/overlay.jpg"
        result["elapsed"] = round(time.perf_counter() - t0, 2)
        result["out_size"] = cfg.out_size
        result["margin"] = cfg.margin
        return result

    def images(self, run_id: str) -> list[dict]:
        meta = self.get(run_id)
        if not meta:
            raise KeyError(run_id)
        csv_path = Path(meta["output"]) / "tables" / "wells.csv"
        if not csv_path.exists():
            return []
        grouped: dict[str, dict] = {}
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            for raw in csv.DictReader(fh):
                if not raw.get("radius") or not raw.get("cx"):
                    continue
                key = str(raw.get("file") or raw.get("rel_path"))
                radius = _num(raw.get("radius"), 0) or 0
                if key not in grouped or radius > (grouped[key].get("radius") or 0):
                    grouped[key] = {
                        "file": raw.get("file", ""),
                        "rel_path": raw.get("rel_path", ""),
                        "status": raw.get("status", ""),
                        "cx": _num(raw.get("cx")),
                        "cy": _num(raw.get("cy")),
                        "radius": radius,
                        "thumb_url": _file_url(run_id, raw.get("thumb", "")),
                        "records": 0,
                    }
                grouped[key]["records"] += 1
        return sorted(grouped.values(), key=lambda item: str(item.get("rel_path", "")).lower())

    # --------------------------------------------------------------------- fs
    def browse(self, raw_path: str | None) -> dict:
        raw = (raw_path or "").strip() or str(Path.home())
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path.cwd() / p
        try:
            p = p.resolve()
        except OSError:
            p = Path.home()
        if p.is_file():
            p = p.parent
        if not p.is_dir():
            p = Path.home()

        dirs = []
        images = []
        image_count = 0
        error = ""
        skip_names = {
            "$RECYCLE.BIN", "System Volume Information", "Config.Msi",
            "__pycache__", ".git", ".idea", ".vscode", "node_modules", ".previews",
        }
        try:
            with os.scandir(p) as it:
                entries = list(it)
        except OSError as exc:
            entries = []
            error = str(exc)
        for entry in entries:
            if entry.name in skip_names:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    dirs.append({"name": entry.name, "path": entry.path})
                elif Path(entry.name).suffix.lower() in IMAGE_EXTS:
                    image_count += 1
                    if len(images) < 60:
                        images.append({"name": entry.name, "path": entry.path})
            except OSError:
                continue
        dirs.sort(key=lambda d: d["name"].casefold())
        images.sort(key=lambda d: d["name"].casefold())
        drives = []
        if os.name == "nt":
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if Path(root).exists():
                    drives.append(root)
        else:
            drives = ["/"]
        shortcuts = []
        for label, path in (
            ("项目目录", APP_ROOT),
            ("运行结果", self.runs_root),
            ("桌面", Path.home() / "Desktop"),
            ("用户目录", Path.home()),
        ):
            if path.exists():
                shortcuts.append({"label": label, "path": str(path)})
        return {
            "path": str(p),
            "parent": str(p.parent) if p.parent != p else None,
            "dirs": dirs,
            "images": images,
            "image_count": image_count,
            "drives": drives,
            "shortcuts": shortcuts,
            "error": error,
        }

    # -------------------------------------------------------------------- scan
    def scan(self, inputs: list[str]) -> dict:
        paths = []
        missing = []
        for raw in inputs:
            p = Path(str(raw)).expanduser()
            if p.exists():
                paths.append(p)
            else:
                missing.append(str(raw))
        items = collect_images(paths, recursive=True, exclude=self.runs_root) if paths else []
        items.sort(key=lambda item: str(item[0]).lower())
        by_ext: dict[str, int] = {}
        for path, _rel in items:
            ext = path.suffix.lower()
            by_ext[ext] = by_ext.get(ext, 0) + 1
        limit = 5000
        listed = []
        for path, rel in items[:limit]:
            listed.append(
                {
                    "path": str(path.resolve()),
                    "rel_path": (Path(rel) / path.name).as_posix(),
                }
            )
        return {
            "count": len(items),
            "by_ext": by_ext,
            "samples": [(Path(rel) / path.name).as_posix() for path, rel in items[:8]],
            "items": listed,
            "truncated": len(items) > limit,
            "missing": missing,
        }

    def state(self) -> dict:
        with self._lock:
            active = self._active
        return {
            "version": VERSION,
            "app_root": str(APP_ROOT),
            "runs_root": str(self.runs_root),
            "default_output": str(self.runs_root),
            "defaults": Config().to_dict(),
            "active": active,
            "status_labels": STATUS_LABELS,
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    manager: RunManager
    cuts: CutStore
    server_version = "WellPrepUI/" + VERSION

    # ------------------------------------------------------------------ helpers
    def log_message(self, fmt: str, *args) -> None:  # keep the console quiet
        return

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": str(message)}, status)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > 4 * 1024 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"JSON 解析失败：{exc}")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _send_file(self, path: Path, download: bool = False) -> None:
        if not path.is_file():
            self._error("文件不存在", 404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype in ("text/html", "text/css", "text/javascript", "application/javascript", "text/plain"):
            ctype += "; charset=utf-8"
        try:
            data = path.read_bytes()
        except OSError as exc:
            self._error(str(exc), 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if download:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        if path.suffix.lower() in (".html", ".js", ".css") or ctype.startswith("text/"):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _safe_child(root: Path, rel: str) -> Path | None:
        target = (root / rel).resolve()
        if target == root or not target.is_relative_to(root):
            return None
        return target

    def _cut_payload(self, cut: dict) -> dict:
        cut = dict(cut)
        cut_id = cut.get("id", "")
        base = f"/cuts/{quote(cut_id)}/files/"
        for key, rel in (
            ("circle", cut.get("circle_file")),
            ("overlay", cut.get("overlay_file")),
            ("contact", cut.get("contact_file")),
            ("csv", cut.get("csv_file")),
        ):
            cut[key + "_url"] = base + quote(str(rel), safe="/") if rel else ""
        rows = []
        for row in cut.get("sector_rows", []):
            item = dict(row)
            item["url"] = base + quote(f"sectors/{row.get('file', '')}", safe="/")
            rows.append(item)
        cut["sector_rows"] = rows
        return cut

    # --------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        try:
            if path == "/api/state":
                self._json(self.manager.state())
                return
            if path == "/api/runs":
                self._json({"runs": self.manager.list()})
                return
            if path == "/api/cuts":
                self._json({"cuts": [self._cut_payload(item) for item in self.cuts.list()]})
                return
            if path == "/api/fs":
                self._json(self.manager.browse(query.get("path")))
                return

            m = re.fullmatch(r"/api/runs/([^/]+)/images", path)
            if m:
                try:
                    self._json({"images": self.manager.images(m.group(1))})
                except KeyError:
                    self._error("运行记录不存在", 404)
                return
            m = re.fullmatch(r"/api/cuts/([0-9]{8}-[0-9]{6}-[0-9a-f]{4})/zip", path)
            if m:
                try:
                    target = self.cuts.zip_sectors(m.group(1))
                except (KeyError, FileNotFoundError):
                    self._error("切割记录不存在", 404)
                else:
                    self._send_file(target, download=True)
                return
            m = re.fullmatch(r"/api/cuts/([0-9]{8}-[0-9]{6}-[0-9a-f]{4})", path)
            if m:
                cut = self.cuts.get(m.group(1))
                if not cut:
                    self._error("切割记录不存在", 404)
                else:
                    self._json({"cut": self._cut_payload(cut)})
                return

            m = re.fullmatch(r"/api/runs/([^/]+)/status", path)
            if m:
                data = self.manager.status(m.group(1))
                if not data:
                    self._error("运行记录不存在", 404)
                else:
                    self._json(data)
                return
            m = re.fullmatch(r"/api/runs/([^/]+)/results", path)
            if m:
                try:
                    self._json(self.manager.results(m.group(1), query))
                except KeyError:
                    self._error("运行记录不存在", 404)
                return
            m = re.fullmatch(r"/api/runs/([^/]+)", path)
            if m:
                meta = self.manager.get(m.group(1))
                if not meta:
                    self._error("运行记录不存在", 404)
                else:
                    self._json({"run": meta})
                return

            m = re.fullmatch(r"/previews/([0-9a-f]{12})/(.+)", path)
            if m:
                target = self._safe_child(self.manager.previews_root / m.group(1), m.group(2))
                if target is None:
                    self._error("非法路径", 403)
                else:
                    self._send_file(target)
                return

            m = re.fullmatch(r"/cuts/([0-9]{8}-[0-9]{6}-[0-9a-f]{4})/files/(.+)", path)
            if m:
                root = (self.cuts.root / m.group(1)).resolve()
                target = self._safe_child(root, m.group(2))
                if target is None:
                    self._error("非法路径", 403)
                else:
                    self._send_file(target, download=query.get("download") == "1")
                return

            m = re.fullmatch(r"/runs/([^/]+)/files/(.+)", path)
            if m:
                meta = self.manager.get(m.group(1))
                if not meta:
                    self._error("运行记录不存在", 404)
                    return
                root = Path(meta["output"]).resolve()
                target = self._safe_child(root, m.group(2))
                if target is None:
                    self._error("非法路径", 403)
                else:
                    self._send_file(target, download=query.get("download") == "1")
                return

            if path == "/" or path == "/index.html":
                self._send_file(WEB_ROOT / "index.html")
                return
            if path.startswith("/static/"):
                target = self._safe_child(WEB_ROOT, path[len("/static/") :])
                if target is None:
                    self._error("非法路径", 403)
                else:
                    self._send_file(target)
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            self._error("未找到", 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except BaseException as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

    # -------------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            body = self._body()
            if path == "/api/scan":
                self._json(self.manager.scan(body.get("inputs") or []))
                return
            if path == "/api/preview":
                self._json(self.manager.preview(body))
                return
            if path == "/api/cuts":
                run_id = str(body.get("run_id") or "")
                run = self.manager.get(run_id)
                if not run:
                    self._error("运行记录不存在", 404)
                    return
                wanted = str(body.get("file") or body.get("rel_path") or "")
                row = None
                for item in self.manager.images(run_id):
                    if wanted and wanted in (item.get("file"), item.get("rel_path")):
                        row = item
                        break
                if not row:
                    self._error("没有找到该图片的提取结果", 404)
                    return
                cut = self.cuts.create(run, row, body)
                self._json({"cut": self._cut_payload(cut)}, 201)
                return
            if path == "/api/runs":
                meta = self.manager.start(body)
                self._json({"run": meta}, 201)
                return
            if path == "/api/open":
                meta = self.manager.get(str(body.get("run_id", "")))
                if not meta:
                    self._error("运行记录不存在", 404)
                    return
                target = Path(meta["output"]).resolve()
                if not target.is_dir():
                    self._error("输出目录不存在", 404)
                    return
                if os.name == "nt":
                    os.startfile(str(target))  # noqa: S606
                else:
                    import subprocess

                    subprocess.Popen(["open" if os.uname().sysname == "Darwin" else "xdg-open", str(target)])
                self._json({"ok": True, "path": str(target)})
                return
            m = re.fullmatch(r"/api/cuts/([0-9]{8}-[0-9]{6}-[0-9a-f]{4})/delete", path)
            if m:
                self._json({"ok": bool(self.cuts.delete(m.group(1)))})
                return
            m = re.fullmatch(r"/api/runs/([^/]+)/cancel", path)
            if m:
                ok = self.manager.cancel(m.group(1))
                self._json({"ok": ok})
                return
            m = re.fullmatch(r"/api/runs/([^/]+)/delete", path)
            if m:
                meta = self.manager.delete(m.group(1), bool(body.get("delete_files")))
                self._json({"ok": bool(meta), "run": meta})
                return
            m = re.fullmatch(r"/api/runs/([^/]+)/delete-image", path)
            if m:
                self._json(self.manager.delete_image(m.group(1), body))
                return
            self._error("未找到", 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except (ValueError, RuntimeError, KeyError) as exc:
            self._error(str(exc), 404 if isinstance(exc, KeyError) else 400)
        except BaseException as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def serve(host: str = "127.0.0.1", port: int = 8768, open_browser: bool = False) -> None:
    manager = RunManager()
    cuts = CutStore(APP_ROOT / "cuts")
    handler = type("BoundHandler", (Handler,), {"manager": manager, "cuts": cuts})
    url = f"http://{host}:{port}/"
    # Starting a second copy is a common accident (double-clicking the .bat).
    # Detect the already-running instance and just open it instead of failing.
    try:
        with urllib.request.urlopen(url + "api/state", timeout=1.2) as resp:
            if resp.status == 200:
                print(f"WellPrep 已在运行：{url}")
                print("无需重复启动；已为你打开现有界面。")
                if open_browser:
                    webbrowser.open(url)
                return
    except Exception:
        pass
    try:
        httpd = QuietServer((host, port), handler)
    except OSError as exc:
        raise SystemExit(f"无法监听 {host}:{port}：{exc}") from exc
    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"
    print(f"WellPrep UI: {url}")
    print(f"运行记录目录: {manager.runs_root}")
    print("按 Ctrl+C 停止服务")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()

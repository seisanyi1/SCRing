"""Programmatic batch runner with progress and cancellation.

The CLI and the local web UI both use this module so that a run produces the
same CSV/report/artefacts regardless of how it was started.
"""

from __future__ import annotations

import csv
import os
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .pipeline import CSV_FIELDS, collect_images, process_image
from .report import write_report

ProgressFn = Callable[[dict], None]
LogFn = Callable[[str], None]


@dataclass
class BatchResult:
    records: list[dict]
    total: int
    done: int
    elapsed: float
    cancelled: bool
    counts: dict[str, int]

    @property
    def images(self) -> int:
        return len({r.get("file") for r in self.records})


def count_status(records: list[dict]) -> dict[str, int]:
    counts = {"ok": 0, "review": 0, "not_found": 0, "error": 0}
    for row in records:
        key = str(row.get("status", "error"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def error_record(path: Path, rel_dir: str, exc: BaseException) -> dict:
    return {
        "file": str(path.resolve()),
        "rel_path": (Path(rel_dir) / path.name).as_posix(),
        "status": "error",
        "note": f"{type(exc).__name__}: {exc}"[:400],
    }


def write_csv(path: str | Path, records: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        records,
        key=lambda r: (str(r.get("rel_path", "")).lower(), int(r.get("object_index") or 0)),
    )
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in ordered:
            writer.writerow(row)


def run_batch(
    inputs: list[str | Path],
    cfg: Config,
    out_root: str | Path,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
    cancel: threading.Event | None = None,
    recursive: bool = True,
    exclude_paths: list[str] | None = None,
) -> BatchResult:
    """Process every image under ``inputs`` into ``out_root``.

    ``on_progress`` receives a JSON-safe snapshot after every image:
    ``done``, ``total``, ``current``, ``records``, ``counts``, ``elapsed``,
    ``cancelled``.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    emit = on_progress or (lambda _p: None)
    log = on_log or (lambda _m: None)
    cancel = cancel or threading.Event()

    items = collect_images([Path(p) for p in inputs], recursive=recursive, exclude=out_root)
    if exclude_paths:
        blocked = {os.path.normcase(str(Path(p).expanduser().resolve())) for p in exclude_paths}
        before = len(items)
        items = [item for item in items if os.path.normcase(str(item[0].resolve())) not in blocked]
        skipped = before - len(items)
        if skipped:
            log(f"已排除 {skipped} 张预审删除的图片")
    total = len(items)
    if total == 0:
        raise RuntimeError("没有找到可处理的图片")

    records: list[dict] = []
    state = {"done": 0, "cancelled": False}

    def snapshot(current: str = "") -> dict:
        counts = count_status(records)
        return {
            "done": state["done"],
            "total": total,
            "current": current,
            "records": len(records),
            "counts": counts,
            "elapsed": round(time.perf_counter() - t0, 2),
            "cancelled": state["cancelled"],
        }

    def absorb(path: Path, rel: str, rows: list[dict]) -> None:
        records.extend(rows)
        state["done"] += 1
        status = rows[0].get("status", "error") if rows else "error"
        radius = rows[0].get("radius", "") if rows else ""
        extra = f" r={radius}px" if radius != "" else ""
        log(f"[{state['done']}/{total}] {path.name} -> {status}{extra}")
        emit(snapshot(path.name))

    t0 = time.perf_counter()
    log(f"开始处理：{total} 张图片，输出到 {out_root}")
    emit(snapshot())

    pool = None
    if cfg.workers > 1 and total > 1:
        try:
            pool = ProcessPoolExecutor(max_workers=min(int(cfg.workers), total))
        except (OSError, PermissionError, RuntimeError) as exc:
            log(f"并行进程不可用（{exc}），回退为单进程")
            pool = None

    if pool is not None:
        futures = {
            pool.submit(process_image, str(path), cfg, str(out_root), rel): (path, rel)
            for path, rel in items
        }
        try:
            for fut in as_completed(futures):
                path, rel = futures[fut]
                if cancel.is_set():
                    log("收到取消请求，停止调度剩余图片")
                    state["cancelled"] = True
                    for f in futures:
                        f.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    rows = fut.result()
                except BaseException as exc:  # noqa: BLE001
                    rows = [error_record(path, rel, exc)]
                    log(f"[error] {path.name}: {exc}")
                absorb(path, rel, rows)
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
    else:
        for path, rel in items:
            if cancel.is_set():
                log("收到取消请求，停止处理剩余图片")
                state["cancelled"] = True
                break
            try:
                rows = process_image(path, cfg, out_root, rel)
            except BaseException as exc:  # noqa: BLE001
                rows = [error_record(path, rel, exc)]
                log(f"[error] {path.name}: {exc}")
                traceback.print_exc()
            absorb(path, rel, rows)

    csv_path = out_root / "tables" / "wells.csv"
    write_csv(csv_path, records)
    cfg.save(out_root / "run_config.json")
    try:
        write_report(records, out_root, cfg)
    except BaseException as exc:  # report failure must not lose the CSV
        log(f"报告生成失败：{exc}")

    elapsed = time.perf_counter() - t0
    counts = count_status(records)
    emit(
        {
            "done": state["done"],
            "total": total,
            "current": "",
            "records": len(records),
            "counts": counts,
            "elapsed": round(elapsed, 2),
            "cancelled": state["cancelled"],
        }
    )
    log(
        f"结束：完成 {state['done']}/{total} 张，"
        f"通过 {counts.get('ok', 0)}，复核 {counts.get('review', 0)}，"
        f"未找到 {counts.get('not_found', 0)}，错误 {counts.get('error', 0)}，"
        f"用时 {elapsed:.1f}s"
    )
    return BatchResult(
        records=records,
        total=total,
        done=state["done"],
        elapsed=elapsed,
        cancelled=state["cancelled"],
        counts=counts,
    )

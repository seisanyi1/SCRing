"""Batch preprocessing of green fluorescence discs (non-fixed radius ROI)."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import fields
from pathlib import Path

from .config import Config
from .imaging import IMAGE_EXTS
from .pipeline import CSV_FIELDS, collect_images, process_image
from .report import write_report

_NUMERIC_OVERRIDES = {
    "smooth_sigma": float,
    "threshold_percentile": float,
    "min_area_frac": float,
    "margin": float,
    "out_size": int,
    "rays": int,
    "edge_level": float,
    "workers": int,
    "overlay_max": int,
    "min_snr": float,
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wellprep",
        description="遍历图片，自适应估计绿色亮圆的圆心与非固定半径，输出归一化圆域 ROI、掩膜、叠加图和 CSV/HTML 报告。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("-i", "--input", nargs="+", required=True, help="图片文件或目录（可多个）")
    p.add_argument("-o", "--output", default="preprocess_out", help="输出目录")
    p.add_argument("--config", help="JSON 配置文件（可选）")
    p.add_argument("--recursive", action="store_true", default=True, help="递归子目录")
    p.add_argument("--no-recursive", dest="recursive", action="store_false", help="只处理当前目录")
    p.add_argument("--workers", type=int, help="并行进程数")
    p.add_argument("--force", action="store_true", help="忽略已有结果，全部重算")
    p.add_argument("--limit", type=int, help="最多处理 N 张（调试用）")
    p.add_argument("--select", choices=["largest", "brightest", "all"], help="每图选一个圆/最亮圆/全部圆")
    p.add_argument("--multi", action="store_true", help="等价于 --select all")
    p.add_argument("--feature", choices=["green_excess", "gray", "max_rgb"], help="前景特征通道")
    p.add_argument("--smooth-sigma", type=float, help="高斯平滑 sigma（像素）")
    p.add_argument("--threshold-mode", choices=["otsu", "percentile"], help="粗分割阈值方式")
    p.add_argument("--threshold-percentile", type=float, help="percentile 模式的分位点")
    p.add_argument("--min-area-frac", type=float, help="候选连通域最小面积占比")
    p.add_argument("--margin", type=float, help="裁剪半径倍数（1.35 表示留 35%% 边缘）")
    p.add_argument("--out-size", type=int, help="归一化 ROI 边长")
    p.add_argument("--rays", type=int, help="径向边缘采样条数")
    p.add_argument("--edge-level", type=float, help="边缘取对比度的百分比（0.5 = 半高）")
    p.add_argument("--min-snr", type=float, help="SNR 复核阈值")
    p.add_argument("--polar", action="store_true", help="额外输出极坐标展开图")
    p.add_argument("--stretch", action="store_true", help="预览按 99.5%% 分位拉伸（16bit 图更易看）")
    p.add_argument("--no-raw", action="store_true", help="不保存原始分辨率方形裁剪")
    p.add_argument("--overlay-max", type=int, help="叠加图最长边")
    p.add_argument("--dry-run", action="store_true", help="只列出将处理的文件")
    p.add_argument("--quiet", action="store_true", help="减少终端输出")
    p.add_argument("--version", action="version", version="wellprep 1.0")
    return p


def _load_existing(csv_path: Path) -> dict[str, list[dict]]:
    if not csv_path.exists():
        return {}
    rows: dict[str, list[dict]] = {}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            key = str(Path(row.get("file", "")).resolve()).lower()
            rows.setdefault(key, []).append(row)
    return rows


def _rows_usable(rows: list[dict], out_root: Path) -> bool:
    if not rows:
        return False
    for row in rows:
        if str(row.get("status")) == "error":
            return False
        for field in ("overlay", "thumb"):
            rel = row.get(field, "")
            if not rel or not (out_root / rel).exists():
                return False
        if str(row.get("status")) in ("ok", "review"):
            rel = row.get("roi_norm", "")
            if not rel or not (out_root / rel).exists():
                return False
    return True


def _config_compatible(snapshot: Path, cfg: Config) -> bool:
    """Do not silently reuse ROIs produced with different analysis settings."""
    if not snapshot.exists():
        return False
    try:
        old = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    skip = {"workers", "force"}
    return all(old.get(k.name) == getattr(cfg, k.name) for k in fields(cfg) if k.name not in skip)


def _error_record(path: Path, rel_dir: str, exc: BaseException) -> dict:
    return {
        "file": str(path.resolve()),
        "rel_path": (Path(rel_dir) / path.name).as_posix(),
        "status": "error",
        "note": f"{type(exc).__name__}: {exc}"[:400],
    }


def _write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(
        records,
        key=lambda r: (str(r.get("rel_path", "")).lower(), int(r.get("object_index") or 0)),
    )
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore", restval="")
        writer.writeheader()
        for r in records:
            writer.writerow(r)


def _fmt_path(path: Path) -> str:
    try:
        return path.resolve().as_uri()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    cfg = Config.load(args.config) if args.config else Config()
    if args.workers is not None:
        cfg.workers = args.workers
    for name, cast in _NUMERIC_OVERRIDES.items():
        value = getattr(args, name.replace("-", "_"), None)
        if value is not None:
            setattr(cfg, name, cast(value))
    if args.feature:
        cfg.feature = args.feature
    if args.threshold_mode:
        cfg.threshold_mode = args.threshold_mode
    if args.select:
        cfg.select = args.select
    if args.multi:
        cfg.select = "all"
    if args.polar:
        cfg.save_polar = True
    if args.stretch:
        cfg.stretch = True
    if args.no_raw:
        cfg.save_raw = False
    if args.force:
        cfg.force = True
    cfg.workers = max(1, int(cfg.workers))
    cfg.validate()

    inputs = [Path(x) for x in args.input]
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        print(f"输入不存在: {', '.join(missing)}", file=sys.stderr)
        return 2

    out_root = Path(args.output).resolve()
    items = collect_images(inputs, recursive=args.recursive, exclude=out_root)
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("没有找到可处理的图片（扩展名: " + ", ".join(sorted(IMAGE_EXTS)) + "）", file=sys.stderr)
        return 1

    if args.dry_run:
        for path, rel in items:
            print(f"{path}  ->  {Path(rel) / path.name}")
        print(f"共 {len(items)} 张")
        return 0

    out_root.mkdir(parents=True, exist_ok=True)
    csv_path = out_root / "tables" / "wells.csv"
    existing: dict[str, list[dict]] = {}
    if not cfg.force and csv_path.exists():
        snapshot = out_root / "run_config.json"
        if _config_compatible(snapshot, cfg):
            existing = _load_existing(csv_path)
        elif not args.quiet:
            print("检测到输出目录的旧配置与本次不同，将重新处理（或加 --force 明确覆盖）")
    records: list[dict] = []
    pending: list[tuple[Path, str]] = []

    for path, rel in items:
        key = str(path.resolve()).lower()
        rows = existing.get(key)
        if rows and _rows_usable(rows, out_root):
            records.extend(rows)
        else:
            pending.append((path, rel))

    if not args.quiet:
        print(f"输出目录: {out_root}")
        print(f"图片 {len(items)} 张，复用 {len(items) - len(pending)} 张，待处理 {len(pending)} 张，进程 {cfg.workers}")

    t0 = time.perf_counter()
    done = 0

    def run_inline(items_in: list[tuple[Path, str]]) -> None:
        nonlocal done
        for path, rel in items_in:
            try:
                records.extend(process_image(path, cfg, out_root, rel))
            except BaseException as exc:  # noqa: BLE001 - keep batch alive
                records.append(_error_record(path, rel, exc))
            done += 1
            if not args.quiet and (done % 10 == 0 or done == len(pending)):
                print(f"  {done}/{len(pending)}")

    if pending:
        pool = None
        if cfg.workers > 1:
            try:
                pool = ProcessPoolExecutor(max_workers=min(cfg.workers, len(pending)))
            except (OSError, PermissionError, RuntimeError) as exc:
                if not args.quiet:
                    print(f"并行进程不可用（{exc}），回退为单进程")
                pool = None
        if pool is None:
            run_inline(pending)
        else:
            with pool:
                futures = {
                    pool.submit(process_image, str(path), cfg, str(out_root), rel): (path, rel)
                    for path, rel in pending
                }
                for fut in as_completed(futures):
                    path, rel = futures[fut]
                    try:
                        records.extend(fut.result())
                    except BaseException as exc:  # noqa: BLE001
                        records.append(_error_record(path, rel, exc))
                    done += 1
                    if not args.quiet and (done % 10 == 0 or done == len(pending)):
                        print(f"  {done}/{len(pending)}")

    _write_csv(csv_path, records)
    cfg.save(out_root / "run_config.json")
    report = write_report(records, out_root, cfg)

    elapsed = time.perf_counter() - t0
    status_count: dict[str, int] = {}
    for r in records:
        status_count[str(r.get("status"))] = status_count.get(str(r.get("status")), 0) + 1
    ok = status_count.get("ok", 0)
    review = status_count.get("review", 0)
    not_found = status_count.get("not_found", 0)
    errors = status_count.get("error", 0)
    images = len({r.get("file") for r in records})
    print()
    print(f"完成: {images} 张图片 / {len(records)} 个圆域，用时 {elapsed:.1f}s")
    print(f"  通过 {ok} | 待复核 {review} | 未找到 {not_found} | 错误 {errors}")
    print(f"  CSV   : {csv_path}")
    print(f"  报告  : {_fmt_path(report)}")
    if errors:
        bad = [r.get("rel_path", "") for r in records if r.get("status") == "error"][:5]
        print("  错误示例: " + ", ".join(str(b) for b in bad))
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

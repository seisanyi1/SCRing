"""Static HTML quality-control report for a preprocessing run."""

from __future__ import annotations

import datetime as dt
import html
import math
from collections import Counter
from pathlib import Path

import numpy as np

from .config import Config

_STATUS_ORDER = {"review": 0, "error": 1, "not_found": 2, "ok": 3}
_STATUS_LABEL = {"ok": "通过", "review": "复核", "not_found": "未找到", "error": "错误"}


def _f(row: dict, key: str, digits: int = 2) -> str:
    v = row.get(key, "")
    if v in ("", None):
        return "–"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "–"
    if not math.isfinite(x):
        return "–"
    return f"{x:.{digits}f}"


def _stats(records: list[dict]) -> dict:
    status = Counter(str(r.get("status", "error")) for r in records)
    radii = []
    cov = []
    snr = []
    for r in records:
        try:
            if str(r.get("status")) in ("ok", "review"):
                radii.append(float(r["radius"]))
                cov.append(float(r["coverage"]))
                if r.get("snr") not in ("", None):
                    snr.append(float(r["snr"]))
        except (KeyError, TypeError, ValueError):
            pass
    return {
        "rows": len(records),
        "images": len({r.get("file") for r in records}),
        "status": status,
        "radius_med": float(np.median(radii)) if radii else float("nan"),
        "radius_min": float(np.min(radii)) if radii else float("nan"),
        "radius_max": float(np.max(radii)) if radii else float("nan"),
        "cov_med": float(np.median(cov)) if cov else float("nan"),
        "snr_med": float(np.median(snr)) if snr else float("nan"),
    }


def write_report(
    records: list[dict],
    out_root: str | Path,
    cfg: Config,
    title: str = "Green-well ROI 预处理质量报告",
) -> Path:
    out_root = Path(out_root)
    stats = _stats(records)
    rows = sorted(
        records,
        key=lambda r: (_STATUS_ORDER.get(str(r.get("status")), 9), str(r.get("rel_path", ""))),
    )

    cards = [
        ("图片数", stats["images"]),
        ("圆域数", stats["rows"]),
        ("通过", stats["status"].get("ok", 0)),
        ("待复核", stats["status"].get("review", 0) + stats["status"].get("not_found", 0)),
        ("错误", stats["status"].get("error", 0)),
        ("半径中位数", f"{stats['radius_med']:.1f} px" if math.isfinite(stats["radius_med"]) else "–"),
        (
            "半径范围",
            f"{stats['radius_min']:.0f}–{stats['radius_max']:.0f} px"
            if math.isfinite(stats["radius_min"])
            else "–",
        ),
        ("覆盖率中位数", f"{stats['cov_med']:.3f}" if math.isfinite(stats["cov_med"]) else "–"),
    ]

    body_rows = []
    for i, r in enumerate(rows, 1):
        status = str(r.get("status", "error"))
        label = _STATUS_LABEL.get(status, status)
        thumb = str(r.get("thumb", "") or "")
        overlay = str(r.get("overlay", "") or "")
        name = html.escape(str(r.get("rel_path", "")))
        note = html.escape(str(r.get("note", "") or ""))
        if thumb:
            img = (
                f'<a href="{html.escape(overlay or thumb)}" target="_blank">'
                f'<img loading="lazy" src="{html.escape(thumb)}" alt="preview"></a>'
            )
        else:
            img = "–"
        body_rows.append(
            "<tr data-status='{status}'>"
            "<td class='num'>{i}</td>"
            "<td class='file'>{name}<div class='note'>{note}</div></td>"
            "<td><span class='badge {status}'>{label}</span></td>"
            "<td class='num'>{cx}</td><td class='num'>{cy}</td>"
            "<td class='num strong'>{rad}</td>"
            "<td class='num'>{cov}</td><td class='num'>{snr}</td>"
            "<td class='num'>{cv}</td><td class='num'>{ms}</td>"
            "<td class='thumb'>{img}</td>"
            "</tr>".format(
                status=status,
                i=i,
                name=name,
                note=note,
                label=label,
                cx=_f(r, "cx", 1),
                cy=_f(r, "cy", 1),
                rad=_f(r, "radius", 1),
                cov=_f(r, "coverage", 3),
                snr=_f(r, "snr", 1),
                cv=_f(r, "radial_cv", 3),
                ms=_f(r, "elapsed_ms", 0),
                img=img,
            )
        )

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cfg_summary = html.escape(
        f"feature={cfg.feature}, margin={cfg.margin}, out_size={cfg.out_size}, "
        f"select={cfg.select}, edge_level={cfg.edge_level:.2f}, rays={cfg.rays}"
    )

    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: "Times New Roman", "SimSun", "宋体", serif;
         background: #f5f6f8; color: #1c2430; }}
  header {{ background: #0f1b2a; color: #eaf2ff; padding: 22px 28px 18px; }}
  header h1 {{ margin: 0 0 6px; font-size: 20px; font-weight: 600; }}
  header p {{ margin: 0; color: #9fb3c8; font-size: 13px; }}
  .wrap {{ padding: 20px 28px 48px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
           gap: 12px; margin-bottom: 18px; }}
  .card {{ background: #fff; border: 1px solid #e2e7ee; border-radius: 10px; padding: 12px 14px; }}
  .card .k {{ color: #6b7a8d; font-size: 12px; }}
  .card .v {{ font-size: 22px; font-weight: 600; margin-top: 2px; }}
  .toolbar {{ display: flex; gap: 8px; align-items: center; margin: 4px 0 12px; }}
  .toolbar button {{ border: 1px solid #cfd8e3; background: #fff; color: #24303f;
                     padding: 6px 12px; border-radius: 7px; cursor: pointer; font-size: 13px; }}
  .toolbar button.active {{ background: #1454a8; border-color: #1454a8; color: #fff; }}
  .toolbar .hint {{ color: #6b7a8d; font-size: 12px; margin-left: auto; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e2e7ee;
           border-radius: 10px; overflow: hidden; }}
  thead th {{ position: sticky; top: 0; background: #eef2f7; font-size: 12px; color: #405268;
              text-align: center; padding: 8px 10px; border-bottom: 1px solid #dbe2ec; }}
  tbody td {{ padding: 8px 10px; border-bottom: 1px solid #eef1f5; font-size: 13px; vertical-align: middle; text-align: center; }}
  tbody tr:hover {{ background: #f8fbff; }}
  td.num {{ text-align: center; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  td.strong {{ font-weight: 600; }}
  td.file {{ max-width: 380px; overflow-wrap: anywhere; }}
  td.file .note {{ color: #a05a00; font-size: 11px; margin-top: 2px; }}
  td.thumb img {{ width: 96px; height: 54px; object-fit: cover; border-radius: 6px;
                  border: 1px solid #dbe2ec; display: block; margin: 0 auto; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
            font-weight: 600; white-space: nowrap; }}
  .badge.ok {{ background: #e3f6ea; color: #157347; }}
  .badge.review {{ background: #fff3cd; color: #8a6100; }}
  .badge.not_found {{ background: #e6eefb; color: #1d4f91; }}
  .badge.error {{ background: #fde8e8; color: #a61b1b; }}
  footer {{ color: #7c8a9c; font-size: 12px; padding: 18px 28px; }}
</style></head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <p>生成时间 {now} · {cfg_summary}</p>
</header>
<div class="wrap">
  <div class="cards">
    {''.join(f'<div class="card"><div class="k">{html.escape(str(k))}</div><div class="v">{html.escape(str(v))}</div></div>' for k, v in cards)}
  </div>
  <div class="toolbar">
    <button data-filter="all" class="active">全部</button>
    <button data-filter="review">待复核</button>
    <button data-filter="bad">未找到/错误</button>
    <button data-filter="ok">仅通过</button>
    <span class="hint">点击缩略图可查看原尺寸叠加图；半径单位为源图像素</span>
  </div>
  <table>
    <thead><tr>
      <th>#</th><th>文件</th><th>状态</th><th>cx</th><th>cy</th><th>半径</th>
      <th>覆盖率</th><th>SNR</th><th>径向CV</th><th>耗时 ms</th><th>预览</th>
    </tr></thead>
    <tbody>
    {''.join(body_rows)}
    </tbody>
  </table>
</div>
<footer>wellprep · 非固定半径圆域提取 · 所有阈值与半径均由图像自适应估计</footer>
<script>
  const buttons = document.querySelectorAll('.toolbar button');
  const rows = document.querySelectorAll('tbody tr');
  buttons.forEach(btn => btn.addEventListener('click', () => {{
    buttons.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const f = btn.dataset.filter;
    rows.forEach(r => {{
      const s = r.dataset.status;
      const show = f === 'all' ? true : (f === 'ok' ? s === 'ok' :
                    f === 'review' ? s === 'review' : (s === 'not_found' || s === 'error'));
      r.style.display = show ? '' : 'none';
    }});
  }}));
</script>
</body></html>
"""
    path = out_root / "report.html"
    path.write_text(doc, encoding="utf-8")
    return path

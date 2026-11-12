"""Configuration for the green-well ROI preprocessing pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class Config:
    # ---------------------------------------------------------------- feature
    # "green_excess" is G - (R + B) / 2, which is what makes the green disc
    # stand out from the blue specks and the dark background.
    feature: str = "green_excess"          # green_excess | gray | max_rgb
    smooth_sigma: float = 1.2              # Gaussian blur (px) before analysis

    # ---------------------------------------------------------- segmentation
    threshold_mode: str = "otsu"           # otsu | percentile
    threshold_percentile: float = 99.0     # used by threshold_mode=percentile
    min_area_frac: float = 0.0006          # ignore blobs smaller than this
    max_area_frac: float = 0.90            # ignore blobs larger than this
    open_radius: int = 1                   # binary opening radius (0 = off)
    select: str = "largest"                # largest | brightest | all
    max_objects: int = 12

    # ------------------------------------------------------------ circle fit
    rays: int = 720                        # angular samples for the edge fit
    profile_points: int = 900              # radial samples per ray
    search_inner: float = 0.80             # start searching at this * r_coarse
    search_outer: float = 1.60             # stop searching at this * r_coarse
    edge_level: float = 0.50               # 50 % of local contrast = the edge
    hysteresis: int = 3                    # consecutive samples below level
    max_iter: int = 3
    trim_sigma: float = 3.5
    min_coverage: float = 0.50

    # --------------------------------------------------------------- outputs
    margin: float = 1.35                   # crop half-size = margin * radius
    out_size: int = 512                    # normalised square side (px)
    save_raw: bool = True                  # native-resolution square crop
    save_mask: bool = True
    save_polar: bool = False
    polar_theta: int = 720
    polar_r: int = 256
    overlay_max: int = 1600
    jpeg_quality: int = 92
    png_compress: int = 4
    stretch: bool = False                  # robust 99.5 % stretch for previews

    # --------------------------------------------------------- quality gates
    min_contrast: float = 0.01             # absolute contrast in 0..1 units
    min_snr: float = 3.0
    max_residual_rel: float = 0.04
    max_edge_width_frac: float = 0.15
    min_radius_px: int = 10

    # ------------------------------------------------------------- internals
    workers: int = 4
    force: bool = False

    def validate(self) -> None:
        if self.feature not in ("green_excess", "gray", "max_rgb"):
            raise ValueError(f"unknown feature: {self.feature}")
        if self.threshold_mode not in ("otsu", "percentile"):
            raise ValueError(f"unknown threshold_mode: {self.threshold_mode}")
        if self.select not in ("largest", "brightest", "all"):
            raise ValueError(f"unknown select: {self.select}")
        if not (0.0 < self.margin <= 3.0):
            raise ValueError("margin must be in (0, 3]")
        if self.out_size < 32:
            raise ValueError("out_size must be >= 32")
        if self.rays < 72:
            raise ValueError("rays must be >= 72")
        if self.max_iter < 1:
            raise ValueError("max_iter must be >= 1")
        if not (0.0 < self.edge_level < 1.0):
            raise ValueError("edge_level must be in (0, 1)")
        if not (0.0 < self.search_inner < self.search_outer):
            raise ValueError("search_inner must be < search_outer")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        valid = {f.name for f in fields(cls)}
        unknown = set(data) - valid
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # allow a wrapper like {"config": {...}}
        if "config" in data and isinstance(data["config"], dict):
            data = data["config"]
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

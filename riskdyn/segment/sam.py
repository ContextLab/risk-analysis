"""Segment Anything automatic mask generation.

Class-agnostic segmentation over the whole image via a fixed grid of point
prompts.  The model revision is pinned so the same image always yields the
same masks; there is no sampling anywhere in SAM's forward pass, but a seed
is fixed anyway as cheap insurance.

Raw masks are cached to disk (``sam_masks.npz`` beside the other per-map
artifacts) keyed by model + parameters, so post-processing can be iterated
without re-running the model.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field

import numpy as np

MODEL_NAME = "facebook/sam-vit-base"
# Pinned 2026-08-10 (main); determinism requires never floating this.
MODEL_REVISION = "70c1a07f894ebb5b307fd9eaaee97b9dfc16068f"


@dataclass(frozen=True)
class SamParams:
    model_name: str = MODEL_NAME
    revision: str = MODEL_REVISION
    points_per_crop: int = 32   # grid is points_per_crop x points_per_crop
    points_per_batch: int = 64
    pred_iou_thresh: float = 0.88
    stability_score_thresh: float = 0.92
    crops_n_layers: int = 0
    crops_nms_thresh: float = 0.7

    def cache_key(self) -> str:
        blob = json.dumps(self.__dict__, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def pick_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


@dataclass
class RawMask:
    """One raw SAM mask, in full-image resolution."""

    mask: np.ndarray  # bool (H, W)
    score: float
    area: int
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 (inclusive-exclusive)


class SamMaskGenerator:
    """Wraps the transformers mask-generation pipeline; one instance per run."""

    def __init__(self, params: SamParams | None = None, device: str | None = None):
        self.params = params or SamParams()
        self.device = device or pick_device()
        self._pipe = None

    def _pipeline(self):
        if self._pipe is None:
            import torch
            from transformers import pipeline

            torch.manual_seed(0)
            self._pipe = pipeline(
                "mask-generation",
                model=self.params.model_name,
                revision=self.params.revision,
                device=self.device,
            )
        return self._pipe

    def generate(self, image: np.ndarray) -> list[RawMask]:
        """Run SAM over the whole image; returns masks sorted deterministically."""
        from PIL import Image

        pil = Image.fromarray(image)
        out = self._pipeline()(
            pil,
            points_per_crop=self.params.points_per_crop,
            points_per_batch=self.params.points_per_batch,
            pred_iou_thresh=self.params.pred_iou_thresh,
            stability_score_thresh=self.params.stability_score_thresh,
            crops_n_layers=self.params.crops_n_layers,
            crops_nms_thresh=self.params.crops_nms_thresh,
        )
        masks = [np.asarray(m, dtype=bool) for m in out["masks"]]
        scores = [float(s) for s in out["scores"]]
        raw = [_to_raw(m, s) for m, s in zip(masks, scores)]
        return _sort_masks(raw)


def _to_raw(mask: np.ndarray, score: float) -> RawMask:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        bbox = (0, 0, 0, 0)
    else:
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return RawMask(mask=mask, score=score, area=int(mask.sum()), bbox=bbox)


def _sort_masks(raw: list[RawMask]) -> list[RawMask]:
    """Deterministic order: area desc, then bbox, then score desc."""
    return sorted(raw, key=lambda r: (-r.area, r.bbox, -r.score))


def cached_generate(
    image: np.ndarray,
    cache_path: pathlib.Path,
    params: SamParams | None = None,
    device: str | None = None,
) -> list[RawMask]:
    """Like SamMaskGenerator.generate but memoized to an .npz file."""
    params = params or SamParams()
    key = params.cache_key()
    if cache_path.is_file():
        with np.load(cache_path, allow_pickle=False) as z:
            if str(z["key"]) == key:
                masks = np.unpackbits(
                    z["packed"], count=int(np.prod(z["shape"]))
                ).reshape(z["shape"]).astype(bool)
                raw = [_to_raw(m, float(s)) for m, s in zip(masks, z["scores"])]
                return _sort_masks(raw)
    raw = SamMaskGenerator(params, device).generate(image)
    if raw:
        stack = np.stack([r.mask for r in raw])
    else:
        stack = np.zeros((0, *image.shape[:2]), dtype=bool)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache_path,
        key=np.str_(key),
        shape=np.array(stack.shape),
        packed=np.packbits(stack),
        scores=np.array([r.score for r in raw], dtype=np.float64),
    )
    return raw

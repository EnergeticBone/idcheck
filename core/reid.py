# core/reid.py
"""Visual Re-Identification (Re-ID) engine for idcheck.

Provides:
- Lightweight deep feature extraction via ONNXRuntime (OSNet / MobileNetV2).
- Spatial-pyramid HSV color histogram fallback when deep model is unavailable.
- Cosine similarity computation between object bounding box crops.
- Visual verification and filtering for ID Switch candidates.
- Visual scoring and enhancement for Track Merge / Fragment candidates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "reid_osnet_x0_25.onnx"

# Global singleton session to avoid reloading model weights on each call
_EXTRACTOR_INSTANCE: Optional["ReIDExtractor"] = None


class ReIDExtractor:
    """Extracts compact appearance embeddings from bounding box crops."""

    def __init__(self, model_path: Optional[Union[str, Path]] = None):
        self.model_path = Path(model_path) if model_path else DEFAULT_MODEL_PATH
        self.session = None
        self.input_name = None
        self.input_shape = (256, 128)  # Height, Width for OSNet
        self.use_onnx = False

        self._init_model()

    def _init_model(self):
        """Attempts to load ONNX model via onnxruntime."""
        if self.model_path and self.model_path.is_file():
            try:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self.session = ort.InferenceSession(
                    str(self.model_path),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"],
                )
                self.input_name = self.session.get_inputs()[0].name
                self.use_onnx = True
                logger.info("Loaded Re-ID ONNX model from %s", self.model_path)
            except Exception as e:
                logger.warning("Could not load Re-ID ONNX model (%s). Using color-spatial descriptor fallback.", e)
                self.session = None
                self.use_onnx = False
        else:
            logger.debug("Re-ID ONNX model file not found at %s. Using color-spatial descriptor.", self.model_path)

    @staticmethod
    def crop_box(
        image: np.ndarray,
        box: Dict,
        margin_ratio: float = 0.05,
        min_w: float = 20.0,
        min_h: float = 20.0,
        min_area: float = 380.0,
    ) -> Optional[np.ndarray]:
        """Extract a bounding box crop from a frame image with safe margin & boundary check.
        Filters out distant micro-boxes (below min dimensions) to prevent Re-ID noise.
        """
        if image is None or image.size == 0:
            return None
        H, W = image.shape[:2]

        if isinstance(box, dict):
            x = float(box.get("x", box.get("x1", 0)))
            y = float(box.get("y", box.get("y1", 0)))
            w = float(box.get("w", float(box.get("x2", 0)) - x if "x2" in box else 0))
            h = float(box.get("h", float(box.get("y2", 0)) - y if "y2" in box else 0))
        else:
            x = float(getattr(box, "x1", 0))
            y = float(getattr(box, "y1", 0))
            w = float(getattr(box, "x2", 0)) - x
            h = float(getattr(box, "y2", 0)) - y

        # Re-ID Size Gating Filter: reject micro-crops that cause interpolation noise
        if w < min_w or h < min_h or (w * h) < min_area:
            return None

        # Add optional margin
        mx = w * margin_ratio
        my = h * margin_ratio

        x1 = max(0, int(round(x - mx)))
        y1 = max(0, int(round(y - my)))
        x2 = min(W, int(round(x + w + mx)))
        y2 = min(H, int(round(y + h + my)))

        if x2 <= x1 or y2 <= y1:
            return None

        crop = image[y1:y2, x1:x2]
        return crop if crop.size > 0 else None

    def _extract_color_pyramid(self, crop: np.ndarray) -> np.ndarray:
        """3-level spatial pyramid HSV color histogram.
        
        Splits crop into top (30%), middle (40%), bottom (30%) to capture
        roof/head, body/torso, wheels/lower parts distinctively.
        """
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        H, W = hsv.shape[:2]

        h1 = max(1, int(H * 0.3))
        h2 = max(h1 + 1, int(H * 0.7))

        parts = [
            hsv[0:h1, :],
            hsv[h1:h2, :],
            hsv[h2:, :],
        ]

        feats = []
        for p in parts:
            if p.size == 0:
                continue
            hist = cv2.calcHist([p], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            feats.append(hist.flatten())

        if not feats:
            return np.zeros(16 * 8 * 3, dtype=np.float32)

        feat_vec = np.concatenate(feats).astype(np.float32)
        norm = np.linalg.norm(feat_vec)
        return feat_vec / (norm + 1e-6)

    def extract_embedding(self, crop: np.ndarray) -> np.ndarray:
        """Extract a single normalized appearance embedding vector."""
        return self.extract_batch_embeddings([crop])[0]

    def extract_batch_embeddings(self, crops: List[np.ndarray]) -> np.ndarray:
        """Extract normalized embeddings for a list of crops.
        
        Returns:
            np.ndarray of shape (N, D) where D is embedding dimension (512 or color hist dim).
        """
        if not crops:
            return np.empty((0, 512), dtype=np.float32)

        if not self.use_onnx or self.session is None:
            # Fallback to color-spatial descriptor
            return np.stack([self._extract_color_pyramid(c) for c in crops])

        # Preprocess for OSNet: resize to (128, 256) (W, H), normalize with ImageNet stats
        H_target, W_target = self.input_shape
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)

        batch_size = 16  # Fixed batch dimension in OSNet ONNX
        n = len(crops)
        all_embeddings = []

        for start_idx in range(0, n, batch_size):
            chunk = crops[start_idx : start_idx + batch_size]
            chunk_len = len(chunk)

            padded_batch = np.zeros((batch_size, 3, H_target, W_target), dtype=np.float32)
            for i, c in enumerate(chunk):
                resized = cv2.resize(c, (W_target, H_target))
                rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).transpose(2, 0, 1).astype(np.float32) / 255.0
                padded_batch[i] = (rgb - mean) / std

            try:
                outputs = self.session.run(None, {self.input_name: padded_batch})[0]
                feats = outputs[:chunk_len]
                norms = np.linalg.norm(feats, axis=1, keepdims=True) + 1e-6
                all_embeddings.append(feats / norms)
            except Exception as e:
                logger.error("ONNX inference failed: %s. Falling back to color descriptor.", e)
                fallback_feats = np.stack([self._extract_color_pyramid(c) for c in chunk])
                all_embeddings.append(fallback_feats)

        return np.concatenate(all_embeddings, axis=0) if all_embeddings else np.empty((0, 512), dtype=np.float32)

    @staticmethod
    def cosine_similarity(feat_a: np.ndarray, feat_b: np.ndarray) -> float:
        """Compute cosine similarity between two feature vectors, bounded in [-1.0, 1.0]."""
        dot = float(np.dot(feat_a, feat_b))
        norm_a = float(np.linalg.norm(feat_a))
        norm_b = float(np.linalg.norm(feat_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def get_reid_extractor() -> ReIDExtractor:
    """Singleton getter for ReIDExtractor."""
    global _EXTRACTOR_INSTANCE
    if _EXTRACTOR_INSTANCE is None:
        _EXTRACTOR_INSTANCE = ReIDExtractor()
    return _EXTRACTOR_INSTANCE


def compare_crops(crop_a: Optional[np.ndarray], crop_b: Optional[np.ndarray]) -> float:
    """Compare two image crops and return visual similarity (0.0 to 1.0)."""
    if crop_a is None or crop_b is None or crop_a.size == 0 or crop_b.size == 0:
        return 0.0
    extractor = get_reid_extractor()
    feats = extractor.extract_batch_embeddings([crop_a, crop_b])
    sim = extractor.cosine_similarity(feats[0], feats[1])
    return max(0.0, sim)


def verify_switch_candidate(
    frame_getter: Callable[[int], Optional[np.ndarray]],
    track_a_prev_box: Dict,
    track_a_curr_box: Dict,
    track_b_prev_box: Dict,
    track_b_curr_box: Dict,
    f_prev: int,
    f_curr: int,
    margin_threshold: float = 0.08,
) -> Tuple[bool, float, float]:
    """Visually check if a candidate ID switch between track A and B actually occurred.

    Compares:
      keep_score = 0.5 * (sim(A_prev, A_curr) + sim(B_prev, B_curr))
      swap_score = 0.5 * (sim(A_prev, B_curr) + sim(B_prev, A_curr))

    Returns:
      (is_confirmed_switch, swap_score, keep_score)
      - If swap_score >= keep_score + margin: confirmed switch.
      - If keep_score >= swap_score + margin: rejected false alarm.
      - Otherwise: ambiguous / keep rule decision.
    """
    img_prev = frame_getter(f_prev)
    img_curr = frame_getter(f_curr)

    if img_prev is None or img_curr is None:
        # Cannot verify visually; return neutral
        return True, 0.5, 0.5

    extractor = get_reid_extractor()

    cA_prev = extractor.crop_box(img_prev, track_a_prev_box)
    cB_prev = extractor.crop_box(img_prev, track_b_prev_box)
    cA_curr = extractor.crop_box(img_curr, track_a_curr_box)
    cB_curr = extractor.crop_box(img_curr, track_b_curr_box)

    crops = [cA_prev, cB_prev, cA_curr, cB_curr]
    if any(c is None for c in crops):
        return True, 0.5, 0.5

    feats = extractor.extract_batch_embeddings(crops)
    fA_prev, fB_prev, fA_curr, fB_curr = feats[0], feats[1], feats[2], feats[3]

    sim_AA = extractor.cosine_similarity(fA_prev, fA_curr)
    sim_BB = extractor.cosine_similarity(fB_prev, fB_curr)
    sim_AB = extractor.cosine_similarity(fA_prev, fB_curr)
    sim_BA = extractor.cosine_similarity(fB_prev, fA_curr)

    keep_score = (sim_AA + sim_BB) / 2.0
    swap_score = (sim_AB + sim_BA) / 2.0

    if swap_score >= keep_score + margin_threshold:
        return True, swap_score, keep_score
    if keep_score >= swap_score + margin_threshold:
        return False, swap_score, keep_score

    # Ambiguous
    return True, swap_score, keep_score


def compute_merge_visual_similarity(
    frame_getter: Callable[[int], Optional[np.ndarray]],
    track_a_end_box: Dict,
    track_b_start_box: Dict,
    f_end: int,
    f_start: int,
) -> float:
    """Compute visual similarity between end of track A and start of track B."""
    img_a = frame_getter(f_end)
    img_b = frame_getter(f_start)
    if img_a is None or img_b is None:
        return 0.5

    extractor = get_reid_extractor()
    cA = extractor.crop_box(img_a, track_a_end_box)
    cB = extractor.crop_box(img_b, track_b_start_box)

    if cA is None or cB is None:
        return 0.5

    return compare_crops(cA, cB)


def reid_filter_and_enhance_issues(
    issues: List[Dict],
    tracks: Dict[int, Any],
    frame_getter: Optional[Callable[[int], Optional[np.ndarray]]],
    margin_threshold: float = 0.05,
) -> List[Dict]:
    """Filter false ID switches and enhance issues with Re-ID visual appearance analysis."""
    if not frame_getter or not issues:
        return issues

    cleaned = []
    for iss in issues:
        t = iss.get("type")
        fdata = iss.get("fix_data") or iss.get("data") or {}

        # Case 1: ID Switch Candidate
        if t == "ID Switch Candidate":
            ta = fdata.get("a") or iss.get("track_id")
            tb = fdata.get("b") or fdata.get("target") or fdata.get("other_id")
            f = iss.get("frame", 0)

            if ta in tracks and tb in tracks and f in tracks[ta].boxes and f in tracks[tb].boxes:
                fa = max([k for k in tracks[ta].boxes if k < f], default=None)
                fb = max([k for k in tracks[tb].boxes if k < f], default=None)

                if fa is not None and fb is not None and fa == fb:
                    pa = tracks[ta].boxes[fa]
                    ca = tracks[ta].boxes[f]
                    pb = tracks[tb].boxes[fb]
                    cb = tracks[tb].boxes[f]

                    try:
                        confirmed, swap_score, keep_score = verify_switch_candidate(
                            frame_getter, pa, ca, pb, cb, fa, f, margin_threshold=margin_threshold
                        )

                        if not confirmed:
                            # False positive switch rejected by visual appearance!
                            logger.info(
                                "Re-ID pruned false switch between track %s and %s at frame %s (keep=%.2f, swap=%.2f)",
                                ta, tb, f, keep_score, swap_score
                            )
                            continue

                        # Confirmed switch: enrich metadata
                        iss_copy = dict(iss)
                        f_copy = dict(fdata)
                        f_copy["reid_confirmed"] = True
                        f_copy["reid_swap_score"] = round(swap_score, 2)
                        f_copy["reid_keep_score"] = round(keep_score, 2)
                        iss_copy["fix_data"] = f_copy
                        iss_copy["data"] = f_copy
                        orig_msg = iss.get("message") or iss.get("detail", "")
                        iss_copy["message"] = f"{orig_msg} · Re-ID: {swap_score*100:.0f}% hoán đổi"
                        cleaned.append(iss_copy)
                        continue
                    except Exception as e:
                        logger.warning("Error verifying switch %s <-> %s: %s", ta, tb, e)

        # Case 2: Gap Frame / Merge Suggestion
        elif t == "Gap Frame" and (
            iss.get("suggestion") == "review"
            or "Gợi ý nối track" in iss.get("message", "")
            or "target" in fdata
        ):
            ta = iss.get("track_id")
            tb = fdata.get("target")
            if (
                ta in tracks
                and tb in tracks
                and tracks[ta].end in tracks[ta].boxes
                and tracks[tb].start in tracks[tb].boxes
            ):
                try:
                    f_end = tracks[ta].end
                    f_start = tracks[tb].start
                    a_box = tracks[ta].boxes[f_end]
                    b_box = tracks[tb].boxes[f_start]
                    vis_sim = compute_merge_visual_similarity(frame_getter, a_box, b_box, f_end, f_start)
                    iss_copy = dict(iss)
                    f_copy = dict(fdata)
                    f_copy["reid_visual_sim"] = round(vis_sim, 2)
                    if vis_sim >= 0.70:
                        f_copy["reid_match"] = True
                        orig_msg = iss.get("message") or iss.get("detail", "")
                        iss_copy["message"] = f"{orig_msg} · Re-ID: {vis_sim*100:.0f}% tương đồng"
                    elif vis_sim < 0.35:
                        # Visual mismatch: prune false merge
                        logger.info("Re-ID pruned false merge %s -> %s (sim=%.2f)", ta, tb, vis_sim)
                        continue
                    iss_copy["fix_data"] = f_copy
                    iss_copy["data"] = f_copy
                    cleaned.append(iss_copy)
                    continue
                except Exception as e:
                    logger.warning("Error computing visual similarity for merge %s -> %s: %s", ta, tb, e)

        cleaned.append(iss)

    return cleaned


def find_additional_visual_merges(
    tracks: Dict[int, Any],
    frame_getter: Optional[Callable[[int], Optional[np.ndarray]]],
    cfg: Dict,
    existing_pairs: Optional[set] = None,
    max_gap: int = 8,
    min_visual_sim: float = 0.75,
) -> List[Dict]:
    """Scan for fragmented tracks that pure geometry missed but Re-ID visual appearance strongly matches."""
    if not frame_getter:
        return []

    from core.rules import center, size, hypot

    existing = set(existing_pairs) if existing_pairs else set()
    new_merges = []

    ends = [(tid, tr.end, tr.boxes[tr.end], tr.label) for tid, tr in tracks.items() if tr.boxes]
    starts = [(tid, tr.start, tr.boxes[tr.start], tr.label) for tid, tr in tracks.items() if tr.boxes]

    for ta, f_end, box_a, lbl_a in ends:
        vis_a = sum(1 for x in tracks[ta].boxes.values() if not x.outside)
        if vis_a < 3:
            continue
        for tb, f_start, box_b, lbl_b in starts:
            if ta == tb or lbl_a != lbl_b:
                continue
            gap = f_start - f_end
            if not (1 <= gap <= max_gap):
                continue
            pair_key = f"{ta}_{tb}"
            if pair_key in existing:
                continue

            ca, cb = center(box_a), center(box_b)
            sz = (size(box_a) + size(box_b)) / 2.0
            dist = hypot(ca[0] - cb[0], ca[1] - cb[1]) / (sz + 1e-6)
            if dist > 1.8:
                continue

            try:
                vis_sim = compute_merge_visual_similarity(frame_getter, box_a, box_b, f_end, f_start)
                if vis_sim >= min_visual_sim:
                    score = round(vis_sim, 2)
                    new_merges.append(dict(
                        id=f"Gap Frame:{f_end}:{ta}:{pair_key}",
                        type="Gap Frame",
                        frame=f_end,
                        track_id=ta,
                        message=f"Gợi ý nối track {ta} ➜ {tb} (gap {gap} frames, Re-ID {score*100:.0f}%)",
                        severity="medium",
                        suggestion="review",
                        fix_data={
                            "pair": pair_key,
                            "target": tb,
                            "gap": gap,
                            "reid_visual_sim": score,
                            "reid_match": True,
                        },
                    ))
                    existing.add(pair_key)
            except Exception:
                continue

    return new_merges


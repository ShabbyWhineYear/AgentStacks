#!/usr/bin/env python3
"""Multimodal Source Preprocessor & Chunker for `/study`.

Handles PDFs, Videos (.mp4/.mov/.mkv/.webm), Audio (.mp3/.wav/.flac), and Image/Markdown folders.
- Ensures raw material is archived immutably under `raw/<tier>/<domain>/`.
- Splits large videos/audio into manageable time segments (and extracts keyframes) via `ffmpeg` if available.
- Splits large PDFs into page-batch units so the multimodal model never hits the 100MB binary cap or context dilution.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import os
from wiki_core import find_repo_root, now_iso, sha256_bytes


def _ensure_ffmpeg_path() -> None:
    user_bin = Path.home() / "Library" / "Python" / f"{sys.version_info.major}.{sys.version_info.minor}" / "bin"
    if user_bin.is_dir() and str(user_bin) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{user_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    try:
        import static_ffmpeg  # type: ignore

        static_ffmpeg.add_paths()
    except Exception:
        pass


_ensure_ffmpeg_path()


def has_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def probe_media_duration(path: Path) -> float | None:
    if not has_cmd("ffprobe"):
        return None
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if res.returncode == 0 and res.stdout.strip():
        try:
            return float(res.stdout.strip())
        except ValueError:
            return None
    return None


def chunk_video_or_audio(
    src_path: Path,
    raw_rel_uri: str,
    chunk_dir: Path,
    segment_seconds: int = 600,
) -> list[dict[str, Any]]:
    duration = probe_media_duration(src_path)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    units: list[dict[str, Any]] = []

    if duration is None or duration <= segment_seconds or not has_cmd("ffmpeg"):
        units.append(
            {
                "unit_id": "unit_01",
                "title": f"{src_path.stem} (Full Media)",
                "media_path": src_path.as_posix(),
                "evidence_uri": raw_rel_uri,
                "status": "pending",
            }
        )
        return units

    num_segments = math.ceil(duration / segment_seconds)
    ext = src_path.suffix
    for i in range(num_segments):
        start_s = i * segment_seconds
        end_s = min(int(duration), (i + 1) * segment_seconds)
        start_mmss = f"{start_s // 60:02d}:{start_s % 60:02d}"
        end_mmss = f"{end_s // 60:02d}:{end_s % 60:02d}"
        out_clip = chunk_dir / f"seg_{i + 1:02d}{ext}"

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(start_s),
                "-to",
                str(end_s),
                "-i",
                str(src_path),
                "-c",
                "copy",
                str(out_clip),
            ],
            capture_output=True,
        )
        units.append(
            {
                "unit_id": f"unit_{i + 1:02d}",
                "title": f"{src_path.stem} [{start_mmss}-{end_mmss}]",
                "media_path": out_clip.as_posix() if out_clip.exists() else src_path.as_posix(),
                "evidence_uri": f"{raw_rel_uri}#t={start_mmss}-{end_mmss}",
                "status": "pending",
            }
        )
    return units


def prepare_source_units(
    repo_root: Path,
    source_path: Path,
    tier: str,
    domain: str,
    pages_per_unit: int = 15,
    segment_seconds: int = 600,
) -> dict[str, Any]:
    """Archive source into `raw/<tier>/<domain>/` and generate study units."""
    source_path = source_path.resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    dest_dir = repo_root / "raw" / tier / domain
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source_path.name
    if source_path != dest_path and not dest_path.exists():
        shutil.copy2(source_path, dest_path)

    raw_rel_uri = f"raw://{tier}/{domain}/{dest_path.name}"
    chunk_dir = repo_root / "raw" / "_chunks" / f"{tier}_{domain}_{dest_path.stem}"
    suffix = dest_path.suffix.lower()

    units: list[dict[str, Any]] = []
    if suffix in (".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".flac", ".m4a"):
        units = chunk_video_or_audio(dest_path, raw_rel_uri, chunk_dir, segment_seconds=segment_seconds)
    elif suffix == ".pdf":
        # Check page count via pdfinfo if available
        total_pages = None
        if has_cmd("pdfinfo"):
            res = subprocess.run(["pdfinfo", str(dest_path)], capture_output=True, text=True)
            for ln in res.stdout.splitlines():
                if ln.startswith("Pages:"):
                    try:
                        total_pages = int(ln.split(":", 1)[1].strip())
                    except ValueError:
                        pass
        if total_pages and total_pages > pages_per_unit:
            num_batches = math.ceil(total_pages / pages_per_unit)
            for b in range(num_batches):
                p_start = b * pages_per_unit + 1
                p_end = min(total_pages, (b + 1) * pages_per_unit)
                units.append(
                    {
                        "unit_id": f"unit_{b + 1:02d}",
                        "title": f"{dest_path.stem} (Pages {p_start}-{p_end})",
                        "media_path": dest_path.as_posix(),
                        "page_range": [p_start, p_end],
                        "evidence_uri": f"{raw_rel_uri}#page={p_start}-{p_end}",
                        "status": "pending",
                    }
                )
        else:
            units.append(
                {
                    "unit_id": "unit_01",
                    "title": f"{dest_path.stem} (Full Document)",
                    "media_path": dest_path.as_posix(),
                    "evidence_uri": raw_rel_uri,
                    "status": "pending",
                }
            )
    else:
        units.append(
            {
                "unit_id": "unit_01",
                "title": dest_path.name,
                "media_path": dest_path.as_posix(),
                "evidence_uri": raw_rel_uri,
                "status": "pending",
            }
        )

    return {
        "schemaVersion": 1,
        "createdAt": now_iso(),
        "tier": tier,
        "domain": domain,
        "sourcePath": dest_path.relative_to(repo_root).as_posix(),
        "sourceUri": raw_rel_uri,
        "units": units,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare and chunk multimodal source material for /study.")
    parser.add_argument("source", type=Path, help="Path to PDF, video, audio, or document")
    parser.add_argument("--tier", choices=["foundations", "tools_craft", "workflows"], required=True)
    parser.add_argument("--domain", type=str, required=True, help="Target domain/tool directory name")
    parser.add_argument("--pages-per-unit", type=int, default=15)
    parser.add_argument("--segment-seconds", type=int, default=600)
    args = parser.parse_args()

    repo_root = find_repo_root()
    plan = prepare_source_units(
        repo_root,
        args.source,
        args.tier,
        args.domain,
        pages_per_unit=args.pages_per_unit,
        segment_seconds=args.segment_seconds,
    )
    run_file = repo_root / "wiki" / ".study-run.json"
    run_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✅ Prepared {len(plan['units'])} study unit(s) for {args.tier}/{args.domain} -> wiki/.study-run.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

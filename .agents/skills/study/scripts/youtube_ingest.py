#!/usr/bin/env python3
"""YouTube Authoritative Course Searcher & Multimodal Ingester (`youtube_ingest.py`).

Uses `yt-dlp` (with `--cookies-from-browser chrome` support and graceful fallback)
and `ffmpeg` / `static-ffmpeg` to:
1. `search`: Search YouTube for authoritative lectures/tutorials and display metadata.
2. `info`: Inspect a YouTube video or playlist (chapters, duration, subtitles, items).
3. `download`: Download video (<=720p MP4) + audio + bilingual subtitles into
   `raw/<tier>/<domain>/<slug>/` and automatically slice via `media_chunker.py`
   so the multimodal model can directly watch the score/DAW and listen to the audio.
4. `roadmap`: Display the 9-stage music composition & production curriculum queries.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from wiki_core import find_repo_root


MUSIC_9_STAGE_ROADMAP: list[dict[str, Any]] = [
    {
        "stage": 1,
        "name": "基本乐理 (Fundamental Music Theory)",
        "tier": "foundations",
        "domain": "music_theory_acoustics",
        "recommended_queries": [
            "Dr. B Music Theory Lesson 1 Pitch and Rhythm",
            "Seth Monahan Music Theory Fundamentals Scales Intervals",
        ],
        "focus": "音高、音程、自然/和声/旋律大小调、教会调式、复合节拍与律动网格",
    },
    {
        "stage": 2,
        "name": "和声学 (Classical, Jazz/Pop & Film Harmony)",
        "tier": "foundations",
        "domain": "music_theory_acoustics",
        "recommended_queries": [
            "Seth Monahan Classical Harmony Voice Leading Tonic Dominant",
            "Alan Belkin Modern Harmony Lecture",
            "Ryan Leach Film Scoring Chromatic Mediants Neo-Riemannian Harmony",
        ],
        "focus": "四部功能和声、离调与转调、爵士/流行延伸和声(9/11/13)、电影色彩非功能和声(Neo-Riemannian)",
    },
    {
        "stage": 3,
        "name": "复调音乐与对位法 (Polyphony & Counterpoint)",
        "tier": "foundations",
        "domain": "music_theory_acoustics",
        "recommended_queries": [
            "Alan Belkin Applied Counterpoint Lesson 1",
            "Jacob Gran Species Counterpoint First Second Third Species",
        ],
        "focus": "一至五类分类对位、模仿复调、不协和音预备与解决、流行副歌副旋律(Counter-melody)与弦乐内声部流动",
    },
    {
        "stage": 4,
        "name": "曲式与作品分析 (Form & Musical Analysis)",
        "tier": "foundations",
        "domain": "music_theory_acoustics",
        "recommended_queries": [
            "Alan Belkin Musical Composition Craft and Form",
            "Seth Monahan Classical Form Sentence Period Sonata",
            "Ryan Leach How to Structure a Film Cue Leitmotif Development",
        ],
        "focus": "乐句与乐段(Sentence/Period)、流行曲式能量曲线(Verse-PreChorus-Chorus-Bridge)、电影动机发展与戏剧打点",
    },
    {
        "stage": 5,
        "name": "配器法 (Orchestration & Acoustic Balance)",
        "tier": "foundations",
        "domain": "music_theory_acoustics",
        "recommended_queries": [
            "Orchestration Online Thomas Goss Principles of Orchestration",
            "Alan Belkin Artistic Orchestration Course",
        ],
        "focus": "管弦乐四大声部音域与最佳音区(Tessitura)、演奏技法、泛音列与频段掩蔽(Masking)、声部叠置(Doubling)",
    },
    {
        "stage": 6,
        "name": "编曲与作曲技术 (Pop Arranging & Cinematic Composition)",
        "tier": "tools_craft",
        "domain": "daw_music_prod",
        "recommended_queries": [
            "Alex Moukala Orchestral Arranging Voicing and Texture",
            "Berklee Online Pop Music Arranging Groove Rhythm Section",
            "Christian Henson Film Scoring Ostinato and Texture",
        ],
        "focus": "流行四大件 Groove 咬合、Hook 层次设计、Hybrid Orchestral 管弦与合成器低频融合",
    },
    {
        "stage": 7,
        "name": "计算机音乐制作与 MIDI 编曲 (MIDI Mockup & Expression)",
        "tier": "tools_craft",
        "domain": "daw_music_prod",
        "recommended_queries": [
            "Guy Michelmore How to Write Realistic Strings MIDI CC1 CC11",
            "Anne-Kathrin Dern MIDI Mockup Orchestral Programming Tutorial",
            "Spitfire Audio Realistic Orchestral Programming Expression Modulation",
        ],
        "focus": "CC1(Modulation)/CC11(Expression)双包络呼吸曲线、Legato 负延迟补偿、Keyswitch 切换、微时值偏移(Humanization)",
    },
    {
        "stage": 8,
        "name": "混音工程与录音艺术 (Mixing Engineering & Spatial Audio)",
        "tier": "tools_craft",
        "domain": "daw_music_prod",
        "recommended_queries": [
            "Dan Worrall FabFilter EQ Compression Mixing Tutorial",
            "iZotope Are You Listening Mixing Series",
        ],
        "focus": "增益架构(Gain Staging)、减法/动态 EQ 避让、压缩器启动/释放时间计算、混响 Pre-delay 与三维景深塑造",
    },
    {
        "stage": 9,
        "name": "母带工程 (Mastering Engineering & Loudness Standards)",
        "tier": "tools_craft",
        "domain": "daw_music_prod",
        "recommended_queries": [
            "iZotope Are You Listening Mastering Full Course",
            "Dan Worrall Loudness LUFS True Peak Mastering",
        ],
        "focus": "流媒体(-14 LUFS)与影视(-23 LUFS)响度标准、True Peak(-1.0 dBTP)限制、M/S 立体声低频收窄与动态均衡",
    },
]


def ensure_media_binaries_on_path() -> dict[str, str | None]:
    """Ensure ~/.local/bin (standalone yt-dlp_macos), user Python bin dir, and static_ffmpeg are on PATH."""
    extra_dirs = [
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path.home() / "Library" / "Python" / f"{sys.version_info.major}.{sys.version_info.minor}" / "bin",
        Path.home() / ".local" / "bin",
    ]
    cur_path = os.environ.get("PATH", "")
    path_parts = cur_path.split(os.pathsep) if cur_path else []
    for d in extra_dirs:
        if d.is_dir():
            if str(d) in path_parts:
                path_parts.remove(str(d))
            path_parts.insert(0, str(d))
    os.environ["PATH"] = os.pathsep.join(path_parts)
    os.environ.setdefault("PYTHONWARNINGS", "ignore")

    # Activate static_ffmpeg if installed
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import static_ffmpeg  # type: ignore

        static_ffmpeg.add_paths()
    except Exception:
        pass

    return {
        "yt-dlp": shutil.which("yt-dlp"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
    }


def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", s)
    s = re.sub(r"[\s_-]+", "_", s).strip("_")
    return s[:64] or "youtube_media"


def run_yt_dlp_json(args_list: list[str], browser_cookie: str | None = "auto") -> tuple[int, str, str]:
    """Run yt-dlp:
    - 'auto': try fast cookieless first; automatically escalate to '--cookies-from-browser chrome' on auth/bot error.
    - 'chrome' (or other browser): use '--cookies-from-browser <browser>' immediately with fallback.
    - None / 'none': cookieless only.
    """
    bins = ensure_media_binaries_on_path()
    yt_dlp_bin = bins["yt-dlp"]
    if not yt_dlp_bin:
        return 1, "", "yt-dlp binary not found on PATH."

    if browser_cookie and browser_cookie != "auto":
        cmd = [yt_dlp_bin, "--cookies-from-browser", browser_cookie] + args_list
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return 0, res.stdout, res.stderr
        print(
            f"ℹ️  Note: '--cookies-from-browser {browser_cookie}' returned non-zero; falling back to public query...",
            file=sys.stderr,
        )

    cmd_public = [yt_dlp_bin] + args_list
    res_pub = subprocess.run(cmd_public, capture_output=True, text=True)
    if res_pub.returncode == 0 and res_pub.stdout.strip():
        return 0, res_pub.stdout, res_pub.stderr

    if browser_cookie == "auto":
        print("🔐 Public query requires authentication; escalating to local Chrome cookies...", file=sys.stderr)
        cmd_chrome = [yt_dlp_bin, "--cookies-from-browser", "chrome"] + args_list
        res_ch = subprocess.run(cmd_chrome, capture_output=True, text=True)
        return res_ch.returncode, res_ch.stdout, res_ch.stderr

    return res_pub.returncode, res_pub.stdout, res_pub.stderr


def cmd_search(query: str, limit: int = 8, browser_cookie: str | None = "chrome") -> int:
    search_spec = f"ytsearch{limit}:{query}"
    rc, out, err = run_yt_dlp_json(
        ["--dump-json", "--flat-playlist", "--no-warnings", search_spec],
        browser_cookie=browser_cookie,
    )
    if rc != 0 and not out.strip():
        print(f"❌ YouTube search failed:\n{err}", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            results.append(
                {
                    "id": data.get("id"),
                    "title": data.get("title"),
                    "url": data.get("url") or f"https://www.youtube.com/watch?v={data.get('id')}",
                    "channel": data.get("channel") or data.get("uploader"),
                    "duration_sec": data.get("duration"),
                    "view_count": data.get("view_count"),
                }
            )
        except Exception:
            continue

    print(f"=== YouTube Search Results for '{query}' ({len(results)} found) ===")
    for idx, r in enumerate(results, 1):
        dur = f"{int(r['duration_sec']) // 60}m{int(r['duration_sec']) % 60:02d}s" if r.get("duration_sec") else "N/A"
        views = f"{r['view_count']:,}" if isinstance(r.get("view_count"), int) else "N/A"
        print(f"{idx}. {r['title']}")
        print(f"   Channel: {r['channel']} | Duration: {dur} | Views: {views}")
        print(f"   URL: {r['url']}")
    return 0


def cmd_info(url: str, browser_cookie: str | None = "chrome") -> int:
    rc, out, err = run_yt_dlp_json(
        ["--dump-single-json", "--flat-playlist", "--no-warnings", url],
        browser_cookie=browser_cookie,
    )
    if rc != 0 or not out.strip():
        print(f"❌ Failed to fetch info for {url}:\n{err}", file=sys.stderr)
        return 1

    data = json.loads(out)
    info_summary = {
        "id": data.get("id"),
        "title": data.get("title"),
        "uploader": data.get("uploader") or data.get("channel"),
        "duration_sec": data.get("duration"),
        "chapters": data.get("chapters") or [],
        "subtitles": list((data.get("subtitles") or {}).keys()),
        "automatic_captions": [k for k in (data.get("automatic_captions") or {}).keys() if k.startswith(("en", "zh"))],
        "playlist_count": data.get("playlist_count"),
        "entries_preview": [
            {"index": i + 1, "id": e.get("id"), "title": e.get("title"), "duration": e.get("duration")}
            for i, e in enumerate((data.get("entries") or [])[:15])
        ],
    }
    print(json.dumps(info_summary, ensure_ascii=False, indent=2))
    return 0


def cmd_download(
    repo_root: Path,
    url: str,
    tier: str,
    domain: str,
    slug: str | None = None,
    max_height: int = 720,
    chunk_seconds: int = 240,
    browser_cookie: str | None = "chrome",
    playlist_items: str | None = None,
) -> int:
    bins = ensure_media_binaries_on_path()
    yt_dlp_bin = bins["yt-dlp"]
    ffmpeg_bin = bins["ffmpeg"]
    if not yt_dlp_bin:
        print("❌ yt-dlp not found.", file=sys.stderr)
        return 1

    target_slug = slug or slugify(url.split("=")[-1].split("/")[-1])
    out_dir = repo_root / "raw" / tier / domain / target_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(out_dir / "%(title).80B [%(id)s].%(ext)s")
    fmt_selector = (
        f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[format_id=140]/"
        f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/"
        f"best[height<={max_height}][ext=mp4]/best[height<={max_height}]"
    )

    dl_args = [
        "--format",
        fmt_selector,
        "--merge-output-format",
        "mp4",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en",
        "--ignore-errors",
        "--write-info-json",
        "--no-overwrites",
        "-o",
        output_template,
    ]
    if ffmpeg_bin:
        dl_args.extend(["--ffmpeg-location", str(Path(ffmpeg_bin).parent)])
    if playlist_items:
        dl_args.extend(["--playlist-items", playlist_items])
    else:
        dl_args.append("--no-playlist")

    dl_args.append(url)

    # Always use public stream with deno JS solver unless browser_cookie is explicitly set to chrome/safari/firefox
    dl_succeeded = False
    if browser_cookie and browser_cookie not in ("auto", "none"):
        if Path(browser_cookie).is_file():
            cmd = [yt_dlp_bin, "--cookies", str(Path(browser_cookie).resolve())] + dl_args
            print(f"⬇️  Downloading via cookie file {browser_cookie} into {out_dir.relative_to(repo_root)} ...")
        else:
            cmd = [yt_dlp_bin, "--cookies-from-browser", browser_cookie] + dl_args
            print(f"⬇️  Downloading via {browser_cookie} cookies into {out_dir.relative_to(repo_root)} ...")
        res = subprocess.run(cmd)
        if res.returncode == 0:
            dl_succeeded = True
        else:
            print("⚠️  Cookie download returned non-zero; retrying without browser cookies...")


    if not dl_succeeded:
        cmd_pub = [yt_dlp_bin] + dl_args
        res_pub = subprocess.run(cmd_pub)
        if res_pub.returncode == 0:
            dl_succeeded = True
        else:
            print("❌ Download failed.", file=sys.stderr)
            return res_pub.returncode

    # Automatically slice downloaded mp4 files via media_chunker.py and generate wiki/.study-run.json
    mp4_files = sorted(p for p in out_dir.glob("*.mp4") if not p.name.startswith("unit_"))
    chunker_script = repo_root / ".agents" / "skills" / "study" / "scripts" / "media_chunker.py"
    for mp4 in mp4_files:
        print(f"✂️  Slicing {mp4.name} into {chunk_seconds}s multimodal study units ...")
        subprocess.run(
            [
                sys.executable,
                str(chunker_script),
                str(mp4),
                "--tier",
                tier,
                "--domain",
                domain,
                "--segment-seconds",
                str(chunk_seconds),
            ],
            check=False,
        )

    print(f"✅ Ingest complete! Raw files & multimodal chunks ready in: {out_dir}")
    return 0


def cmd_roadmap() -> int:
    print("=== 9-Stage Music Theory, Composition & Audio Engineering Curriculum ===")
    for item in MUSIC_9_STAGE_ROADMAP:
        print(f"\n[Stage {item['stage']}] {item['name']} -> wiki/{item['tier']}/{item['domain']}")
        print(f"  核心能力: {item['focus']}")
        print(f"  推荐检索词: {', '.join(repr(q) for q in item['recommended_queries'])}")
    return 0


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=None, help="Repository root")
    common.add_argument(
        "--cookies-from",
        type=str,
        default="auto",
        help="Cookie mode: 'auto' (try public first, escalate to Chrome cookies if needed), 'chrome' (force Chrome cookies), or 'none'",
    )

    parser = argparse.ArgumentParser(
        description="Search and ingest authoritative YouTube courses into raw/.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", parents=[common], help="Search YouTube for authoritative tutorials")
    p_search.add_argument("query", type=str, help="Search query string")
    p_search.add_argument("--limit", type=int, default=8, help="Max results")

    p_info = sub.add_parser("info", parents=[common], help="Inspect video or playlist metadata and chapters")
    p_info.add_argument("url", type=str, help="YouTube video or playlist URL")

    p_dl = sub.add_parser("download", parents=[common], help="Download video+audio+subs to raw/ and slice into multimodal chunks")
    p_dl.add_argument("url", type=str, help="YouTube URL")
    p_dl.add_argument("--tier", required=True, choices=["foundations", "tools_craft", "workflows"])
    p_dl.add_argument("--domain", required=True, help="Target domain folder name")
    p_dl.add_argument("--slug", default=None, help="Subdirectory slug under raw/<tier>/<domain>/")
    p_dl.add_argument("--max-height", type=int, default=720, help="Max video height (default: 720)")
    p_dl.add_argument("--chunk-seconds", type=int, default=240, help="Chunk duration in seconds (default: 240)")
    p_dl.add_argument("--playlist-items", default=None, help="E.g. '1-3' if URL is a playlist")

    sub.add_parser("roadmap", parents=[common], help="Show the 9-stage music curriculum & recommended YouTube queries")
    sub.add_parser("doctor", parents=[common], help="Verify yt-dlp, ffmpeg, ffprobe, and Chrome cookie path")

    args = parser.parse_args()
    repo_root = find_repo_root(args.root)
    cookie_browser = None if str(args.cookies_from).lower() in ("none", "false", "") else args.cookies_from

    if args.cmd == "doctor":
        bins = ensure_media_binaries_on_path()
        chrome_dir = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        print(
            json.dumps(
                {
                    "binaries": bins,
                    "chrome_profile_exists": chrome_dir.is_dir(),
                    "chrome_profile_path": str(chrome_dir),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if (bins["yt-dlp"] and bins["ffmpeg"]) else 1

    if args.cmd == "roadmap":
        return cmd_roadmap()
    if args.cmd == "search":
        return cmd_search(args.query, limit=args.limit, browser_cookie=cookie_browser)
    if args.cmd == "info":
        return cmd_info(args.url, browser_cookie=cookie_browser)
    if args.cmd == "download":
        return cmd_download(
            repo_root,
            args.url,
            tier=args.tier,
            domain=args.domain,
            slug=args.slug,
            max_height=args.max_height,
            chunk_seconds=args.chunk_seconds,
            browser_cookie=cookie_browser,
            playlist_items=args.playlist_items,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())

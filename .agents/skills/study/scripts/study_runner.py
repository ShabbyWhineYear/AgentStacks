#!/usr/bin/env python3
"""Resumable `/study` Lifecycle Manager, Curriculum Gap Logger & Atomic Git Transaction Runner.

Combines:
1. OpenWiki's resumable `.study-run.json` job queue (`status`, `next`, `complete-unit`).
2. Active Epistemic Curiosity registration (`add-gap` -> writes `wiki/.gaps.json` and refreshes `CURRICULUM_GAPS.md`).
3. Skill Training's pre-commit lint gate + atomic Git commits & scoped rollback (`commit`, `rollback`).
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from wiki_core import find_repo_root, now_iso
from wiki_finalize import finalize_wiki
from wiki_linter import lint_wiki


def append_wiki_log(repo_root: Path, verb: str, domain: str, summary: str) -> None:
    log_path = repo_root / "wiki" / "log.md"
    today = datetime.date.today().isoformat()
    entry = f"## [{today}] {verb} | {domain} | {summary}\n"
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            text += "\n"
        log_path.write_text(text + entry, encoding="utf-8")
    else:
        log_path.write_text(f"# Wiki Operation & Evolution Log\n\n{entry}", encoding="utf-8")


def cmd_status(repo_root: Path) -> int:
    run_file = repo_root / "wiki" / ".study-run.json"
    if not run_file.exists():
        print("No active .study-run.json session.")
        return 0
    data = json.loads(run_file.read_text(encoding="utf-8"))
    units = data.get("units", [])
    done = sum(1 for u in units if u.get("status") == "completed")
    print(f"Active /study session: {data.get('tier')}/{data.get('domain')} ({data.get('sourceUri')})")
    print(f"Progress: {done}/{len(units)} units completed.")
    for u in units:
        print(f"  - [{u.get('status')}] {u.get('unit_id')}: {u.get('title')} ({u.get('evidence_uri')})")
    return 0


def cmd_next(repo_root: Path) -> int:
    run_file = repo_root / "wiki" / ".study-run.json"
    if not run_file.exists():
        print("No active .study-run.json session.")
        return 1
    data = json.loads(run_file.read_text(encoding="utf-8"))
    for u in data.get("units", []):
        if u.get("status") != "completed":
            print(json.dumps(u, ensure_ascii=False, indent=2))
            return 0
    print("All study units completed! Run `study_runner.py finish` to close the session.")
    return 0


def cmd_complete_unit(repo_root: Path, unit_id: str, summary: str) -> int:
    run_file = repo_root / "wiki" / ".study-run.json"
    if not run_file.exists():
        print("No active .study-run.json session.", file=sys.stderr)
        return 1
    data = json.loads(run_file.read_text(encoding="utf-8"))
    found = False
    for u in data.get("units", []):
        if u.get("unit_id") == unit_id:
            u["status"] = "completed"
            u["completedAt"] = now_iso()
            u["summary"] = summary
            found = True
            break
    if not found:
        print(f"Unit '{unit_id}' not found in .study-run.json", file=sys.stderr)
        return 1

    run_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    all_done = all(u.get("status") == "completed" for u in data.get("units", []))
    if all_done:
        cmd_prune_raw(repo_root)
    return cmd_commit(repo_root, verb="study", domain=f"{data.get('tier')}/{data.get('domain')}", summary=f"{unit_id}: {summary}")


def _get_pending_source_units(repo_root: Path) -> list[tuple[str, list[str]]]:
    """Return list of (source_uri, [pending_unit_ids]) for any source in .source-coverage.json or .study-run.json with unfinished reusable units."""
    pending_sources: list[tuple[str, list[str]]] = []
    cov_file = repo_root / "wiki" / ".source-coverage.json"
    if cov_file.exists():
        try:
            cov_data = json.loads(cov_file.read_text(encoding="utf-8"))
            for s_uri, s_obj in cov_data.get("sources", {}).items():
                pend = [
                    f"{u.get('unit_id')} ({u.get('title', '')})"
                    for u in s_obj.get("units", [])
                    if u.get("reusable", True) and u.get("status") != "completed"
                ]
                if pend:
                    pending_sources.append((s_uri, pend))
        except Exception:
            pass

    run_file = repo_root / "wiki" / ".study-run.json"
    if run_file.exists():
        try:
            r_data = json.loads(run_file.read_text(encoding="utf-8"))
            pend_r = [
                f"{u.get('unit_id')} ({u.get('title', '')})"
                for u in r_data.get("units", [])
                if u.get("status") != "completed"
            ]
            if pend_r:
                s_uri = str(r_data.get("sourceUri", ".study-run.json"))
                if not any(k == s_uri for k, _ in pending_sources):
                    pending_sources.append((s_uri, pend_r))
        except Exception:
            pass
    return pending_sources


def cmd_prune_raw(repo_root: Path, max_size_bytes: int = 1_000_000) -> int:
    """Delete studied video/audio files and _chunks/ in raw/, while PERMANENTLY PRESERVING PDF/EPUB books.
    HARD GATE: Refuses to prune any media while any source has unfinished reusable units!
    """
    import hashlib
    import shutil

    pending_sources = _get_pending_source_units(repo_root)
    if pending_sources:
        print(
            "⛔ [ANTI-PARTIAL-SKIMMING HARD GATE] Refusing to prune raw media/chunks because the following sources still have UNLEARNED reusable chapters/units:",
            file=sys.stderr,
        )
        for s_uri, pend_list in pending_sources:
            print(f"  - {s_uri}: {len(pend_list)} pending units -> {', '.join(pend_list[:5])}", file=sys.stderr)
        print(
            "👉 You MUST study and complete 100% of reusable units (`complete-source-unit`) before pruning raw media!",
            file=sys.stderr,
        )
        return 1

    raw_root = repo_root / "raw"
    if not raw_root.is_dir():
        return 0

    freed_bytes = 0
    preserved_books = 0
    chunks_dir = raw_root / "_chunks"
    if chunks_dir.is_dir():
        for p in chunks_dir.rglob("*"):
            if p.is_file():
                freed_bytes += p.stat().st_size
        shutil.rmtree(chunks_dir, ignore_errors=True)

    media_exts = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4a", ".wav", ".flac", ".mp3", ".ogg"}
    # Books & text references have high re-consultation value and must NEVER be deleted
    protected_book_exts = {".pdf", ".epub", ".djvu", ".mobi", ".azw3", ".txt", ".md", ".vtt", ".srt", ".json"}

    for fpath in sorted(raw_root.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.name.endswith(".receipt.json") or fpath.name == ".DS_Store":
            continue
        suffix = fpath.suffix.lower()
        fsize = fpath.stat().st_size

        if suffix in protected_book_exts:
            if suffix in {".pdf", ".epub", ".djvu", ".mobi", ".azw3"}:
                preserved_books += 1
                receipt_path = fpath.with_name(fpath.name + ".receipt.json")
                if not receipt_path.exists():
                    h = hashlib.sha256(fpath.read_bytes()).hexdigest()
                    receipt_data = {
                        "originalFile": fpath.name,
                        "sizeBytes": fsize,
                        "sha256": f"sha256:{h}",
                        "recordedAt": now_iso(),
                        "note": "Reference textbook preserved on disk for future consultation; fingerprint recorded for Grounded Claims.",
                    }
                    receipt_path.write_text(json.dumps(receipt_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue

        if suffix in media_exts or fsize > max_size_bytes:
            h = hashlib.sha256(fpath.read_bytes()).hexdigest()
            receipt_path = fpath.with_name(fpath.name + ".receipt.json")
            receipt_data = {
                "originalFile": fpath.name,
                "sizeBytes": fsize,
                "sha256": f"sha256:{h}",
                "prunedAt": now_iso(),
                "note": "Heavy raw audio/video media pruned after /study to save disk space; fingerprint preserved for Grounded Claims.",
            }
            receipt_path.write_text(json.dumps(receipt_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            fpath.unlink()
            freed_bytes += fsize

    freed_mb = freed_bytes / (1024 * 1024)
    print(
        f"🧹 Pruned studied raw audio/video & chunks: freed {freed_mb:.2f} MB "
        f"(📚 {preserved_books} PDF/EPUB textbooks permanently preserved on disk)."
    )
    return 0


def cmd_compact_git(repo_root: Path, keep_recent: int = 15) -> int:
    """Keep only the most recent `keep_recent` commits for rollback, squash older history, and run aggressive git gc."""
    cnt_res = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=str(repo_root), capture_output=True, text=True)
    total_commits = int(cnt_res.stdout.strip()) if cnt_res.returncode == 0 and cnt_res.stdout.strip().isdigit() else 0

    if total_commits > keep_recent:
        cutoff_res = subprocess.run(
            ["git", "rev-parse", f"HEAD~{keep_recent - 1}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
        if cutoff_res.returncode == 0 and cutoff_res.stdout.strip():
            cutoff_sha = cutoff_res.stdout.strip()
            # Create a squashed root commit for everything up to cutoff_sha
            tree_sha = subprocess.run(
                ["git", "rev-parse", f"{cutoff_sha}^{{tree}}"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
            ).stdout.strip()
            new_root = subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=LLM-Wiki-Agent",
                    "-c",
                    "user.email=wiki@local",
                    "commit-tree",
                    tree_sha,
                    "-m",
                    f"chore(compact): Squashed historical commits prior to last {keep_recent} operations",
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
            ).stdout.strip()
            if new_root:
                subprocess.run(["git", "replace", "--graft", cutoff_sha, new_root], cwd=str(repo_root), check=False)

    subprocess.run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=str(repo_root), check=False)
    subprocess.run(["git", "gc", "--prune=now", "--aggressive"], cwd=str(repo_root), check=False)
    print(f"🗜️  Git history compacted and garbage-collected (keeping last {keep_recent} commits for rollback).")
    return 0


def cmd_add_gap(
    repo_root: Path,
    target_domain: str,
    topic: str,
    reason: str,
    books: list[str],
    priority: str = "HIGH",
) -> int:
    gaps_json_path = repo_root / "wiki" / ".gaps.json"
    data: dict[str, Any] = {"schemaVersion": 1, "gaps": []}
    if gaps_json_path.exists():
        try:
            data = json.loads(gaps_json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    gaps = data.get("gaps", [])
    # Update existing if same topic, else append
    updated = False
    for g in gaps:
        if g.get("target_domain") == target_domain and g.get("topic") == topic:
            g["reason"] = reason
            g["recommended_textbooks"] = books
            g["priority"] = priority
            g["updatedAt"] = now_iso()
            updated = True
            break
    if not updated:
        gaps.append(
            {
                "id": f"gap_{len(gaps) + 1:02d}",
                "target_domain": target_domain,
                "topic": topic,
                "reason": reason,
                "recommended_textbooks": books,
                "priority": priority,
                "createdAt": now_iso(),
            }
        )
    data["gaps"] = gaps
    gaps_json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finalize_wiki(repo_root, actor="curriculum-gap-engine/1.0")
    print(f"✅ Logged curriculum gap for '{target_domain}' ({topic}) and updated wiki/CURRICULUM_GAPS.md.")
    return 0


def cmd_record_trace(
    repo_root: Path,
    helpful_cards: list[str],
    harmful_cards: list[str],
    helpful_scripts: list[str],
    task_summary: str,
) -> int:
    """Ported from PenguinHarness ACE (`ace-rsi-toolkit`): Track actual rule & script application and gain."""
    ace_path = repo_root / "wiki" / ".ace-telemetry.json"
    data: dict[str, Any] = {"schemaVersion": 1, "cards": {}, "scripts": {}, "recentTraces": []}
    if ace_path.exists():
        try:
            data = json.loads(ace_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    cards = data.setdefault("cards", {})
    scripts = data.setdefault("scripts", {})
    ts = now_iso()

    import math
    for cid in helpful_cards:
        entry = cards.setdefault(
            cid, {"helpful": 0, "harmful": 0, "last_used_at": ts, "last_task": "", "queries": [], "hotness": 0.2}
        )
        entry["helpful"] = int(entry.get("helpful", 0)) + 1
        entry["last_used_at"] = ts
        entry["last_task"] = task_summary
        q_list = entry.setdefault("queries", [])
        if isinstance(q_list, list) and task_summary and task_summary not in q_list:
            q_list.append(task_summary)
            entry["queries"] = q_list[-5:]
        net_h = max(0, int(entry.get("helpful", 0)) - int(entry.get("harmful", 0)))
        entry["hotness"] = round(min(1.0, math.log1p(net_h + 0.5 * len(entry.get("queries", []))) / math.log1p(50.0)), 4)

    for cid in harmful_cards:
        entry = cards.setdefault(
            cid, {"helpful": 0, "harmful": 0, "last_used_at": ts, "last_task": "", "queries": [], "hotness": 0.2}
        )
        entry["harmful"] = int(entry.get("harmful", 0)) + 1
        entry["last_used_at"] = ts
        entry["last_task"] = task_summary
        net_h = max(0, int(entry.get("helpful", 0)) - int(entry.get("harmful", 0)))
        entry["hotness"] = round(min(1.0, math.log1p(net_h + 0.5 * len(entry.get("queries", []))) / math.log1p(50.0)), 4)

    for sname in helpful_scripts:
        clean_name = Path(sname).name
        entry = scripts.setdefault(clean_name, {"helpful": 0, "harmful": 0, "last_used_at": ts, "last_task": ""})
        entry["helpful"] = int(entry.get("helpful", 0)) + 1
        entry["last_used_at"] = ts
        entry["last_task"] = task_summary

    traces = data.setdefault("recentTraces", [])
    traces.append(
        {
            "at": ts,
            "task": task_summary,
            "helpful_cards": helpful_cards,
            "harmful_cards": harmful_cards,
            "helpful_scripts": [Path(s).name for s in helpful_scripts],
        }
    )
    data["recentTraces"] = traces[-40:]
    ace_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finalize_wiki(repo_root, actor="ace-telemetry/1.0")
    print(
        f"🎯 Recorded ACE Trace: +{len(helpful_cards)} helpful cards, +{len(helpful_scripts)} scripts, "
        f"-{len(harmful_cards)} harmful cards ({task_summary})."
    )
    return 0


def cmd_record_gotcha(
    repo_root: Path,
    gotcha_id: str,
    scope: str,
    symptom: str,
    root_cause: str,
    recipe: str,
    helpful: int = 1,
) -> int:
    """Ported from PenguinHarness Continual-Learning & Rules-That-Save-Retries."""
    gotchas_path = repo_root / "wiki" / ".gotchas.json"
    data: dict[str, Any] = {"schemaVersion": 1, "entries": []}
    if gotchas_path.exists():
        try:
            data = json.loads(gotchas_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    entries = data.setdefault("entries", [])
    updated = False
    for e in entries:
        if e.get("id") == gotcha_id:
            e["scope"] = scope
            e["symptom"] = symptom
            e["root_cause"] = root_cause
            e["recipe"] = recipe
            e["helpful"] = int(e.get("helpful", 0)) + helpful
            e["updatedAt"] = now_iso()
            updated = True
            break
    if not updated:
        entries.append(
            {
                "id": gotcha_id,
                "scope": scope,
                "symptom": symptom,
                "root_cause": root_cause,
                "recipe": recipe,
                "helpful": helpful,
                "harmful": 0,
                "createdAt": now_iso(),
            }
        )
    gotchas_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finalize_wiki(repo_root, actor="gotcha-harvester/1.0")
    print(f"🛡️  Recorded retry-saving gotcha '{gotcha_id}' ({scope}) into wiki/GOTCHAS_AND_RETRY_SAVERS.md.")
    return 0


def cmd_record_workflow(
    repo_root: Path,
    wf_id: str,
    title: str,
    applies_when: str,
    params_json: str,
    steps_json: str,
) -> int:
    """Ported from PenguinHarness AWM (`awm-rsi-toolkit`): Induce parameterized sub-routines from successful runs."""
    awm_path = repo_root / "wiki" / ".awm-workflows.json"
    data: dict[str, Any] = {"schemaVersion": 1, "workflows": []}
    if awm_path.exists():
        try:
            data = json.loads(awm_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    params = json.loads(params_json)
    steps = json.loads(steps_json)
    workflows = data.setdefault("workflows", [])
    updated = False
    for wf in workflows:
        if wf.get("id") == wf_id:
            wf["title"] = title
            wf["applies_when"] = applies_when
            wf["parameters"] = params
            wf["steps"] = steps
            wf["helpful"] = int(wf.get("helpful", 0)) + 1
            wf["updatedAt"] = now_iso()
            updated = True
            break
    if not updated:
        workflows.append(
            {
                "id": wf_id,
                "title": title,
                "applies_when": applies_when,
                "parameters": params,
                "steps": steps,
                "helpful": 1,
                "createdAt": now_iso(),
            }
        )
    awm_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finalize_wiki(repo_root, actor="awm-inducer/1.0")
    print(f"🔄 Recorded AWM parameterized workflow '{wf_id}' ({title}) into wiki/AWM_WORKFLOWS.md.")
    return 0


def update_scoreboard_snapshot(repo_root: Path, verb: str, domain: str, summary: str) -> None:
    """Append a versioned RSI evaluation snapshot to wiki/.scoreboard.json before finalization."""
    from wiki_core import discover_wiki_pages

    wiki_root = repo_root / "wiki"
    sb_path = wiki_root / ".scoreboard.json"
    sb_data: dict[str, Any] = {"schemaVersion": 1, "version": 0, "history": []}
    if sb_path.exists():
        try:
            sb_data = json.loads(sb_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    pages = discover_wiki_pages(repo_root)
    total_cards = len(pages)
    canonical_cards = sum(1 for p in pages if p.epistemic_status == "canonical")
    canonical_pct = round((canonical_cards / total_cards * 100.0), 1) if total_cards else 0.0
    rs_scripts = len([s for s in wiki_root.rglob("*.rs") if "scripts" in s.parts and not s.name.startswith("test_")])

    ace_helpful = 0
    ace_path = wiki_root / ".ace-telemetry.json"
    if ace_path.exists():
        try:
            ad = json.loads(ace_path.read_text(encoding="utf-8"))
            ace_helpful = sum(int(v.get("helpful", 0)) for v in ad.get("cards", {}).values()) + sum(
                int(v.get("helpful", 0)) for v in ad.get("scripts", {}).values()
            )
        except Exception:
            pass

    next_ver = int(sb_data.get("version", 0)) + 1
    sb_data["version"] = next_ver
    history = sb_data.setdefault("history", [])
    history.append(
        {
            "version": next_ver,
            "time": now_iso(),
            "verb": f"{verb}({domain})",
            "total_cards": total_cards,
            "canonical_cards": canonical_cards,
            "canonical_pct": canonical_pct,
            "rust_scripts": rs_scripts,
            "ace_helpful_total": ace_helpful,
            "summary": summary,
        }
    )
    sb_data["history"] = history[-50:]
    sb_path.write_text(json.dumps(sb_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_commit(repo_root: Path, verb: str, domain: str, summary: str, allow_checkpoint: bool = False) -> int:
    """Update RSI Scoreboard -> run finalizer -> run linter & unit tests -> log operation -> create atomic git commit."""
    if verb == "study":
        pending_sources = _get_pending_source_units(repo_root)
        if pending_sources and not allow_checkpoint:
            print(
                "⛔ [ANTI-PARTIAL-SKIMMING HARD GATE] Commit blocked because active study source(s) still have UNFINISHED reusable chapters/segments:",
                file=sys.stderr,
            )
            for s_uri, pend_list in pending_sources:
                print(f"  - {s_uri}: {len(pend_list)} remaining -> {', '.join(pend_list[:6])}", file=sys.stderr)
            print(
                "👉 Rule: Every textbook or tutorial video MUST be 100% exhaustively ingested across all reusable chapters/segments (including cross-domain chapters) to prevent future rework!\n"
                "   • Complete all remaining units via `study_runner.py complete-source-unit`, OR\n"
                "   • Pass `--checkpoint` only if saving an intermediate mid-book checkpoint while continuing `/study`.",
                file=sys.stderr,
            )
            return 1
        elif pending_sources and allow_checkpoint:
            print("⚠️  [MID-BOOK CHECKPOINT] Unfinished reusable units remain — DO NOT stop `/study` until 100% completed:")
            for s_uri, pend_list in pending_sources:
                print(f"   • {s_uri}: {len(pend_list)} remaining -> {', '.join(pend_list[:6])}")

    update_scoreboard_snapshot(repo_root, verb, domain, summary)
    finalize_wiki(repo_root, actor=f"{verb}-skill/1.0")
    errors, warnings = lint_wiki(repo_root, run_tests=True)
    if errors:
        print("❌ Commit blocked by Wiki Linter errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    append_wiki_log(repo_root, verb, domain, summary)
    stage_targets = [
        t
        for t in ("GEMINI.md", "CLAUDE.md", "AGENTS.md", "SCHEMA.md", "Cargo.toml", "src", "wiki", ".agents", ".claude", ".cursor")
        if (repo_root / t).exists()
    ]
    subprocess.run(["git", "add", *stage_targets], cwd=str(repo_root), check=False)

    # Check if there are staged changes
    diff_res = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(repo_root))
    if diff_res.returncode == 0:
        print("ℹ️  No changes to commit.")
        return 0

    commit_msg = f"{verb}({domain}): {summary}"
    res = subprocess.run(
        ["git", "-c", "user.name=LLM-Wiki-Agent", "-c", "user.email=wiki@local", "commit", "-m", commit_msg],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"⚠️ Git commit failed: {res.stderr}", file=sys.stderr)
        return 1
    print(f"✅ Atomic commit created: '{commit_msg}'")
    return 0


def cmd_rollback(repo_root: Path, target_commit: str = "HEAD") -> int:
    """Remediation Channel 4: Safely revert a specific study/refine/lint commit without losing later work."""
    res = subprocess.run(
        ["git", "-c", "user.name=LLM-Wiki-Agent", "-c", "user.email=wiki@local", "revert", "--no-edit", target_commit],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"❌ Git revert failed: {res.stderr}", file=sys.stderr)
        return 1
    finalize_wiki(repo_root, actor="rollback/1.0")
    print(f"✅ Successfully reverted commit {target_commit} and re-synchronized wiki indexes.")
    return 0



def _tokenize_query(q_str: str) -> list[str]:
    import re
    tokens: list[str] = []
    for part in re.split(r"[\s,;，；、/|]+", q_str.strip().lower()):
        if not part:
            continue
        tokens.append(part)
        cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", part)
        for run in cjk_runs:
            if run not in tokens:
                tokens.append(run)
            for i in range(len(run) - 1):
                bg = run[i : i + 2]
                if bg not in tokens:
                    tokens.append(bg)
    return tokens


def cmd_search(repo_root: Path, query_parts: list[str], domain_filter: str | None = None, top_k: int = 8) -> int:
    """Hybrid Full-Text + Claims + Graph-Closure Search Engine (Zero-Precision-Loss Recall)."""
    import math
    wiki_root = repo_root / "wiki"
    s_idx_path = wiki_root / ".search-index.json"
    if not s_idx_path.exists():
        finalize_wiki(repo_root, actor="search-indexer/1.0")
    s_data = json.loads(s_idx_path.read_text(encoding="utf-8"))
    cards: list[dict[str, Any]] = s_data.get("cards", [])
    subtrees: list[dict[str, Any]] = s_data.get("subtrees", [])
    topic_map: dict[str, list[Any]] = s_data.get("topicMap", {})
    id_lookup = {c["id"]: c for c in cards}

    q_str = " ".join(query_parts).strip()
    tokens = _tokenize_query(q_str)
    if not tokens:
        print("⚠️ Please provide search keywords.", file=sys.stderr)
        return 1

    # Phase 0: Match L2 META Subtrees
    matched_subtrees: list[tuple[float, dict[str, Any]]] = []
    for st in subtrees:
        if domain_filter and domain_filter.lower() not in str(st.get("domain", "")).lower():
            continue
        st_hay = f"{st.get('slug', '')} {st.get('title', '')} {st.get('when', '')}".lower()
        st_sc = sum((2.5 if len(t) >= 2 else 0.5) for t in tokens if t in st_hay)
        if st_sc >= 2.5:
            matched_subtrees.append((st_sc, st))
    matched_subtrees.sort(key=lambda x: x[0], reverse=True)

    # DF-pruned CorpusTopicMap boost
    topic_boost: dict[str, float] = {}
    for tok in tokens:
        if len(tok) < 2:
            continue
        postings = topic_map.get(tok, [])
        if not postings:
            continue
        idf_w = max(0.8, 3.8 - math.log1p(len(postings)))
        for post in postings:
            cid = post.split("#", 1)[0] if isinstance(post, str) and "#" in post else ""
            if cid:
                topic_boost[cid] = topic_boost.get(cid, 0.0) + idf_w

    scored: list[tuple[float, dict[str, Any], list[str], list[str], str]] = []
    for c in cards:
        if domain_filter and domain_filter.lower() not in f"{c['tier']}/{c['domain']}".lower():
            continue
        id_tags = f"{c['id']} {' '.join(c.get('tags', []))} {' '.join(c.get('aliases', []))}".lower()
        title_desc = f"{c['title']} {c['description']}".lower()
        r_headings: list[str] = c.get("ruleHeadings", [])
        claims: list[str] = c.get("claims", [])
        patterns: list[str] = c.get("patterns", [])
        hist_q = " ".join(str(q) for q in c.get("queries", [])).lower()

        score = topic_boost.get(c["id"], 0.0)
        matched_rules: list[str] = []
        matched_claims: list[str] = []

        for tok in tokens:
            w_len = 1.5 if len(tok) >= 3 else 1.0
            if tok in id_tags:
                score += 4.0 * w_len
            if tok in title_desc:
                score += 3.0 * w_len
            if tok in hist_q:
                score += 2.5 * w_len
            for pat in patterns:
                if tok in pat.lower():
                    score += 2.4 * w_len
                    if pat not in matched_rules:
                        matched_rules.append(pat[:95])
            for rh in r_headings:
                if tok in rh.lower():
                    score += 2.2 * w_len
                    if rh not in matched_rules:
                        matched_rules.append(rh)
            for cl in claims:
                if tok in cl.lower():
                    score += 1.8 * w_len
                    if cl not in matched_claims:
                        matched_claims.append(cl)

        if score > 0:
            ace_h = int(c.get("aceHelpful", 0))
            ace_m = int(c.get("aceHarmful", 0))
            hotness = float(c.get("hotness", 0.0))
            score += 0.45 * math.log1p(max(0, ace_h - ace_m)) + 1.5 * hotness
            if c.get("subdir") == "00_core_aesthetics":
                score += 0.8

            best_sec = ""
            best_sec_s = 0.0
            for sec in c.get("sections", []):
                sec_t = f"{sec.get('title', '')} {sec.get('summary', '')}".lower()
                s_sc = sum(2.0 for tok in tokens if len(tok) >= 2 and tok in sec_t)
                if s_sc > best_sec_s:
                    best_sec_s = s_sc
                    lr = sec.get("lineRange", [1, 1])
                    best_sec = f"#{sec.get('slug', '')} (L{lr[0]}-{lr[1]})"
            scored.append((score, c, matched_rules[:3], matched_claims[:2], best_sec))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_hits = scored[:top_k]

    print(f'=== 🔍 Wiki Hybrid Graph + CorpusTopicMap Search: "{q_str}" (Matched {len(scored)}/{len(cards)} cards) ===')
    if matched_subtrees:
        print("🌿 [Phase 0: 命中 L2 子学科分支路由 (META Subtree Routers)]:")
        for _, st in matched_subtrees[:2]:
            st_rel = st.get("relPath") or st.get("path", "")
            print(
                f"   • `{st['domain']}/{st['slug']}` ({st['cardCount']} cards): {st['title']}\n"
                f"     Router: wiki/{st_rel}  (一键加载: `wiki_query.py read --subtree {st['domain']}/{st['slug']}`)"
            )
    if not top_hits:
        print("  (No direct lexical matches; check `wiki/index.md` or broaden terms.)")
        return 0

    selected_ids = {c["id"] for _, c, _, _, _ in top_hits}
    co_retrieval_ids: list[str] = []
    bound_verifiers: list[str] = []
    bound_generators: list[str] = []

    for rank, (sc, c, m_rules, m_claims, best_sec) in enumerate(top_hits, 1):
        ace_str = f" 🎯ACE:+{c.get('aceHelpful',0)}/-{c.get('aceHarmful',0)}" if (c.get("aceHelpful") or c.get("aceHarmful")) else ""
        st_str = f" 🌿{c.get('subtree')}" if c.get("subtree") and c.get("subtree") != "core_default" else ""
        sec_str = f" 📍{best_sec}" if best_sec else ""
        print(f"\n{rank}. [{c['id']}] (score={sc:.1f}, L{c.get('abstractionLevel', 2)}{st_str}{ace_str}) -> wiki/{c['relPath']}{sec_str}")
        print(f"   • 标题: {c['title']}")
        if m_rules:
            print("   • 命中铁律/小节 (Patterns & Rules): " + " | ".join(m_rules))
        if m_claims:
            preview_c = [mc[:95] + ("..." if len(mc) > 95 else "") for mc in m_claims]
            print("   • 命中定理断言 (Claims): " + " | ".join(preview_c))
        for dep_id in c.get("dependsOn", []):
            if dep_id not in selected_ids and dep_id in id_lookup and dep_id not in co_retrieval_ids:
                co_retrieval_ids.append(dep_id)
        for s_obj in c.get("scripts", []):
            sp = s_obj.get("path", "")
            if "verifier" in s_obj.get("role", "") or "check_" in sp:
                if sp and sp not in bound_verifiers:
                    bound_verifiers.append(sp)
            elif sp and sp not in bound_generators:
                bound_generators.append(sp)

    if co_retrieval_ids:
        print("\n🔗 [1-Hop Graph Closure 强制联动前置卡片 (depends_on)]:")
        for cid in co_retrieval_ids[:6]:
            cc = id_lookup[cid]
            print(f"   - [{cid}] wiki/{cc['relPath']} ({cc['title']})")

    if bound_verifiers or bound_generators:
        print("\n🛡️ [HarnessPro 绑定可执行考卷与工具链 (Bound Executable Scripts)]:")
        for vp in bound_verifiers:
            print(f"   - [verifier]  {vp}")
        for gp in bound_generators:
            print(f"   - [generator] {gp}")
    return 0


def cmd_verify_suite(repo_root: Path, domain_filter: str = "all", artifact_path: str | None = None) -> int:
    """HarnessPro-style Frozen Executable Test Suite Runner with RTK Compact Output."""
    wiki_root = repo_root / "wiki"
    s_idx_path = wiki_root / ".search-index.json"
    if not s_idx_path.exists():
        finalize_wiki(repo_root, actor="verify-suite/1.0")
    s_data = json.loads(s_idx_path.read_text(encoding="utf-8"))
    cards: list[dict[str, Any]] = s_data.get("cards", [])

    script_to_cards: dict[str, list[str]] = {}
    target_scripts: list[Path] = []

    for c in cards:
        dom_key = f"{c['tier']}/{c['domain']}"
        if domain_filter != "all" and domain_filter.lower() not in dom_key.lower():
            continue
        for s_obj in c.get("scripts", []):
            sp = repo_root / s_obj.get("path", "")
            if sp.exists():
                stem = sp.stem
                script_to_cards.setdefault(stem, [])
                if c["id"] not in script_to_cards[stem]:
                    script_to_cards[stem].append(c["id"])
                if sp not in target_scripts:
                    target_scripts.append(sp)

    for sp in sorted(wiki_root.rglob("*.rs")):
        if "scripts" not in sp.parts:
            continue
        rel_w = sp.relative_to(wiki_root).as_posix()
        if domain_filter != "all" and domain_filter.lower() not in rel_w.lower():
            continue
        if sp not in target_scripts:
            target_scripts.append(sp)

    rs_bins = [sp.stem for sp in target_scripts if sp.suffix == ".rs"]
    print(f"=== 🛡️ HarnessPro Frozen Verifier Suite (domain={domain_filter}, rust_targets={len(rs_bins)}) ===")

    failed_any = False
    if rs_bins:
        cmd = ["cargo", "test", "--quiet"]
        for b in rs_bins:
            cmd.extend(["--bin", b])
        res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True)
        if res.returncode == 0:
            print(f"✅ [PASS] Cargo Unit Test Suite: {len(rs_bins)} binaries passed all embedded unit tests (0 failures).")
        else:
            failed_any = True
            print(f"❌ [FAIL] Cargo Unit Test Suite failed:\n{res.stdout}\n{res.stderr}")

    if artifact_path:
        art_p = Path(artifact_path).resolve()
        if not art_p.exists():
            print(f"❌ Artifact not found: {art_p}", file=sys.stderr)
            return 1
        verifiers = [sp for sp in target_scripts if sp.suffix == ".rs" and sp.stem.startswith("check_")]
        print(f"--- Running {len(verifiers)} Frozen Verifier Binaries on Artifact: {art_p.name} ---")
        for vp in verifiers:
            stem = vp.stem
            gov_cards = script_to_cards.get(stem, ["(domain-general)"])
            bin_exe = repo_root / "target" / "debug" / stem
            run_cmd = [str(bin_exe), str(art_p)] if bin_exe.exists() else ["cargo", "run", "--quiet", "--bin", stem, "--", str(art_p)]
            r_v = subprocess.run(run_cmd, cwd=str(repo_root), capture_output=True, text=True)
            if r_v.returncode == 0:
                print(f"✅ [PASS] {stem} -> 0 violations (Governing Cards: {', '.join(gov_cards)})")
            else:
                failed_any = True
                print(f"❌ [FAIL] {stem} (exit={r_v.returncode}) — MUST RE-READ CARDS: {', '.join(gov_cards)}")
                out_txt = (r_v.stdout + "\n" + r_v.stderr).strip()
                for line in out_txt.splitlines()[:20]:
                    print(f"   {line}")

    return 1 if failed_any else 0


def _extract_card_raw_sources(repo_root: Path, page: Any) -> list[str]:
    """Extract all primary raw source references (raw://, raw/..., source_urls) from a card's frontmatter & .claims."""
    from wiki_core import parse_markdown

    fm = parse_markdown(page.abs_path.read_text(encoding="utf-8")).frontmatter
    raw_refs: list[str] = []

    def _add(val: Any) -> None:
        if not val:
            return
        if isinstance(val, str):
            v = val.strip()
            if v and not v.startswith("script://") and not v.startswith("wiki/") and v not in raw_refs:
                raw_refs.append(v)
        elif isinstance(val, dict):
            for k in ("uri", "resource", "locator", "url", "path"):
                if k in val:
                    _add(val[k])
            for k, v in val.items():
                if k.startswith("raw/") or k.startswith("raw://"):
                    _add(f"{k}:{v}")
        elif isinstance(val, list):
            for item in val:
                _add(item)

    _add(fm.get("sources"))
    _add(fm.get("source_urls"))

    claim_p = repo_root / "wiki" / ".claims" / (page.rel_path[:-3] + ".json")
    if claim_p.exists():
        try:
            cd = json.loads(claim_p.read_text(encoding="utf-8"))
            for cl in cd.get("claims", []):
                _add(cl.get("evidence_uri"))
                _add(cl.get("evidence"))
        except Exception:
            pass

    return raw_refs


def _resolve_raw_ref_to_files(repo_root: Path, ref: str) -> list[Path]:
    """Resolve a raw:// or raw/... URI (with optional #fragment or glob) to actual files on disk."""
    clean = ref.split("#")[0].strip()
    if clean.startswith("raw://"):
        clean = "raw/" + clean[len("raw://") :]
    if clean.startswith("http://") or clean.startswith("https://"):
        return []
    if not clean.startswith("raw/"):
        return []

    cand = repo_root / clean
    if cand.exists() and cand.is_file():
        return [cand]
    if cand.exists() and cand.is_dir():
        return sorted(p for p in cand.rglob("*") if p.is_file() and not p.name.startswith("."))

    # Handle globs or range shorthand like 01..04_*.vtt
    parent = cand.parent
    if parent.exists() and parent.is_dir():
        return sorted(
            p
            for p in parent.iterdir()
            if p.is_file() and p.suffix.lower() in {".pdf", ".epub", ".vtt", ".jpg", ".jpeg", ".png", ".json"}
        )
    return []


def cmd_inspect_sources(repo_root: Path, targets: list[str], domain_filter: str | None = None) -> int:
    """Mandatory Primary-Source Re-Ingest Helper: Resolve and verify raw/ primary textbooks & video transcripts for cards."""
    from wiki_core import discover_wiki_pages

    pages = discover_wiki_pages(repo_root)
    matched = []
    target_set = {t.strip().lower() for t in targets if t.strip()}

    for p in pages:
        if domain_filter and domain_filter.lower() not in f"{p.tier}/{p.domain}".lower():
            continue
        if target_set:
            if (
                p.page_id.lower() in target_set
                or p.rel_path.lower() in target_set
                or any(t in p.page_id.lower() or t in p.rel_path.lower() for t in target_set)
            ):
                matched.append(p)
        else:
            matched.append(p)

    if not matched:
        print("⚠️ No matching Wiki cards found for inspect-sources.", file=sys.stderr)
        return 1

    print(f"=== 📚 Primary-Source Traceability & Re-Ingest Inspector ({len(matched)} cards) ===")
    print("RULE: Before editing, consolidating, or merging ANY card, you MUST read its primary raw/ files directly (`read_file`), NEVER secondary card text!\n")

    missing_cards: list[str] = []
    for idx, p in enumerate(matched, 1):
        refs = _extract_card_raw_sources(repo_root, p)
        resolved_files: list[Path] = []
        for r in refs:
            for rf in _resolve_raw_ref_to_files(repo_root, r):
                if rf not in resolved_files:
                    resolved_files.append(rf)

        status_icon = "✅" if refs else "⚠️ [NO EXPLICIT RAW URI]"
        if not refs:
            missing_cards.append(f"{p.page_id} (wiki/{p.rel_path})")

        print(f"{idx:03d}. {status_icon} [{p.page_id}] -> wiki/{p.rel_path}")
        print(f"     • Title: {p.title}")
        if refs:
            print(f"     • Declared Primary References ({len(refs)}):")
            for r in refs[:6]:
                print(f"       - {r}")
        if resolved_files:
            print(f"     • Resolved Disk Files to Re-Read ({len(resolved_files)} files):")
            for rf in resolved_files[:8]:
                sz_kb = rf.stat().st_size / 1024.0
                print(f"       -> {rf} ({sz_kb:.1f} KB)")

    if missing_cards:
        print(f"\n⚠️ Cards missing explicit raw/ source URIs in frontmatter/.claims ({len(missing_cards)}):")
        for mc in missing_cards:
            print(f"  - {mc}")
    else:
        print(f"\n✅ All {len(matched)} inspected cards have explicit primary source bindings.")
    return 0


def cmd_skilltrain_audit(repo_root: Path, domain_filter: str = "music_theory_acoustics") -> int:
    """Audit a domain against Skill-Training (arXiv:2607.27557) Memory Regularization & Quantitative Rule Density metrics."""
    import re
    from wiki_core import discover_wiki_pages

    pages = [
        p
        for p in discover_wiki_pages(repo_root)
        if domain_filter == "all" or domain_filter.lower() in f"{p.tier}/{p.domain}".lower()
    ]
    if not pages:
        print(f"No pages found for domain={domain_filter}", file=sys.stderr)
        return 1

    by_sub: dict[str, list[Any]] = {}
    total_action_chars = 0
    total_quant_rules = 0
    quant_pattern = re.compile(
        r"(\d+(\.\d+)?\s*(%|dB|Hz|kHz|ms|s|BPM|st|cent|拍|小节|度|音分|行|字|px|pt|g|mg|kcal)|[<>≤≥]=?\s*\d+|\[\d+\s*,\s*\d+\]|严禁|不得超过|至少|必须)",
        re.IGNORECASE,
    )

    for p in pages:
        sub = p.rel_path.split("/")[2] if len(p.rel_path.split("/")) > 2 else "root"
        by_sub.setdefault(sub, []).append(p)
        if sub in ("00_core_aesthetics", "01_craft_playbooks", "02_style_specifics"):
            body = p.clean_body.strip()
            total_action_chars += len(body)
            for line in body.splitlines():
                if quant_pattern.search(line):
                    total_quant_rules += 1

    quant_per_1k = (total_quant_rules / (total_action_chars / 1000.0)) if total_action_chars > 0 else 0.0
    print(f"=== 🧠 Skill-Training (arXiv:2607.27557) Pool Audit: {domain_filter} ===")
    print(f"• Total Pages: {len(pages)}")
    for sub, plist in sorted(by_sub.items()):
        print(f"  - {sub}: {len(plist)} cards (avg {sum(len(x.clean_body.splitlines()) for x in plist)//max(1,len(plist))} lines/card)")
    print(f"• Action Card Text Volume (00/01/02): {total_action_chars:,} chars (~{total_action_chars//4:,} tokens)")
    print(f"• Quantitative Hard-Constraint Density: {quant_per_1k:.2f} rules / 1k chars (Paper benchmark: Sonnet +Ours = 0.67, Opus = 0.13)")
    return 0


def cmd_trace_diagnose(repo_root: Path, domain_filter: str = "all") -> int:
    """HarnessPro (arXiv:2610.26770) Trace Diagnosis & Pareto Performance-Cost Frontier Analyzer
    fused with PenguinHarness OPRO (arXiv:2309.03409) Utility-per-KB Scoring.
    """
    from wiki_core import discover_wiki_pages

    pages = [
        p
        for p in discover_wiki_pages(repo_root)
        if domain_filter == "all" or domain_filter.lower() in f"{p.tier}/{p.domain}".lower()
    ]
    ace_path = repo_root / "wiki" / ".ace-telemetry.json"
    ace_data = json.loads(ace_path.read_text(encoding="utf-8")) if ace_path.exists() else {}
    ace_cards: dict[str, dict[str, Any]] = ace_data.get("cards", {})
    recent_traces: list[dict[str, Any]] = ace_data.get("recentTraces", [])

    print("=" * 84)
    print(f"🔬 HARNESSPRO (arXiv:2610.26770) × OPRO TRACE DIAGNOSIS & PARETO AUDIT (domain={domain_filter})")
    print("=" * 84)

    if not pages:
        print("ℹ️ Clean Day-0 framework (0 cards currently ingested). Ready for `/study` ingestion.")
        print("• Available Pareto Reading Modes: `--mode lite` (<=6KB) | `--mode medium` (<=18KB) | `--mode heavy` (<=40KB)")
        return 0

    pareto_rows: list[tuple[float, float, int, int, Any]] = []
    bloated_cold: list[tuple[float, Any]] = []
    by_dom: dict[str, list[Any]] = {}

    for p in pages:
        by_dom.setdefault(f"{p.tier}/{p.domain}", []).append(p)
        c_stat = ace_cards.get(p.page_id, {})
        h = int(c_stat.get("helpful", 0))
        m = int(c_stat.get("harmful", 0))
        net = h - 2 * m
        kb = max(0.2, len(p.clean_body.encode("utf-8")) / 1024.0)
        # OPRO utility-per-KB score (rewards high helpfulness per kilobyte of context cost)
        opro_eff = (net + 0.5) / max(0.5, kb)
        pareto_rows.append((opro_eff, kb, h, m, p))
        if p.subdir != "03_deep_concepts" and kb > 3.8 and net <= 0:
            bloated_cold.append((kb, p))

    pareto_rows.sort(key=lambda x: (x[0], x[2]), reverse=True)
    bloated_cold.sort(key=lambda x: x[0], reverse=True)

    print("\n🏆 1. Top Pareto-Optimal Cards (High ACE Utility / Low Context KB — OPRO Frontier):")
    for eff, kb, h, m, p in pareto_rows[:8]:
        print(f"   • `{p.page_id}` ({p.domain}) — OPRO={eff:.2f}/KB | Size={kb:.1f} KB | ACE=+{h}/-{m}")

    print("\n⚖️ 2. Domain Pareto Operating Mode Recommendations (`Lite` vs `Medium` vs `Heavy`):")
    for dom_key, dpages in sorted(by_dom.items()):
        avg_kb = sum(len(x.clean_body.encode("utf-8")) / 1024.0 for x in dpages) / max(1, len(dpages))
        if len(dpages) > 30:
            rec = "`--mode lite` (routine lookup, <=6KB) or `--mode medium` (standard synthesis, <=18KB)"
        elif len(dpages) > 10:
            rec = "`--mode medium` (balanced Pareto knee point, <=18KB)"
        else:
            rec = "`--mode heavy` (full 1-hop context, <=40KB)"
        print(f"   • [{dom_key}] ({len(dpages)} cards, avg {avg_kb:.1f} KB/card) -> Recommended: {rec}")

    if bloated_cold:
        print(f"\n⚠️ 3. Pareto-Suboptimal Candidates ({len(bloated_cold)} action cards >3.8 KB with 0 net ACE hits):")
        for kb, p in bloated_cold[:6]:
            print(f"   • `{p.page_id}` ({kb:.1f} KB, `wiki/{p.rel_path}`) -> Candidate for `B-Correct` description sharpening or `D-Merge`")
    else:
        print("\n✅ 3. Zero Pareto-suboptimal bloated action cards detected.")

    print(f"\n📊 4. Trajectory Summary: {len(recent_traces)} ACE traces recorded (Single-Hypothesis Atomic Edits, Zero Multi-Candidate Token Waste).")
    return 0


def _normalize_source_uri(source_uri: str) -> str:
    s = source_uri.strip()
    if s.startswith("raw://"):
        s = "raw/" + s[len("raw://") :]
    return s


def cmd_register_source(repo_root: Path, source_uri: str, title: str, units_json: str) -> int:
    """Register a full textbook (TOC) or video tutorial (timeline segments) into `wiki/.source-coverage.json`
    BEFORE starting `/study`, enforcing 100% full-source exhaustive ingestion & cross-domain triage.
    """
    norm_uri = _normalize_source_uri(source_uri)
    try:
        raw_units = json.loads(units_json)
        if not isinstance(raw_units, list) or not raw_units:
            raise ValueError("units_json must be a non-empty JSON array of chapter/segment objects.")
    except Exception as e:
        print(f"❌ Invalid --units-json: {e}", file=sys.stderr)
        return 1

    units: list[dict[str, Any]] = []
    reusable_cnt = 0
    for idx, u in enumerate(raw_units, 1):
        if not isinstance(u, dict):
            continue
        uid = str(u.get("unit_id") or f"u{idx:02d}").strip()
        utitle = str(u.get("title") or f"Unit {idx}").strip()
        loc = str(u.get("locator") or "").strip()
        t_dom = str(u.get("target_domain") or "").strip()
        reusable = bool(u.get("reusable", True))
        if reusable:
            reusable_cnt += 1
        units.append(
            {
                "unit_id": uid,
                "title": utitle,
                "locator": loc,
                "target_domain": t_dom,
                "reusable": reusable,
                "status": "pending" if reusable else "non_content",
                "cards": [],
                "summary": "",
            }
        )

    if not units or reusable_cnt == 0:
        print("❌ Source registration must contain at least 1 reusable chapter/unit.", file=sys.stderr)
        return 1
    if reusable_cnt / len(units) < 0.70:
        print(
            f"❌ Anti-Cherry-Picking Guard: Only {reusable_cnt}/{len(units)} units marked `reusable: true` (<70%). "
            "You may only mark frontmatter/copyright/index pages as `reusable: false`; all substantive chapters (even in other domains) MUST be `reusable: true`!",
            file=sys.stderr,
        )
        return 1

    cov_path = repo_root / "wiki" / ".source-coverage.json"
    cov_data: dict[str, Any] = {"schemaVersion": 1, "sources": {}}
    if cov_path.exists():
        try:
            cov_data = json.loads(cov_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    sources = cov_data.setdefault("sources", {})
    existing = sources.get(norm_uri, {})
    existing_units = {u.get("unit_id"): u for u in existing.get("units", []) if isinstance(u, dict)}

    for u in units:
        old_u = existing_units.get(u["unit_id"])
        if old_u and old_u.get("status") == "completed" and old_u.get("cards"):
            u["status"] = "completed"
            u["cards"] = old_u["cards"]
            u["summary"] = old_u.get("summary", "")
            u["completed_at"] = old_u.get("completed_at", now_iso())

    all_done = all((not u["reusable"]) or u["status"] == "completed" for u in units)
    sources[norm_uri] = {
        "source_uri": norm_uri,
        "title": title.strip(),
        "registered_at": existing.get("registered_at", now_iso()),
        "updated_at": now_iso(),
        "status": "completed" if all_done else "in_progress",
        "exhaustively_ingested": all_done,
        "units": units,
    }
    cov_path.write_text(json.dumps(cov_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    done_cnt = sum(1 for u in units if u["reusable"] and u["status"] == "completed")
    print(
        f"✅ Registered full source TOC/Timeline in `wiki/.source-coverage.json`: '{title}' ({norm_uri})\n"
        f"   • Total Units: {len(units)} ({reusable_cnt} reusable chapters/segments, {done_cnt}/{reusable_cnt} completed)\n"
        f"   • Cross-Domain Triage Targets: {sorted({u['target_domain'] for u in units if u['target_domain']})}"
    )
    return 0


def cmd_complete_source_unit(
    repo_root: Path,
    source_uri: str,
    unit_id: str,
    card_ids: list[str],
    summary: str,
) -> int:
    """Sign off a chapter/segment of a registered source in `wiki/.source-coverage.json`
    after verifying that all bound Wiki card IDs exist on disk.
    """
    from wiki_core import discover_wiki_pages

    norm_uri = _normalize_source_uri(source_uri)
    cov_path = repo_root / "wiki" / ".source-coverage.json"
    if not cov_path.exists():
        print(
            "❌ `wiki/.source-coverage.json` does not exist. Run `study_runner.py register-source` first!",
            file=sys.stderr,
        )
        return 1

    cov_data = json.loads(cov_path.read_text(encoding="utf-8"))
    sources = cov_data.get("sources", {})
    if norm_uri not in sources:
        print(
            f"❌ Source '{norm_uri}' is not registered in `wiki/.source-coverage.json`. Run `register-source` first!",
            file=sys.stderr,
        )
        return 1

    clean_cards = [c.strip() for c in card_ids if c.strip()]
    if not clean_cards:
        print("❌ You must specify at least one `--cards <card_id>` produced/updated from this chapter/unit!", file=sys.stderr)
        return 1

    pages = discover_wiki_pages(repo_root)
    valid_ids = {p.page_id for p in pages} | {p.rel_path for p in pages} | {a for p in pages for a in p.aliases}
    missing = [cid for cid in clean_cards if cid not in valid_ids]
    if missing:
        print(
            f"❌ Cannot sign off unit '{unit_id}': the following card IDs do not exist in `wiki/`: {missing}",
            file=sys.stderr,
        )
        return 1

    s_obj = sources[norm_uri]
    found = False
    for u in s_obj.get("units", []):
        if u.get("unit_id") == unit_id:
            u["status"] = "completed"
            u["cards"] = clean_cards
            u["summary"] = summary.strip()
            u["completed_at"] = now_iso()
            found = True
            break

    if not found:
        print(f"❌ Unit '{unit_id}' not found under source '{norm_uri}'.", file=sys.stderr)
        return 1

    reusable_units = [u for u in s_obj.get("units", []) if u.get("reusable", True)]
    done_units = [u for u in reusable_units if u.get("status") == "completed"]
    all_done = len(done_units) == len(reusable_units)
    s_obj["status"] = "completed" if all_done else "in_progress"
    s_obj["exhaustively_ingested"] = all_done
    s_obj["updated_at"] = now_iso()
    if all_done:
        s_obj["completed_at"] = now_iso()

    cov_path.write_text(json.dumps(cov_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if all_done:
        print(
            f"🎉 [100% EXHAUSTIVE SOURCE COVERAGE ACHIEVED] '{s_obj.get('title')}' ({norm_uri}): "
            f"All {len(done_units)}/{len(reusable_units)} reusable chapters/segments completed and bound to verified cards!"
        )
    else:
        rem = [f"{u['unit_id']} ({u['title']})" for u in reusable_units if u.get("status") != "completed"]
        print(
            f"✅ Signed off unit '{unit_id}' -> cards {clean_cards} ({len(done_units)}/{len(reusable_units)} reusable units done).\n"
            f"⏳ Remaining units that MUST be studied before finishing this source: {', '.join(rem[:6])}"
        )
    return 0


def cmd_coverage_audit(repo_root: Path, domain_filter: str = "all") -> int:
    """Audit full-book / full-video ingestion coverage across `wiki/.source-coverage.json` and physical `raw/` files
    to guarantee zero partial-skimming rework.
    """
    from wiki_core import discover_wiki_pages

    pages = discover_wiki_pages(repo_root)
    cov_path = repo_root / "wiki" / ".source-coverage.json"
    cov_data = json.loads(cov_path.read_text(encoding="utf-8")) if cov_path.exists() else {"sources": {}}
    reg_sources: dict[str, dict[str, Any]] = cov_data.get("sources", {})

    print("=" * 88)
    print(f"📚 WHOLE-SOURCE EXHAUSTIVE COVERAGE & ANTI-REWORK AUDIT (domain={domain_filter})")
    print("=" * 88)

    # 1. Registered Sources in .source-coverage.json
    print(f"\n📋 1. Registered Full-TOC / Full-Timeline Sources ({len(reg_sources)} tracked):")
    if not reg_sources:
        print("   ℹ️  No sources registered in `wiki/.source-coverage.json` yet.")
        print("   👉 When starting `/study`, run `study_runner.py register-source --source <raw_uri> --title <title> --units-json '[...]'`")
    else:
        for s_uri, s_obj in sorted(reg_sources.items()):
            if domain_filter != "all" and domain_filter.lower() not in s_uri.lower():
                unit_doms = " ".join(str(u.get("target_domain", "")) for u in s_obj.get("units", [])).lower()
                if domain_filter.lower() not in unit_doms:
                    continue
            reusable = [u for u in s_obj.get("units", []) if u.get("reusable", True)]
            done = [u for u in reusable if u.get("status") == "completed"]
            pend = [u for u in reusable if u.get("status") != "completed"]
            icon = "✅ [100% EXHAUSTIVE]" if not pend else "⚠️ [INCOMPLETE - BLOCKING]"
            print(f"   • {icon} {s_obj.get('title', s_uri)} (`{s_uri}`) — {len(done)}/{len(reusable)} reusable units done")
            if pend:
                print(f"     ⏳ Pending Units ({len(pend)}): " + ", ".join(f"{u['unit_id']}:{u['title']}" for u in pend[:5]))

    # 2. Physical raw/ files cross-referenced against card sources, .claims, and SHA-256 receipts
    raw_root = repo_root / "raw"
    if raw_root.is_dir():
        import re as _re

        # Collect SHA-256 hashes from all .receipt.json files in raw/
        path_to_sha: dict[str, str] = {}
        sha_to_paths: dict[str, list[str]] = {}
        for rfp in sorted(raw_root.rglob("*.receipt.json")):
            try:
                rd = json.loads(rfp.read_text(encoding="utf-8"))
                sha = str(rd.get("sha256", "")).strip()
                base_rel = rfp.relative_to(repo_root).as_posix()[: -len(".receipt.json")]
                if sha:
                    path_to_sha[base_rel] = sha
                    sha_to_paths.setdefault(sha, []).append(base_rel)
            except Exception:
                pass

        raw_books_and_media: list[Path] = []
        seen_keys: set[str] = set()
        for fp in sorted(raw_root.rglob("*")):
            if not fp.is_file() or fp.name.startswith(".") or "_chunks" in fp.parts:
                continue
            if fp.suffix.lower() in {".pdf", ".epub"} or fp.name.endswith(".mp4.receipt.json"):
                rel_key = fp.relative_to(repo_root).as_posix().replace(".receipt.json", "")
                if domain_filter != "all" and domain_filter.lower() not in rel_key.lower():
                    continue
                sha = path_to_sha.get(rel_key, "")
                dedup_k = sha if sha else rel_key
                if dedup_k not in seen_keys:
                    seen_keys.add(dedup_k)
                    raw_books_and_media.append(fp)

        # Build citation map from all cards + .claims sha256
        card_refs_by_page: list[tuple[str, list[str], set[str]]] = []
        for p in pages:
            refs = _extract_card_raw_sources(repo_root, p)
            shas: set[str] = set()
            claim_p = repo_root / "wiki" / ".claims" / (p.rel_path[:-3] + ".json")
            if claim_p.exists():
                try:
                    cd = json.loads(claim_p.read_text(encoding="utf-8"))
                    for cl in cd.get("claims", []):
                        ev = cl.get("evidence", {})
                        if isinstance(ev, dict) and ev.get("source_sha256"):
                            shas.add(str(ev.get("source_sha256")))
                except Exception:
                    pass
            card_refs_by_page.append((p.page_id, refs, shas))

        print(f"\n🗂️  2. Physical `raw/` Textbooks & Video Receipts Coverage ({len(raw_books_and_media)} unique primary sources):")
        uncovered: list[str] = []
        single_card_books: list[tuple[str, str]] = []
        for fp in raw_books_and_media:
            rel_s = fp.relative_to(repo_root).as_posix().replace(".receipt.json", "")
            sha = path_to_sha.get(rel_s, "")
            alias_rels = sha_to_paths.get(sha, [rel_s]) if sha else [rel_s]
            alias_stems = {Path(a).stem.lower() for a in alias_rels} | {Path(a).name.lower() for a in alias_rels}
            # Also extract significant alphanumeric/CJK tokens from stem for fuzzy title match
            sig_toks = [
                t
                for t in _re.findall(r"[a-z0-9]{4,}|[\u4e00-\u9fff]{2,}", Path(rel_s).stem.lower())
                if t not in {"library", "edition", "third", "second", "vol1", "vol2", "book", "guide"}
            ]

            citing_cards: list[str] = []
            locators: set[str] = set()
            for cid, refs, c_shas in card_refs_by_page:
                matched_card = False
                if sha and sha in c_shas:
                    matched_card = True
                for r in refs:
                    rl = r.lower()
                    if any(st in rl for st in alias_stems) or (len(sig_toks) >= 2 and all(t in rl for t in sig_toks[:2])):
                        matched_card = True
                        if "#" in r:
                            locators.add(r.split("#", 1)[1])
                        elif ":" in r and ("pp." in rl or "ch" in rl or "sec" in rl):
                            locators.add(r.split(":", 1)[1].strip())
                if matched_card and cid not in citing_cards:
                    citing_cards.append(cid)

            if not citing_cards:
                uncovered.append(rel_s)
            elif len(citing_cards) == 1 and fp.suffix.lower() in {".pdf", ".epub"} and len(locators) <= 1:
                single_card_books.append((rel_s, citing_cards[0]))

        covered_cnt = len(raw_books_and_media) - len(uncovered)
        print(f"   • Primary Sources Backed by Wiki Cards & Grounded Claims: {covered_cnt}/{len(raw_books_and_media)}")
        if uncovered:
            print(f"   ⚠️  Unstudied / Unreferenced `raw/` Sources ({len(uncovered)}):")
            for u_rel in uncovered[:10]:
                print(f"      - `{u_rel}` (0 citing cards — schedule `/study` full-book ingestion)")
        if single_card_books:
            print(f"   🔎 Books with Single-Card / Single-Locator Citation (Verify Full-TOC Coverage) ({len(single_card_books)}):")
            for b_rel, cid in single_card_books[:10]:
                print(f"      - `{b_rel}` -> cited only by `{cid}` (check if other chapters need cross-domain cards)")
        if not uncovered and not single_card_books:
            print("   ✅ All physical `raw/` textbooks and video receipts have multi-card / multi-section Grounded Claim coverage.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage /study lifecycle, RSI self-evolution, and atomic Git commits.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show active .study-run.json queue status")
    sub.add_parser("next", help="Get next pending study unit")

    p_comp = sub.add_parser("complete-unit", help="Mark a study unit complete, finalize, lint, and commit")
    p_comp.add_argument("unit_id", type=str)
    p_comp.add_argument("summary", type=str)

    p_gap = sub.add_parser("add-gap", help="Register an active curriculum gap and textbook recommendation")
    p_gap.add_argument("--domain", required=True, help="e.g. foundations/visual_aesthetics")
    p_gap.add_argument("--topic", required=True, help="Missing systematic topic")
    p_gap.add_argument("--reason", required=True, help="Why this gap was triggered")
    p_gap.add_argument("--books", nargs="+", required=True, help="Recommended authoritative textbooks")
    p_gap.add_argument("--priority", default="HIGH", choices=["HIGH", "MEDIUM", "LOW"])

    p_trace = sub.add_parser("record-trace", help="Record ACE helpful/harmful rule & script applications")
    p_trace.add_argument("--helpful", nargs="*", default=[], help="Card IDs applied that helped")
    p_trace.add_argument("--harmful", nargs="*", default=[], help="Card IDs that misled or needed fix")
    p_trace.add_argument("--scripts", nargs="*", default=[], help="Script names executed that helped")
    p_trace.add_argument("--task", required=True, help="Brief task summary")

    p_gotcha = sub.add_parser("record-gotcha", help="Record a retry-saving gotcha into GOTCHAS_AND_RETRY_SAVERS.md")
    p_gotcha.add_argument("--id", required=True, help="Gotcha ID, e.g. gotcha-chrome-dbsc-youtube")
    p_gotcha.add_argument("--scope", required=True, help="Tool or domain scope")
    p_gotcha.add_argument("--symptom", required=True, help="What failed or caused retries")
    p_gotcha.add_argument("--root-cause", required=True, help="Why it failed")
    p_gotcha.add_argument("--recipe", required=True, help="One-shot working solution")
    p_gotcha.add_argument("--helpful", type=int, default=1)

    p_wf = sub.add_parser("record-workflow", help="Record an AWM parameterized workflow into AWM_WORKFLOWS.md")
    p_wf.add_argument("--id", required=True, help="Workflow ID, e.g. awm-chrome-devtools-video-ingest")
    p_wf.add_argument("--title", required=True, help="Workflow title")
    p_wf.add_argument("--applies-when", required=True, help="When this workflow applies")
    p_wf.add_argument("--params-json", required=True, help="JSON object of {param_name: description}")
    p_wf.add_argument("--steps-json", required=True, help="JSON array of {observation, reasoning, action}")

    p_commit = sub.add_parser("commit", help="Finalize, lint, test, and create an atomic git commit")
    p_commit.add_argument("--verb", required=True, choices=["study", "stacks", "organize", "refine", "lint", "init"])
    p_commit.add_argument("--domain", required=True)
    p_commit.add_argument("--summary", required=True)
    p_commit.add_argument(
        "--checkpoint",
        action="store_true",
        help="Allow intermediate mid-book checkpoint commit while reusable chapters are still in_progress",
    )

    p_reg = sub.add_parser(
        "register-source",
        help="Register full book TOC or video timeline units in wiki/.source-coverage.json before /study",
    )
    p_reg.add_argument("--source", required=True, help="Primary source URI, e.g. raw/foundations/ludology_systems/book.pdf")
    p_reg.add_argument("--title", required=True, help="Full textbook or video course title")
    p_reg.add_argument(
        "--units-json",
        required=True,
        help="JSON array of {unit_id, title, locator, target_domain, reusable}",
    )

    p_csu = sub.add_parser(
        "complete-source-unit",
        help="Sign off a studied chapter/segment in wiki/.source-coverage.json with verified Wiki card IDs",
    )
    p_csu.add_argument("--source", required=True, help="Primary source URI registered in .source-coverage.json")
    p_csu.add_argument("--unit-id", required=True, help="Chapter/unit ID, e.g. ch01")
    p_csu.add_argument("--cards", nargs="+", required=True, help="Wiki card IDs created or updated from this unit")
    p_csu.add_argument("--summary", required=True, help="Summary of reusable knowledge & scripts extracted from this unit")

    p_ca = sub.add_parser(
        "coverage-audit",
        help="Audit 100% full-book / full-video coverage across wiki/.source-coverage.json and raw/ to prevent partial-skimming rework",
    )
    p_ca.add_argument("--domain", default="all", help="Optional domain filter (or 'all')")

    p_rb = sub.add_parser("rollback", help="Revert a specific commit atomically")
    p_rb.add_argument("--commit", default="HEAD", help="Commit hash or HEAD")

    sub.add_parser("prune-raw", help="Delete studied raw video/audio/chunks and leave 1KB .receipt.json")
    p_cg = sub.add_parser("compact-git", help="Squash old git history and run aggressive GC to save disk space")
    p_cg.add_argument("--keep-recent", "--keep", dest="keep_recent", type=int, default=15)

    p_search = sub.add_parser("search", help="Hybrid Full-Text + Claims + Graph-Closure search across all Wiki cards")
    p_search.add_argument("query", nargs="+", help="Search keywords (Chinese or English)")
    p_search.add_argument("--domain", default=None, help="Optional domain filter, e.g. music_theory_acoustics or visual_aesthetics")
    p_search.add_argument("--top", type=int, default=8, help="Max top cards to return")

    p_vs = sub.add_parser("verify-suite", help="HarnessPro-style Frozen Verifier Suite runner with RTK compact output")
    p_vs.add_argument("--domain", default="all", help="Domain filter (e.g. music_theory_acoustics, visual_aesthetics, or all)")
    p_vs.add_argument("--artifact", default=None, help="Optional artifact path (e.g. score.json) to test against all domain check_* binaries")

    p_is = sub.add_parser("inspect-sources", help="Resolve and verify primary raw/ textbooks & video transcripts for cards before editing/consolidating")
    p_is.add_argument("targets", nargs="*", default=[], help="Card IDs or relative paths to inspect")
    p_is.add_argument("--domain", default=None, help="Optional domain filter, e.g. music_theory_acoustics")

    p_sa = sub.add_parser("skilltrain-audit", help="Audit a domain against Skill-Training (arXiv:2607.27557) pool size and quantitative rule density metrics")
    p_sa.add_argument("--domain", default="music_theory_acoustics", help="Domain filter (or 'all')")

    p_td = sub.add_parser(
        "trace-diagnose",
        help="HarnessPro (arXiv:2610.26770) × OPRO Trace Diagnosis & Pareto Performance-Cost Frontier Analyzer",
    )
    p_td.add_argument("--domain", default="all", help="Domain filter (or 'all')")

    args = parser.parse_args()
    repo_root = find_repo_root()

    if args.cmd == "status":
        return cmd_status(repo_root)
    if args.cmd == "next":
        return cmd_next(repo_root)
    if args.cmd == "complete-unit":
        return cmd_complete_unit(repo_root, args.unit_id, args.summary)
    if args.cmd == "add-gap":
        return cmd_add_gap(repo_root, args.domain, args.topic, args.reason, args.books, args.priority)
    if args.cmd == "record-trace":
        return cmd_record_trace(repo_root, args.helpful, args.harmful, args.scripts, args.task)
    if args.cmd == "record-gotcha":
        return cmd_record_gotcha(repo_root, args.id, args.scope, args.symptom, args.root_cause, args.recipe, args.helpful)
    if args.cmd == "record-workflow":
        return cmd_record_workflow(repo_root, args.id, args.title, args.applies_when, args.params_json, args.steps_json)
    if args.cmd == "commit":
        return cmd_commit(repo_root, args.verb, args.domain, args.summary, getattr(args, "checkpoint", False))
    if args.cmd == "register-source":
        return cmd_register_source(repo_root, args.source, args.title, args.units_json)
    if args.cmd == "complete-source-unit":
        return cmd_complete_source_unit(repo_root, args.source, args.unit_id, args.cards, args.summary)
    if args.cmd == "coverage-audit":
        return cmd_coverage_audit(repo_root, args.domain)
    if args.cmd == "rollback":
        return cmd_rollback(repo_root, args.commit)
    if args.cmd == "prune-raw":
        return cmd_prune_raw(repo_root)
    if args.cmd == "compact-git":
        return cmd_compact_git(repo_root, args.keep_recent)
    if args.cmd == "search":
        return cmd_search(repo_root, args.query, args.domain, args.top)
    if args.cmd == "verify-suite":
        return cmd_verify_suite(repo_root, args.domain, args.artifact)
    if args.cmd == "inspect-sources":
        return cmd_inspect_sources(repo_root, args.targets, args.domain)
    if args.cmd == "skilltrain-audit":
        return cmd_skilltrain_audit(repo_root, args.domain)
    if args.cmd == "trace-diagnose":
        return cmd_trace_diagnose(repo_root, args.domain)
    return 1


if __name__ == "__main__":
    sys.exit(main())

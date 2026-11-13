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
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from wiki_core import find_repo_root, now_iso, write_text_if_changed
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


PRUNABLE_MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4a", ".wav", ".flac", ".mp3", ".ogg", ".aac", ".opus", ".m4v"}


def _source_protection_prefixes(pending_sources: list[tuple[str, list[str]]]) -> list[str]:
    """Map sources that still have unfinished units to raw/ path prefixes whose media must be kept.

    A pending `raw/a/b/lecture.mp4` protects `raw/a/b/lecture*` (the video, its chunks and sidecars);
    a pending directory source protects the whole directory.
    """
    prefixes: list[str] = []
    for s_uri, _ in pending_sources:
        s = _normalize_source_uri(s_uri).split("#", 1)[0].rstrip("/")
        if not s.startswith("raw/"):
            continue
        p = Path(s)
        prefixes.append(s + "/" if not p.suffix else (p.parent / p.stem).as_posix())
    return prefixes


def cmd_prune_raw(repo_root: Path, max_size_bytes: int = 1_000_000, dry_run: bool = False) -> int:
    """Delete studied audio/video and `raw/_chunks/`, leaving a fingerprint `.receipt.json` behind.

    Safety contract (v2.1):
    - ONLY audio/video extensions and `raw/_chunks/` are ever deleted. Previously every non-book file
      > 1 MB was deleted too, which destroyed user reference material such as `.psd`/`.kra`/`.blend`/
      `.png` that is not tracked by git and cannot be recovered.
    - Media belonging to a source that still has unfinished reusable units is kept (per-source gate),
      instead of blocking every prune in the whole library while any single book is in progress.
    - Hashing is streamed, so multi-GB lecture videos do not have to fit in RAM.
    `max_size_bytes` is kept for CLI backward compatibility and no longer deletes non-media files.
    """
    import shutil

    from wiki_core import sha256_file

    del max_size_bytes
    pending_sources = _get_pending_source_units(repo_root)
    protected_prefixes = _source_protection_prefixes(pending_sources)
    if pending_sources:
        print("⏳ Keeping media of sources that still have unfinished reusable units:", file=sys.stderr)
        for s_uri, pend_list in pending_sources:
            print(f"  - {s_uri}: {len(pend_list)} pending -> {', '.join(pend_list[:5])}", file=sys.stderr)

    raw_root = repo_root / "raw"
    if not raw_root.is_dir():
        return 0

    verb = "Would prune" if dry_run else "Pruned"
    freed_bytes = 0
    kept_pending = 0
    preserved_books = 0
    chunks_dir = raw_root / "_chunks"
    if chunks_dir.is_dir() and not pending_sources:
        for p in chunks_dir.rglob("*"):
            if p.is_file():
                freed_bytes += p.stat().st_size
        if not dry_run:
            shutil.rmtree(chunks_dir, ignore_errors=True)
    elif chunks_dir.is_dir():
        print("⏳ Keeping raw/_chunks/ because at least one source is still being studied.", file=sys.stderr)

    for fpath in sorted(raw_root.rglob("*")):
        if not fpath.is_file() or "_chunks" in fpath.relative_to(raw_root).parts:
            continue
        suffix = fpath.suffix.lower()
        if suffix in {".pdf", ".epub", ".djvu", ".mobi", ".azw3"}:
            preserved_books += 1
            receipt_path = fpath.with_name(fpath.name + ".receipt.json")
            if not receipt_path.exists() and not dry_run:
                receipt_data = {
                    "originalFile": fpath.name,
                    "sizeBytes": fpath.stat().st_size,
                    "sha256": sha256_file(fpath),
                    "recordedAt": now_iso(),
                    "note": "Reference textbook preserved on disk for future consultation; fingerprint recorded for Grounded Claims.",
                }
                receipt_path.write_text(json.dumps(receipt_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            continue
        if suffix not in PRUNABLE_MEDIA_EXTS:
            continue  # images, project files, transcripts, receipts, notes: never deleted
        rel = fpath.relative_to(repo_root).as_posix()
        if any(rel.startswith(pref) for pref in protected_prefixes):
            kept_pending += 1
            continue
        fsize = fpath.stat().st_size
        freed_bytes += fsize
        if dry_run:
            print(f"  would prune {rel} ({fsize / (1024 * 1024):.1f} MB)")
            continue
        receipt_path = fpath.with_name(fpath.name + ".receipt.json")
        receipt_data = {
            "originalFile": fpath.name,
            "sizeBytes": fsize,
            "sha256": sha256_file(fpath),
            "prunedAt": now_iso(),
            "note": "Heavy raw audio/video media pruned after /study to save disk space; fingerprint preserved for Grounded Claims.",
        }
        receipt_path.write_text(json.dumps(receipt_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fpath.unlink()

    freed_mb = freed_bytes / (1024 * 1024)
    print(
        f"🧹 {verb} studied raw audio/video & chunks: {freed_mb:.2f} MB "
        f"(📚 {preserved_books} PDF/EPUB textbooks preserved; ⏳ {kept_pending} media files kept for in-progress sources)."
    )
    return 0


def _git(repo_root: Path, *args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    res = subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True, text=True, env=env)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def _git_dir_size_bytes(repo_root: Path) -> int:
    git_dir = Path(_git(repo_root, "rev-parse", "--absolute-git-dir"))
    return sum(p.stat().st_size for p in git_dir.rglob("*") if p.is_file())


def cmd_compact_git(
    repo_root: Path,
    keep_recent: int = 15,
    force: bool = False,
    dry_run: bool = False,
    backup: bool = True,
) -> int:
    """Rewrite the current branch to `<squashed root> + last N commits` and garbage-collect the rest.

    v2.0 used `git replace --graft`, which only overlays a *local* view: `git gc` keeps the original
    objects reachable, so no disk space was freed, while `reflog expire --expire=now --all` destroyed
    the only undo path. v2.1 really rebuilds the last N commits on top of a squashed root (same trees,
    messages, authors and dates, so the working tree is untouched), writes a `git bundle` backup outside
    the repo first, and refuses to rewrite history that already exists on the upstream remote unless
    `--force` is given (that would require a force-push).
    """
    import os
    import time

    if keep_recent < 1:
        print("❌ --keep must be >= 1.", file=sys.stderr)
        return 1
    try:
        branch = _git(repo_root, "symbolic-ref", "--short", "HEAD")
    except RuntimeError:
        print("❌ HEAD is detached; check out the branch you want to compact first.", file=sys.stderr)
        return 1

    # Drop v2.0 graft overlays first: they hide the real history from `rev-list` and confuse everything.
    legacy = [
        ln.split()[0]
        for ln in _git(repo_root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/replace/").splitlines()
        if ln.strip()
    ]
    for ref in legacy:
        replacement = _git(repo_root, "for-each-ref", "--format=%(objectname)", ref)
        msg = _git(repo_root, "--no-replace-objects", "log", "-1", "--format=%s", replacement, check=False)
        if msg.startswith("chore(compact)") and not dry_run:
            _git(repo_root, "update-ref", "-d", ref)

    head = _git(repo_root, "--no-replace-objects", "rev-parse", "HEAD")
    kept = _git(repo_root, "--no-replace-objects", "rev-list", "--first-parent", f"--max-count={keep_recent}", head).split()
    kept.reverse()  # oldest -> newest
    base = _git(repo_root, "--no-replace-objects", "rev-parse", "--verify", "--quiet", f"{kept[0]}^", check=False)
    if not base:
        print(f"ℹ️  Branch '{branch}' has <= {keep_recent} commits on its first-parent chain; nothing to compact.")
        return 0
    squashed_count = int(_git(repo_root, "--no-replace-objects", "rev-list", "--count", base) or "0")

    upstream = _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if upstream:
        on_remote = subprocess.run(
            ["git", "--no-replace-objects", "merge-base", "--is-ancestor", base, upstream],
            cwd=str(repo_root), capture_output=True,
        ).returncode == 0
        if on_remote and not force:
            print(
                f"⛔ The {squashed_count} commits to squash are already on '{upstream}'. Rewriting them would make "
                f"'{branch}' diverge and require `git push --force`.\n"
                "   Re-run with `--force` only if you really want that (other machines must then re-clone or hard-reset).",
                file=sys.stderr,
            )
            return 1

    print(f"🗜️  Plan: squash {squashed_count} old commits of '{branch}' into one root, keep the last {len(kept)} commits.")
    if dry_run:
        return 0

    size_before = _git_dir_size_bytes(repo_root)
    if backup:
        backup_dir = Path(os.environ.get("AGENTSTACKS_BACKUP_DIR", Path.home() / ".cache" / "agentstacks" / "backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)
        bundle = backup_dir / f"{repo_root.name}-{branch.replace('/', '_')}-pre-compact-{time.strftime('%Y%m%d-%H%M%S')}.bundle"
        _git(repo_root, "--no-replace-objects", "bundle", "create", str(bundle), branch)
        print(f"💾 Full pre-compact backup: {bundle} ({bundle.stat().st_size / 1024 / 1024:.1f} MB) — "
              f"restore with `git fetch {bundle} {branch}:restored`; delete it once you are satisfied.")

    def _commit_env(sha: str) -> dict[str, str]:
        fields = _git(repo_root, "--no-replace-objects", "show", "-s", "--date=raw",
                      "--format=%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd", sha).split("\x00")
        env = os.environ.copy()
        env.update({
            "GIT_AUTHOR_NAME": fields[0], "GIT_AUTHOR_EMAIL": fields[1], "GIT_AUTHOR_DATE": fields[2],
            "GIT_COMMITTER_NAME": fields[3], "GIT_COMMITTER_EMAIL": fields[4], "GIT_COMMITTER_DATE": fields[5],
        })
        return env

    root_env = _commit_env(base)
    root_msg = f"chore(compact): squashed {squashed_count} historical commits (up to {base[:10]})"
    new_parent = _git(repo_root, "--no-replace-objects", "commit-tree", f"{base}^{{tree}}", "-m", root_msg, env=root_env)
    for sha in kept:
        message = _git(repo_root, "--no-replace-objects", "log", "-1", "--format=%B", sha)
        new_parent = _git(
            repo_root, "--no-replace-objects", "commit-tree", f"{sha}^{{tree}}", "-p", new_parent, "-m", message or "(no message)",
            env=_commit_env(sha),
        )
    # Compare-and-swap so a concurrent commit is never lost.
    _git(repo_root, "update-ref", "-m", f"agentstacks compact-git --keep {keep_recent}", f"refs/heads/{branch}", new_parent, head)
    if _git(repo_root, "rev-parse", f"{new_parent}^{{tree}}") != _git(repo_root, "rev-parse", f"{head}^{{tree}}"):
        raise RuntimeError("compact-git invariant violated: rebuilt HEAD tree differs from original HEAD tree")

    _git(repo_root, "reflog", "expire", "--expire=now", "--all", check=False)
    _git(repo_root, "gc", "--prune=now", "--quiet", check=False)
    size_after = _git_dir_size_bytes(repo_root)
    print(
        f"✅ '{branch}' now has {len(kept) + 1} commits; .git {size_before / 1024 / 1024:.1f} MB -> {size_after / 1024 / 1024:.1f} MB."
        + ("\n⚠️  History was rewritten: push with `git push --force-with-lease`." if upstream else "")
    )
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

    # Normalize paths/aliases/filenames to canonical page ids: search & trace-diagnose look telemetry up by
    # page id, so `--helpful foundations/x/01_craft_playbooks/a.md` used to be counted under a dead key.
    from wiki_core import discover_wiki_pages

    canon: dict[str, str] = {}
    for p in discover_wiki_pages(repo_root):
        for key in (p.page_id, p.rel_path, p.rel_path.removesuffix(".md"), f"wiki/{p.rel_path}", p.abs_path.stem, *p.aliases):
            canon.setdefault(key, p.page_id)

    def _canon(ids: list[str]) -> list[str]:
        out: list[str] = []
        for raw_id in ids:
            cid = canon.get(raw_id.strip(), raw_id.strip())
            if cid not in canon.values():
                print(f"⚠️  record-trace: '{raw_id}' does not match any wiki card id/path; recorded as-is.", file=sys.stderr)
            if cid and cid not in out:
                out.append(cid)
        return out

    helpful_cards = _canon(helpful_cards)
    harmful_cards = _canon(harmful_cards)

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


def _stage_wiki_changes(repo_root: Path) -> None:
    """Stage every file a wiki operation may touch.

    Besides the wiki/config trees this stages the lightweight primary-source artifacts under raw/
    (`*.receipt.json` fingerprints and `.vtt`/`.srt` transcripts). v2.0 never staged raw/, so on a second
    machine every raw:// claim pointed at a gitignored PDF/video with no receipt and could not be verified.
    Heavy media stays excluded by .gitignore.
    """
    stage_targets = [
        t
        for t in ("GEMINI.md", "CLAUDE.md", "AGENTS.md", "README.md", "README_EN.md", "SCHEMA.md", "Cargo.toml", "src", "wiki", ".agents", ".claude", ".codex", ".cursor")
        if (repo_root / t).exists() or (repo_root / t).is_symlink()
    ]
    subprocess.run(["git", "add", "-A", "--", *stage_targets], cwd=str(repo_root), check=False)
    if (repo_root / "raw").is_dir():
        for pattern in ("raw/**/*.receipt.json", "raw/**/*.vtt", "raw/**/*.srt"):
            # One pathspec per call: a non-matching pathspec makes `git add` abort the whole batch.
            subprocess.run(["git", "add", "-A", "--", f":(glob){pattern}"], cwd=str(repo_root), capture_output=True, check=False)


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
    _stage_wiki_changes(repo_root)

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
    """Remediation Channel 4: Safely revert a specific study/refine/lint commit without losing later work.

    The re-finalized indexes are folded into the same revert commit, so the working tree is clean
    afterwards (v2.0 left regenerated index files uncommitted after every rollback).
    """
    subject = _git(repo_root, "log", "-1", "--format=%s", target_commit, check=False)
    res = subprocess.run(["git", "revert", "--no-commit", target_commit], cwd=str(repo_root), capture_output=True, text=True)
    if res.returncode != 0:
        subprocess.run(["git", "revert", "--abort"], cwd=str(repo_root), capture_output=True)
        print(f"❌ Git revert failed (aborted, tree unchanged): {res.stderr.strip()}", file=sys.stderr)
        return 1
    finalize_wiki(repo_root, actor="rollback/1.0")
    _stage_wiki_changes(repo_root)
    res = subprocess.run(
        ["git", "-c", "user.name=LLM-Wiki-Agent", "-c", "user.email=wiki@local", "commit", "-m",
         f'Revert "{subject}"\n\nThis reverts commit {_git(repo_root, "rev-parse", target_commit, check=False)} '
         "and re-synchronizes generated wiki indexes."],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    if res.returncode != 0:
        print(f"❌ Git commit of the revert failed: {res.stderr.strip() or res.stdout.strip()}", file=sys.stderr)
        return 1
    print(f"✅ Successfully reverted commit {target_commit} and re-synchronized wiki indexes in the same commit.")
    return 0



def _tokenize_query(q_str: str) -> list[str]:
    from wiki_search_engine import tokenize_query

    return tokenize_query(q_str)


def cmd_search(
    repo_root: Path,
    query_parts: list[str],
    domain_filter: str | None = None,
    top_k: int = 10,
    as_json: bool = False,
    explain: bool = False,
) -> int:
    """Unified 4-Channel Recall-First + Faceted-Precision Search Engine (shared with `wiki_query.py search`)."""
    from wiki_search_engine import execute_hybrid_search

    q_str = " ".join(query_parts).strip()
    if not q_str:
        print("⚠️ Please provide search keywords.", file=sys.stderr)
        return 1
    return execute_hybrid_search(
        repo_root,
        query=q_str,
        domain_filter=domain_filter,
        limit=top_k,
        as_json=as_json,
        explain=explain,
    )


def cmd_retrieval_bench(
    repo_root: Path,
    bench_file: Path | None = None,
    as_json: bool = False,
) -> int:
    """Evaluate cross-domain recall, MRR, and negative-trap rejection against `golden_retrieval_queries.json`."""
    import time

    from wiki_core import discover_wiki_pages
    from wiki_search_engine import search_hybrid_ranked

    wiki_bench = repo_root / "wiki" / ".retrieval-bench.json"
    default_path = (
        wiki_bench
        if wiki_bench.is_file()
        else (Path(__file__).resolve().parent.parent / "tests" / "golden_retrieval_queries.json")
    )
    target_path = bench_file or default_path
    if not target_path.is_file():
        if bench_file is None and not discover_wiki_pages(repo_root):
            msg = (
                "ℹ️ Clean Day-0 framework (0 cards currently ingested). "
                "Ingest cards via `/study` and add custom benchmark queries in `wiki/.retrieval-bench.json` "
                "(or pass `--file <queries.json>`)."
            )
            if as_json:
                print(json.dumps({"totalQueries": 0, "status": "day0_empty", "message": msg}, ensure_ascii=False))
            else:
                print(msg)
            return 0
        print(f"❌ Benchmark file not found: {target_path}", file=sys.stderr)
        return 1

    bench_data = json.loads(target_path.read_text(encoding="utf-8"))
    queries: list[dict[str, Any]] = bench_data.get("queries", [])
    if not queries:
        print("❌ Benchmark file contains 0 queries.", file=sys.stderr)
        return 1

    recall5_hits = 0
    recall10_hits = 0
    positive_cases = 0
    cross_domain_cases = 0
    cross_domain_hits = 0
    trap_cases = 0
    trap_rejections = 0
    rr_sum = 0.0
    case_details: list[dict[str, Any]] = []

    t0 = time.perf_counter()
    for q_case in queries:
        qid = str(q_case.get("id", ""))
        q_text = str(q_case.get("query", ""))
        category = str(q_case.get("category", "single_domain"))
        expected_any_ids = [str(x) for x in q_case.get("expected_any_ids", [])]
        expected_domains = [str(x) for x in q_case.get("expected_domains", [])]
        forbidden_top3_ids = {str(x) for x in q_case.get("forbidden_top3_ids", [])}

        res = search_hybrid_ranked(repo_root, q_text, limit=10)
        hits = res.get("results", [])
        top10_ids = [h["id"] for h in hits[:10]]
        top5_ids = top10_ids[:5]
        top3_ids = top10_ids[:3]
        # Include 1-Hop closure in recall coverage for cross-tier prerequisite checks
        closure_ids = [d["id"] for d in res.get("oneHopClosure", [])]
        top10_domains = {h["card"]["domain"] for h in hits[:10]} | {
            d["tierDomain"].split("/")[-1] for d in res.get("oneHopClosure", [])
        }

        passed_case = True
        first_rank = 0
        if expected_any_ids:
            positive_cases += 1
            for r_idx, hid in enumerate(top10_ids, 1):
                if hid in expected_any_ids:
                    first_rank = r_idx
                    break
            if first_rank == 0 and any(eid in closure_ids for eid in expected_any_ids):
                first_rank = 10
            if 1 <= first_rank <= 5:
                recall5_hits += 1
            if first_rank >= 1:
                recall10_hits += 1
                rr_sum += 1.0 / first_rank
            else:
                passed_case = False

        dom_ok = True
        if len(expected_domains) >= 2:
            cross_domain_cases += 1
            dom_ok = all(ed in top10_domains for ed in expected_domains)
            if dom_ok:
                cross_domain_hits += 1
            else:
                passed_case = False

        trap_ok = True
        if forbidden_top3_ids:
            trap_cases += 1
            trap_ok = not bool(forbidden_top3_ids & set(top3_ids))
            if trap_ok:
                trap_rejections += 1
            else:
                passed_case = False

        case_details.append({
            "id": qid,
            "category": category,
            "query": q_text,
            "passed": passed_case,
            "firstRank": first_rank,
            "domainsMatched": sorted(top10_domains & set(expected_domains)) if expected_domains else sorted(top10_domains)[:3],
            "trapRejected": trap_ok,
            "top3": top3_ids,
        })

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    r5 = round(100.0 * recall5_hits / max(1, positive_cases), 1)
    r10 = round(100.0 * recall10_hits / max(1, positive_cases), 1)
    cd_r10 = round(100.0 * cross_domain_hits / max(1, cross_domain_cases), 1) if cross_domain_cases else 100.0
    mrr = round(rr_sum / max(1, positive_cases), 3)
    trap_rate = round(100.0 * trap_rejections / max(1, trap_cases), 1) if trap_cases else 100.0

    summary = {
        "totalQueries": len(queries),
        "elapsedMs": round(elapsed_ms, 1),
        "avgLatencyMs": round(elapsed_ms / max(1, len(queries)), 2),
        "recallAt5Pct": r5,
        "recallAt10Pct": r10,
        "crossDomainRecallAt10Pct": cd_r10,
        "mrr": mrr,
        "negativeTrapRejectionPct": trap_rate,
        "cases": case_details,
    }

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("=" * 84)
        print(f"🎯 AgentStacks v3.0 Golden Retrieval Benchmark ({len(queries)} Queries in {elapsed_ms:.1f} ms)")
        print("=" * 84)
        print(f"  • Recall@5                     : {r5:.1f}% ({recall5_hits}/{positive_cases})")
        print(f"  • Recall@10                    : {r10:.1f}% ({recall10_hits}/{positive_cases})")
        print(f"  • Cross-Domain Recall@10       : {cd_r10:.1f}% ({cross_domain_hits}/{cross_domain_cases})")
        print(f"  • Mean Reciprocal Rank (MRR)   : {mrr:.3f}")
        print(f"  • Negative-Trap Rejection Rate : {trap_rate:.1f}% ({trap_rejections}/{trap_cases})")
        print(f"  • Avg Query Latency            : {summary['avgLatencyMs']:.2f} ms/query")
        failures = [c for c in case_details if not c["passed"]]
        if failures:
            print("\n⚠️  Failed Benchmark Cases:")
            for fc in failures:
                print(f"  - [{fc['id']}] {fc['query']} -> top3={fc['top3']}")
        else:
            print("\n✅ All golden retrieval & cross-domain synergy cases PASSED.")

    return 0 if (r10 >= 90.0 and cd_r10 >= 90.0 and trap_rate >= 95.0) else 1


def cmd_verify_suite(repo_root: Path, domain_filter: str = "all", artifact_path: str | None = None) -> int:
    """HarnessPro-style Frozen Executable Test Suite Runner with RTK Compact Output."""
    import os

    from wiki_core import resolve_cargo, rust_subprocess_env, wiki_rust_scripts

    wiki_root = repo_root / "wiki"
    s_idx_path = wiki_root / ".search-index.json"
    if not s_idx_path.exists():
        finalize_wiki(repo_root, actor="verify-suite/1.0")
    s_data = json.loads(s_idx_path.read_text(encoding="utf-8"))
    cards: list[dict[str, Any]] = s_data.get("cards", [])

    script_to_cards: dict[str, list[str]] = {}
    target_scripts: list[Path] = []
    py_verifiers: list[Path] = []

    for c in cards:
        dom_key = f"{c['tier']}/{c['domain']}"
        if domain_filter != "all" and domain_filter.lower() not in dom_key.lower():
            continue
        for s_obj in c.get("scripts", []):
            raw_p = str(s_obj.get("path", ""))
            sp = repo_root / raw_p if raw_p.startswith("wiki/") else wiki_root / raw_p  # same rule as the linter
            if sp.exists():
                stem = sp.stem
                script_to_cards.setdefault(stem, [])
                if c["id"] not in script_to_cards[stem]:
                    script_to_cards[stem].append(c["id"])
                if sp not in target_scripts:
                    target_scripts.append(sp)
                if sp.suffix == ".py" and "verifier" in str(s_obj.get("role", "")) and sp not in py_verifiers:
                    py_verifiers.append(sp)

    for sp in wiki_rust_scripts(wiki_root):
        rel_w = sp.relative_to(wiki_root).as_posix()
        if domain_filter != "all" and domain_filter.lower() not in rel_w.lower():
            continue
        if sp not in target_scripts:
            target_scripts.append(sp)

    rs_bins = [sp.stem for sp in target_scripts if sp.suffix == ".rs"]
    print(f"=== 🛡️ HarnessPro Frozen Verifier Suite (domain={domain_filter}, rust_targets={len(rs_bins)}, py_verifiers={len(py_verifiers)}) ===")

    failed_any = False
    cargo_cmd = resolve_cargo()
    if rs_bins and (repo_root / "Cargo.toml").exists():
        # A verifier written since the last finalize must still be graded, never skipped as "no bin target".
        from wiki_finalize import sync_cargo_bins

        sync_cargo_bins(repo_root / "Cargo.toml", repo_root, wiki_rust_scripts(wiki_root))
    if rs_bins and not cargo_cmd:
        print("❌ [FAIL] Rust verifiers exist but `cargo` is not installed (https://rustup.rs).")
        return 1
    if rs_bins:
        bin_args = [a for b in rs_bins for a in ("--bin", b)]
        res = subprocess.run([cargo_cmd, "test", "--quiet", *bin_args], cwd=str(repo_root), capture_output=True, text=True, env=rust_subprocess_env())
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
        print(f"--- Running {len(verifiers)} Rust + {len(py_verifiers)} Python Frozen Verifiers on Artifact: {art_p.name} ---")
        if verifiers:
            # Always (incrementally) rebuild first: executing a stale target/debug binary would grade the
            # artifact against an old version of the rule and report a false PASS.
            b_args = [a for v in verifiers for a in ("--bin", v.stem)]
            build = subprocess.run([cargo_cmd, "build", "--quiet", *b_args], cwd=str(repo_root), capture_output=True, text=True, env=rust_subprocess_env())
            if build.returncode != 0:
                print(f"❌ [FAIL] cargo build of verifiers failed:\n{build.stderr.strip()}")
                return 1
        exe_suffix = ".exe" if os.name == "nt" else ""
        runs: list[tuple[str, list[str]]] = [
            (vp.stem, [str(repo_root / "target" / "debug" / f"{vp.stem}{exe_suffix}"), str(art_p)]) for vp in verifiers
        ] + [(vp.stem, [sys.executable, str(vp), str(art_p)]) for vp in py_verifiers]
        for stem, run_cmd in runs:
            gov_cards = script_to_cards.get(stem, ["(domain-general)"])
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
    print("RULE: Before editing, consolidating, or merging ANY card, you MUST read its primary raw/ files directly (`read_file` / `Read`), NEVER secondary card text!\n")

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
        print("• Available Pareto Reading Modes: `--mode lite` (<=6KB) | `--mode medium` (<=20KB) | `--mode heavy` (<=40KB)")
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


_MANAGED_SKILLS = ("study", "stacks", "organize")
_CURSOR_RULE_DESCRIPTIONS: dict[str, str] = {
    "agentstacks": "AgentStacks (书栈) Global Constitution & L0 Prefix-Cache-Pinned Router",
    "study": "Invoke ONLY when user explicitly asks for /study or @study to ingest a textbook/video from raw/.",
    "stacks": "Invoke when user asks for /stacks or @stacks to consult the AgentStacks library and refine at turn-end.",
    "organize": "Invoke ONLY when user explicitly asks for /organize or @organize to consolidate and audit AgentStacks.",
}


def _extract_yaml_frontmatter_block(text: str) -> tuple[str, str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return "", normalized
    end_idx = normalized.find("\n---\n", 4)
    if end_idx != -1:
        return normalized[4:end_idx], normalized[end_idx + 5 :]
    if normalized.endswith("\n---"):
        return normalized[4:-4], ""
    return "", normalized


def _strip_yaml_frontmatter(text: str) -> str:
    _, body = _extract_yaml_frontmatter_block(text)
    return body.lstrip("\n")


def _normalize_prompt_links(text: str) -> str:
    return text.replace("(prompts/", "(.agents/skills/study/prompts/").replace("[prompts/", "[.agents/skills/study/prompts/")


def _render_cursor_mdc(description: str, always_apply: bool, body: str) -> str:
    escaped_desc = description.replace('"', '\\"')
    flag = "true" if always_apply else "false"
    return f'---\ndescription: "{escaped_desc}"\nglobs:\nalwaysApply: {flag}\n---\n\n{body.rstrip()}\n'


def _render_claude_command(skill_text: str) -> str:
    fm, body = _extract_yaml_frontmatter_block(skill_text)
    cleaned_body = _normalize_prompt_links(body).lstrip("\n").rstrip()
    if not fm.strip():
        return cleaned_body + "\n"
    kept_lines = [line for line in fm.splitlines() if not line.startswith("name:")]
    if not kept_lines:
        return cleaned_body + "\n"
    return "---\n" + "\n".join(kept_lines) + "\n---\n\n" + cleaned_body + "\n"


def _global_shadow_warnings(repo_root: Path) -> list[str]:
    warnings: list[str] = []
    repo_resolved = repo_root.resolve()
    for base in (Path.home() / ".codex" / "skills", Path.home() / ".claude" / "skills"):
        if not base.exists():
            continue
        for skill in _MANAGED_SKILLS:
            cand = base / skill
            if cand.exists() or cand.is_symlink():
                try:
                    resolved = cand.resolve()
                    is_inside = resolved == repo_resolved or repo_resolved in resolved.parents
                except OSError:
                    is_inside = False
                if not is_inside:
                    warnings.append(f"⚠️ Global skill shadow detected: {cand} (may shadow repo-local /{skill})")
    return warnings


def cmd_link_harnesses(repo_root: Path, copy_mode: bool = False, check_only: bool = False) -> int:
    """Sync multi-harness adapters (AGENTS.md, CLAUDE.md, GEMINI.md, .claude, .codex, .cursor) via symlinks or --copy."""
    agents_path = repo_root / "AGENTS.md"
    if not agents_path.exists():
        print("❌ AGENTS.md not found at repo root.", file=sys.stderr)
        return 1
    agents_text = agents_path.read_text(encoding="utf-8")

    skill_texts: dict[str, str] = {}
    for skill in _MANAGED_SKILLS:
        s_path = repo_root / ".agents" / "skills" / skill / "SKILL.md"
        if not s_path.exists():
            print(f"❌ Canonical skill missing: {s_path}", file=sys.stderr)
            return 1
        skill_texts[skill] = s_path.read_text(encoding="utf-8")

    expected_cursor: dict[Path, str] = {
        repo_root / ".cursor" / "rules" / "agentstacks.mdc": _render_cursor_mdc(
            _CURSOR_RULE_DESCRIPTIONS["agentstacks"],
            True,
            _strip_yaml_frontmatter(agents_text),
        )
    }
    expected_claude_cmds: dict[Path, str] = {}
    for skill in _MANAGED_SKILLS:
        expected_cursor[repo_root / ".cursor" / "rules" / f"{skill}.mdc"] = _render_cursor_mdc(
            _CURSOR_RULE_DESCRIPTIONS[skill],
            False,
            _normalize_prompt_links(_strip_yaml_frontmatter(skill_texts[skill])),
        )
        expected_claude_cmds[repo_root / ".claude" / "commands" / f"{skill}.md"] = _render_claude_command(skill_texts[skill])

    symlink_specs: list[tuple[Path, str]] = [
        (repo_root / "CLAUDE.md", "AGENTS.md"),
        (repo_root / "GEMINI.md", "AGENTS.md"),
        (repo_root / ".claude" / "skills", "../.agents/skills"),
        (repo_root / ".codex" / "skills", "../.agents/skills"),
    ]

    if check_only:
        issues: list[str] = []
        effective_copy = copy_mode or not any(p.is_symlink() for p, _ in symlink_specs)
        if effective_copy:
            for root_doc in (repo_root / "CLAUDE.md", repo_root / "GEMINI.md"):
                if root_doc.is_symlink():
                    issues.append(f"{root_doc.name} is a symlink in --copy mode")
                elif not root_doc.exists():
                    issues.append(f"Missing {root_doc.name}")
                elif root_doc.read_text(encoding="utf-8") != agents_text:
                    issues.append(f"Drifted root instructions copy: {root_doc.name} != AGENTS.md")
            for sym_dir in (repo_root / ".claude" / "skills", repo_root / ".codex" / "skills"):
                if sym_dir.is_symlink():
                    issues.append(f"Unexpected symlink in --copy mode: {sym_dir.relative_to(repo_root)}")
            for cmd_path, want in expected_claude_cmds.items():
                rel = cmd_path.relative_to(repo_root)
                if not cmd_path.exists():
                    issues.append(f"Missing Claude command copy: {rel}")
                elif cmd_path.read_text(encoding="utf-8") != want:
                    issues.append(f"Drifted Claude command copy: {rel}")
        else:
            for link_path, target in symlink_specs:
                rel = link_path.relative_to(repo_root)
                if not link_path.is_symlink():
                    issues.append(f"Expected symlink missing or not a symlink: {rel} -> {target}")
                elif os.readlink(link_path) != target:
                    issues.append(f"Symlink target mismatch for {rel}: {os.readlink(link_path)} != {target}")
            for cmd_path in expected_claude_cmds:
                if cmd_path.exists() or cmd_path.is_symlink():
                    issues.append(f"Legacy duplicate Claude command present in symlink mode: {cmd_path.relative_to(repo_root)}")

        for mdc_path, want in expected_cursor.items():
            rel = mdc_path.relative_to(repo_root)
            if not mdc_path.exists():
                issues.append(f"Missing Cursor rule: {rel}")
            elif mdc_path.read_text(encoding="utf-8") != want:
                issues.append(f"Drifted Cursor rule: {rel}")

        for w in _global_shadow_warnings(repo_root):
            print(w)

        if issues:
            for iss in issues:
                print(f"❌ {iss}", file=sys.stderr)
            return 1
        mode_label = "copy" if effective_copy else "symlink"
        print(f"✅ Harness parity check PASSED ({mode_label} mode; Cursor rules & multi-harness adapters synced).")
        return 0

    changed_count = 0
    if copy_mode:
        for doc_path in (repo_root / "CLAUDE.md", repo_root / "GEMINI.md"):
            if doc_path.is_symlink():
                doc_path.unlink()
                changed_count += 1
            if write_text_if_changed(doc_path, agents_text):
                changed_count += 1
        for sym_dir in (repo_root / ".claude" / "skills", repo_root / ".codex" / "skills"):
            if sym_dir.is_symlink():
                sym_dir.unlink()
                changed_count += 1
        for cmd_path, content in expected_claude_cmds.items():
            if cmd_path.is_symlink():
                cmd_path.unlink()
                changed_count += 1
            if write_text_if_changed(cmd_path, content):
                changed_count += 1
    else:
        for link_path, target in symlink_specs:
            if link_path.is_symlink() and os.readlink(link_path) == target:
                continue
            link_path.parent.mkdir(parents=True, exist_ok=True)
            if link_path.is_symlink() or link_path.is_file():
                link_path.unlink()
            elif link_path.exists():
                print(
                    f"❌ Refusing to overwrite non-symlink directory `{link_path.relative_to(repo_root)}`; move it aside first.",
                    file=sys.stderr,
                )
                return 1
            os.symlink(target, link_path)
            changed_count += 1

        for cmd_path in expected_claude_cmds:
            if cmd_path.exists() or cmd_path.is_symlink():
                cmd_path.unlink()
                changed_count += 1
        cmd_dir = repo_root / ".claude" / "commands"
        if cmd_dir.is_dir() and not any(cmd_dir.iterdir()):
            cmd_dir.rmdir()

    for mdc_path, content in expected_cursor.items():
        if write_text_if_changed(mdc_path, content):
            changed_count += 1

    for w in _global_shadow_warnings(repo_root):
        print(w)

    mode_label = "copy" if copy_mode else "symlink"
    print(f"✅ link-harnesses ({mode_label} mode) complete ({changed_count} item(s) updated).")
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
        help="Audit 100%% full-book / full-video coverage across wiki/.source-coverage.json and raw/ to prevent partial-skimming rework",
    )
    p_ca.add_argument("--domain", default="all", help="Optional domain filter (or 'all')")

    p_rb = sub.add_parser("rollback", help="Revert a specific commit atomically")
    p_rb.add_argument("--commit", default="HEAD", help="Commit hash or HEAD")

    p_prune = sub.add_parser("prune-raw", help="Delete studied raw video/audio/chunks and leave 1KB .receipt.json (never touches other files)")
    p_prune.add_argument("--dry-run", action="store_true", help="Only list what would be deleted")
    p_cg = sub.add_parser("compact-git", help="Rebuild branch as squashed root + last N commits, then gc (writes a bundle backup first)")
    p_cg.add_argument("--keep-recent", "--keep", dest="keep_recent", type=int, default=15)
    p_cg.add_argument("--force", action="store_true", help="Allow rewriting commits that are already on the upstream remote")
    p_cg.add_argument("--dry-run", action="store_true", help="Only print the plan")
    p_cg.add_argument("--no-backup", action="store_true", help="Skip the pre-compact git bundle backup")

    p_search = sub.add_parser("search", help="Unified 4-Channel Recall-First + Faceted-Precision search across all Wiki cards")
    p_search.add_argument("query", nargs="+", help="Search keywords (Chinese or English)")
    p_search.add_argument("--domain", default=None, help="Optional domain filter, e.g. music_theory_acoustics or visual_aesthetics")
    p_search.add_argument("--top", "--limit", dest="top", type=int, default=10, help="Max top cards to return")
    p_search.add_argument("--json", action="store_true", help="Emit structured JSON output")
    p_search.add_argument("--explain", action="store_true", help="Show per-channel score breakdown")

    p_rb_bench = sub.add_parser(
        "retrieval-bench",
        help="Run the 30-query Golden Retrieval & Cross-Domain Synergy Benchmark",
    )
    p_rb_bench.add_argument("--file", type=Path, default=None, help="Optional custom golden queries JSON path")
    p_rb_bench.add_argument("--json", action="store_true", help="Emit benchmark metrics as JSON")

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

    p_lh = sub.add_parser(
        "link-harnesses",
        help="Sync or verify multi-harness adapters (CLAUDE.md, GEMINI.md, .claude, .codex, .cursor)",
    )
    p_lh.add_argument("--copy", action="store_true", help="Write regular file copies (Windows-safe) instead of POSIX symlinks")
    p_lh.add_argument("--check", action="store_true", help="Verify multi-harness adapter parity without modifying files")

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
        return cmd_prune_raw(repo_root, dry_run=args.dry_run)
    if args.cmd == "compact-git":
        return cmd_compact_git(repo_root, args.keep_recent, force=args.force, dry_run=args.dry_run, backup=not args.no_backup)
    if args.cmd == "search":
        return cmd_search(repo_root, args.query, args.domain, args.top, as_json=args.json, explain=args.explain)
    if args.cmd == "retrieval-bench":
        return cmd_retrieval_bench(repo_root, bench_file=args.file, as_json=args.json)
    if args.cmd == "verify-suite":
        return cmd_verify_suite(repo_root, args.domain, args.artifact)
    if args.cmd == "inspect-sources":
        return cmd_inspect_sources(repo_root, args.targets, args.domain)
    if args.cmd == "skilltrain-audit":
        return cmd_skilltrain_audit(repo_root, args.domain)
    if args.cmd == "trace-diagnose":
        return cmd_trace_diagnose(repo_root, args.domain)
    if args.cmd == "link-harnesses":
        return cmd_link_harnesses(repo_root, copy_mode=args.copy, check_only=args.check)
    return 1


if __name__ == "__main__":
    sys.exit(main())

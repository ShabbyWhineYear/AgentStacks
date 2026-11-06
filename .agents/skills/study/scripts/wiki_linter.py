#!/usr/bin/env python3
"""Wiki Linter & Test Gate (ported from skill-training/skilltrain/memory.py & OpenWiki OKF validator).

Enforces:
1. Required OKF v0.2 + Schema v1 frontmatter fields (`id`, `type`, `title`, `description`, `epistemic_status`).
2. Single-line `description` <= 800 chars without multi-line wrapping.
3. Hard body line/char caps on action cards (`core-rule` <= 45 lines, `craft-card`/`style-card` <= 60 lines, <= 4800 chars).
4. Prohibition of training post-mortem phrasing ("the model wrote X, the human wrote Y").
5. Cross-tier `depends_on` target resolution and bound `scripts` file existence.
6. Execution of all `scripts/tests/test_*.py` unit tests across the wiki.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from wiki_core import (
    ALL_PAGE_TYPES,
    CARD_TYPES,
    DOC_TYPES,
    EPISTEMIC_STATUSES,
    RESERVED_FILENAMES,
    TIERS,
    discover_wiki_pages,
    find_repo_root,
    parse_markdown,
    strip_deps_banner,
)

MAX_CORE_LINES = int(__import__("os").environ.get("WIKI_MAX_CORE_LINES", "45"))
MAX_CARD_LINES = int(__import__("os").environ.get("WIKI_MAX_CARD_LINES", "60"))
MAX_BODY_CHARS = int(__import__("os").environ.get("WIKI_MAX_BODY_CHARS", "4800"))
MAX_DESC_CHARS = int(__import__("os").environ.get("WIKI_MAX_DESCRIPTION_CHARS", "800"))
MAX_CORE_CARDS_PER_DOMAIN = int(__import__("os").environ.get("WIKI_MAX_CORE_CARDS_PER_DOMAIN", "5"))

FORBIDDEN_PATTERNS = [
    (
        re.compile(
            r"\b(the )?(AI|model|agent)('s)? (mistake|error|version|draft|tendency)|"
            r"\b(the )?human('s)? (version|standard|draft)|"
            r"the model wrote|human wrote|model draft|原稿对比中模型",
            re.IGNORECASE,
        ),
        "禁止在规则卡中记录训练期『模型写了X vs 人类写了Y』的过程叙事，只写面向下次任务的普适规则",
    ),
]


def lint_wiki(repo_root: Path, run_tests: bool = True) -> tuple[list[str], list[str]]:
    """Run all structural, frontmatter, length, dependency, and unit-test checks.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    pages = discover_wiki_pages(repo_root)
    id_map: dict[str, str] = {}
    rel_set: set[str] = set()
    alias_set: set[str] = set()
    core_counts: dict[str, int] = {}

    for p in pages:
        rel_set.add(p.rel_path)
        if "/00_core_aesthetics/" in p.rel_path:
            dom_key = f"{p.tier}/{p.domain}"
            core_counts[dom_key] = core_counts.get(dom_key, 0) + 1
        for a in p.aliases:
            alias_set.add(a)
        if p.page_id in id_map:
            errors.append(f"[{p.rel_path}] Duplicate page id '{p.page_id}' (already used by {id_map[p.page_id]})")
        else:
            id_map[p.page_id] = p.rel_path

    for dom_key, cnt in sorted(core_counts.items()):
        if cnt > MAX_CORE_CARDS_PER_DOMAIN:
            errors.append(
                f"[{dom_key}/00_core_aesthetics] Has {cnt} cards (limit is {MAX_CORE_CARDS_PER_DOMAIN}). "
                "00_core_aesthetics is read in full every time; merge core laws or move component playbooks to 01_craft_playbooks/."
            )

    for p in pages:
        raw_text = p.abs_path.read_text(encoding="utf-8")
        parsed = parse_markdown(raw_text)
        if not parsed.has_frontmatter:
            errors.append(f"[{p.rel_path}] Missing YAML frontmatter block (`--- ... ---`)")
            continue

        fm = parsed.frontmatter
        # 1. Required fields
        for req in ("id", "type", "title", "description", "epistemic_status"):
            if not fm.get(req):
                errors.append(f"[{p.rel_path}] Missing required frontmatter field: '{req}'")

        if p.page_type and p.page_type not in ALL_PAGE_TYPES:
            errors.append(f"[{p.rel_path}] Invalid type '{p.page_type}'. Allowed: {', '.join(ALL_PAGE_TYPES)}")

        if p.epistemic_status not in EPISTEMIC_STATUSES:
            errors.append(
                f"[{p.rel_path}] Invalid epistemic_status '{p.epistemic_status}'. Allowed: {', '.join(EPISTEMIC_STATUSES)}"
            )

        # 2. Single-line description check (ported from skill-training/memory.py)
        desc_lines = [ln for ln in parsed.raw_frontmatter_lines if ln.startswith("description:")]
        if not desc_lines:
            errors.append(f"[{p.rel_path}] 'description' must be a top-level single-line field")
        else:
            if len(p.description) > MAX_DESC_CHARS:
                errors.append(
                    f"[{p.rel_path}] 'description' length ({len(p.description)}) exceeds max {MAX_DESC_CHARS} chars"
                )
            if len(p.description) < 12:
                warnings.append(f"[{p.rel_path}] 'description' is very short; state clearly when to read and when to skip")

        # 3. Body length & structure check
        clean_body = p.clean_body.strip()
        body_lines = clean_body.splitlines() if clean_body else []
        num_lines = len(body_lines)
        num_chars = len(clean_body)

        if p.page_type in CARD_TYPES:
            limit = MAX_CORE_LINES if p.page_type == "core-rule" else MAX_CARD_LINES
            if num_lines > limit:
                errors.append(
                    f"[{p.rel_path}] Card body has {num_lines} lines (limit for {p.page_type} is {limit}). "
                    "Compress, split into two cards, move theory to 03_deep_concepts/, or extract a Python script."
                )
            if num_chars > MAX_BODY_CHARS:
                errors.append(
                    f"[{p.rel_path}] Card body has {num_chars} chars (limit is {MAX_BODY_CHARS})."
                )
            if "## Rules" not in clean_body and "## 规则" not in clean_body:
                warnings.append(f"[{p.rel_path}] Action card ({p.page_type}) should include a '## Rules' section")

        elif p.page_type in DOC_TYPES:
            if "## " not in clean_body:
                errors.append(
                    f"[{p.rel_path}] Reference/concept document ({p.page_type}) must contain at least one '## ' section heading for #section partial reading"
                )

        # 4. Provisional warning banner check
        if p.epistemic_status == "provisional" and "provisional" not in clean_body.lower() and "二手碎片" not in clean_body:
            warnings.append(
                f"[{p.rel_path}] Marked as epistemic_status: provisional, but body lacks a visible [!WARNING] scope notice"
            )

        # 5. Forbidden post-mortem phrases
        for regex, msg in FORBIDDEN_PATTERNS:
            if regex.search(clean_body):
                errors.append(f"[{p.rel_path}] {msg}")

        # 6. Verify depends_on targets exist
        for dep in p.depends_on:
            dep_clean = dep.split("#")[0].strip()
            if dep_clean not in id_map and dep_clean not in rel_set and dep_clean not in alias_set:
                errors.append(
                    f"[{p.rel_path}] Unresolved depends_on target '{dep}'. Target card ID or path does not exist in wiki/."
                )

        # 7. Check cross-tier foundation linkage for tools_craft action cards
        if p.tier == "tools_craft" and p.page_type in CARD_TYPES and not p.depends_on:
            warnings.append(
                f"[{p.rel_path}] Tool craft card has no 'depends_on' linking to foundations/. "
                "Consider linking the underlying aesthetic/theoretical foundation card."
            )

        # 8. Verify bound scripts exist
        for sc in p.scripts:
            sc_path_str = sc["path"]
            sc_path = repo_root / sc_path_str if sc_path_str.startswith("wiki/") else repo_root / "wiki" / sc_path_str
            if not sc_path.exists():
                errors.append(f"[{p.rel_path}] Bound script does not exist: '{sc_path_str}'")

    # 8b. Sirchmunk Context-Explosion & Hierarchical Tree Guards
    wiki_root = repo_root / "wiki"
    tree_idx_path = wiki_root / ".tree-index.json"
    if not tree_idx_path.exists():
        errors.append("[wiki/.tree-index.json] Missing PageIndex DocumentTree sidecar; run wiki_finalize.py.")
    gotchas_md = wiki_root / "GOTCHAS_AND_RETRY_SAVERS.md"
    if gotchas_md.exists() and len(gotchas_md.read_bytes()) > 16384:
        errors.append(
            f"[wiki/GOTCHAS_AND_RETRY_SAVERS.md] Exceeds 16 KB ({len(gotchas_md.read_bytes())} bytes); "
            "must remain a compact L1 Scoped Router."
        )
    dom_page_counts: dict[tuple[str, str], int] = {}
    for p in pages:
        dom_page_counts[(p.tier, p.domain)] = dom_page_counts.get((p.tier, p.domain), 0) + 1
    for (tier, dom), cnt in sorted(dom_page_counts.items()):
        qs_file = wiki_root / tier / dom / "quickstart.md"
        if qs_file.exists() and len(qs_file.read_bytes()) > 14336:
            errors.append(
                f"[wiki/{tier}/{dom}/quickstart.md] L1 quickstart exceeds 14 KB ({len(qs_file.read_bytes())} bytes); "
                "use L2 _subtrees/ routing."
            )
        if cnt > 15:
            st_dir = wiki_root / tier / dom / "_subtrees"
            if not st_dir.is_dir() or not list(st_dir.glob("*.md")):
                errors.append(
                    f"[wiki/{tier}/{dom}] Domain has {cnt} cards (>15) but lacks L2 `_subtrees/*.md` routers."
                )

    # 9. Run unit tests across all domain scripts/ (Python unittest + Rust rustc --test)
    if run_tests:
        wiki_root = repo_root / "wiki"
        test_dirs = [d for d in sorted(wiki_root.rglob("tests")) if d.is_dir() and any(d.glob("test_*.py"))]
        for tdir in test_dirs:
            res = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(tdir), "-p", "test_*.py"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                rel_tdir = tdir.relative_to(repo_root).as_posix()
                errors.append(f"[Python Unit Tests Failed in {rel_tdir}]\n{res.stderr.strip() or res.stdout.strip()}")

        # Rust unit tests via Cargo workspace (`cargo test --quiet`)
        cargo_toml = repo_root / "Cargo.toml"
        if cargo_toml.exists():
            import os

            cargo_bin = Path.home() / ".cargo" / "bin" / "cargo"
            cargo_cmd = str(cargo_bin) if cargo_bin.exists() else "cargo"
            rust_env = os.environ.copy()
            macos_sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
            if macos_sdk.exists():
                rust_env["SDKROOT"] = str(macos_sdk.resolve())
            res = subprocess.run(
                [cargo_cmd, "test", "--quiet"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                env=rust_env,
            )
            if res.returncode != 0:
                errors.append(f"[Cargo Unit Tests Failed]\n{res.stderr.strip()}\n{res.stdout.strip()}")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint the 3-Tier LLM Wiki and run bound script unit tests.")
    parser.add_argument("--root", type=Path, default=None, help="Repository root path")
    parser.add_argument("--no-tests", action="store_true", help="Skip running Python script unit tests")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    repo_root = find_repo_root(args.root)
    errors, warnings = lint_wiki(repo_root, run_tests=not args.no_tests)

    print(f"=== Wiki Linter Report ({repo_root.name}) ===")
    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  ⚠️  {w}")
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  ❌ {e}")
        return 1
    if args.strict and warnings:
        return 1

    print("\n✅ All wiki cards, cross-tier dependencies, OKF frontmatter, and script unit tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
import json
import re
import subprocess
import sys
from pathlib import Path

from wiki_core import (
    ALL_PAGE_TYPES,
    CARD_TYPES,
    DOC_TYPES,
    EPISTEMIC_STATUSES,
    MANAGED_END,
    MANAGED_START,
    MAX_L0_MANAGED_BYTES,
    MAX_SUBTREE_BYTES,
    RESERVED_FILENAMES,
    TIERS,
    discover_wiki_pages,
    find_repo_root,
    frontmatter_scalar_problems,
    parse_markdown,
    resolve_cargo,
    resolve_evidence_fingerprint,
    rust_subprocess_env,
    strip_deps_banner,
    wiki_rust_scripts,
)

MAX_CORE_LINES = int(__import__("os").environ.get("WIKI_MAX_CORE_LINES", "45"))
MAX_CARD_LINES = int(__import__("os").environ.get("WIKI_MAX_CARD_LINES", "60"))
MAX_BODY_CHARS = int(__import__("os").environ.get("WIKI_MAX_BODY_CHARS", "4800"))
MAX_DESC_CHARS = int(__import__("os").environ.get("WIKI_MAX_DESCRIPTION_CHARS", "800"))
MAX_CORE_CARDS_PER_DOMAIN = int(__import__("os").environ.get("WIKI_MAX_CORE_CARDS_PER_DOMAIN", "5"))
ADVICE = "[advice] "  # printed, but never fails --strict (heuristic checks)
NEAR_DUP_JACCARD = float(__import__("os").environ.get("WIKI_NEAR_DUP_JACCARD", "0.62"))

FORBIDDEN_PATTERNS = [
    (
        re.compile(
            r"\b(the )?(AI|model|agent)('s)? (mistake|error|version|draft|tendency)|"
            r"\b(the )?human('s)? (version|standard|draft)|"
            r"the model wrote|human wrote|model draft|原稿对比中模型|"
            r"(模型|大模型|AI|Agent|重建稿)\s*(写了|写成了|犯了|犯的|的初稿|的草稿|的版本|的错误|漏掉了)|"
            r"人类(的)?(版本|原稿写|写了|写的是)",
            re.IGNORECASE,
        ),
        "禁止在规则卡中记录训练期『模型写了X vs 人类写了Y』的过程叙事，只写面向下次任务的普适规则",
    ),
]

# Episode/exercise pointers usually mean a single-example rule leaked into a card. Warning only, because
# music cards legitimately talk about "EP" releases.
EPISODE_PATTERN = re.compile(r"\bep(isode)?\s*\.?\s*\d+\b|在?第\s*\d+\s*(集|话|课时?)(练习|中|里)", re.IGNORECASE)


def _shingles(text: str) -> set[str]:
    """Zero-dependency near-duplicate fingerprint: lowercase ASCII words + CJK character bigrams."""
    low = text.lower()
    out = set(re.findall(r"[a-z0-9]{2,}", low))
    for run in re.findall(r"[\u4e00-\u9fff]+", low):
        if len(run) == 1:
            out.add(run)
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def _find_dependency_cycles(pages: list, resolve: dict[str, str]) -> list[list[str]]:
    """Return depends_on cycles (by page id). Graph size is a few hundred cards, so plain DFS is fine."""
    graph: dict[str, list[str]] = {}
    for p in pages:
        targets = (d.split("#")[0].strip() for d in p.depends_on)
        graph[p.page_id] = [resolve[t] for t in targets if t in resolve]
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * len(graph) + 100))
    color: dict[str, int] = {}
    stack: list[str] = []
    seen: set[frozenset[str]] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for child in graph.get(node, []):
            if color.get(child) == 1:
                cyc = stack[stack.index(child):] + [child]
                key = frozenset(cyc)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cyc)
            elif not color.get(child):
                visit(child)
        stack.pop()
        color[node] = 2

    for node in graph:
        if not color.get(node):
            visit(node)
    return cycles


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
            shape_problems = frontmatter_scalar_problems(parsed.raw_frontmatter_lines, "description")
            for sp in shape_problems:
                errors.append(f"[{p.rel_path}] {sp}")
            if len(p.description) > MAX_DESC_CHARS:
                errors.append(
                    f"[{p.rel_path}] 'description' length ({len(p.description)}) exceeds max {MAX_DESC_CHARS} chars"
                )
            if len(p.description) < 12 and not shape_problems:
                warnings.append(f"[{p.rel_path}] 'description' is very short; state clearly when to read and when to skip")
            if (
                p.page_type in CARD_TYPES
                and p.description
                and not re.search(r"不适用|不用于|禁止用于|不要用于|跳过|无需|不需要|not for|skip|don't use|do not use", p.description, re.IGNORECASE)
            ):
                warnings.append(
                    f"{ADVICE}[{p.rel_path}] 'description' states when to read but not when to skip (add '不适用于…' / 'Not for …'); "
                    "a one-sided routing key over-triggers as the library grows"
                )

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
        if p.page_type in CARD_TYPES:
            m_ep = EPISODE_PATTERN.search(clean_body)
            if m_ep:
                warnings.append(
                    f"{ADVICE}[{p.rel_path}] Possible single-example pointer '{m_ep.group(0)}' in an action card; "
                    "a rule that only holds for one episode/exercise belongs in 03_deep_concepts/, not 00/01/02"
                )

        # 5b. Primary-source citations must resolve to a raw file or its committed .receipt.json
        raw_sources = p.frontmatter.get("sources", [])
        if isinstance(raw_sources, list):
            for src in raw_sources:
                src_s = str(src.get("resource", "")) if isinstance(src, dict) else str(src)
                if src_s.startswith("raw://"):
                    ok, _ = resolve_evidence_fingerprint(repo_root, src_s)
                    if not ok:
                        warnings.append(
                            f"{ADVICE}[{p.rel_path}] Unverifiable primary source '{src_s}': neither the raw file nor its "
                            "'.receipt.json' exists (typo, not-yet-ingested book, or fabricated citation)"
                        )

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

    # 8a. Graph-level hygiene: alias collisions, depends_on cycles, near-duplicate routing keys
    for p in pages:
        for a in p.aliases:
            if a in id_map and id_map[a] != p.rel_path:
                errors.append(f"[{p.rel_path}] Alias '{a}' collides with the id of {id_map[a]}; links would resolve ambiguously")

    resolve_ref: dict[str, str] = {}
    for p in pages:
        resolve_ref[p.page_id] = p.page_id
        resolve_ref[p.rel_path] = p.page_id
        for a in p.aliases:
            resolve_ref.setdefault(a, p.page_id)
    for cyc in _find_dependency_cycles(pages, resolve_ref):
        warnings.append(
            f"{ADVICE}[depends_on cycle] {' -> '.join(cyc)}; cycles make 1-hop bundles mutually re-inject each other — "
            "keep the more fundamental card as the dependency and drop the back-edge"
        )

    action_pages = [p for p in pages if p.page_type in CARD_TYPES and p.description]
    shingle_cache = {p.page_id: _shingles(f"{p.title} {p.description}") for p in action_pages}
    # Inverted shingle index + DF pruning so 5,000+ cards avoid O(N^2) blowup & 20MB advice floods
    shingle_df: dict[str, int] = {}
    for sh_set in shingle_cache.values():
        for sh in sh_set:
            shingle_df[sh] = shingle_df.get(sh, 0) + 1
    rare_cutoff = max(12, int(0.18 * max(1, len(action_pages))))
    shingle_postings: dict[str, list[int]] = {}
    for idx_p, p_obj in enumerate(action_pages):
        # Index up to 16 rarest shingles per card (prefix-filtering guarantee for high Jaccard)
        ordered_sh = sorted(shingle_cache[p_obj.page_id], key=lambda s: shingle_df.get(s, 0))[:16]
        for sh in ordered_sh:
            if shingle_df.get(sh, 0) <= rare_cutoff or len(action_pages) <= 80:
                shingle_postings.setdefault(sh, []).append(idx_p)

    candidate_pairs: set[tuple[int, int]] = set()
    if len(action_pages) <= 80:
        for i in range(len(action_pages)):
            for j in range(i + 1, len(action_pages)):
                candidate_pairs.add((i, j))
    else:
        for idx_list in shingle_postings.values():
            if len(idx_list) > 80:
                continue
            for ii in range(len(idx_list)):
                u = idx_list[ii]
                for jj in range(ii + 1, len(idx_list)):
                    v = idx_list[jj]
                    candidate_pairs.add((u, v) if u < v else (v, u))

    near_dups: list[tuple[float, str]] = []
    for i, j in sorted(candidate_pairs):
        a, b = action_pages[i], action_pages[j]
        sa, sb = shingle_cache[a.page_id], shingle_cache[b.page_id]
        if len(sa) < 6 or len(sb) < 6:
            continue
        jac = len(sa & sb) / len(sa | sb)
        if jac >= NEAR_DUP_JACCARD:
            same_dom = (a.tier, a.domain) == (b.tier, b.domain)
            hint = (
                "run `/organize` D-Merge (keep the canonical one, add the other path to aliases)"
                if same_dom
                else "if the rule genuinely holds in both modules, keep it once in foundations/ and point the other via depends_on"
            )
            near_dups.append((
                jac,
                f"{ADVICE}[near-duplicate] `{a.page_id}` ({a.rel_path}) ≈ `{b.page_id}` ({b.rel_path}) "
                f"(title+description Jaccard {jac:.2f}); {hint}",
            ))
    near_dups.sort(key=lambda x: x[0], reverse=True)
    max_dup_advice = 25
    for _, msg in near_dups[:max_dup_advice]:
        warnings.append(msg)
    if len(near_dups) > max_dup_advice:
        warnings.append(
            f"{ADVICE}[near-duplicate-summary] {len(near_dups) - max_dup_advice} additional near-duplicate card pair(s) omitted "
            "to protect context window; run `/organize` for full cluster merge."
        )

    # 8a'. Claims whose raw:// evidence cannot be resolved anywhere
    claims_root = repo_root / "wiki" / ".claims"
    if claims_root.is_dir():
        for cf in sorted(claims_root.rglob("*.json")):
            try:
                cdata = json.loads(cf.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"[{cf.relative_to(repo_root).as_posix()}] Invalid JSON: {e}")
                continue
            for cl in cdata.get("claims", []):
                if cl.get("status") == "unverified":
                    warnings.append(
                        f"[{cf.relative_to(repo_root).as_posix()}] Claim '{cl.get('id', '?')}' has raw:// evidence with no file "
                        "and no .receipt.json; re-ingest the source or fix the URI"
                    )

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
        st_dir = wiki_root / tier / dom / "_subtrees"
        if st_dir.is_dir():
            for st_file in sorted(st_dir.glob("*.md")):
                st_bytes = len(st_file.read_bytes())
                if st_bytes > MAX_SUBTREE_BYTES:
                    rel_st = st_file.relative_to(repo_root).as_posix()
                    errors.append(
                        f"[{rel_st}] L2 subtree router exceeds {MAX_SUBTREE_BYTES // 1024} KB "
                        f"({st_bytes} bytes > {MAX_SUBTREE_BYTES}); adaptive compaction required."
                    )
        if cnt > 15:
            if not st_dir.is_dir() or not list(st_dir.glob("*.md")):
                errors.append(
                    f"[wiki/{tier}/{dom}] Domain has {cnt} cards (>15) but lacks L2 `_subtrees/*.md` routers."
                )

    for l0_rel in ("GEMINI.md", "AGENTS.md", "CLAUDE.md", ".cursor/rules/agentstacks.mdc"):
        l0_file = repo_root / l0_rel
        if not l0_file.exists():
            continue
        l0_text = l0_file.read_text(encoding="utf-8")
        if MANAGED_START in l0_text and MANAGED_END in l0_text:
            s_idx = l0_text.index(MANAGED_START)
            e_idx = l0_text.index(MANAGED_END) + len(MANAGED_END)
            managed_bytes = len(l0_text[s_idx:e_idx].encode("utf-8"))
            if managed_bytes > MAX_L0_MANAGED_BYTES:
                errors.append(
                    f"[{l0_rel}] L0 WIKI-MANAGED block exceeds {MAX_L0_MANAGED_BYTES} bytes "
                    f"({managed_bytes} bytes) — risks system-prompt truncation. Run wiki_finalize.py."
                )

    # 8c. Whole-Source Exhaustive Coverage & Anti-Cherry-Picking Gate (wiki/.source-coverage.json)
    cov_path = wiki_root / ".source-coverage.json"
    if cov_path.exists():
        try:
            cov_data = json.loads(cov_path.read_text(encoding="utf-8"))
            for s_uri, s_obj in cov_data.get("sources", {}).items():
                units = s_obj.get("units", [])
                if not units:
                    errors.append(f"[wiki/.source-coverage.json] Source '{s_uri}' has 0 registered units.")
                    continue
                reusable_units = [u for u in units if u.get("reusable", True)]
                if len(reusable_units) / len(units) < 0.70:
                    errors.append(
                        f"[wiki/.source-coverage.json] Source '{s_uri}' marks <70% of units as reusable ({len(reusable_units)}/{len(units)}). "
                        "Substantive chapters must never be marked reusable=false to skip studying them."
                    )
                for u in reusable_units:
                    uid = u.get("unit_id", "?")
                    if u.get("status") == "completed":
                        u_cards = u.get("cards", [])
                        if not u_cards:
                            errors.append(
                                f"[wiki/.source-coverage.json] Source '{s_uri}' unit '{uid}' is marked completed but binds 0 Wiki cards."
                            )
                        for cid in u_cards:
                            if cid not in id_map and cid not in rel_set and cid not in alias_set:
                                errors.append(
                                    f"[wiki/.source-coverage.json] Source '{s_uri}' unit '{uid}' references non-existent card '{cid}'."
                                )
                if s_obj.get("status") == "completed" or s_obj.get("exhaustively_ingested") is True:
                    pend_u = [u.get("unit_id") for u in reusable_units if u.get("status") != "completed"]
                    if pend_u:
                        errors.append(
                            f"[wiki/.source-coverage.json] Source '{s_uri}' claims status=completed while {len(pend_u)} reusable units are still pending: {pend_u}"
                        )
        except Exception as e:
            errors.append(f"[wiki/.source-coverage.json] Invalid JSON format: {e}")

    # 9. Run unit tests across all domain scripts/ and engine test suite (Python unittest + Rust cargo test)
    if run_tests:
        import os

        wiki_root = repo_root / "wiki"
        test_dirs = [d for d in sorted(wiki_root.rglob("tests")) if d.is_dir() and any(d.glob("test_*.py"))]
        engine_test_dir = repo_root / ".agents" / "skills" / "study" / "tests"
        if (
            engine_test_dir.is_dir()
            and any(engine_test_dir.glob("test_*.py"))
            and os.environ.get("AGENTSTACKS_IN_ENGINE_TEST") != "1"
        ):
            test_dirs.append(engine_test_dir)
        test_env = {**os.environ, "AGENTSTACKS_IN_ENGINE_TEST": "1"}
        for tdir in test_dirs:
            res = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(tdir), "-p", "test_*.py"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                env=test_env,
            )
            if res.returncode != 0:
                rel_tdir = tdir.relative_to(repo_root).as_posix()
                errors.append(f"[Python Unit Tests Failed in {rel_tdir}]\n{res.stderr.strip() or res.stdout.strip()}")

        # Rust unit tests via Cargo workspace (`cargo test --quiet`)
        cargo_toml = repo_root / "Cargo.toml"
        rs_scripts = wiki_rust_scripts(wiki_root)
        if cargo_toml.exists() and rs_scripts:
            # Guard against the v2.0 regression where scripts were never registered and `cargo test`
            # passed vacuously: every wiki Rust script must be a registered [[bin]] target.
            cargo_text = cargo_toml.read_text(encoding="utf-8")
            unregistered = [
                s.relative_to(repo_root).as_posix()
                for s in rs_scripts
                if f'path = "{s.relative_to(repo_root).as_posix()}"' not in cargo_text
            ]
            if unregistered:
                errors.append(
                    f"[Cargo.toml] {len(unregistered)} Rust script(s) are not registered as [[bin]] targets and would never be "
                    f"compiled or tested: {unregistered[:5]}. Run wiki_finalize.py."
                )
            cargo_cmd = resolve_cargo()
            if not cargo_cmd:
                errors.append(
                    f"[Cargo] {len(rs_scripts)} Rust script(s) exist but no `cargo` was found; install Rust via rustup "
                    "(https://rustup.rs) so the verifier gate can actually run."
                )
            else:
                res = subprocess.run(
                    [cargo_cmd, "test", "--quiet"],
                    cwd=str(repo_root),
                    capture_output=True,
                    text=True,
                    env=rust_subprocess_env(),
                )
                if res.returncode != 0:
                    errors.append(f"[Cargo Unit Tests Failed]\n{res.stderr.strip()}\n{res.stdout.strip()}")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint the 3-Tier LLM Wiki and run bound script unit tests.")
    parser.add_argument("--root", type=Path, default=None, help="Repository root path")
    parser.add_argument("--no-tests", action="store_true", help="Skip running Python/Rust script unit tests")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    repo_root = find_repo_root(args.root)
    errors, warnings = lint_wiki(repo_root, run_tests=not args.no_tests)

    print(f"=== Wiki Linter Report ({repo_root.name}) ===")
    advice = [w for w in warnings if w.startswith(ADVICE)]
    hard_warnings = [w for w in warnings if not w.startswith(ADVICE)]
    if advice:
        print(f"\nAdvice ({len(advice)}, heuristic — does not fail --strict):")
        for w in advice:
            print(f"  💡 {w[len(ADVICE):]}")
    if hard_warnings:
        print(f"\nWarnings ({len(hard_warnings)}):")
        for w in hard_warnings:
            print(f"  ⚠️  {w}")
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors:
            print(f"  ❌ {e}")
        return 1
    if args.strict and hard_warnings:
        return 1

    print("\n✅ All wiki cards, cross-tier dependencies, OKF frontmatter, and script unit tests PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

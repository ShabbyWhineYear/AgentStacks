#!/usr/bin/env python3
"""Zero-Model-Cost Wiki Tree Router, CorpusTopicMap Search & LENS Budgeted Dependency Reader.

Upgraded with Sirchmunk (arXiv:2608.16185) Architecture:
1. `tree [target]`: Inspect L0->L1->L2 META Subtree hierarchy or a card's PageIndex `DocumentTree` (`#slug`, line/char ranges, rule counts).
2. `search <query>`: Phase 0 Historical-Query/Subtree Reuse + Phase 1 DF-Pruned `CorpusTopicMap` section-level hybrid retrieval + `Hotness` weighting.
3. `read <path_or_id>[#section]`: LENS token-budgeted reader (`--budget-kb 20.0`, `--mode {auto,full,digest,tree}`, `--subtree <domain>/<slug>`):
   - Renders 1-Hop `depends_on` cards as compact Sirchmunk `KnowledgeCluster` Contract Digests (`Patterns + Constraints + Scripts + #section slugs`, ~350B/card) instead of unbounded transitive full-body dumps (eliminating the 476 KB context explosion).
   - Auto-attaches Top 3 domain-scoped Gotcha recipes (`Rules That Save Retries`) at zero extra query cost.
4. `gotchas [--scope <domain>] [--query <kw>]`: Scoped retrieval over `wiki/.gotchas.json` and `_domain_gotchas.md`.
5. `gaps` / `workflows` / `scoreboard` / `compose-checklist`: Operational telemetry and checklists.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from wiki_core import (
    MAX_SUBTREE_BYTES,
    WikiPageInfo,
    discover_wiki_pages,
    find_repo_root,
    parse_markdown,
    slugify_heading,
)
from wiki_search_engine import execute_hybrid_search, tokenize_query


def extract_sections(clean_body: str) -> list[tuple[str, str, str]]:
    """Return list of (anchor_slug, heading_title, section_text)."""
    lines = clean_body.splitlines()
    sections: list[tuple[str, str, list[str]]] = []
    cur_slug = "overview"
    cur_title = "Overview"
    cur_lines: list[str] = []

    for ln in lines:
        m = re.match(r"^(#{1,3})\s+(.+)$", ln.strip())
        if m:
            if cur_lines:
                sections.append((cur_slug, cur_title, cur_lines))
            cur_title = m.group(2).strip()
            cur_slug = slugify_heading(cur_title)
            cur_lines = [ln]
        else:
            cur_lines.append(ln)
    if cur_lines:
        sections.append((cur_slug, cur_title, cur_lines))

    return [(slug, title, "\n".join(lns).strip()) for slug, title, lns in sections]


def _load_json_safe(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _constraint_strs(page: WikiPageInfo) -> list[str]:
    out: list[str] = []
    for c in page.constraints:
        if isinstance(c, dict):
            desc = str(c.get("description", "")).strip()
            if desc:
                out.append(desc)
        elif isinstance(c, str) and c.strip():
            out.append(c.strip())
    return out


def format_contract_digest(page: WikiPageInfo, subtree_slug: str = "", label: str = "1-Hop Dependency Contract") -> str:
    """Format a compact Sirchmunk KnowledgeCluster Contract Digest (~350-500 bytes)."""
    st_part = f" | subtree={subtree_slug}" if subtree_slug else ""
    lines = [
        f"--- [{label}: `{page.page_id}` | {page.tier}/{page.domain}{st_part} | level=L{page.abstraction_level} | status={page.epistemic_status}] ---",
        f"Title: {page.title} (`wiki/{page.rel_path}`)",
        f"Constraint (When to use): {page.description}",
    ]
    if page.patterns:
        lines.append("Patterns (Core Quantitative Rules):")
        for pat in page.patterns[:4]:
            lines.append(f"  • {pat}")
    c_strs = _constraint_strs(page)
    if c_strs:
        lines.append(f"Hard Thresholds: {' ; '.join(c_strs[:4])}")
    if page.scripts:
        sc_str = ", ".join(f"{s['role']}={s['path']}" for s in page.scripts)
        lines.append(f"Bound Scripts: {sc_str}")
    if page.doc_tree:
        sec_Hints = [
            f"`#{n.slug}` (L{n.line_range[0]}-{n.line_range[1]})"
            for n in page.doc_tree.all_flat_nodes()
            if n.slug != "overview"
        ][:6]
        if sec_Hints:
            lines.append(f"Drill-Down Sections (`wiki_query.py read {page.page_id}#<slug>`): {', '.join(sec_Hints)}")
    return "\n".join(lines)


def format_document_tree_outline(page: WikiPageInfo, subtree_slug: str = "") -> str:
    """Format a PageIndex-style DocumentTree table + Patterns/Constraints contract for a card."""
    st_part = f" | Subtree: `{subtree_slug}`" if subtree_slug else ""
    lines = [
        f"🌳 DOCUMENT TREE (Sirchmunk PageIndex): {page.title}",
        f"   ID: `{page.page_id}` | Path: `wiki/{page.rel_path}` | Level: `L{page.abstraction_level}`{st_part}",
        f"   Boundary: {page.description}",
    ]
    if page.patterns:
        lines.append("   Core Patterns (Extracted Digest):")
        for pat in page.patterns[:5]:
            lines.append(f"     • {pat}")
    c_strs = _constraint_strs(page)
    if c_strs:
        lines.append(f"   Hard Constraints: {' ; '.join(c_strs[:5])}")
    if page.scripts:
        sc_str = ", ".join(f"{s['role']}={s['path']}" for s in page.scripts)
        lines.append(f"   Bound Scripts: {sc_str}")
    lines.append("")
    lines.append("   | Node | Level | Section Title | Line Range | Rules | Drill-Down Command / Summary |")
    lines.append("   | :--- | :---: | :--- | :---: | :---: | :--- |")
    if page.doc_tree:
        for n in page.doc_tree.all_flat_nodes():
            h_lvl = f"H{n.level}"
            l_rng = f"L{n.line_range[0]}-{n.line_range[1]}"
            short_sum = n.summary[:95] + "…" if len(n.summary) > 95 else n.summary
            cmd_hint = f"`read {page.page_id}#{n.slug}` — {short_sum}"
            lines.append(
                f"   | `{n.node_id}` | {h_lvl} | **{n.title}** | `{l_rng}` | {n.quant_rule_count} | {cmd_hint} |"
            )
    return "\n".join(lines)


def select_scoped_gotchas(
    repo_root: Path,
    domains: set[str],
    query_tokens: list[str] | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Select top retry-saving gotchas matching the active domains and/or tokens."""
    gotchas_path = repo_root / "wiki" / ".gotchas.json"
    gdata = _load_json_safe(gotchas_path)
    entries = gdata.get("entries", [])
    if not isinstance(entries, list) or not entries:
        return []

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for idx, g in enumerate(entries):
        scope_l = str(g.get("scope", "")).lower()
        gid_l = str(g.get("id", "")).lower()
        sym_l = str(g.get("symptom", "")).lower()
        rec_l = str(g.get("recipe", "")).lower()
        hay = f"{gid_l} {scope_l} {sym_l} {rec_l}"

        s = 0.0
        for d in domains:
            dl = d.lower()
            if dl in scope_l or dl in gid_l:
                s += 5.0
            elif dl == "daw_music_prod" and any(k in hay for k in ("reaper", "exs", "sv2", "svr3", "sampler")):
                s += 4.0
            elif dl == "music_theory_acoustics" and any(k in hay for k in ("mandopop", "harmony", "chord", "topline")):
                s += 3.5
            elif dl == "visual_aesthetics" and any(k in hay for k in ("turnaround", "limb", "ankle", "head", "manga")):
                s += 3.5
            elif dl == "blender_3d_layout" and any(k in hay for k in ("gpv3", "blender")):
                s += 4.0
        if query_tokens:
            for tok in query_tokens:
                if len(tok) > 1 and tok in hay:
                    s += 2.0
        if s > 0:
            # Prefer more recent entries on tie
            scored.append((s, idx, g))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [g for _, _, g in scored[:limit]]


def search_wiki(
    repo_root: Path,
    query: str,
    tier_filter: str | None = None,
    domain_filter: str | None = None,
    limit: int = 10,
    as_json: bool = False,
    explain: bool = False,
) -> int:
    return execute_hybrid_search(
        repo_root,
        query=query,
        tier_filter=tier_filter,
        domain_filter=domain_filter,
        limit=limit,
        as_json=as_json,
        explain=explain,
    )


def resolve_page(pages: list[WikiPageInfo], target: str) -> WikiPageInfo | None:
    clean = target.removeprefix("wiki/").lstrip("/").strip()
    clean_no_ext = clean.removesuffix(".md")
    clean_norm = clean_no_ext.lower().replace("_", "-")
    if not clean_no_ext:
        # An empty target used to suffix-match ("x".endswith("") is True) and silently return the first card.
        return None

    # 1. Exact ID, relative path, alias, or filename match
    for p in pages:
        if (
            p.page_id == clean_no_ext
            or p.rel_path == clean
            or p.rel_path.removesuffix(".md") == clean_no_ext
            or clean_no_ext in p.aliases
            or p.abs_path.name == clean
            or p.abs_path.stem == clean_no_ext
        ):
            return p

    # 2. Suffix match fallback
    for p in pages:
        if p.rel_path.endswith(clean) or p.rel_path.removesuffix(".md").endswith(clean_no_ext):
            return p

    # 3. Normalized substring / token overlap fallback on ID, stem, and aliases
    q_tokens = [t for t in re.split(r"[-_/\s]+", clean_norm) if len(t) >= 2]
    best_page: WikiPageInfo | None = None
    best_score = 0.0
    for p in pages:
        id_norm = p.page_id.lower().replace("_", "-")
        stem_norm = p.abs_path.stem.lower().replace("_", "-")
        alias_norm = " ".join(a.lower().replace("_", "-") for a in p.aliases)
        haystack = f"{id_norm} {stem_norm} {alias_norm}"

        score = 0.0
        if clean_norm in id_norm or clean_norm in stem_norm:
            score += 10.0
        if q_tokens:
            matched = sum(1 for tok in q_tokens if tok in haystack)
            if matched == len(q_tokens):
                score += 6.0 + matched
            elif matched >= max(2, len(q_tokens) - 1):
                score += 3.0 * (matched / len(q_tokens))
        if score > 0:
            if p.page_type != "deep-concept":
                score += 0.5
            if score > best_score:
                best_score = score
                best_page = p

    return best_page


def print_compose_checklist(style: str = "general", stage_filter: str | None = None) -> int:
    """Print dynamic stage-by-stage & role-by-role activation checklist from the active Wiki."""
    print("=" * 84)
    print(f"🧭 WIKI STAGE & ROLE ACTIVATION CHECKLIST (style={style})")
    print("=" * 84)
    print("  1. Run `python3 .agents/skills/study/scripts/wiki_query.py tree` to inspect available L1/L2 subtrees.")
    print("  2. Run `python3 .agents/skills/study/scripts/wiki_query.py search \"<keywords>\"` to locate domain cards.")
    print("  3. Run `python3 .agents/skills/study/scripts/wiki_query.py read <card_id>` to load 1-Hop Contract Digests.")
    print("  4. Run `python3 .agents/skills/study/scripts/study_runner.py verify-suite --domain <domain>` after generation.")
    print("=" * 84)
    return 0


def inspect_tree(repo_root: Path, target: str | None = None) -> int:
    """Inspect L0->L1->L2 META Subtree hierarchy or a specific card's PageIndex DocumentTree."""
    pages = discover_wiki_pages(repo_root)
    s_index = _load_json_safe(repo_root / "wiki" / ".search-index.json")
    subtrees = s_index.get("subtrees", [])
    manifest_pages = _load_json_safe(repo_root / "wiki" / ".page-manifest.json").get("pages", {})
    id_to_subtree = {
        str(v["id"]): str(v.get("subtree", ""))
        for v in manifest_pages.values()
        if isinstance(v, dict) and "id" in v
    }

    if not target:
        print("=" * 84)
        print("🌳 LLM-WIKI × SIRCHMUNK GLOBAL HIERARCHICAL TREE ROUTER (L0 -> L1 -> L2 -> L3/L4)")
        print("=" * 84)
        by_domain: dict[tuple[str, str], list[WikiPageInfo]] = {}
        for p in pages:
            by_domain.setdefault((p.tier, p.domain), []).append(p)
        for (tier, dom), dpages in sorted(by_domain.items()):
            dom_sts = [st for st in subtrees if st.get("domain") == dom]
            sec_total = sum(len(p.doc_tree.all_flat_nodes()) for p in dpages if p.doc_tree)
            print(f"\n📁 [{tier}/{dom}] — {len(dpages)} cards, {sec_total} L4 section nodes (`wiki/{tier}/{dom}/quickstart.md`)")
            if dom_sts:
                for st in dom_sts:
                    st_rel = st.get("relPath") or st.get("path", "")
                    print(
                        f"   ├─ 🌿 L2 Subtree `{dom}/{st['slug']}` ({st['cardCount']} cards): {st['title']}\n"
                        f"   │     Router: `wiki/{st_rel}` | Core: {', '.join(st.get('coreIds', [])[:2]) or '—'}"
                    )
            else:
                core_ids = [p.page_id for p in dpages if p.subdir == "00_core_aesthetics"]
                print(f"   └─ 📄 Direct Domain (`{len(dpages)}` cards) | Core: {', '.join(core_ids[:3]) or '—'}")
        print("\n💡 Tip: Run `wiki_query.py tree <domain>/<subtree>` or `wiki_query.py tree <card_id>` to drill down.")
        return 0

    clean_t = target.removeprefix("wiki/").lstrip("/").strip()

    # Check if target matches a domain or domain/subtree
    matched_sts = [
        st for st in subtrees
        if clean_t in (st.get("domain"), st.get("slug"), f"{st.get('domain')}/{st.get('slug')}")
    ]
    if matched_sts:
        for st in matched_sts:
            st_rel = st.get("relPath") or st.get("path", "")
            st_file = repo_root / "wiki" / str(st_rel)
            if st_file.exists():
                print(st_file.read_text(encoding="utf-8").strip())
                print()
        return 0

    # Check if target is a domain without subtrees
    dom_pages = [p for p in pages if p.domain == clean_t or f"{p.tier}/{p.domain}" == clean_t]
    if dom_pages:
        t0, d0 = dom_pages[0].tier, dom_pages[0].domain
        print(f"=== 📁 Domain Tree: `{t0}/{d0}` ({len(dom_pages)} cards) ===")
        for p in dom_pages:
            sec_cnt = len(p.doc_tree.all_flat_nodes()) if p.doc_tree else 1
            print(
                f"  • `{p.page_id}` [L{p.abstraction_level}, {sec_cnt} sections] — {p.title}\n"
                f"    Boundary: {p.description}"
            )
        return 0

    # Otherwise resolve as a card ID / path and print its PageIndex DocumentTree
    page = resolve_page(pages, clean_t)
    if not page:
        print(f"❌ Target not found in tree: '{target}'", file=sys.stderr)
        return 1

    print(format_document_tree_outline(page, id_to_subtree.get(page.page_id, "")))
    return 0


class BudgetTracker:
    """Airtight UTF-8 byte budget accumulator across Primary + Foundations + 1-Hop Deps + Gotchas."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = max(1024, limit_bytes)
        self.used_bytes = 0
        self.truncated = False

    @property
    def remaining(self) -> int:
        return max(0, self.limit_bytes - self.used_bytes)

    def fits(self, text: str, reserve_bytes: int = 0) -> bool:
        return (len(text.encode("utf-8")) + 1 + reserve_bytes) <= self.remaining

    def emit(self, text: str, allow_truncate: bool = True) -> bool:
        raw = text + "\n"
        b = raw.encode("utf-8")
        if self.used_bytes + len(b) <= self.limit_bytes:
            print(text)
            self.used_bytes += len(b)
            return True
        if not allow_truncate or self.remaining < 160:
            self.truncated = True
            return False
        notice = "\n… ⚡ [LENS Hard Byte Budget Reached — pass `#<section>` or `--mode full` for remaining text]"
        nb = notice.encode("utf-8")
        avail = max(0, self.remaining - len(nb) - 2)
        if avail <= 0:
            self.truncated = True
            return False
        clipped = b[:avail].decode("utf-8", errors="ignore").rstrip()
        out = clipped + notice
        print(out)
        self.used_bytes += len(out.encode("utf-8")) + 1
        self.truncated = True
        return False


def _resolve_mode_budget_bytes(mode: str, budget_kb: float | None) -> int:
    if budget_kb is not None:
        return max(1536, int(budget_kb * 1024))
    if mode == "lite":
        return 6 * 1024
    if mode == "heavy":
        return 40 * 1024
    if mode == "full":
        return 192 * 1024
    return 20 * 1024


def read_subtree_router(
    repo_root: Path,
    subtree_spec: str,
    include_deps: bool = True,
    budget_kb: float | None = None,
    mode: str = "auto",
) -> int:
    """Read an L2 META Subtree Router (`<domain>/<subtree_slug>`) + Scoped 00_core Contract Digests."""
    pages = discover_wiki_pages(repo_root)
    s_index = _load_json_safe(repo_root / "wiki" / ".search-index.json")
    subtrees = s_index.get("subtrees", [])
    clean_s = subtree_spec.removeprefix("wiki/").lstrip("/").removesuffix(".md").strip()

    matched = None
    for st in subtrees:
        st_rel = str(st.get("relPath") or st.get("path", ""))
        if clean_s in (
            st.get("slug"),
            f"{st.get('domain')}/{st.get('slug')}",
            st_rel.removesuffix(".md"),
        ):
            matched = st
            break

    if not matched:
        print(f"❌ Subtree not found: '{subtree_spec}'. Run `wiki_query.py tree` to list all L2 subtrees.", file=sys.stderr)
        return 1

    st_rel = str(matched.get("relPath") or matched.get("path", ""))
    st_path = repo_root / "wiki" / st_rel
    if not st_path.exists():
        print(f"❌ Subtree file missing: {st_path}", file=sys.stderr)
        return 1

    cap_bytes = _resolve_mode_budget_bytes(mode, budget_kb)
    tracker = BudgetTracker(cap_bytes)
    header = "\n".join([
        "=" * 80,
        f"🌿 L2 META SUBTREE ROUTER: {matched['domain']}/{matched['slug']} (`wiki/{st_rel}`)",
        "=" * 80,
        st_path.read_text(encoding="utf-8").strip(),
    ])
    tracker.emit(header)

    if include_deps and tracker.remaining > 400:
        core_ids = matched.get("coreIds", [])
        if core_ids:
            sec_hdr = "\n" + "#" * 80 + f"\n🛡️ SCOPED 00_CORE_AESTHETICS CONTRACT DIGESTS ({len(core_ids)} Card(s))\n" + "#" * 80
            if tracker.emit(sec_hdr, allow_truncate=False):
                for cid in core_ids:
                    cp = resolve_page(pages, cid)
                    if cp:
                        dig = "\n" + format_contract_digest(cp, matched["slug"], label="Scoped 00_Core Contract")
                        if not tracker.emit(dig, allow_truncate=False):
                            break

    gotchas = select_scoped_gotchas(repo_root, {str(matched["domain"])}, tokenize_query(matched["slug"]), limit=3)
    if gotchas and tracker.remaining > 220:
        g_lines = [
            "\n" + "-" * 80,
            f"⚡ SCOPED RETRY-SAVERS (`{matched['domain']}` Top {len(gotchas)} Gotchas):",
        ]
        for g in gotchas:
            g_lines.append(f"  • [{g.get('id')}] ({g.get('scope')}): {str(g.get('recipe', ''))[:200]}")
        tracker.emit("\n".join(g_lines), allow_truncate=True)
    return 0


def read_wiki_pages(
    repo_root: Path,
    target_specs: list[str],
    include_deps: bool = True,
    full_deps: bool = False,
    mode: str = "auto",
    budget_kb: float | None = None,
) -> int:
    """Airtight LENS Budgeted Reader with 1-Hop Contract Digests and DocumentTree section routing."""
    pages = discover_wiki_pages(repo_root)
    manifest_pages = _load_json_safe(repo_root / "wiki" / ".page-manifest.json").get("pages", {})
    id_to_subtree = {
        str(v["id"]): str(v.get("subtree", ""))
        for v in manifest_pages.values()
        if isinstance(v, dict) and "id" in v
    }
    s_index = _load_json_safe(repo_root / "wiki" / ".search-index.json")
    subtrees = s_index.get("subtrees", [])

    visited_primary: set[str] = set()
    one_hop_deps: list[WikiPageInfo] = []
    one_hop_ids: set[str] = set()
    two_hop_ids: list[str] = []
    active_domains: set[str] = set()
    query_tokens: list[str] = []
    exit_code = 0

    merged_specs: list[str] = []
    for spec in target_specs:
        if spec.startswith("#") and merged_specs and "#" not in merged_specs[-1]:
            merged_specs[-1] += spec
        elif spec.strip():
            merged_specs.append(spec)
    target_specs = merged_specs

    budget_bytes = _resolve_mode_budget_bytes(mode, budget_kb)
    eff_budget_kb = budget_bytes / 1024.0
    if mode in ("heavy", "full"):
        full_deps = True
    tracker = BudgetTracker(budget_bytes)
    dep_reserve_bytes = 900 if (include_deps and mode != "full") else 0

    for idx, target_spec in enumerate(target_specs):
        path_part, _, section_anchor = target_spec.partition("#")
        query_tokens.extend(tokenize_query(target_spec))

        clean_rel = path_part.removeprefix("wiki/").lstrip("/")
        direct_file = repo_root / "wiki" / clean_rel
        main_page = resolve_page(pages, path_part)

        if not main_page and direct_file.is_file() and direct_file.suffix == ".md":
            raw_txt = direct_file.read_text(encoding="utf-8")
            parsed = parse_markdown(raw_txt)
            title = str(parsed.frontmatter.get("title", direct_file.name))
            desc = str(parsed.frontmatter.get("description", ""))
            req_foundations = parsed.frontmatter.get("requires_foundations", [])
            for part in Path(clean_rel).parts:
                if part not in ("foundations", "tools_craft", "workflows", "_subtrees", "quickstart.md"):
                    active_domains.add(part)

            hdr_lines = ["=" * 76, f"📖 ROUTER / DOC PAGE: {title} (`wiki/{clean_rel}`)"]
            if desc:
                hdr_lines.append(f"   Boundary: {desc}")
            if isinstance(req_foundations, list) and req_foundations:
                hdr_lines.append(f"   Required Foundations: {', '.join(str(x) for x in req_foundations)}")
            hdr_lines.append("=" * 76)
            tracker.emit("\n".join(hdr_lines))
            tracker.emit(parsed.body.strip())

            if include_deps and isinstance(req_foundations, list) and req_foundations and tracker.remaining > 450:
                # Task-conditioned foundation filtering: rank foundations by token overlap, expand Top 2 inline
                scored_rfs: list[tuple[float, str, str, list[dict[str, Any]]]] = []
                for rf in req_foundations:
                    rf_str = str(rf)
                    rf_dom = rf_str.split("/")[-1]
                    rf_sts = [st for st in subtrees if st.get("domain") == rf_dom]
                    rf_hay = f"{rf_dom} " + " ".join(f"{s.get('slug', '')} {s.get('title', '')} {s.get('when', '')}" for s in rf_sts)
                    rel_sc = sum(2.0 for qt in query_tokens if len(qt) > 1 and qt in rf_hay.lower())
                    scored_rfs.append((rel_sc, rf_str, rf_dom, rf_sts))
                scored_rfs.sort(key=lambda x: x[0], reverse=True)
                max_inline_rf = len(scored_rfs) if mode in ("heavy", "full") else min(2, len(scored_rfs))
                inline_rfs = scored_rfs[:max_inline_rf]
                deferred_rfs = scored_rfs[max_inline_rf:]

                tracker.emit(
                    "\n" + "#" * 76
                    + f"\n🔗 AUTO-BUNDLED REQUIRED FOUNDATIONS (Top {len(inline_rfs)} of {len(scored_rfs)} Module(s) — Budget Guarded)\n"
                    + "#" * 76,
                    allow_truncate=False,
                )
                for _, rf_str, rf_dom, rf_sts in inline_rfs:
                    active_domains.add(rf_dom)
                    if rf_sts and not full_deps:
                        block_lines = [f"\n--- [Foundation L1 Subtree Map: `wiki/{rf_str}/quickstart.md` ({len(rf_sts)} L2 Subtrees)] ---"]
                        for st in rf_sts[:5]:
                            block_lines.append(
                                f"  • 🌿 `{rf_dom}/{st['slug']}` ({st['cardCount']} cards): {st['title']} "
                                f"| Load: `wiki_query.py read --subtree {rf_dom}/{st['slug']}`"
                            )
                        if not tracker.emit("\n".join(block_lines), allow_truncate=False):
                            break
                    else:
                        rf_qs = repo_root / "wiki" / rf_str / "quickstart.md"
                        if rf_qs.is_file():
                            rf_parsed = parse_markdown(rf_qs.read_text(encoding="utf-8"))
                            block = f"\n--- [Foundation Quickstart: `wiki/{rf_str}/quickstart.md`] ---\n{rf_parsed.body.strip()}"
                            if not tracker.emit(block, allow_truncate=True):
                                break
                if deferred_rfs and tracker.remaining > 180:
                    def_str = ", ".join(f"`wiki/{r[1]}/quickstart.md`" for r in deferred_rfs)
                    tracker.emit(f"\n📌 [Additional Linked Foundations — load on demand]: {def_str}", allow_truncate=False)
            continue

        if not main_page:
            print(f"❌ Page or ID not found: '{path_part}'", file=sys.stderr)
            exit_code = 1
            continue

        visited_primary.add(main_page.page_id)
        active_domains.add(main_page.domain)
        st_slug = id_to_subtree.get(main_page.page_id, "")
        st_info = f" | Subtree: {st_slug}" if st_slug and st_slug != "core_default" else ""

        hdr_lines = [
            "=" * 76,
            f"📖 PRIMARY PAGE: {main_page.title} (`{main_page.rel_path}`)",
            f"   ID: {main_page.page_id} | Level: {main_page.abstraction_level}{st_info} | Status: {main_page.epistemic_status}",
            f"   Boundary: {main_page.description}",
        ]
        if main_page.scripts:
            scripts_fmt = ", ".join(f"{s['role']}={s['path']}" for s in main_page.scripts)
            hdr_lines.append(f"   Bound Scripts: {scripts_fmt}")
        hdr_lines.append("=" * 76)
        hdr_text = "\n".join(hdr_lines)

        if not tracker.fits(hdr_text, reserve_bytes=220):
            tracker.emit(
                f"📌 [Deferred Primary Card due to {eff_budget_kb:.1f} KB budget]: `{main_page.page_id}` (`wiki/{main_page.rel_path}`)",
                allow_truncate=False,
            )
        else:
            tracker.emit(hdr_text, allow_truncate=False)
            if mode == "tree":
                tracker.emit(format_document_tree_outline(main_page, st_slug))
            elif mode in ("digest", "lite"):
                lbl = "HarnessPro Lite Mode — Primary Card Contract Digest" if mode == "lite" else "Primary Card Contract Digest"
                tracker.emit(format_contract_digest(main_page, st_slug, label=lbl))
            elif section_anchor:
                target_slug = slugify_heading(section_anchor)
                matched_nodes = []
                dedup_nodes = []
                body_lines: list[str] = []
                if main_page.doc_tree:
                    body_lines = main_page.clean_body.splitlines()
                    all_nodes = main_page.doc_tree.all_flat_nodes()
                    for n in all_nodes:
                        if n.slug == target_slug or section_anchor.lower() in n.title.lower() or section_anchor.lower() == n.node_id.lower():
                            matched_nodes.append(n)
                    if not matched_nodes:
                        sec_toks = [t for t in tokenize_query(section_anchor) if len(t) > 1]
                        if sec_toks:
                            scored_nodes = [
                                (sum(1 for t in sec_toks if t in n.title.lower() or t in n.slug), n)
                                for n in all_nodes
                            ]
                            scored_nodes.sort(key=lambda x: (x[0], -x[1].level), reverse=True)
                            if scored_nodes and scored_nodes[0][0] >= min(2, len(sec_toks)):
                                matched_nodes.append(scored_nodes[0][1])
                    for n in matched_nodes:
                        if not any(
                            p is not n and p.line_range[0] <= n.line_range[0] and p.line_range[1] >= n.line_range[1]
                            for p in matched_nodes
                        ):
                            dedup_nodes.append(n)
                if dedup_nodes:
                    for n in dedup_nodes:
                        s_idx = max(0, n.line_range[0] - 1)
                        e_idx = min(len(body_lines), n.line_range[1])
                        text = "\n".join(body_lines[s_idx:e_idx]).strip()
                        tracker.emit(f"📍 [Section Node `{n.node_id}` #{n.slug} | Lines {n.line_range[0]}-{n.line_range[1]}]\n{text}")
                else:
                    matched = [
                        (slug, title, text)
                        for slug, title, text in extract_sections(main_page.clean_body)
                        if slug == target_slug or section_anchor.lower() in title.lower()
                    ]
                    if matched:
                        for _, _, text in matched:
                            tracker.emit(text)
                    else:
                        out_txt = f"⚠️ Section '#{section_anchor}' not found; showing DocumentTree outline:\n\n" + format_document_tree_outline(main_page, st_slug)
                        tracker.emit(out_txt)
            else:
                body_str = main_page.clean_body.strip()
                body_bytes = len(body_str.encode("utf-8"))
                # Enforce budget from idx == 0 (no first-card exemption!)
                should_fold_to_tree = mode in ("auto", "medium") and (
                    ((main_page.subdir == "03_deep_concepts" or main_page.abstraction_level == 4) and body_bytes > 14336)
                    or not tracker.fits(body_str, reserve_bytes=dep_reserve_bytes)
                )
                if should_fold_to_tree:
                    guard_msg = (
                        f"⚡ [LENS Context Budget Guard: Card body is {body_bytes / 1024:.1f} KB "
                        f"(budget={eff_budget_kb:.1f} KB, remaining={tracker.remaining / 1024:.1f} KB). "
                        f"Showing Contract Digest + PageIndex DocumentTree; pass `#<section>` or `--mode full` for full prose.]\n"
                    )
                    out_txt = guard_msg + format_document_tree_outline(main_page, st_slug)
                    if tracker.fits(out_txt):
                        tracker.emit(out_txt, allow_truncate=False)
                    else:
                        tracker.emit(format_contract_digest(main_page, st_slug, label="Budget-Compacted Primary Digest"), allow_truncate=True)
                else:
                    tracker.emit(body_str, allow_truncate=(mode != "full"))

        # Collect 1-Hop Direct Dependencies (bounded!)
        if include_deps and main_page.depends_on:
            for dep_ref in main_page.depends_on:
                dep_clean = dep_ref.split("#")[0].strip()
                dep_page = resolve_page(pages, dep_clean)
                if dep_page and dep_page.page_id not in visited_primary:
                    active_domains.add(dep_page.domain)
                    if dep_page.page_id not in one_hop_ids:
                        one_hop_ids.add(dep_page.page_id)
                        one_hop_deps.append(dep_page)
                    for sub_dep in dep_page.depends_on:
                        sub_clean = sub_dep.split("#")[0].strip()
                        sub_page = resolve_page(pages, sub_clean)
                        if (
                            sub_page
                            and sub_page.page_id not in visited_primary
                            and sub_page.page_id not in one_hop_ids
                            and sub_page.page_id not in two_hop_ids
                        ):
                            two_hop_ids.append(sub_page.page_id)

    # Pre-format Scoped Gotchas so 1-Hop Dependency expansion reserves exact space for Retry-Savers
    gotcha_block = ""
    if active_domains:
        g_limit = 2 if mode == "lite" else (5 if mode == "heavy" else 3)
        scoped_gotchas = select_scoped_gotchas(repo_root, active_domains, query_tokens, limit=g_limit)
        if scoped_gotchas:
            g_lines = [
                "\n" + "-" * 76,
                f"⚡ SCOPED RETRY-SAVERS ({', '.join(sorted(active_domains))[:60]} — Top {len(scoped_gotchas)} Gotchas):",
            ]
            for g in scoped_gotchas:
                rec = str(g.get("recipe", ""))
                max_rec = 125 if mode == "lite" else 220
                if len(rec) > max_rec:
                    rec = rec[: max_rec - 1].rstrip() + "…"
                g_lines.append(f"  • [{g.get('id')}] ({g.get('scope')}): {rec}")
            gotcha_block = "\n".join(g_lines)
    gotcha_reserve = (len(gotcha_block.encode("utf-8")) + 24) if gotcha_block else 0

    # Render 1-Hop Dependencies strictly within remaining byte budget (reserving space for Scoped Retry-Savers)
    if include_deps and one_hop_deps and tracker.remaining > (320 + gotcha_reserve):
        if full_deps:
            hdr = (
                "\n" + "#" * 76
                + f"\n🔗 AUTO-BUNDLED 1-HOP DEPENDENCIES (BUDGET-GUARDED FULL/DIGEST MODE — {len(one_hop_deps)} Card(s))\n"
                + "#" * 76
            )
            if tracker.fits(hdr, reserve_bytes=gotcha_reserve) and tracker.emit(hdr, allow_truncate=False):
                omitted_ids: list[str] = []
                for bp in one_hop_deps:
                    full_block = (
                        f"\n--- [Dependency: `{bp.page_id}` | {bp.tier}/{bp.domain} | status: {bp.epistemic_status}] ---\n"
                        f"Title: {bp.title} (`wiki/{bp.rel_path}`)\n"
                        + bp.clean_body.strip()
                    )
                    if mode == "full" or tracker.fits(full_block, reserve_bytes=max(450, gotcha_reserve)):
                        if not tracker.emit(full_block, allow_truncate=(mode != "full")):
                            omitted_ids.append(bp.page_id)
                    else:
                        # Graceful degradation in `--mode heavy`: fall back to compact Contract Digest before overflowing 40 KB!
                        bp_st = id_to_subtree.get(bp.page_id, "")
                        dig_block = "\n" + format_contract_digest(bp, bp_st, label="1-Hop Dependency (Compacted to fit Budget)")
                        if tracker.fits(dig_block, reserve_bytes=gotcha_reserve) and tracker.emit(dig_block, allow_truncate=False):
                            pass
                        else:
                            omitted_ids.append(bp.page_id)
                if omitted_ids:
                    omit_msg = "📌 [Remaining 1-Hop Deps deferred to stay within byte cap]: " + ", ".join(
                        f"`{oid}`" for oid in omitted_ids[:8]
                    )
                    if tracker.fits(omit_msg, reserve_bytes=gotcha_reserve):
                        tracker.emit(omit_msg, allow_truncate=False)
        else:
            hdr = (
                "\n" + "#" * 76
                + f"\n🔗 AUTO-BUNDLED 1-HOP DEPENDENCY CONTRACTS ({len(one_hop_deps)} Card(s) — Sirchmunk Compact Digest)\n"
                + "   (Use `wiki_query.py read <dep_id>#<section>` or `--full-deps` if full prose is needed)\n"
                + "#" * 76
            )
            if tracker.fits(hdr, reserve_bytes=gotcha_reserve) and tracker.emit(hdr, allow_truncate=False):
                dep_slice = one_hop_deps[:3] if mode == "lite" else one_hop_deps
                omitted_ids = [bp.page_id for bp in one_hop_deps[len(dep_slice) :]]
                for bp in dep_slice:
                    bp_st = id_to_subtree.get(bp.page_id, "")
                    dig = "\n" + format_contract_digest(bp, bp_st, label="1-Hop Dependency Contract")
                    if tracker.fits(dig, reserve_bytes=gotcha_reserve) and tracker.emit(dig, allow_truncate=False):
                        pass
                    else:
                        omitted_ids.insert(0, bp.page_id)
                if omitted_ids:
                    omit_msg = "📌 [Additional 1-Hop Deps omitted by budget]: " + ", ".join(
                        f"`{oid}`" for oid in omitted_ids[:8]
                    )
                    if tracker.fits(omit_msg, reserve_bytes=gotcha_reserve):
                        tracker.emit(omit_msg, allow_truncate=False)

        if two_hop_ids:
            th_msg = f"\n📌 [2-Hop Transitive Pointers (Not Expanded — {len(two_hop_ids)} Card(s))]: " + ", ".join(
                f"`{tid}`" for tid in two_hop_ids[:10]
            )
            if tracker.fits(th_msg, reserve_bytes=gotcha_reserve):
                tracker.emit(th_msg, allow_truncate=False)

    # Auto-attach Scoped Gotchas within the remaining byte budget
    if gotcha_block and tracker.remaining > 180:
        tracker.emit(gotcha_block, allow_truncate=True)

    return exit_code


def query_gotchas(
    repo_root: Path,
    scope: str | None = None,
    query: str | None = None,
    limit: int = 10,
) -> int:
    """Display the compact L1 Gotcha Router or filter scoped entries from `wiki/.gotchas.json`."""
    if not scope and not query:
        p = repo_root / "wiki" / "GOTCHAS_AND_RETRY_SAVERS.md"
        if p.exists():
            print(p.read_text(encoding="utf-8"))
        return 0

    gdata = _load_json_safe(repo_root / "wiki" / ".gotchas.json")
    entries = gdata.get("entries", [])
    tokens = tokenize_query(f"{scope or ''} {query or ''}")
    scope_pool = select_scoped_gotchas(repo_root, {scope}, None, limit=200) if scope else entries
    scope_ids = {g.get("id") for g in scope_pool}

    matched: list[tuple[float, int, dict[str, Any]]] = []
    for idx, g in enumerate(entries):
        root_cause = g.get("root_cause", g.get("rootCause", ""))
        hay = f"{g.get('id', '')} {g.get('scope', '')} {g.get('symptom', '')} {root_cause} {g.get('recipe', '')}".lower()
        if scope and scope.lower() not in hay and g.get("id") not in scope_ids:
            continue
        score = sum((2.5 if len(t) > 1 else 0.5) for t in tokens if t in hay) if query else 1.0
        if score > 0:
            matched.append((score, idx, g))

    matched.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if not matched:
        print(f"No gotchas matched scope='{scope}' query='{query}'.")
        return 0

    eff_limit = len(matched) if limit <= 0 else min(limit, len(matched))
    print(f"=== 🛡️ Scoped Gotchas & Retry Savers (Showing {eff_limit} of {len(matched)} — pass --limit 50 for more) ===")
    for idx, (_, _, g) in enumerate(matched[:eff_limit], 1):
        sym = str(g.get("symptom", ""))
        if len(sym) > 150:
            sym = sym[:147].rstrip() + "…"
        rec = str(g.get("recipe", ""))
        if len(rec) > 220:
            rec = rec[:217].rstrip() + "…"
        print(
            f"{idx}. [{g.get('id')}] Scope: {g.get('scope')} (Updated: {g.get('updatedAt', '—')})\n"
            f"   Symptom: {sym}\n"
            f"   ✅ Recipe: {rec}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search, inspect trees, and read the 3-Tier LLM Wiki with Sirchmunk LENS context budgeting."
    )
    parser.add_argument("--root", type=Path, default=None, help="Repository root")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Unified 4-channel Recall-First + Precision-Reranked cross-domain search")
    p_search.add_argument("query", nargs="+", help="Search keywords (unquoted multi-word queries are joined)")
    p_search.add_argument("--tier", choices=["foundations", "tools_craft", "workflows"], default=None)
    p_search.add_argument("--domain", type=str, default=None)
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--json", action="store_true", help="Emit structured JSON output")
    p_search.add_argument("--explain", action="store_true", help="Show per-channel score breakdown")

    p_tree = sub.add_parser(
        "tree",
        help="Inspect L0->L1->L2 META Subtree hierarchy, domain subtrees, or a card's PageIndex DocumentTree",
    )
    p_tree.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Optional domain, <domain>/<subtree>, or <card_id>",
    )

    p_read = sub.add_parser(
        "read",
        help="Read cards/sections with strict LENS byte budget & 1-Hop Contract Digest dependency bundling",
    )
    p_read.add_argument("targets", nargs="*", type=str, help="Card IDs, relative paths, or path#section")
    p_read.add_argument("--subtree", type=str, default=None, help="Load an L2 META Subtree Router (<domain>/<slug>)")
    p_read.add_argument("--no-deps", action="store_true", help="Do not auto-bundle depends_on cards")
    p_read.add_argument(
        "--full-deps",
        action="store_true",
        help="Output full markdown bodies of 1-hop depends_on cards (subject to budget unless --mode full)",
    )
    p_read.add_argument(
        "--mode",
        choices=["auto", "lite", "medium", "heavy", "full", "digest", "tree"],
        default="auto",
        help="HarnessPro Pareto & Sirchmunk LENS reading modes: lite (<=6KB), medium/auto (<=20KB), heavy (<=40KB), full (unbounded), digest, tree",
    )
    p_read.add_argument(
        "--budget-kb",
        type=float,
        default=None,
        help="Explicit hard context budget in KB (defaults: lite=6KB, auto/medium=20KB, heavy=40KB)",
    )

    p_chk = sub.add_parser(
        "compose-checklist",
        help="Print stage-by-stage (Intro/Verse/Transition/Chorus/Modulation) & track-by-track textbook checklist",
    )
    p_chk.add_argument("--style", type=str, default="mandopop", help="Target style (default: mandopop)")
    p_chk.add_argument("--stage", type=str, default=None, help="Optional filter: intro, verse, transition, chorus, modulation")

    sub.add_parser("gaps", help="Display active curriculum gaps and recommended textbooks")

    p_gotchas = sub.add_parser("gotchas", help="Display L1 Scoped Gotcha Router or filter by --scope / --query")
    p_gotchas.add_argument("--scope", type=str, default=None, help="Domain or tool scope (e.g. daw_music_prod, visual_aesthetics, env)")
    p_gotchas.add_argument("--query", type=str, default=None, help="Filter keywords")
    p_gotchas.add_argument("--limit", type=int, default=12, help="Max entries to display")

    sub.add_parser("workflows", help="Display AWM Parameterized Workflow Memory (AWM_WORKFLOWS.md)")
    sub.add_parser("scoreboard", help="Display RSI Evolution Scoreboard & ACE Telemetry (SCOREBOARD.md)")

    args = parser.parse_args()
    repo_root = find_repo_root(args.root)

    if args.cmd == "search":
        return search_wiki(
            repo_root,
            " ".join(args.query),
            args.tier,
            args.domain,
            args.limit,
            as_json=args.json,
            explain=args.explain,
        )
    if args.cmd == "tree":
        return inspect_tree(repo_root, args.target)
    if args.cmd == "read":
        if args.subtree:
            return read_subtree_router(
                repo_root,
                args.subtree,
                include_deps=not args.no_deps,
                budget_kb=args.budget_kb,
                mode=args.mode,
            )
        if not args.targets:
            print("❌ Please provide one or more card IDs/paths or `--subtree <domain>/<slug>`.", file=sys.stderr)
            return 1
        return read_wiki_pages(
            repo_root,
            args.targets,
            include_deps=not args.no_deps,
            full_deps=args.full_deps,
            mode=args.mode,
            budget_kb=args.budget_kb,
        )
    if args.cmd == "compose-checklist":
        return print_compose_checklist(args.style, args.stage)
    if args.cmd == "gaps":
        gaps_path = repo_root / "wiki" / "CURRICULUM_GAPS.md"
        if gaps_path.exists():
            print(gaps_path.read_text(encoding="utf-8"))
        return 0
    if args.cmd == "gotchas":
        return query_gotchas(repo_root, args.scope, args.query, args.limit)
    if args.cmd == "workflows":
        p = repo_root / "wiki" / "AWM_WORKFLOWS.md"
        if p.exists():
            print(p.read_text(encoding="utf-8"))
        return 0
    if args.cmd == "scoreboard":
        p = repo_root / "wiki" / "SCOREBOARD.md"
        if p.exists():
            print(p.read_text(encoding="utf-8"))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

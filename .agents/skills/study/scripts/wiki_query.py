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
    WikiPageInfo,
    discover_wiki_pages,
    find_repo_root,
    parse_markdown,
    slugify_heading,
)


def tokenize_query(text: str) -> list[str]:
    """Tokenize English words and CJK characters/bigrams for hybrid search."""
    text_low = text.lower()
    en_tokens = re.findall(r"[a-z0-9_+-]{2,}", text_low)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text_low)
    cjk_bigrams = [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]
    return list(dict.fromkeys(en_tokens + cjk_bigrams + cjk_chars))


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
    limit: int = 8,
) -> int:
    pages = discover_wiki_pages(repo_root)
    tokens = tokenize_query(query)
    if not tokens:
        print("Empty query.")
        return 1

    ace_data = _load_json_safe(repo_root / "wiki" / ".ace-telemetry.json")
    ace_cards: dict[str, dict[str, Any]] = ace_data.get("cards", {})
    s_index = _load_json_safe(repo_root / "wiki" / ".search-index.json")
    topic_map: dict[str, list[Any]] = s_index.get("topicMap", {})
    subtrees_list: list[dict[str, Any]] = s_index.get("subtrees", [])
    manifest_pages = _load_json_safe(repo_root / "wiki" / ".page-manifest.json").get("pages", {})

    # Map page_id -> subtree
    id_to_subtree: dict[str, str] = {}
    for _, m_info in manifest_pages.items():
        if isinstance(m_info, dict) and "id" in m_info:
            id_to_subtree[str(m_info["id"])] = str(m_info.get("subtree", ""))

    # Phase 0: Match L2 META Subtrees (`KnowledgeEvolver` community routing)
    matched_subtrees: list[tuple[float, dict[str, Any]]] = []
    for st in subtrees_list:
        if domain_filter and st.get("domain") != domain_filter:
            continue
        if tier_filter and st.get("tier") != tier_filter:
            continue
        st_hay = f"{st.get('slug', '')} {st.get('title', '')} {st.get('when', '')}".lower()
        st_score = sum(
            (3.0 if len(tok) > 1 else 0.5)
            for tok in tokens
            if tok in st_hay
        )
        if st_score >= 3.0:
            matched_subtrees.append((st_score, st))
    matched_subtrees.sort(key=lambda x: x[0], reverse=True)

    # Build CorpusTopicMap boost per (page_id, node_id)
    topic_card_boost: dict[str, float] = {}
    topic_card_best_node: dict[str, tuple[float, str]] = {}
    for tok in tokens:
        if len(tok) < 2:
            continue
        postings = topic_map.get(tok, [])
        if not postings:
            continue
        # Higher IDF weight for rarer section terms
        idf_w = max(1.0, 4.5 - math.log1p(len(postings)))
        for post in postings:
            if isinstance(post, str) and "#" in post:
                cid, nid = post.split("#", 1)
            elif isinstance(post, dict):
                cid = str(post.get("cardId", ""))
                nid = str(post.get("nodeId", ""))
            else:
                continue
            if not cid:
                continue
            topic_card_boost[cid] = topic_card_boost.get(cid, 0.0) + idf_w
            prev_best = topic_card_best_node.get(cid, (0.0, ""))
            if idf_w > prev_best[0]:
                topic_card_best_node[cid] = (idf_w, nid)

    scored: list[tuple[float, WikiPageInfo, str, str, bool]] = []
    query_low = query.lower().strip()
    for p in pages:
        if tier_filter and p.tier != tier_filter:
            continue
        if domain_filter and p.domain != domain_filter:
            continue

        title_l = p.title.lower()
        desc_l = p.description.lower()
        tags_l = " ".join(p.tags).lower()
        id_l = p.page_id.lower()
        pats_l = " ".join(p.patterns).lower()
        cons_l = " ".join(_constraint_strs(p)).lower()
        body_l = p.clean_body.lower()

        c_stat = ace_cards.get(p.page_id, {})
        hist_queries = [str(q).lower() for q in c_stat.get("queries", []) if isinstance(q, str)]
        last_task_l = str(c_stat.get("last_task", "")).lower()
        if last_task_l and last_task_l not in hist_queries:
            hist_queries.append(last_task_l)
        hist_hay = " ".join(hist_queries)

        score = 0.0
        reused_query_hit = False
        if query_low and len(query_low) >= 4 and any(query_low in hq or hq in query_low for hq in hist_queries):
            score += 12.0
            reused_query_hit = True

        for tok in tokens:
            weight = 2.0 if len(tok) > 1 else 0.5
            if tok in id_l:
                score += 6.0 * weight
            if tok in title_l:
                score += 5.0 * weight
            if tok in tags_l:
                score += 4.0 * weight
            if tok in desc_l:
                score += 3.2 * weight
            if tok in pats_l or tok in cons_l:
                score += 3.5 * weight
            if tok in hist_hay:
                score += 2.8 * weight
                reused_query_hit = True
            if tok in body_l:
                score += 1.0 * weight * min(3, body_l.count(tok))

        # Add DF-pruned CorpusTopicMap boost
        score += topic_card_boost.get(p.page_id, 0.0)

        if score > 0:
            net_h = int(c_stat.get("helpful", 0)) - int(c_stat.get("harmful", 0))
            hotness = float(c_stat.get("hotness", 0.0))
            if net_h > 0:
                score += 0.9 * math.log1p(net_h) + 2.0 * hotness

            # Determine best section anchor & line range via DocumentTree
            best_anchor = ""
            best_line_info = ""
            best_sec_score = 0.0
            pref_nid = topic_card_best_node.get(p.page_id, (0.0, ""))[1]
            if p.doc_tree:
                for node in p.doc_tree.all_flat_nodes():
                    sec_s = sum(
                        2.5
                        for tok in tokens
                        if len(tok) > 1 and (tok in node.title.lower() or tok in node.summary.lower())
                    )
                    if node.node_id == pref_nid:
                        sec_s += 3.0
                    if sec_s > best_sec_score:
                        best_sec_score = sec_s
                        best_anchor = f"#{node.slug}"
                        best_line_info = f" (L{node.line_range[0]}-{node.line_range[1]}, {node.quant_rule_count} rules)"
            scored.append((score, p, best_anchor, best_line_info, reused_query_hit))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Phase 2: SAG2-inspired Zero-LLM 2-Hop Dynamic Bridge-Term Expansion (Query-Time Hyperedges)
    hop2_bridges_used: list[tuple[str, float, list[str], list[str]]] = []
    hop2_page_via: dict[str, list[str]] = {}
    if scored:
        seed_ids = {p.page_id for _, p, _, _, _ in scored[:3]}
        eligible_pages: dict[str, WikiPageInfo] = {}
        for p in pages:
            if tier_filter and p.tier != tier_filter:
                continue
            if domain_filter and p.domain != domain_filter:
                continue
            eligible_pages[p.page_id] = p

        term_to_cids: dict[str, set[str]] = {}
        seed_tag_terms: set[str] = set()
        for bt_raw, plist in topic_map.items():
            bt = str(bt_raw).strip().lower()
            if len(bt) < 2 or bt.isdigit():
                continue
            for post in plist:
                cid = post.split("#", 1)[0] if isinstance(post, str) and "#" in post else (
                    str(post.get("cardId", "")) if isinstance(post, dict) else ""
                )
                if cid in eligible_pages:
                    term_to_cids.setdefault(bt, set()).add(cid)

        for pid, p_obj in eligible_pages.items():
            for tg in p_obj.tags:
                bt = str(tg).strip().lower()
                if len(bt) >= 2 and not bt.isdigit():
                    term_to_cids.setdefault(bt, set()).add(pid)
                    if pid in seed_ids:
                        seed_tag_terms.add(bt)
            if not topic_map and p_obj.doc_tree:
                for node in p_obj.doc_tree.all_flat_nodes():
                    for tok_m in re.finditer(r"[a-zA-Z0-9][a-zA-Z0-9_\-]{2,24}|[\u4e00-\u9fff]{2,8}", node.title):
                        bt = tok_m.group(0).lower()
                        if not bt.isdigit():
                            term_to_cids.setdefault(bt, set()).add(pid)

        bridge_stopwords = {
            "rules", "overview", "summary", "patterns", "constraints", "core",
            "aesthetics", "playbook", "provisional", "verified", "section",
            "design", "designing", "system", "systems", "cases", "case", "study",
            "guide", "manual", "principles", "theory", "method", "methods",
            "analysis", "models", "model", "engine", "engines", "framework",
            "pipeline", "workflow", "architecture", "introduction", "chapter",
            "part", "level", "phase", "step", "steps", "data", "code", "script",
            "概述", "规则", "总结", "核心", "基础", "进阶", "案例", "清单",
            "设计", "系统", "原理", "方法", "指南", "手册", "架构", "工作流",
            "流程", "分析", "模型", "引擎", "章节", "实战", "铁律", "标准",
        }
        q_tok_set = {t for t in tokens if len(t) >= 2}
        cand_bridges: list[tuple[float, str, list[str], list[str]]] = []
        for bt, cids in term_to_cids.items():
            if bt in bridge_stopwords or bt in q_tok_set:
                continue
            if any(bt in qt or qt in bt for qt in q_tok_set):
                continue
            seed_hits = sorted(cids & seed_ids)
            hop2_targets = sorted(cids - seed_ids)
            if not seed_hits or not (1 <= len(hop2_targets) <= 6):
                continue
            idf_bt = max(1.0, 4.2 - math.log1p(len(cids)))
            tag_bonus = 1.8 if bt in seed_tag_terms else 0.0
            bt_score = (len(seed_hits) * 2.5) + idf_bt + tag_bonus + (0.5 if len(bt) >= 4 else 0.0)
            cand_bridges.append((bt_score, bt, seed_hits, hop2_targets))

        cand_bridges.sort(key=lambda x: (-x[0], -len(x[3]), x[1]))
        hop2_boost_map: dict[str, float] = {}
        covered_h2_targets: set[str] = set()
        for bt_score, bt, s_hits, h2_targets in cand_bridges:
            if len(hop2_bridges_used) >= 4:
                break
            if covered_h2_targets and set(h2_targets).issubset(covered_h2_targets):
                continue
            covered_h2_targets.update(h2_targets)
            hop2_bridges_used.append((bt, bt_score, s_hits, h2_targets))
            hop2_w = round(0.45 * bt_score, 2)
            for tid in h2_targets:
                hop2_boost_map[tid] = hop2_boost_map.get(tid, 0.0) + hop2_w
                if bt not in hop2_page_via.setdefault(tid, []):
                    hop2_page_via[tid].append(bt)

        if hop2_boost_map:
            existing_scored_ids: dict[str, int] = {item[1].page_id: idx for idx, item in enumerate(scored)}
            for tid, h2_add in hop2_boost_map.items():
                via_terms = hop2_page_via.get(tid, [])
                if tid in existing_scored_ids:
                    idx = existing_scored_ids[tid]
                    old_sc, p_obj, b_anc, b_line, q_hit = scored[idx]
                    scored[idx] = (old_sc + h2_add, p_obj, b_anc, b_line, q_hit)
                elif tid in eligible_pages:
                    p_obj = eligible_pages[tid]
                    c_stat = ace_cards.get(p_obj.page_id, {})
                    net_h = int(c_stat.get("helpful", 0)) - int(c_stat.get("harmful", 0))
                    hotness = float(c_stat.get("hotness", 0.0))
                    base_sc = h2_add + (0.9 * math.log1p(net_h) + 1.5 * hotness if net_h > 0 else 0.0)
                    best_anchor = ""
                    best_line_info = ""
                    if p_obj.doc_tree:
                        for node in p_obj.doc_tree.all_flat_nodes():
                            if any(vt in node.title.lower() or vt in node.summary.lower() for vt in via_terms):
                                best_anchor = f"#{node.slug}"
                                best_line_info = f" (L{node.line_range[0]}-{node.line_range[1]}, {node.quant_rule_count} rules)"
                                break
                    scored.append((base_sc, p_obj, best_anchor, best_line_info, False))
            scored.sort(key=lambda x: x[0], reverse=True)

    if not scored and not matched_subtrees:
        print(f"No matching wiki cards found for '{query}'.")
        return 0

    if matched_subtrees:
        print(f"=== 🌿 Phase 0: Matched L2 META Subtree Routers (Top {min(3, len(matched_subtrees))}) ===")
        for _, st in matched_subtrees[:3]:
            st_rel = st.get("relPath") or st.get("path", "")
            print(
                f"  • [{st['domain']}] Subtree `{st['slug']}` ({st['cardCount']} cards): {st['title']}\n"
                f"    Trigger: {st['when']}\n"
                f"    Router : wiki/{st_rel}  (Run: `wiki_query.py read --subtree {st['domain']}/{st['slug']}`)"
            )
        print()

    if hop2_bridges_used:
        print(f"=== 🌉 Phase 2: SAG2 2-Hop Dynamic Bridge-Term Expansion (Top {len(hop2_bridges_used)} Zero-LLM Hyperedges) ===")
        for bt, bt_sc, s_hits, h2_targs in hop2_bridges_used:
            print(
                f"  • Bridge Term `{bt}` (IDF={bt_sc:.1f}): Seed [{', '.join(s_hits[:2])}] ──(2-Hop)──> [{', '.join(h2_targs[:4])}]"
            )
        print()

    print(f"=== 🎯 Phase 1: Hybrid CorpusTopicMap + KnowledgeCluster Results for '{query}' (Top {min(limit, len(scored))}) ===")
    for idx, (score, p, anchor, line_info, q_hit) in enumerate(scored[:limit], 1):
        c_stat = ace_cards.get(p.page_id, {})
        h_cnt = int(c_stat.get("helpful", 0))
        m_cnt = int(c_stat.get("harmful", 0))
        hotness = float(c_stat.get("hotness", 0.0))
        ace_str = f", ACE:+{h_cnt}/-{m_cnt}, hot={hotness:.2f}" if (h_cnt or m_cnt or hotness > 0) else ""
        st_slug = id_to_subtree.get(p.page_id, "")
        st_str = f", subtree={st_slug}" if st_slug and st_slug != "core_default" else ""
        reuse_badge = " ⚡[Query-Memory Hit]" if q_hit else ""
        via_list = hop2_page_via.get(p.page_id, [])
        via_badge = f" 🌉[2-Hop via: {', '.join(via_list[:2])}]" if via_list else ""
        deps_str = f" | 🔗 1-hop deps: {', '.join(p.depends_on[:4])}" if p.depends_on else ""
        scripts_str = f" | ⚙️ scripts: {', '.join(s['path'] for s in p.scripts)}" if p.scripts else ""
        top_pat = f"\n   Pattern : {p.patterns[0]}" if p.patterns else ""
        print(
            f"{idx}. [{p.tier}/{p.domain}] {p.title} (id: {p.page_id}, level: {p.abstraction_level}{st_str}{ace_str}){reuse_badge}{via_badge}\n"
            f"   Ref     : {p.rel_path}{anchor}{line_info}\n"
            f"   Boundary: {p.description}{deps_str}{scripts_str}{top_pat}"
        )
    return 0


def resolve_page(pages: list[WikiPageInfo], target: str) -> WikiPageInfo | None:
    clean = target.removeprefix("wiki/").lstrip("/").strip()
    clean_no_ext = clean.removesuffix(".md")
    clean_norm = clean_no_ext.lower().replace("_", "-")

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


def read_subtree_router(repo_root: Path, subtree_spec: str, include_deps: bool = True) -> int:
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

    print("=" * 80)
    print(f"🌿 L2 META SUBTREE ROUTER: {matched['domain']}/{matched['slug']} (`wiki/{st_rel}`)")
    print("=" * 80)
    print(st_path.read_text(encoding="utf-8").strip())

    if include_deps:
        core_ids = matched.get("coreIds", [])
        if core_ids:
            print("\n" + "#" * 80)
            print(f"🛡️ SCOPED 00_CORE_AESTHETICS CONTRACT DIGESTS ({len(core_ids)} Card(s))")
            print("#" * 80)
            for cid in core_ids:
                cp = resolve_page(pages, cid)
                if cp:
                    print("\n" + format_contract_digest(cp, matched["slug"], label="Scoped 00_Core Contract"))

    gotchas = select_scoped_gotchas(repo_root, {str(matched["domain"])}, tokenize_query(matched["slug"]), limit=3)
    if gotchas:
        print("\n" + "-" * 80)
        print(f"⚡ SCOPED RETRY-SAVERS (`{matched['domain']}` Top {len(gotchas)} Gotchas):")
        for g in gotchas:
            print(f"  • [{g.get('id')}] ({g.get('scope')}): {g.get('recipe')}")
    return 0


def read_wiki_pages(
    repo_root: Path,
    target_specs: list[str],
    include_deps: bool = True,
    full_deps: bool = False,
    mode: str = "auto",
    budget_kb: float = 20.0,
) -> int:
    """LENS Budgeted Reader with 1-Hop Contract Digests and DocumentTree section routing."""
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

    budget_bytes = int(budget_kb * 1024)
    if mode == "lite":
        budget_bytes = min(budget_bytes, 6 * 1024)
    elif mode in ("heavy", "full"):
        budget_bytes = max(budget_bytes, 40 * 1024)
        if mode == "heavy":
            full_deps = True
    used_bytes = 0
    multi_target = len(target_specs) > 1

    for idx, target_spec in enumerate(target_specs):
        path_part, _, section_anchor = target_spec.partition("#")
        query_tokens.extend(tokenize_query(path_part))

        # Check if user passed a router/doc markdown path directly (e.g. quickstart.md or _subtrees/*.md)
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

            print("=" * 76)
            print(f"📖 ROUTER / DOC PAGE: {title} (`wiki/{clean_rel}`)")
            if desc:
                print(f"   Boundary: {desc}")
            if isinstance(req_foundations, list) and req_foundations:
                print(f"   Required Foundations: {', '.join(str(x) for x in req_foundations)}")
            print("=" * 76)
            body_out = parsed.body.strip()
            print(body_out)
            used_bytes += len(body_out.encode("utf-8"))

            if include_deps and isinstance(req_foundations, list) and req_foundations:
                print("\n" + "#" * 76)
                print(f"🔗 AUTO-BUNDLED REQUIRED FOUNDATIONS (L1/L2 Subtree Digests — {len(req_foundations)} Module(s))")
                print("#" * 76)
                for rf in req_foundations:
                    rf_str = str(rf)
                    rf_dom = rf_str.split("/")[-1]
                    active_domains.add(rf_dom)
                    rf_sts = [st for st in subtrees if st.get("domain") == rf_dom]
                    if rf_sts and not full_deps:
                        print(f"\n--- [Foundation L1 Subtree Map: `wiki/{rf_str}/quickstart.md` ({len(rf_sts)} L2 Subtrees)] ---")
                        for st in rf_sts:
                            print(
                                f"  • 🌿 `{rf_dom}/{st['slug']}` ({st['cardCount']} cards): {st['title']}\n"
                                f"    When: {st['when']} | Load: `wiki_query.py read --subtree {rf_dom}/{st['slug']}`"
                            )
                    else:
                        rf_qs = repo_root / "wiki" / rf_str / "quickstart.md"
                        if rf_qs.is_file():
                            rf_parsed = parse_markdown(rf_qs.read_text(encoding="utf-8"))
                            print(f"\n--- [Foundation Quickstart: `wiki/{rf_str}/quickstart.md`] ---")
                            print(rf_parsed.body.strip())
            continue

        if not main_page:
            print(f"❌ Page or ID not found: '{path_part}'", file=sys.stderr)
            exit_code = 1
            continue

        visited_primary.add(main_page.page_id)
        active_domains.add(main_page.domain)
        st_slug = id_to_subtree.get(main_page.page_id, "")
        st_info = f" | Subtree: {st_slug}" if st_slug and st_slug != "core_default" else ""

        print("=" * 76)
        print(f"📖 PRIMARY PAGE: {main_page.title} (`{main_page.rel_path}`)")
        print(
            f"   ID: {main_page.page_id} | Level: {main_page.abstraction_level}{st_info} | Status: {main_page.epistemic_status}"
        )
        print(f"   Boundary: {main_page.description}")
        if main_page.scripts:
            scripts_fmt = ", ".join(f"{s['role']}={s['path']}" for s in main_page.scripts)
            print(f"   Bound Scripts: {scripts_fmt}")
        print("=" * 76)

        if mode == "tree":
            out_txt = format_document_tree_outline(main_page, st_slug)
            print(out_txt)
            used_bytes += len(out_txt.encode("utf-8"))
        elif mode in ("digest", "lite"):
            lbl = "HarnessPro Lite Mode — Primary Card Contract Digest" if mode == "lite" else "Primary Card Contract Digest"
            out_txt = format_contract_digest(main_page, st_slug, label=lbl)
            print(out_txt)
            used_bytes += len(out_txt.encode("utf-8"))
        elif section_anchor:
            target_slug = slugify_heading(section_anchor)
            matched_nodes = []
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
                # Deduplicate child nodes already covered by a parent node's line range
                dedup_nodes = []
                for n in matched_nodes:
                    if not any(
                        p is not n and p.line_range[0] <= n.line_range[0] and p.line_range[1] >= n.line_range[1]
                        for p in matched_nodes
                    ):
                        dedup_nodes.append(n)
            if matched_nodes:
                for n in dedup_nodes:
                    s_idx = max(0, n.line_range[0] - 1)
                    e_idx = min(len(body_lines), n.line_range[1])
                    text = "\n".join(body_lines[s_idx:e_idx]).strip()
                    print(f"📍 [Section Node `{n.node_id}` #{n.slug} | Lines {n.line_range[0]}-{n.line_range[1]}]")
                    print(text)
                    used_bytes += len(text.encode("utf-8"))
            else:
                matched = [
                    (slug, title, text)
                    for slug, title, text in extract_sections(main_page.clean_body)
                    if slug == target_slug or section_anchor.lower() in title.lower()
                ]
                if matched:
                    for _, _, text in matched:
                        print(text)
                        used_bytes += len(text.encode("utf-8"))
                else:
                    print(f"⚠️ Section '#{section_anchor}' not found; showing DocumentTree outline:\n")
                    out_txt = format_document_tree_outline(main_page, st_slug)
                    print(out_txt)
                    used_bytes += len(out_txt.encode("utf-8"))
        else:
            body_str = main_page.clean_body.strip()
            body_bytes = len(body_str.encode("utf-8"))
            # LENS Auto-Budget Guard:
            # 1) If reading a `03_deep_concepts` / L4 case card > 14 KB without `#section` in auto mode, OR
            # 2) If reading multiple cards in a batch and cumulative bytes already exceed budget_bytes:
            if mode in ("auto", "medium") and (
                ((main_page.subdir == "03_deep_concepts" or main_page.abstraction_level == 4) and body_bytes > 14336)
                or (multi_target and idx > 0 and (used_bytes + body_bytes) > budget_bytes)
            ):
                print(
                    f"⚡ [LENS Context Budget Guard: Card body is {body_bytes / 1024:.1f} KB "
                    f"(cumulative budget={budget_kb:.1f} KB). Showing Contract Digest + PageIndex DocumentTree; "
                    f"pass `#<section>` or `--mode full` for full prose.]\n"
                )
                out_txt = format_document_tree_outline(main_page, st_slug)
                print(out_txt)
                used_bytes += len(out_txt.encode("utf-8"))
            else:
                print(body_str)
                used_bytes += body_bytes

        # Collect 1-Hop Direct Dependencies (bounded! No unbounded BFS!)
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

    # Render 1-Hop Dependencies as compact Sirchmunk Contract Digests (unless --full-deps is explicitly requested)
    if include_deps and one_hop_deps:
        print("\n" + "#" * 76)
        if full_deps:
            print(f"🔗 AUTO-BUNDLED 1-HOP DEPENDENCIES (FULL TEXT MODE — {len(one_hop_deps)} Card(s))")
            print("#" * 76)
            for bp in one_hop_deps:
                print(f"\n--- [Dependency: `{bp.page_id}` | {bp.tier}/{bp.domain} | status: {bp.epistemic_status}] ---")
                print(f"Title: {bp.title} (`wiki/{bp.rel_path}`)")
                print(bp.clean_body.strip())
        else:
            print(
                f"🔗 AUTO-BUNDLED 1-HOP DEPENDENCY CONTRACTS ({len(one_hop_deps)} Card(s) — Sirchmunk Compact Digest)"
            )
            print("   (Use `wiki_query.py read <dep_id>#<section>` or `--full-deps` if full prose is needed)")
            print("#" * 76)
            dep_slice = one_hop_deps[:3] if mode == "lite" else one_hop_deps
            for bp in dep_slice:
                bp_st = id_to_subtree.get(bp.page_id, "")
                print("\n" + format_contract_digest(bp, bp_st, label="1-Hop Dependency Contract"))
        if two_hop_ids:
            print(
                f"\n📌 [2-Hop Transitive Pointers (Not Expanded — {len(two_hop_ids)} Card(s))]: "
                + ", ".join(f"`{tid}`" for tid in two_hop_ids[:10])
            )

    # Auto-attach Scoped Gotchas for the active domains (2 in lite mode, 5 in heavy mode, 3 in auto/medium)
    if active_domains:
        g_limit = 2 if mode == "lite" else (5 if mode == "heavy" else 3)
        scoped_gotchas = select_scoped_gotchas(repo_root, active_domains, query_tokens, limit=g_limit)
        if scoped_gotchas:
            print("\n" + "-" * 76)
            print(f"⚡ SCOPED RETRY-SAVERS ({', '.join(sorted(active_domains))} — Top {len(scoped_gotchas)} Gotchas):")
            for g in scoped_gotchas:
                print(f"  • [{g.get('id')}] ({g.get('scope')}): {g.get('recipe')}")

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
        hay = f"{g.get('id', '')} {g.get('scope', '')} {g.get('symptom', '')} {g.get('rootCause', '')} {g.get('recipe', '')}".lower()
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

    p_search = sub.add_parser("search", help="Hybrid CorpusTopicMap + Subtree search across wiki cards and sections")
    p_search.add_argument("query", type=str, help="Search keywords")
    p_search.add_argument("--tier", choices=["foundations", "tools_craft", "workflows"], default=None)
    p_search.add_argument("--domain", type=str, default=None)
    p_search.add_argument("--limit", type=int, default=8)

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
        help="Read cards/sections with LENS token budget & 1-Hop Contract Digest dependency bundling",
    )
    p_read.add_argument("targets", nargs="*", type=str, help="Card IDs, relative paths, or path#section")
    p_read.add_argument("--subtree", type=str, default=None, help="Load an L2 META Subtree Router (<domain>/<slug>)")
    p_read.add_argument("--no-deps", action="store_true", help="Do not auto-bundle depends_on cards")
    p_read.add_argument(
        "--full-deps",
        action="store_true",
        help="Output full markdown bodies of 1-hop depends_on cards instead of compact Contract Digests",
    )
    p_read.add_argument(
        "--mode",
        choices=["auto", "lite", "medium", "heavy", "full", "digest", "tree"],
        default="auto",
        help="HarnessPro Pareto & Sirchmunk LENS reading modes: lite (<=6KB Contract Digest), medium/auto (<=18KB primary + 1-hop digests), heavy/full (untruncated + full 1-hop deps), digest, tree",
    )
    p_read.add_argument(
        "--budget-kb",
        type=float,
        default=20.0,
        help="Soft context budget in KB for auto mode (default: 20.0 KB)",
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
        return search_wiki(repo_root, args.query, args.tier, args.domain, args.limit)
    if args.cmd == "tree":
        return inspect_tree(repo_root, args.target)
    if args.cmd == "read":
        if args.subtree:
            return read_subtree_router(repo_root, args.subtree, include_deps=not args.no_deps)
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

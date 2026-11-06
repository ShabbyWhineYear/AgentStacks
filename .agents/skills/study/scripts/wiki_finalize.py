#!/usr/bin/env python3
"""Deterministic Wiki Finalizer, Link Redirector, Claims Reconciler & Curriculum Gap Engine.

Ported and expanded from LangChain OpenWiki's `wiki-finalizer.ts`:
1. Surgical Frontmatter Migration (`--migrate`) & File Move/Alias Redirect (`--move old new`).
2. Mermaid diagram syntax validation.
3. Grounded Claims (`wiki/.claims/*.json`) SHA-256 fingerprint verification & staleness detection.
4. Auto-injection of cross-tier dependency banners (`<!-- WIKI-DEPS:START -->`) into card bodies.
5. Deterministic generation of `_domain_index.md`, `wiki/index.md`, and `GEMINI.md` routing tables.
6. Active Epistemic Curiosity analysis -> updates `wiki/CURRICULUM_GAPS.md` and `wiki/.page-manifest.json`.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from wiki_core import (
    ALL_PAGE_TYPES,
    CARD_TYPES,
    DEPS_BANNER_END,
    DEPS_BANNER_START,
    TIERS,
    WikiPageInfo,
    discover_wiki_pages,
    find_repo_root,
    now_iso,
    parse_markdown,
    replace_managed_block,
    resolve_evidence_fingerprint,
    strip_deps_banner,
    surgical_update_frontmatter,
    write_json_if_changed,
    write_text_if_changed,
)

SUPPORTED_MERMAID_TYPES = (
    "flowchart ",
    "graph ",
    "sequenceDiagram",
    "stateDiagram",
    "classDiagram",
    "erDiagram",
    "xychart-beta",
)


def move_and_redirect_page(repo_root: Path, old_rel: str, new_rel: str) -> None:
    """Remediation Channel 1: Move a wiki page, record old path in aliases, and rewrite all references."""
    wiki_root = repo_root / "wiki"
    old_rel = old_rel.removeprefix("wiki/").lstrip("/")
    new_rel = new_rel.removeprefix("wiki/").lstrip("/")

    old_path = wiki_root / old_rel
    new_path = wiki_root / new_rel
    if not old_path.exists():
        raise FileNotFoundError(f"Source page does not exist: {old_path}")

    new_path.parent.mkdir(parents=True, exist_ok=True)
    raw = old_path.read_text(encoding="utf-8")
    parsed = parse_markdown(raw)
    aliases = parsed.frontmatter.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = []
    if old_rel not in aliases:
        aliases.append(old_rel)
    updated_raw = surgical_update_frontmatter(raw, {"aliases": aliases})
    new_path.write_text(updated_raw, encoding="utf-8")
    old_path.unlink()

    # Move .claims sidecar if present
    old_claim = wiki_root / ".claims" / Path(old_rel).with_suffix(".json")
    new_claim = wiki_root / ".claims" / Path(new_rel).with_suffix(".json")
    if old_claim.exists():
        new_claim.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_claim), str(new_claim))

    # Rewrite references across all markdown files in wiki/
    for md in wiki_root.rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        if old_rel in text:
            new_text = text.replace(old_rel, new_rel)
            md.write_text(new_text, encoding="utf-8")

    print(f"✅ Moved '{old_rel}' -> '{new_rel}', recorded alias, and redirected all references.")


def validate_mermaid_blocks(clean_body: str, rel_path: str) -> list[str]:
    """Validate Mermaid code fences for unsupported types or unquoted parentheses in labels."""
    issues: list[str] = []
    pattern = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
    for idx, match in enumerate(pattern.finditer(clean_body), 1):
        block = match.group(1).strip()
        if not block:
            issues.append(f"[{rel_path}] Empty mermaid block #{idx}")
            continue
        first_line = block.splitlines()[0].strip()
        if not any(first_line.startswith(t) for t in SUPPORTED_MERMAID_TYPES):
            issues.append(
                f"[{rel_path}] Mermaid block #{idx} uses unsupported header '{first_line}'. "
                f"Use one of: {', '.join(SUPPORTED_MERMAID_TYPES)}"
            )
        # Check for unquoted parentheses inside square brackets like id[Label (info)]
        for ln_no, ln in enumerate(block.splitlines(), 1):
            if re.search(r"\[[^\"\]\n]*\([^\"\]\n]*\)[^\"\]\n]*\]", ln):
                issues.append(
                    f"[{rel_path}] Mermaid block #{idx} line {ln_no} has unquoted parentheses inside [...]; use id[\"Label (info)\"]"
                )
    return issues


def reconcile_page_claims(
    repo_root: Path,
    page: WikiPageInfo,
    manifest_entry: dict[str, Any] | None,
    actor: str,
    refresh_claims: bool = False,
) -> tuple[list[str], list[str], bool]:
    """Reconcile `.claims/` sidecar for a page, detect stale script/source evidence, and project sources.

    Returns (projected_sources, stale_messages, is_clean_verified).
    """
    wiki_root = repo_root / "wiki"
    sidecar_path = wiki_root / ".claims" / Path(page.rel_path).with_suffix(".json")
    projected_sources: list[str] = []
    stale_msgs: list[str] = []

    existing_sources = page.frontmatter.get("sources", [])
    if isinstance(existing_sources, list):
        for s in existing_sources:
            if isinstance(s, str) and s not in projected_sources:
                projected_sources.append(s)
            elif isinstance(s, dict) and "resource" in s and s["resource"] not in projected_sources:
                projected_sources.append(str(s["resource"]))

    if not sidecar_path.exists():
        # Create an initial sidecar skeleton so every card has a durable claim sidecar
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_data = {
            "schemaVersion": 1,
            "pageId": page.page_id,
            "pageVersion": page.body_hash,
            "updatedAt": now_iso(),
            "claims": [],
        }
        sidecar_path.write_text(json.dumps(sidecar_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return projected_sources, stale_msgs, True

    try:
        sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception as exc:
        stale_msgs.append(f"[{page.rel_path}] Invalid JSON in sidecar {sidecar_path.name}: {exc}")
        return projected_sources, stale_msgs, False

    claims = sidecar_data.get("claims", [])
    all_clean = True
    for claim in claims:
        cid = claim.get("id", "unknown")
        evidences = claim.get("evidence", [])
        claim_stale = False
        for ev in evidences:
            res_uri = ev.get("resource", "")
            if res_uri and res_uri not in projected_sources:
                projected_sources.append(res_uri)
            exists, current_fp = resolve_evidence_fingerprint(repo_root, res_uri)
            recorded_fp = ev.get("fingerprint", "")
            if not exists:
                # Raw textbook PDFs might be external or not yet committed; only mark script:// or wiki:// missing as hard stale
                if res_uri.startswith(("script://", "wiki://")):
                    claim_stale = True
                    all_clean = False
                    stale_msgs.append(
                        f"[{page.rel_path}] Claim '{cid}' references missing resource '{res_uri}'"
                    )
            else:
                if not recorded_fp or refresh_claims:
                    ev["fingerprint"] = current_fp
                elif recorded_fp != current_fp:
                    claim_stale = True
                    all_clean = False
                    stale_msgs.append(
                        f"[{page.rel_path}] Claim '{cid}' is STALE: evidence '{res_uri}' changed "
                        f"({recorded_fp[:15]}... -> {current_fp[:15]}...)"
                    )
        claim["status"] = "stale" if claim_stale else "clean"

    sidecar_data["pageId"] = page.page_id
    sidecar_data["pageVersion"] = page.body_hash
    sidecar_data["updatedAt"] = now_iso()
    write_json_if_changed(sidecar_path, sidecar_data)
    return projected_sources, stale_msgs, all_clean



SCENE_BUNDLE_RULES: list[tuple[str, str, str, list[str], list[str]]] = []

# Sirchmunk (arXiv:2608.16185) KnowledgeEvolver L2 META Subtree Specifications
# Loaded dynamically from `wiki/.subtree-specs.json` (or auto-clustered when a domain exceeds 15 cards).
META_SUBTREE_SPECS: dict[str, list[dict[str, Any]]] = {}


def _load_subtree_specs(repo_root: Path, dom_name: str, dom_pages: list[WikiPageInfo]) -> list[dict[str, Any]]:
    spec_file = repo_root / "wiki" / ".subtree-specs.json"
    if spec_file.exists():
        try:
            data = json.loads(spec_file.read_text(encoding="utf-8"))
            custom = data.get("domains", {}).get(dom_name, [])
            if isinstance(custom, list) and custom:
                return custom
        except Exception:
            pass
    if dom_name in META_SUBTREE_SPECS and META_SUBTREE_SPECS[dom_name]:
        return META_SUBTREE_SPECS[dom_name]
    # Auto-cluster by top tags if domain grows beyond 15 cards without manual specs
    if len(dom_pages) <= 15:
        return []
    tag_counts: dict[str, int] = {}
    for p in dom_pages:
        for t in p.tags:
            tl = t.lower().strip()
            if len(tl) >= 3:
                tag_counts[tl] = tag_counts.get(tl, 0) + 1
    top_tags = [t for t, _ in sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:4]]
    if not top_tags:
        return []
    auto_specs: list[dict[str, Any]] = []
    for t in top_tags:
        slug = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
        auto_specs.append({
            "slug": f"cluster_{slug}",
            "title": f"{dom_name} — {t} Subtree Cluster",
            "when": f"当处理 {dom_name} 中与 {t} 相关的子任务时激活",
            "keywords": [t],
            "script_keywords": [slug],
        })
    return auto_specs


def compute_weak_semantic_edges_and_subtrees(
    pages: list[WikiPageInfo],
    ace_data: dict[str, Any],
) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]], dict[str, list[str]], dict[str, float]]:
    """Sirchmunk KnowledgeEvolver: Compute WeakSemanticEdges, assign L2 META subtrees, and derive queries/hotness.

    Returns (page_to_subtree, page_to_weak_edges, page_to_queries, page_to_hotness).
    """
    ace_cards = ace_data.get("cards", {})
    traces = ace_data.get("recentTraces", [])

    # 1. Build historical queries per card from ACE traces & last_task
    page_to_queries: dict[str, list[str]] = {p.page_id: [] for p in pages}
    co_occur_counts: dict[tuple[str, str], int] = {}

    for p in pages:
        c_stat = ace_cards.get(p.page_id, {})
        existing_q = c_stat.get("queries", [])
        if isinstance(existing_q, list):
            for q in existing_q:
                if isinstance(q, str) and q.strip() and q.strip() not in page_to_queries[p.page_id]:
                    page_to_queries[p.page_id].append(q.strip())
        last_t = str(c_stat.get("last_task", "")).strip()
        if last_t and last_t not in page_to_queries[p.page_id]:
            page_to_queries[p.page_id].append(last_t)

    for tr in traces:
        t_str = str(tr.get("task", "")).strip()
        h_cards = [str(c) for c in tr.get("helpful_cards", []) if isinstance(c, str)]
        for cid in h_cards:
            if cid in page_to_queries and t_str and t_str not in page_to_queries[cid]:
                page_to_queries[cid].append(t_str)
        for i in range(len(h_cards)):
            for j in range(i + 1, len(h_cards)):
                a, b = sorted((h_cards[i], h_cards[j]))
                co_occur_counts[(a, b)] = co_occur_counts.get((a, b), 0) + 1

    for cid in page_to_queries:
        page_to_queries[cid] = page_to_queries[cid][-5:]

    # 2. Build WeakSemanticEdges (depends_on + shared_script + ace_co_occur + tag_sim)
    adj: dict[str, dict[str, tuple[float, str]]] = {p.page_id: {} for p in pages}
    id_set = {p.page_id for p in pages}

    def _add_edge(u: str, v: str, w: float, src: str) -> None:
        if u == v or u not in adj or v not in adj:
            return
        prev_w, prev_src = adj[u].get(v, (0.0, src))
        new_w = min(1.0, round(prev_w + w, 3))
        best_src = src if w >= prev_w else prev_src
        adj[u][v] = (new_w, best_src)
        adj[v][u] = (new_w, best_src)

    # 2a. Direct depends_on edges
    for p in pages:
        for dep in p.depends_on:
            dep_id = dep.split("#")[0].strip()
            if dep_id in id_set:
                _add_edge(p.page_id, dep_id, 0.45, "depends_on")

    # 2b. Shared script edges
    script_to_pids: dict[str, list[str]] = {}
    for p in pages:
        for sc in p.scripts:
            sname = Path(sc["path"]).name
            script_to_pids.setdefault(sname, []).append(p.page_id)
    for _, pids in script_to_pids.items():
        if len(pids) <= 12:
            for i in range(len(pids)):
                for j in range(i + 1, len(pids)):
                    _add_edge(pids[i], pids[j], 0.40, "shared_script")

    # 2c. ACE co-occurrence edges
    for (u, v), cnt in co_occur_counts.items():
        if u in id_set and v in id_set:
            _add_edge(u, v, min(0.45, 0.20 * cnt), "co_occur")

    page_to_weak_edges: dict[str, list[dict[str, Any]]] = {}
    for pid, nbrs in adj.items():
        ranked = sorted(nbrs.items(), key=lambda x: x[1][0], reverse=True)[:6]
        page_to_weak_edges[pid] = [
            {"targetId": n_id, "weight": w, "source": src}
            for n_id, (w, src) in ranked
            if w >= 0.30
        ]

    # 3. Compute Sirchmunk Hotness score in [0.1, 1.0]
    page_to_hotness: dict[str, float] = {}
    for p in pages:
        c_stat = ace_cards.get(p.page_id, {})
        h = int(c_stat.get("helpful", 0))
        m = int(c_stat.get("harmful", 0))
        deg = len(page_to_weak_edges.get(p.page_id, []))
        base = 0.35 if p.subdir == "00_core_aesthetics" else 0.20
        hot = min(1.0, round(base + 0.12 * max(0, h - m) + 0.04 * deg, 2))
        page_to_hotness[p.page_id] = hot

    # 4. Assign each card to an L2 META Subtree (Seed matching + Graph Community Label Propagation)
    page_to_subtree: dict[str, str] = {}
    for dom_name, specs in META_SUBTREE_SPECS.items():
        dom_pages = [p for p in pages if p.domain == dom_name]
        unassigned: list[WikiPageInfo] = []
        for p in dom_pages:
            hay = f"{p.page_id} {p.title} {p.description} {' '.join(p.tags)} {' '.join(s['path'] for s in p.scripts)}".lower()
            best_slug = ""
            best_hits = 0
            for sp in specs:
                hits = sum(2 for kw in sp["keywords"] if kw.lower() in hay)
                hits += sum(3 for skw in sp.get("script_keywords", []) if skw.lower() in hay)
                if hits > best_hits:
                    best_hits = hits
                    best_slug = sp["slug"]
            if best_slug:
                page_to_subtree[p.page_id] = best_slug
            else:
                unassigned.append(p)

        # Graph label propagation for any unassigned card in the domain
        for p in unassigned:
            votes: dict[str, float] = {}
            for nbr_id, (w, _) in adj.get(p.page_id, {}).items():
                nbr_sub = page_to_subtree.get(nbr_id)
                if nbr_sub:
                    votes[nbr_sub] = votes.get(nbr_sub, 0.0) + w
            if votes:
                page_to_subtree[p.page_id] = max(votes.items(), key=lambda x: x[1])[0]
            else:
                page_to_subtree[p.page_id] = specs[0]["slug"]

    for p in pages:
        if p.page_id not in page_to_subtree:
            page_to_subtree[p.page_id] = "core_default"

    return page_to_subtree, page_to_weak_edges, page_to_queries, page_to_hotness


def build_corpus_topic_map(
    pages: list[WikiPageInfo],
    stop_document_fraction: float = 0.35,
    min_documents_for_pruning: int = 8,
) -> dict[str, list[str]]:
    """Sirchmunk CorpusTopicMap: Compact token-to-'cardId#nodeId' map from tree section titles with DF pruning."""
    token_re = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_\-]{2,24}|[\u4e00-\u9fff]{2,8}")
    doc_freq: dict[str, set[str]] = {}
    postings: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    for p in pages:
        if not p.doc_tree:
            continue
        for node in p.doc_tree.all_flat_nodes():
            title_str = node.title
            tokens: set[str] = set()
            for m in token_re.finditer(title_str):
                tok = m.group(0).lower()
                if tok.isdigit():
                    continue
                tokens.add(tok)
                if re.fullmatch(r"[\u4e00-\u9fff]{3,6}", tok):
                    for w in (2, 3):
                        for idx in range(len(tok) - w + 1):
                            tokens.add(tok[idx : idx + w])
            ref_str = f"{p.page_id}#{node.node_id}"
            for tok in tokens:
                doc_freq.setdefault(tok, set()).add(p.page_id)
                tok_seen = seen.setdefault(tok, set())
                if ref_str in tok_seen:
                    continue
                plist = postings.setdefault(tok, [])
                if len(plist) >= 12:
                    continue
                tok_seen.add(ref_str)
                plist.append(ref_str)

    total_docs = len(pages)
    if total_docs >= min_documents_for_pruning and stop_document_fraction > 0:
        cutoff = stop_document_fraction * total_docs
        for tok, doc_set in list(doc_freq.items()):
            if len(doc_set) >= cutoff or len(doc_set) <= 1 and len(tok) > 10:
                postings.pop(tok, None)

    return postings


def render_domain_subtrees_and_l1_quickstart(
    repo_root: Path,
    tier: str,
    dom_name: str,
    dom_dir: Path,
    dom_pages: list[WikiPageInfo],
    dom_scripts: list[str],
    page_to_subtree: dict[str, str],
    ace_cards: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate L2 `_subtrees/<slug>.md` files and compress L1 `quickstart.md` via WIKI-MANAGED block."""
    wiki_root = repo_root / "wiki"
    specs = _load_subtree_specs(repo_root, dom_name, dom_pages)
    if not specs:
        return []

    # Load preserved task rows from wiki/.quickstart-task-rows.json
    qs_rows_path = wiki_root / ".quickstart-task-rows.json"
    dom_rel = f"{tier}/{dom_name}"
    preserved_rows: list[dict[str, str]] = []
    if qs_rows_path.exists():
        try:
            preserved_rows = json.loads(qs_rows_path.read_text(encoding="utf-8")).get("domains", {}).get(dom_rel, [])
        except Exception:
            pass

    subtrees_dir = dom_dir / "_subtrees"
    subtrees_dir.mkdir(parents=True, exist_ok=True)
    valid_st_files = {f"{sp['slug']}.md" for sp in specs}
    for old_st in subtrees_dir.glob("*.md"):
        if old_st.name not in valid_st_files:
            old_st.unlink()

    subtree_summaries: list[dict[str, Any]] = []
    core_pages = [p for p in dom_pages if p.subdir == "00_core_aesthetics"]

    for sp in specs:
        slug = sp["slug"]
        st_title = sp["title"]
        st_when = sp["when"]
        kws = [k.lower() for k in sp["keywords"]]
        skws = [k.lower() for k in sp.get("script_keywords", [])]

        st_pages = [p for p in dom_pages if page_to_subtree.get(p.page_id) == slug]
        # Identify scoped 00_core_aesthetics cards relevant to this subtree
        scoped_core = [
            cp for cp in core_pages
            if page_to_subtree.get(cp.page_id) == slug
            or any(k in f"{cp.page_id} {cp.title} {cp.description}".lower() for k in kws)
        ]
        if not scoped_core and core_pages:
            scoped_core = core_pages[:2]

        st_scripts = [
            s for s in dom_scripts
            if any(sk in s.lower() for sk in skws)
            or any(
                Path(s).name == Path(psc["path"]).name
                for p in st_pages
                for psc in p.scripts
            )
        ]
        verifiers = [Path(s).stem for s in st_scripts if "check_" in Path(s).name or "verifier" in s]

        # Match preserved quickstart task rows belonging to this subtree
        st_page_ids = {p.page_id.lower() for p in st_pages}
        st_page_rels = {p.abs_path.relative_to(dom_dir).as_posix().lower() for p in st_pages}
        matched_rows: list[dict[str, str]] = []
        for r in preserved_rows:
            cards_cell = r.get("cards", "").lower()
            if (
                any(pid in cards_cell for pid in st_page_ids)
                or any(prel in cards_cell for prel in st_page_rels)
            ):
                matched_rows.append(r)

        st_lines = [
            "---",
            'okf_version: "0.2"',
            "type: orientation-guide",
            f"id: subtree-{dom_name.replace('_', '-')}-{slug.replace('_', '-')}",
            f'title: "{dom_name} / {st_title} — L2 子树路由索引 (Subtree Router)"',
            f'description: "{st_when}"',
            "---",
            "",
            f"# 🌿 L2 子树路由：`{dom_name}/{slug}` — {st_title}",
            "",
            f"- **触发屏障 (`Constraint / Applies when`)**：{st_when}",
            f"- **返回上级 L1 领域主干**：[../quickstart.md](../quickstart.md) · [../_domain_index.md](../_domain_index.md)",
            f"- **一键捆绑点读本子树核心卡**：`python3 .agents/skills/study/scripts/wiki_query.py read --subtree {dom_name}/{slug}`",
            "",
            "## 1. 本子树专属核心审美底线 (`Scoped 00_core_aesthetics` Contract Digest)",
            "",
        ]
        for cp in scoped_core:
            rel_cp = "../" + cp.abs_path.relative_to(dom_dir).as_posix()
            st_lines.append(f"- **[`{cp.page_id}`]({rel_cp})** (*{cp.title}*)")
            for pat in cp.patterns[:2]:
                st_lines.append(f"  - 🎯 `Pattern`: {pat[:120]}")
            for con in cp.constraints[:1]:
                st_lines.append(f"  - 🚧 `Constraint`: {con['description'][:120]}")
        st_lines.append("")

        if matched_rows:
            st_lines.extend([
                "## 2. 子任务 $\\leftrightarrow$ 卡片与脚本精准联动矩阵 (Task-to-Card Matrix)",
                "",
                "| 具体子任务场景 | 必读技法卡 / 跨层绑定卡片 (`wiki_query.py read`) | 强制调用生成/校验脚本 (`scripts/`) |",
                "| :--- | :--- | :--- |",
            ])
            for mr in matched_rows:
                t_str = mr["task"]
                if len(t_str) > 130:
                    t_str = t_str[:127].rstrip() + "…**" if t_str.startswith("**") else t_str[:127].rstrip() + "…"
                st_lines.append(f"| {t_str} | {mr['cards']} | {mr['scripts']} |")
            st_lines.append("")

        st_lines.append("## 3. 本子树技法规则卡 (`01_craft_playbooks` & `02_style_specifics`)")
        st_lines.append("")
        action_pages = [p for p in st_pages if p.subdir in ("01_craft_playbooks", "02_style_specifics")]
        if not action_pages:
            st_lines.append("- *(暂无行动卡)*")
        else:
            for ap in action_pages:
                rel_ap = "../" + ap.abs_path.relative_to(dom_dir).as_posix()
                sc_str = ", ".join(f"`{Path(s['path']).name}`" for s in ap.scripts[:2])
                sc_note = f" | ⚙️ {sc_str}" if sc_str else ""
                pat_note = f" — *{ap.patterns[0][:105]}*" if ap.patterns else ""
                st_lines.append(f"- **[`{ap.page_id}`]({rel_ap})** (*{ap.title}*){sc_note}{pat_note}")
        st_lines.append("")

        st_lines.append("## 4. 深层理论与大师案例树索引 (`03_deep_concepts` DocumentTree `#section` Anchors)")
        st_lines.append("")
        deep_pages = [p for p in st_pages if p.subdir == "03_deep_concepts"]
        if not deep_pages:
            st_lines.append("- *(暂无深层案例卡)*")
        else:
            for dp in deep_pages:
                rel_dp = "../" + dp.abs_path.relative_to(dom_dir).as_posix()
                st_lines.append(f"- **[`{dp.page_id}`]({rel_dp})** — *{dp.title}* (`{len(dp.clean_body):,} chars`, 点读: `wiki_query.py read {dp.page_id}#<section>`)")
                if dp.doc_tree:
                    for tn in dp.doc_tree.nodes[:5]:
                        st_lines.append(
                            f"  - `#{tn.slug}` (`L{tn.line_range[0]}–{tn.line_range[1]}`, 规则:{tn.quant_rule_count}) — {tn.summary[:85]}"
                        )
        st_lines.append("")

        write_text_if_changed(subtrees_dir / f"{slug}.md", "\n".join(st_lines))

        subtree_summaries.append({
            "domain": dom_name,
            "slug": slug,
            "title": st_title,
            "when": st_when,
            "relPath": f"{tier}/{dom_name}/_subtrees/{slug}.md",
            "cardCount": len(st_pages),
            "coreIds": [cp.page_id for cp in scoped_core],
            "cardIds": [p.page_id for p in st_pages],
            "verifiers": verifiers[:4],
        })

    # Update L1 `quickstart.md` to use the compact L1 -> L2 META Subtree Router Table
    qs_path = dom_dir / "quickstart.md"
    if qs_path.exists():
        qs_text = qs_path.read_text(encoding="utf-8")
        l1_block = [
            "## 2. L1 $\\rightarrow$ L2 子学科树形路由矩阵 (Sirchmunk `META` Subtree Router — 按需展开子树)",
            "",
            "> [!TIP]",
            "> **上下文防爆树形导航协议（Sirchmunk `PageIndex` + `LENS` 预算控制）**：",
            f"> 本领域共 **{len(dom_pages)}** 张卡片已按图社区聚类划分为 **{len(subtree_summaries)} 棵正交 L2 子树**。请只打开与当前任务匹配的 **1 棵子树路由表（`_subtrees/<slug>.md`）**，或运行 `python3 .agents/skills/study/scripts/wiki_query.py read --subtree {dom_name}/<slug>` 一键加载该子树的精简契约！",
            "",
            "| L2 子学科分支 (`META Subtree`) | 触发条件与职责边界 (`Constraint / Applies when`) | 作用域底线卡 (`Scoped 00_core`) | 卡片数 | 核心冻结校验器 (`cargo run --bin`) | 一键展开子树 / 激活命令 |",
            "| :--- | :--- | :--- | :---: | :--- | :--- |",
        ]
        for st_info in subtree_summaries:
            s_slug = st_info["slug"]
            c_ids = ", ".join(f"`{cid}`" for cid in st_info["coreIds"][:2]) or "—"
            v_ids = ", ".join(f"`{v}`" for v in st_info["verifiers"][:2]) or "—"
            l1_block.append(
                f"| **[`{s_slug}`](_subtrees/{s_slug}.md)**<br/>*{st_info['title']}* | {st_info['when']} | {c_ids} | **{st_info['cardCount']}** | {v_ids} | `wiki_query.py tree {dom_name}/{s_slug}` |"
            )
        l1_block.extend([
            "",
            "## 3. 全量目录与章节级检索",
            f"- **领域全量树目录**: [_domain_index.md](_domain_index.md) · **本领域专属防踩坑账本**: [_domain_gotchas.md](_domain_gotchas.md)",
            f"- **章节级激光检索 (`CorpusTopicMap`)**: `python3 .agents/skills/study/scripts/wiki_query.py search \"<关键词>\" --domain {dom_name}`",
        ])
        managed_str = "\n".join(l1_block)

        if "<!-- WIKI-MANAGED:START -->" in qs_text:
            new_qs = replace_managed_block(qs_text, managed_str)
        elif "## 2." in qs_text:
            prefix = qs_text.split("## 2.")[0].rstrip()
            new_qs = f"{prefix}\n\n<!-- WIKI-MANAGED:START -->\n{managed_str}\n<!-- WIKI-MANAGED:END -->\n"
        else:
            new_qs = replace_managed_block(qs_text, managed_str)

        if new_qs != qs_text:
            qs_path.write_text(new_qs, encoding="utf-8")

    return subtree_summaries

def extract_rule_headings_and_claims(repo_root: Path, page: WikiPageInfo) -> tuple[list[str], list[str]]:
    import re
    headings: list[str] = []
    for m in re.finditer(r"\*\*([^\*\n]{3,90})\*\*", page.clean_body):
        h = m.group(1).strip().rstrip("：:")
        if h and h not in headings and not h.startswith("跨层强绑定"):
            headings.append(h)
    claims_list: list[str] = []
    cp = page.abs_path.with_suffix(".claims.json")
    if cp.exists():
        try:
            cdata = json.loads(cp.read_text(encoding="utf-8"))
            for c in cdata.get("claims", []):
                st = str(c.get("statement", "")).strip()
                if st:
                    claims_list.append(st)
        except Exception:
            pass
    return headings[:18], claims_list

def build_domain_scene_bundles_md(
    dom_name: str,
    dom_dir: Path,
    dom_pages: list[WikiPageInfo],
    dom_scripts: list[str],
) -> list[str]:
    if len(dom_pages) < 2:
        return []
    lines = [
        "## 🎯 任务场景必读簇 (Task Scene Bundles — Zero-Miss Co-Retrieval)",
        "> **高召回检索协议 (`HarnessPro` 零漏检路由)**：执行具体任务时，请先匹配下方【任务场景簇】一次性打包读取正交卡簇并运行绑定的冻结校验器，或运行 `python3 .agents/skills/study/scripts/study_runner.py search '<关键词>' --domain " + dom_name + "` 进行全文+定理图谱检索。",
        "",
    ]
    matched_rules = [r for r in SCENE_BUNDLE_RULES if r[0] == dom_name]
    if matched_rules:
        for _, b_title, trig, c_kws, s_kws in matched_rules:
            b_cards = []
            for p in dom_pages:
                hay = f"{p.page_id} {p.title} {p.description} {' '.join(p.tags)}".lower()
                if p.subdir == "00_core_aesthetics" or any(k.lower() in hay for k in c_kws):
                    rel_to_dom = p.abs_path.relative_to(dom_dir).as_posix()
                    b_cards.append(f"[`{p.page_id}`]({rel_to_dom})")
            b_scripts = [f"`wiki/{s}`" for s in dom_scripts if any(sk.lower() in s.lower() for sk in s_kws)]
            if b_cards:
                lines.append(f"- **{b_title}**")
                lines.append(f"  - *触发条件*: {trig}")
                lines.append(f"  - *必读正交卡簇 ({len(b_cards)} 卡)*: " + " · ".join(b_cards[:8]))
                if b_scripts:
                    lines.append(f"  - *必跑冻结考卷 (`verify-suite`)*: " + " · ".join(b_scripts))
    else:
        # Generic automatic bundle: all 00_core + 01_craft_playbooks + verifiers
        core_and_play = [
            f"[`{p.page_id}`]({p.abs_path.relative_to(dom_dir).as_posix()})"
            for p in dom_pages
            if p.subdir in ("00_core_aesthetics", "01_craft_playbooks")
        ]
        ver_scripts = [f"`wiki/{s}`" for s in dom_scripts if "verifier" in s or "check_" in s]
        if core_and_play:
            lines.append(f"- **🎯 `{dom_name}` 标准全量正交工作簇 (Core + Playbooks Bundle)**")
            lines.append("  - *必读正交卡簇*: " + " · ".join(core_and_play[:8]))
            if ver_scripts:
                lines.append("  - *必跑冻结考卷*: " + " · ".join(ver_scripts))
    lines.append("")
    return lines


def build_deps_banner(page: WikiPageInfo, id_map: dict[str, WikiPageInfo], rel_map: dict[str, WikiPageInfo]) -> str:
    """Build the auto-injected cross-tier dependency & script banner for a card (compact Sirchmunk digest)."""
    if not page.depends_on and not page.scripts:
        return ""
    lines = [
        DEPS_BANNER_START,
        "> [!IMPORTANT]",
        "> **跨层强绑定与脚本契约 (Auto-Managed by `wiki_finalize.py`)**",
    ]
    if page.depends_on:
        lines.append("> - **必读底层学科/前置卡片 (`depends_on`)**：")
        for dep in page.depends_on[:5]:
            target = id_map.get(dep) or rel_map.get(dep)
            if target:
                short_desc = target.description if len(target.description) <= 115 else target.description[:112].rstrip() + "…"
                lines.append(
                    f">   - [`{target.page_id}` ({target.title})](file://{target.abs_path.as_posix()}) — *{short_desc}*"
                )
            else:
                lines.append(f">   - `{dep}` *(⚠️ 未解析)*")
        if len(page.depends_on) > 5:
            extra = ", ".join(f"`{d}`" for d in page.depends_on[5:])
            lines.append(f">   - *更多关联前置卡 ({len(page.depends_on) - 5})*: {extra}")
    if page.scripts:
        lines.append("> - **绑定可执行脚本 (`scripts`)**：")
        for sc in page.scripts:
            lines.append(f">   - `[{sc['role']}]` `{sc['path']}`")
    lines.append(DEPS_BANNER_END)
    return "\n".join(lines) + "\n\n"


def _match_gotcha_to_domains(g: dict[str, Any], domain_names: list[str]) -> list[str]:
    """Map a gotcha entry's scope string to one or more Wiki domain names (or ['_env_and_cli'])."""
    scope_l = str(g.get("scope", "")).lower()
    gid_l = str(g.get("id", "")).lower()
    hay = f"{scope_l} {gid_l}"
    matched: list[str] = []
    for d in domain_names:
        if d.lower() in hay or any(tok in hay for tok in d.lower().split("_") if len(tok) >= 3):
            matched.append(d)
    return matched or ["_env_and_cli"]


def finalize_wiki(
    repo_root: Path,
    actor: str = "wiki-finalizer/1.0",
    refresh_claims: bool = False,
) -> int:
    wiki_root = repo_root / "wiki"
    manifest_path = wiki_root / ".page-manifest.json"
    old_manifest: dict[str, Any] = {"schemaVersion": 1, "pages": {}}
    if manifest_path.exists():
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    pages = discover_wiki_pages(repo_root)
    id_map = {p.page_id: p for p in pages}
    rel_map = {p.rel_path: p for p in pages}
    for p in pages:
        for alias in p.aliases:
            rel_map[alias] = p

    mermaid_issues: list[str] = []
    stale_claim_issues: list[str] = []
    new_manifest_pages: dict[str, Any] = {}
    timestamp = now_iso()

    # Load ACE telemetry for utility badges & Sirchmunk KnowledgeEvolver graph
    ace_path = wiki_root / ".ace-telemetry.json"
    ace_data: dict[str, Any] = {"schemaVersion": 1, "cards": {}, "scripts": {}, "recentTraces": []}
    ace_cards: dict[str, dict[str, Any]] = {}
    ace_scripts: dict[str, dict[str, Any]] = {}
    if ace_path.exists():
        try:
            ace_data = json.loads(ace_path.read_text(encoding="utf-8"))
            ace_cards = ace_data.get("cards", {})
            ace_scripts = ace_data.get("scripts", {})
        except Exception:
            pass

    page_to_subtree, page_to_weak_edges, page_to_queries, page_to_hotness = compute_weak_semantic_edges_and_subtrees(
        pages, ace_data
    )

    # Sync enriched queries & hotness back into ace_cards
    for pid, qlist in page_to_queries.items():
        if pid in ace_cards:
            ace_cards[pid]["queries"] = qlist
            ace_cards[pid]["hotness"] = page_to_hotness.get(pid, 0.2)

    # 1. Process each page: Mermaid check, Claims reconciliation, Deps banner, OKF provenance
    for p in pages:
        mermaid_issues.extend(validate_mermaid_blocks(p.clean_body, p.rel_path))
        prev_entry = old_manifest.get("pages", {}).get(p.rel_path)
        projected_sources, stale_msgs, claims_clean = reconcile_page_claims(
            repo_root, p, prev_entry, actor, refresh_claims=refresh_claims
        )
        stale_claim_issues.extend(stale_msgs)

        # Determine generated & verified stamps (only advance when clean_body hash changed — preserves KV/File Cache!)
        prev_gen = p.frontmatter.get("generated")
        if prev_entry and prev_entry.get("bodyHash") == p.body_hash and isinstance(prev_gen, dict):
            gen_stamp = prev_gen
        else:
            gen_stamp = {"by": actor, "at": timestamp}

        updates: dict[str, Any] = {"generated": gen_stamp}
        if projected_sources:
            updates["sources"] = projected_sources
        if claims_clean:
            prev_ver = p.frontmatter.get("verified")
            if (
                prev_entry
                and prev_entry.get("bodyHash") == p.body_hash
                and prev_entry.get("claimsClean") == claims_clean
                and not refresh_claims
                and isinstance(prev_ver, list)
                and prev_ver
            ):
                updates["verified"] = prev_ver
            else:
                updates["verified"] = [{"by": actor, "at": timestamp}]

        raw_content = p.abs_path.read_text(encoding="utf-8")
        updated_fm_content = surgical_update_frontmatter(raw_content, updates)
        parsed_after = parse_markdown(updated_fm_content)
        clean_body = strip_deps_banner(parsed_after.body)
        banner = build_deps_banner(p, id_map, rel_map)
        final_body = "\n" + banner + clean_body.lstrip("\n")
        if not final_body.endswith("\n"):
            final_body += "\n"

        final_content = "---\n" + "\n".join(parsed_after.raw_frontmatter_lines) + "\n---" + final_body
        if final_content != raw_content:
            p.abs_path.write_text(final_content, encoding="utf-8")

        new_manifest_pages[p.rel_path] = {
            "id": p.page_id,
            "type": p.page_type,
            "tier": p.tier,
            "domain": p.domain,
            "subtree": page_to_subtree.get(p.page_id, "core_default"),
            "abstractionLevel": p.abstraction_level,
            "epistemicStatus": p.epistemic_status,
            "aliases": p.aliases,
            "dependsOn": p.depends_on,
            "bodyHash": p.body_hash,
            "claimsClean": claims_clean,
            "updatedAt": gen_stamp.get("at", timestamp),
        }

    # 2. Deterministically generate L2 `_subtrees/*.md`, L1 `quickstart.md`, and compact `_domain_index.md`
    domain_summaries: list[dict[str, Any]] = []
    all_subtree_summaries: list[dict[str, Any]] = []
    for tier in TIERS:
        tier_dir = wiki_root / tier
        if not tier_dir.is_dir():
            continue
        for dom_dir in sorted(d for d in tier_dir.iterdir() if d.is_dir() and not d.name.startswith(".")):
            dom_name = dom_dir.name
            dom_pages = [p for p in pages if p.tier == tier and p.domain == dom_name]
            scripts_dir = dom_dir / "scripts"
            dom_scripts = (
                sorted(
                    s.relative_to(wiki_root).as_posix()
                    for ext in ("*.rs", "*.py")
                    for s in scripts_dir.rglob(ext)
                    if not s.name.startswith("test_") and s.name != "__init__.py"
                )
                if scripts_dir.is_dir()
                else []
            )

            # Generate L2 _subtrees/<slug>.md and update L1 quickstart.md if domain has META subtrees
            dom_subtrees = render_domain_subtrees_and_l1_quickstart(
                repo_root, tier, dom_name, dom_dir, dom_pages, dom_scripts, page_to_subtree, ace_cards
            )
            all_subtree_summaries.extend(dom_subtrees)

            # Read quickstart description if available
            qs_path = dom_dir / "quickstart.md"
            qs_desc = f"{tier}/{dom_name} 领域知识与脚本索引"
            req_foundations: list[str] = []
            if qs_path.exists():
                qs_parsed = parse_markdown(qs_path.read_text(encoding="utf-8"))
                qs_desc = str(qs_parsed.frontmatter.get("description", qs_desc))
                rf = qs_parsed.frontmatter.get("requires_foundations", [])
                if isinstance(rf, list):
                    req_foundations = [str(x) for x in rf]

            idx_lines = [
                "---",
                'okf_version: "0.2"',
                f"title: {dom_name} Domain Index",
                f'description: "Auto-generated hierarchical catalog of subtrees, cards, and scripts in {tier}/{dom_name}."',
                "---",
                "",
                f"# `{tier}/{dom_name}` 领域树形索引 (Auto-Generated)",
                "",
                f"- **L1 核心路由入口**: [quickstart.md](quickstart.md) — {qs_desc}",
                "- **本领域专属防踩坑账本**: [_domain_gotchas.md](_domain_gotchas.md)",
            ]
            if (dom_dir / "INSTRUCTIONS.md").exists():
                idx_lines.append("- **人类最高宪章 (只读)**: [INSTRUCTIONS.md](INSTRUCTIONS.md)")
            if req_foundations:
                idx_lines.append(f"- **强制关联底层学科 (`requires_foundations`)**: `{', '.join(req_foundations)}`")
            idx_lines.append("")

            if dom_subtrees:
                idx_lines.extend([
                    "## 🌿 L2 子学科分支索引 (`META Subtrees` — 推荐优先按子树点读)",
                    "",
                    "| 子树 ID (`Subtree`) | 职责与触发边界 (`Constraint`) | 卡片数 | 入口链接 |",
                    "| :--- | :--- | :---: | :--- |",
                ])
                for st_s in dom_subtrees:
                    idx_lines.append(
                        f"| **`{st_s['slug']}`** | {st_s['title']} | **{st_s['cardCount']}** | [`_subtrees/{st_s['slug']}.md`](_subtrees/{st_s['slug']}.md) |"
                    )
                idx_lines.append("")
            else:
                idx_lines.extend(build_domain_scene_bundles_md(dom_name, dom_dir, dom_pages, dom_scripts))

            # Group pages by subdirectory (compact description <= 135 chars to prevent index bloat)
            for sub_label, sub_key in [
                ("00_core_aesthetics (必读核心审美与底线)", "00_core_aesthetics"),
                ("01_craft_playbooks (按需技法规则卡)", "01_craft_playbooks"),
                ("02_style_specifics (子流派专属规则)", "02_style_specifics"),
                ("03_deep_concepts (深层理论与案例拆解 — 支持 #section 树节点点读)", "03_deep_concepts"),
            ]:
                sub_pages = [p for p in dom_pages if p.subdir == sub_key]
                idx_lines.append(f"## {sub_label}")
                if not sub_pages:
                    idx_lines.append("- *(暂无词条)*")
                else:
                    for sp in sub_pages:
                        rel_to_dom = sp.abs_path.relative_to(dom_dir).as_posix()
                        badge = "🟢`canonical`" if sp.epistemic_status == "canonical" else (
                            "🟡`provisional`" if sp.epistemic_status == "provisional" else "🔵`empirical`"
                        )
                        st_tag = page_to_subtree.get(sp.page_id, "")
                        st_badge = f", 🌿`{st_tag}`" if st_tag and st_tag != "core_default" else ""
                        max_desc_len = 64 if dom_subtrees else 135
                        short_d = sp.description if len(sp.description) <= max_desc_len else sp.description[: max_desc_len - 3].rstrip() + "…"
                        deps_note = f" *(🔗{len(sp.depends_on)} deps)*" if sp.depends_on else ""
                        sec_cnt = len(sp.doc_tree.nodes) if sp.doc_tree else 1
                        sec_note = f" `[🌳{sec_cnt} sections]`" if sub_key == "03_deep_concepts" else ""
                        idx_lines.append(
                            f"- [{sp.title}]({rel_to_dom}) (`id: {sp.page_id}`, {badge}{st_badge}){sec_note} — {short_d}{deps_note}"
                        )
                idx_lines.append("")

            idx_lines.append("## 可执行脚本库 (`scripts/`)")
            if not dom_scripts:
                idx_lines.append("- *(暂无脚本)*")
            else:
                for sc_rel in dom_scripts:
                    idx_lines.append(f"- `wiki/{sc_rel}`")
            idx_lines.append("")

            write_text_if_changed(dom_dir / "_domain_index.md", "\n".join(idx_lines))

            canonical_cnt = sum(1 for p in dom_pages if p.epistemic_status == "canonical")
            provisional_cnt = sum(1 for p in dom_pages if p.epistemic_status == "provisional")
            domain_summaries.append(
                {
                    "tier": tier,
                    "domain": dom_name,
                    "description": qs_desc,
                    "requires_foundations": req_foundations,
                    "total_pages": len(dom_pages),
                    "canonical_count": canonical_cnt,
                    "provisional_count": provisional_cnt,
                    "script_count": len(dom_scripts),
                    "subtrees": [s["slug"] for s in dom_subtrees],
                }
            )

    # 3. Rebuild root `wiki/index.md`
    root_idx = [
        "---",
        'okf_version: "0.2"',
        "title: Three-Tier LLM Wiki Root Index",
        'description: "Root MoE & Sirchmunk Tree Router across Foundations, Tools Craft, and Composite Workflows."',
        "---",
        "",
        "# LLM Wiki 全域总索引 (Root Tree Router — `LLM-Wiki × Sirchmunk`)",
        "",
        "- **RSI 自进化记分板与 ACE 命中看板**: [SCOREBOARD.md](SCOREBOARD.md)",
        "- **排错防踩坑分级树账本 (Scoped Rules That Save Retries)**: [GOTCHAS_AND_RETRY_SAVERS.md](GOTCHAS_AND_RETRY_SAVERS.md)",
        "- **AWM 参数化工作流记忆库 (Workflow Memory)**: [AWM_WORKFLOWS.md](AWM_WORKFLOWS.md)",
        "- **知识缺口与主动求知榜单**: [CURRICULUM_GAPS.md](CURRICULUM_GAPS.md)",
        "- **全局演进审计日志**: [log.md](log.md)",
        "",
    ]
    tier_titles = {
        "foundations": "Tier 1: 底层基础学科层 (`wiki/foundations/`) — 跨工具权威美学与科学公理",
        "tools_craft": "Tier 2: 软件工具与媒介技艺层 (`wiki/tools_craft/`) — 软件实操与自动化脚本",
        "workflows": "Tier 3: 复合生产管线层 (`wiki/workflows/`) — 多工具跨学科协同流水线",
    }
    for tier in TIERS:
        root_idx.append(f"## {tier_titles[tier]}")
        root_idx.append("")
        root_idx.append("| 领域/模块 | L1/L2 树形入口路由 | 词条数 (`Canonical` / `Provisional`) | 脚本数 | 强制关联底层学科 (`requires_foundations`) | 职责边界说明 |")
        root_idx.append("| :--- | :--- | :---: | :---: | :--- | :--- |")
        for ds in [d for d in domain_summaries if d["tier"] == tier]:
            dom = ds["domain"]
            reqs = ", ".join(f"`{r}`" for r in ds["requires_foundations"]) if ds["requires_foundations"] else "—"
            st_cnt = len(ds.get("subtrees", []))
            st_str = f" · 🌿`{st_cnt} subtrees`" if st_cnt else ""
            root_idx.append(
                f"| **`{dom}`** | [quickstart]({tier}/{dom}/quickstart.md) · [index]({tier}/{dom}/_domain_index.md){st_str} "
                f"| {ds['total_pages']} (`{ds['canonical_count']}` / `{ds['provisional_count']}`) "
                f"| {ds['script_count']} | {reqs} | {ds['description']} |"
            )
        root_idx.append("")

    write_text_if_changed(wiki_root / "index.md", "\n".join(root_idx))

    # 4. Update `<!-- WIKI-MANAGED:START -->` in root `GEMINI.md`
    # IMPORTANT (Prefix-Cache Pinning Law): Never embed volatile card/script/ACE counters in GEMINI.md!
    # GEMINI.md sits at Token 0 of the System Prompt (<user_rules>); any byte change invalidates 100% of KV Prefix Cache.
    gemini_md_path = repo_root / "GEMINI.md"
    if gemini_md_path.exists():
        managed_lines = [
            "## 6. 当前知识库全域路由地图（Auto-Managed by `wiki_finalize.py` — `AgentStacks` Prefix-Cache Pinned L0 Router）",
            "",
            "| 层级 (Tier) | 模块名 | L1 快速路由入口 (`≤4.5KB`) | L2 子学科树 (`META Subtrees`) | 强制联动底层学科 (`requires_foundations`) |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for ds in domain_summaries:
            t, d = ds["tier"], ds["domain"]
            reqs = ", ".join(f"`{r}`" for r in ds["requires_foundations"]) if ds["requires_foundations"] else "*(自身即为底层学科)*"
            sts = ds.get("subtrees", [])
            st_cell = ", ".join(f"[`{s}`](wiki/{t}/{d}/_subtrees/{s}.md)" for s in sts) if sts else "*(单层直达)*"
            managed_lines.append(
                f"| `{t}` | **`{d}`** | [`wiki/{t}/{d}/quickstart.md`](wiki/{t}/{d}/quickstart.md) | {st_cell} | {reqs} |"
            )
        managed_lines.append("")
        managed_lines.append("- **排错防踩坑分级树账本 (`Scoped Rules That Save Retries`)**: [`wiki/GOTCHAS_AND_RETRY_SAVERS.md`](wiki/GOTCHAS_AND_RETRY_SAVERS.md) (`wiki_query.py gotchas --scope <domain>`)")
        managed_lines.append("- **AWM 参数化工作流记忆 (`Workflow Memory`)**: [`wiki/AWM_WORKFLOWS.md`](wiki/AWM_WORKFLOWS.md)")
        managed_lines.append("- **RSI 自进化记分板 (`Scoreboard & ACE Telemetry`)**: [`wiki/SCOREBOARD.md`](wiki/SCOREBOARD.md)")
        managed_lines.append("- **主动求知与教材征集榜**: [`wiki/CURRICULUM_GAPS.md`](wiki/CURRICULUM_GAPS.md)")
        managed_lines.append("- **🌳 Sirchmunk 树形结构与章节大纲导航 (`PageIndex Tree Navigator`)**: `python3 .agents/skills/study/scripts/wiki_query.py tree [<domain>/<subtree> | <card_id>]`")
        managed_lines.append("- **📖 LENS & HarnessPro 帕累托三档有界读取器 (`Lite <=6KB / Medium <=18KB / Heavy <=40KB`)**: `python3 .agents/skills/study/scripts/wiki_query.py read <card_id>[#<section>] [--mode {lite,medium,heavy}] [--subtree <domain>/<slug>]`")
        managed_lines.append("- **🔍 全文+定理+章节主题图高召回检索 (`CorpusTopicMap + RRF Search`)**: `python3 .agents/skills/study/scripts/study_runner.py search \"<关键词>\" [--domain <模块名>]`")
        managed_lines.append("- **🔬 HarnessPro × OPRO 轨迹诊断与帕累托前沿审计 (`Trace Diagnosis & Pareto Audit`)**: `python3 .agents/skills/study/scripts/study_runner.py trace-diagnose [--domain <模块名>]`")
        managed_lines.append("- **🛡️ HarnessPro 领域冻结联合考卷 (`Frozen Verifier Suite`)**: `python3 .agents/skills/study/scripts/study_runner.py verify-suite --domain <模块名> [--artifact <产物路径>]`")
        new_gemini = replace_managed_block(gemini_md_path.read_text(encoding="utf-8"), "\n".join(managed_lines))
        write_text_if_changed(gemini_md_path, new_gemini)

    # 4b. Auto-sync Cargo.toml [[bin]] targets for all wiki/**/*.rs scripts
    cargo_toml_path = repo_root / "Cargo.toml"
    rs_scripts = sorted(
        s for s in wiki_root.rglob("*.rs")
        if "scripts" in s.parts and not s.name.startswith("test_")
    )
    if cargo_toml_path.exists():
        cargo_text = cargo_toml_path.read_text(encoding="utf-8")
        start_marker = "# === WIKI-MANAGED-BINS:START ==="
        end_marker = "# === WIKI-MANAGED-BINS:END ==="
        if start_marker in cargo_text and end_marker in cargo_text:
            bin_blocks: list[str] = [start_marker]
            for rs_p in rs_scripts:
                rel_rs = rs_p.relative_to(repo_root).as_posix()
                bin_blocks.append("[[bin]]")
                bin_blocks.append(f'name = "{rs_p.stem}"')
                bin_blocks.append(f'path = "{rel_rs}"')
                bin_blocks.append("")
            if bin_blocks[-1] == "":
                bin_blocks.pop()
            bin_blocks.append(end_marker)
            pre = cargo_text.split(start_marker)[0]
            post = cargo_text.split(end_marker)[1]
            new_cargo = pre + "\n".join(bin_blocks) + post
            if new_cargo != cargo_text:
                cargo_toml_path.write_text(new_cargo, encoding="utf-8")

    # 5. Active Epistemic Curiosity / Curriculum Gap Analyzer
    update_curriculum_gaps(repo_root, pages, domain_summaries, stale_claim_issues)

    # 5b. Render Scoped Gotchas Tree, AWM Workflows, RSI Scoreboard, and Rolling Log Archive
    render_gotchas_and_workflows_and_scoreboard(
        repo_root, pages, domain_summaries, len(rs_scripts), len(stale_claim_issues), ace_cards, ace_scripts
    )

    # 5c. Build Sirchmunk DocumentTree Index (`wiki/.tree-index.json`) & Hybrid Search Index (`wiki/.search-index.json`)
    tree_index_entries: dict[str, Any] = {}
    for p in pages:
        if p.doc_tree:
            tree_index_entries[p.page_id] = p.doc_tree.to_dict()
    tree_index_path = wiki_root / ".tree-index.json"
    write_json_if_changed(
        tree_index_path,
        {
            "schemaVersion": 2,
            "updatedAt": timestamp,
            "totalTrees": len(tree_index_entries),
            "trees": tree_index_entries,
        },
    )

    topic_map = build_corpus_topic_map(pages)

    reverse_deps: dict[str, list[str]] = {p.page_id: [] for p in pages}
    for p in pages:
        for dep in p.depends_on:
            dep_clean = dep.split("#")[0].strip()
            if dep_clean in reverse_deps and p.page_id not in reverse_deps[dep_clean]:
                reverse_deps[dep_clean].append(p.page_id)

    search_entries: list[dict[str, Any]] = []
    for p in pages:
        r_headings, c_stmts = extract_rule_headings_and_claims(repo_root, p)
        c_stat = ace_cards.get(p.page_id, {})
        scripts_bound = []
        raw_scripts = p.frontmatter.get("scripts", [])
        if isinstance(raw_scripts, list):
            for s_item in raw_scripts:
                if isinstance(s_item, dict) and s_item.get("path"):
                    scripts_bound.append({"role": str(s_item.get("role", "tool")), "path": str(s_item["path"])})
        sections_compact = []
        if p.doc_tree:
            for tn in p.doc_tree.nodes[:12]:
                sections_compact.append({
                    "nodeId": tn.node_id,
                    "slug": tn.slug,
                    "title": tn.title,
                    "lineRange": list(tn.line_range),
                    "summary": tn.summary[:140],
                })
        search_entries.append({
            "id": p.page_id,
            "title": p.title,
            "tier": p.tier,
            "domain": p.domain,
            "subtree": page_to_subtree.get(p.page_id, "core_default"),
            "abstractionLevel": p.abstraction_level,
            "subdir": p.subdir,
            "relPath": p.rel_path,
            "type": p.page_type,
            "status": p.epistemic_status,
            "description": p.description,
            "tags": p.tags,
            "aliases": p.aliases,
            "patterns": p.patterns,
            "constraints": p.constraints,
            "sections": sections_compact,
            "ruleHeadings": r_headings,
            "claims": c_stmts,
            "dependsOn": p.depends_on,
            "dependedBy": reverse_deps.get(p.page_id, []),
            "weakEdges": page_to_weak_edges.get(p.page_id, []),
            "historicalQueries": page_to_queries.get(p.page_id, []),
            "hotness": page_to_hotness.get(p.page_id, 0.2),
            "scripts": scripts_bound,
            "aceHelpful": int(c_stat.get("helpful", 0)),
            "aceHarmful": int(c_stat.get("harmful", 0)),
        })
    search_index_path = wiki_root / ".search-index.json"
    write_json_if_changed(
        search_index_path,
        {
            "schemaVersion": 2,
            "updatedAt": timestamp,
            "totalCards": len(search_entries),
            "subtrees": all_subtree_summaries,
            "topicMap": topic_map,
            "cards": search_entries,
        },
    )

    # 6. Persist `.page-manifest.json`
    manifest_data = {
        "schemaVersion": 2,
        "lastFinalizedAt": timestamp,
        "lastFinalizedBy": actor,
        "pages": new_manifest_pages,
    }
    write_json_if_changed(
        manifest_path,
        manifest_data,
        volatile_keys=("updatedAt", "lastFinalizedAt", "lastFinalizedBy"),
    )

    if mermaid_issues:
        print("⚠️  Mermaid validation warnings:")
        for m in mermaid_issues:
            print(f"  - {m}")
    if stale_claim_issues:
        print("⚠️  Stale Grounded Claims detected (run /wiki-lint to reconcile):")
        for s in stale_claim_issues:
            print(f"  - {s}")

    print(
        f"✅ Finalized {len(pages)} wiki pages across {len(domain_summaries)} domains "
        f"({len(all_subtree_summaries)} L2 META Subtrees, {len(tree_index_entries)} DocumentTrees, "
        f"{len(topic_map)} DF-pruned CorpusTopicMap terms)."
    )
    return 0


def update_curriculum_gaps(
    repo_root: Path,
    pages: list[WikiPageInfo],
    domain_summaries: list[dict[str, Any]],
    stale_claim_issues: list[str],
) -> None:
    """Detect provisional foundation fragments, cross-tool hub bottlenecks, and wanted pages."""
    wiki_root = repo_root / "wiki"
    gaps_md_path = wiki_root / "CURRICULUM_GAPS.md"
    gaps_json_path = wiki_root / ".gaps.json"

    recorded_gaps: list[dict[str, Any]] = []
    if gaps_json_path.exists():
        try:
            recorded_gaps = json.loads(gaps_json_path.read_text(encoding="utf-8")).get("gaps", [])
        except Exception:
            pass

    auto_lines = [
        "### A. 底层基础学科权威度诊断 (Foundation Epistemic Health)",
        "",
        "| 底层学科 (`foundations/*`) | 权威体系卡 (`canonical`) | 二手碎片卡 (`provisional`) | 被上层工具依赖次数 | 诊断状态与进修建议 |",
        "| :--- | :---: | :---: | :---: | :--- |",
    ]

    for ds in [d for d in domain_summaries if d["tier"] == "foundations"]:
        dom = ds["domain"]
        c_cnt = ds["canonical_count"]
        p_cnt = ds["provisional_count"]
        # Count how many tools/workflows require this foundation
        dep_tools = [
            x["domain"]
            for x in domain_summaries
            if f"foundations/{dom}" in x["requires_foundations"]
        ]
        if c_cnt == 0 and p_cnt == 0:
            status = "⚪ **空白待建** — 尚无教材入库，若涉及相关创作请先运行 `/study`"
        elif p_cnt > 0 and c_cnt <= p_cnt:
            status = (
                f"🔴 **亟需权威教材补课** — 当前含有 {p_cnt} 张从工具教程剥离的二手碎片卡 (`provisional`)，"
                "缺乏系统公理支撑，请尽快投入权威专著运行 `/study` 升格！"
            )
        else:
            status = f"🟢 **体系健康** ({c_cnt} 张权威卡支撑)"

        auto_lines.append(
            f"| **`foundations/{dom}`** | {c_cnt} | {p_cnt} | {len(dep_tools)} ({', '.join(dep_tools) or '无'}) | {status} |"
        )

    auto_lines.append("")
    auto_lines.append("### B. 当前处于 `provisional`（二手碎片级）待权威教材转正的词条")
    prov_pages = [p for p in pages if p.epistemic_status == "provisional"]
    if not prov_pages:
        auto_lines.append("- *(当前无二手碎片词条)*")
    else:
        for pp in prov_pages:
            auto_lines.append(
                f"- **[`{pp.page_id}`]({pp.rel_path})** (`{pp.tier}/{pp.domain}`) — *{pp.title}*：{pp.description}"
            )

    if recorded_gaps:
        auto_lines.append("")
        auto_lines.append("### C. 主动求知提案与推荐权威教材清单 (Active Study Proposals)")
        for g in recorded_gaps:
            auto_lines.append(f"- **[{g.get('priority', 'HIGH')}] `{g.get('target_domain', '')}` — {g.get('topic', '')}**")
            auto_lines.append(f"  - **触发原因**: {g.get('reason', '')}")
            books = g.get("recommended_textbooks", [])
            if books:
                auto_lines.append(f"  - **推荐补充教材**: {'；'.join(books)}")

    if stale_claim_issues:
        auto_lines.append("")
        auto_lines.append("### D. 待复核的过期断言 (Stale Claims)")
        for sc in stale_claim_issues:
            auto_lines.append(f"- ⚠️ {sc}")

    if gaps_md_path.exists():
        content = gaps_md_path.read_text(encoding="utf-8")
        new_content = replace_managed_block(content, "\n".join(auto_lines))
        write_text_if_changed(gaps_md_path, new_content)


def render_gotchas_and_workflows_and_scoreboard(
    repo_root: Path,
    pages: list[WikiPageInfo],
    domain_summaries: list[dict[str, Any]],
    rs_script_count: int,
    stale_claim_count: int,
    ace_cards: dict[str, dict[str, Any]],
    ace_scripts: dict[str, dict[str, Any]],
) -> None:
    """Deterministically render Scoped GOTCHAS_AND_RETRY_SAVERS.md, per-domain _domain_gotchas.md, AWM_WORKFLOWS.md, SCOREBOARD.md, and rolling log.md."""
    wiki_root = repo_root / "wiki"

    # 1. Render Scoped GOTCHAS_AND_RETRY_SAVERS.md + per-domain _domain_gotchas.md from .gotchas.json
    gotchas_json_path = wiki_root / ".gotchas.json"
    gotchas: list[dict[str, Any]] = []
    if gotchas_json_path.exists():
        try:
            gotchas = json.loads(gotchas_json_path.read_text(encoding="utf-8")).get("entries", [])
        except Exception:
            pass

    domain_names = [ds["domain"] for ds in domain_summaries]
    dom_to_tier = {ds["domain"]: ds["tier"] for ds in domain_summaries}
    gotchas_by_dom: dict[str, list[dict[str, Any]]] = {d: [] for d in domain_names}
    gotchas_by_dom["_env_and_cli"] = []

    for g in gotchas:
        matched_doms = _match_gotcha_to_domains(g, domain_names)
        for md in matched_doms:
            gotchas_by_dom.setdefault(md, []).append(g)

    # Write full detailed tables into each domain's `_domain_gotchas.md`
    for ds in domain_summaries:
        t, d = ds["tier"], ds["domain"]
        d_gotchas = gotchas_by_dom.get(d, [])
        dg_lines = [
            "---",
            'okf_version: "0.2"',
            f"title: {d} 领域专属防踩坑账本 (Domain-Scoped Gotchas)",
            f'description: "Scoped retry-saving rules and troubleshooting recipes for {t}/{d}."',
            "---",
            "",
            f"# 🛡️ `{t}/{d}` 领域专属防踩坑账本 (`_domain_gotchas.md`)",
            "",
            "- **返回全局防踩坑树索引**: [../../GOTCHAS_AND_RETRY_SAVERS.md](../../GOTCHAS_AND_RETRY_SAVERS.md)",
            f"- **命令行按需过滤**: `python3 .agents/skills/study/scripts/wiki_query.py gotchas --scope {d}`",
            "",
            "| ID | 领域/工具 (`scope`) | 报错症状与死胡同 (`symptom`) | 根因诊断 (`root_cause`) | 一步到位标准解法 (`recipe`) | ACE (`+H/-M`) |",
            "| :--- | :--- | :--- | :--- | :--- | :---: |",
        ]
        if not d_gotchas:
            dg_lines.append("| — | *(本领域暂无专属踩坑记录)* | — | — | — | — |")
        else:
            for g in d_gotchas:
                gid = g.get("id", "g-00")
                scope = g.get("scope", d)
                sym = str(g.get("symptom", "")).replace("|", "\\|")
                rc = str(g.get("root_cause", "")).replace("|", "\\|")
                rec = str(g.get("recipe", "")).replace("|", "\\|")
                gh = int(g.get("helpful", 0))
                gm = int(g.get("harmful", 0))
                dg_lines.append(f"| **`{gid}`** | `{scope}` | {sym} | {rc} | {rec} | `+{gh}/-{gm}` |")
        dg_lines.append("")
        write_text_if_changed(wiki_root / t / d / "_domain_gotchas.md", "\n".join(dg_lines))

    # Write global CLI/Browser/OS gotchas into `wiki/_env_and_cli_gotchas.md`
    env_gotchas = gotchas_by_dom.get("_env_and_cli", [])
    env_lines = [
        "---",
        'okf_version: "0.2"',
        "title: 全局环境、浏览器与 CLI 防踩坑账本 (Environment & CLI Gotchas)",
        'description: "Browser, macOS, SSH, PDF/multimodal and CLI retry-saving recipes."',
        "---",
        "",
        "# 🛠️ 全局环境、Chrome DevTools 与 macOS CLI 防踩坑账本 (`_env_and_cli_gotchas.md`)",
        "",
        "| ID | 领域/工具 (`scope`) | 报错症状与死胡同 (`symptom`) | 根因诊断 (`root_cause`) | 一步到位标准解法 (`recipe`) | ACE (`+H/-M`) |",
        "| :--- | :--- | :--- | :--- | :--- | :---: |",
    ]
    for g in env_gotchas:
        gid = g.get("id", "g-00")
        scope = g.get("scope", "env")
        sym = str(g.get("symptom", "")).replace("|", "\\|")
        rc = str(g.get("root_cause", "")).replace("|", "\\|")
        rec = str(g.get("recipe", "")).replace("|", "\\|")
        gh = int(g.get("helpful", 0))
        gm = int(g.get("harmful", 0))
        env_lines.append(f"| **`{gid}`** | `{scope}` | {sym} | {rc} | {rec} | `+{gh}/-{gm}` |")
    env_lines.append("")
    write_text_if_changed(wiki_root / "_env_and_cli_gotchas.md", "\n".join(env_lines))

    # Render root `wiki/GOTCHAS_AND_RETRY_SAVERS.md` as a compact L1 Scoped Tree Router (~5KB instead of 113.5KB!)
    g_lines = [
        "---",
        'okf_version: "0.2"',
        "title: 排错防踩坑分级树账本 (Scoped Rules That Save Retries — PenguinHarness × Sirchmunk)",
        'description: "Hierarchical L1 router for 110+ retry-saving environment, compiler, DAW, and domain gotchas."',
        "---",
        "",
        "# 排错防踩坑分级树账本 (`Rules That Save Retries` — L1 Scoped Router)",
        "",
        "> [!IMPORTANT]",
        "> **零重试复发与作用域屏障铁律（`PenguinHarness` Continual-Learning × `Sirchmunk` Barrier Edges）**：",
        "> 1. 为防止全量 115+ 条跨领域踩坑记录撑爆上下文，本根账本采用 **L1 作用域树形索引（Scoped Tree Router）**，完整症状/根因/解法已按领域分发至各模块的 `_domain_gotchas.md`。",
        "> 2. **推荐按需精准检索**：运行 `python3 .agents/skills/study/scripts/wiki_query.py gotchas --scope <domain_or_tool>`（或 `--query <关键词>`）；同时 `wiki_query.py read <card>` 会自动附带当前领域的 Top 防踩坑提示！",
        "> 3. 凡在单次会话中经历了 $\\ge 2$ 次工具调用报错/重试才摸索出正确方案，收尾时必须运行 `python3 .agents/skills/study/scripts/study_runner.py record-gotcha` 记入本账本。",
        "",
        "## 1. L1 领域/工具防踩坑分册路由表 (Scoped Gotcha Sub-Ledgers)",
        "",
        "| 作用域 (`Scope / Domain`) | 条目数 | 分册详情入口 (`Full Recipes`) | 一键按作用域检索命令 |",
        "| :--- | :---: | :--- | :--- |",
        f"| **`_env_and_cli`** *(Chrome DevTools / macOS / PDF / SSH)* | **{len(env_gotchas)}** | [`_env_and_cli_gotchas.md`](_env_and_cli_gotchas.md) | `wiki_query.py gotchas --scope chrome` |",
    ]
    for ds in domain_summaries:
        t, d = ds["tier"], ds["domain"]
        cnt = len(gotchas_by_dom.get(d, []))
        if cnt > 0:
            g_lines.append(
                f"| **`{t}/{d}`** | **{cnt}** | [`{t}/{d}/_domain_gotchas.md`]({t}/{d}/_domain_gotchas.md) | `wiki_query.py gotchas --scope {d}` |"
            )

    # Top 12 highest-ACE / multi-hit gotchas one-line digest
    ranked_g = sorted(gotchas, key=lambda x: int(x.get("helpful", 0)), reverse=True)[:12]
    g_lines.extend([
        "",
        "## 2. 高频必知 Top 12 跨领域防踩坑速查摘要 (High-Hotness Digest)",
        "",
        "| ID | 作用域 (`scope`) | 核心一步到位标准解法摘要 (`1-Line Recipe Digest`) | ACE |",
        "| :--- | :--- | :--- | :---: |",
    ])
    for g in ranked_g:
        gid = g.get("id", "g-00")
        scope = g.get("scope", "global")
        rec = str(g.get("recipe", "")).replace("|", "\\|")
        if len(rec) > 135:
            rec = rec[:132].rstrip() + "…"
        gh = int(g.get("helpful", 0))
        gm = int(g.get("harmful", 0))
        g_lines.append(f"| **`{gid}`** | `{scope}` | {rec} | `+{gh}/-{gm}` |")
    g_lines.append("")
    write_text_if_changed(wiki_root / "GOTCHAS_AND_RETRY_SAVERS.md", "\n".join(g_lines))

    # 1b. Rolling compaction of `wiki/log.md` -> keep latest 35 entries, archive older to `wiki/.archive/log_history.md`
    log_path = wiki_root / "log.md"
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8")
        log_entries = [e.strip() for e in re.split(r"(?=^## \[)", log_text, flags=re.MULTILINE) if e.strip().startswith("## [")]
        if len(log_entries) > 35:
            older_entries = log_entries[:-35]
            recent_entries = log_entries[-35:]
            arch_dir = wiki_root / ".archive"
            arch_dir.mkdir(parents=True, exist_ok=True)
            arch_path = arch_dir / "log_history.md"
            existing_arch = arch_path.read_text(encoding="utf-8") if arch_path.exists() else "# Wiki Historical Operation Archive\n\n"
            for oe in older_entries:
                if oe not in existing_arch:
                    existing_arch = existing_arch.rstrip() + "\n\n" + oe + "\n"
            arch_path.write_text(existing_arch, encoding="utf-8")
            new_log = (
                "# Wiki Operation & Evolution Log (Recent 35 Operations)\n\n"
                "> Historical entries archived in [`.archive/log_history.md`](.archive/log_history.md)\n\n"
                + "\n".join(recent_entries)
                + "\n"
            )
            log_path.write_text(new_log, encoding="utf-8")

    # 2. Render AWM_WORKFLOWS.md from .awm-workflows.json
    awm_json_path = wiki_root / ".awm-workflows.json"
    workflows: list[dict[str, Any]] = []
    if awm_json_path.exists():
        try:
            workflows = json.loads(awm_json_path.read_text(encoding="utf-8")).get("workflows", [])
        except Exception:
            pass

    w_lines = [
        "---",
        'okf_version: "0.2"',
        "title: AWM 参数化工作流记忆库 (Agent Workflow Memory — Ported from PenguinHarness)",
        'description: "Reusable parameterized sub-routines (Observation -> Reasoning -> Action) induced from successful trajectories."',
        "---",
        "",
        "# AWM 参数化工作流记忆库 (`Agent Workflow Memory`)",
        "",
        "> [!TIP]",
        "> **参数化工作流复用铁律（Ported from `PenguinHarness` `awm-rsi-toolkit`）**：",
        "> 每个工作流均从真实成功轨迹中剥离具体实例值（抽离为 `{parameter}` 占位符），固化为 `Observation -> Reasoning -> Action` 标准序列。遇到同类任务时直接绑定参数执行，严禁重新试探。",
        "",
    ]
    if not workflows:
        w_lines.append("- *(暂无已归纳的参数化工作流)*")
    else:
        for wf in workflows:
            wid = wf.get("id", "wf-00")
            wtitle = wf.get("title", wid)
            applies = wf.get("applies_when", "")
            params = wf.get("parameters", {})
            steps = wf.get("steps", [])
            wh = int(wf.get("helpful", 0))
            w_lines.append(f"## `{wid}`: {wtitle} (🎯 `ACE:+{wh}`)")
            w_lines.append("")
            w_lines.append(f"- **适用场景 (`Applies when`)**：{applies}")
            if params:
                p_str = "；".join(f"`{{{k}}}` ({v})" for k, v in params.items())
                w_lines.append(f"- **绑定参数 (`Parameters`)**：{p_str}")
            w_lines.append("")
            for s_idx, st in enumerate(steps, 1):
                obs = st.get("observation", "")
                reas = st.get("reasoning", "")
                act = st.get("action", "")
                w_lines.append(f"{s_idx}. **Observation**: {obs} $\\rightarrow$ **Reasoning**: {reas} $\\rightarrow$ **Action**: {act}")
            w_lines.append("")
    write_text_if_changed(wiki_root / "AWM_WORKFLOWS.md", "\n".join(w_lines))

    # 3. Render SCOREBOARD.md from live stats + .scoreboard.json history
    sb_json_path = wiki_root / ".scoreboard.json"
    sb_history: list[dict[str, Any]] = []
    if sb_json_path.exists():
        try:
            sb_history = json.loads(sb_json_path.read_text(encoding="utf-8")).get("history", [])
        except Exception:
            pass

    total_pages = len(pages)
    canonical_pages = sum(1 for p in pages if p.epistemic_status == "canonical")
    provisional_pages = sum(1 for p in pages if p.epistemic_status == "provisional")
    canonical_pct = round((canonical_pages / total_pages * 100.0), 1) if total_pages else 0.0
    total_ace_helpful = sum(int(v.get("helpful", 0)) for v in ace_cards.values()) + sum(
        int(v.get("helpful", 0)) for v in ace_scripts.values()
    )

    s_lines = [
        "---",
        'okf_version: "0.2"',
        "title: RSI 自进化记分板与 ACE 命中看板 (Evolution Scoreboard)",
        'description: "Versioned quantitative scoreboard tracking Wiki canonical maturity, Cargo verifier coverage, and ACE rule effectiveness."',
        "---",
        "",
        "# RSI 自进化记分板与 ACE 实战命中看板 (`SCOREBOARD`)",
        "",
        "## 1. 当前实时基线指标 (Live Reference State)",
        "",
        "| 指标维度 | 当前数值 | 状态说明 |",
        "| :--- | :---: | :--- |",
        f"| **知识卡总数 (`Total Cards`)** | **{total_pages}** | 权威卡 `{canonical_pages}` 张 (`{canonical_pct}%`) / 临时卡 `{provisional_pages}` 张 |",
        f"| **Cargo Rust 脚本数 (`[[bin]]`)** | **{rs_script_count}** | 全量纳入 `cargo test --quiet` 确定性门禁 |",
        f"| **密码学证据链健康度 (`Claims Clean`)** | **{total_pages - stale_claim_count}/{total_pages}** | 过期断言数: `{stale_claim_count}` |",
        f"| **防踩坑配方数 (`Gotchas`) & AWM 工作流** | **{len(gotchas)} / {len(workflows)}** | 已沉淀的零重试经验与参数化子例程 |",
        f"| **ACE 累计实战有效命中 (`Total Helpful`)** | **`+{total_ace_helpful}`** | 区分『仅读取』与『实战真正应用且通过校验』 |",
        "",
        "## 2. ACE 规则卡与 Rust 脚本实战命中排行榜 (Top Applied Assets)",
        "",
        "| 类型 | 资产 ID / 脚本名 | `Helpful` (`+`) | `Harmful` (`-`) | 最近一次实战应用任务 (`last_task`) |",
        "| :---: | :--- | :---: | :---: | :--- |",
    ]
    ranked_assets: list[tuple[int, str, str, int, int, str]] = []
    for cid, cstat in ace_cards.items():
        h = int(cstat.get("helpful", 0))
        m = int(cstat.get("harmful", 0))
        ranked_assets.append((h - m, "Card", f"`{cid}`", h, m, str(cstat.get("last_task", "—"))))
    for sname, sstat in ace_scripts.items():
        h = int(sstat.get("helpful", 0))
        m = int(sstat.get("harmful", 0))
        ranked_assets.append((h - m, "Script", f"`{sname}`", h, m, str(sstat.get("last_task", "—"))))
    ranked_assets.sort(key=lambda x: (x[0], x[3]), reverse=True)
    if not ranked_assets:
        s_lines.append("| — | *(暂无实战命中记录)* | 0 | 0 | — |")
    else:
        for _, atype, aname, h, m, ltask in ranked_assets[:15]:
            s_lines.append(f"| `{atype}` | {aname} | **`+{h}`** | `-{m}` | {ltask} |")

    s_lines.extend(
        [
            "",
            "## 3. 版本演进历史轨迹 (`scoreboard.json` History)",
            "",
            "| 版本 (`vN`) | 时间 (`UTC/ISO`) | 动作 (`verb`) | 权威卡占比 (`Canonical`) | Rust 脚本数 | ACE 累计有效命中 | 演进摘要 (`summary`) |",
            "| :---: | :--- | :---: | :---: | :---: | :---: | :--- |",
        ]
    )
    if not sb_history:
        s_lines.append("| `v1` | — | `init` | — | — | — | 初始基线 |")
    else:
        for row in reversed(sb_history[-15:]):
            s_lines.append(
                f"| **`v{row.get('version', 1)}`** | `{str(row.get('time', ''))[:19]}` | `{row.get('verb', '')}` "
                f"| `{row.get('canonical_cards', 0)}/{row.get('total_cards', 0)}` (`{row.get('canonical_pct', 0)}%`) "
                f"| `{row.get('rust_scripts', 0)}` | `+{row.get('ace_helpful_total', 0)}` | {row.get('summary', '')} |"
            )
    s_lines.append("")
    write_text_if_changed(wiki_root / "SCOREBOARD.md", "\n".join(s_lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic Wiki Finalizer & Link Redirector.")
    parser.add_argument("--root", type=Path, default=None, help="Repository root")
    parser.add_argument("--actor", type=str, default="wiki-finalizer/1.0", help="Producer actor name")
    parser.add_argument(
        "--move",
        nargs=2,
        metavar=("OLD_REL_PATH", "NEW_REL_PATH"),
        help="Remediation Channel 1: Move a wiki page and automatically redirect all links & sidecars",
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help="Remediation Channel 2: Run surgical schema migration across all pages",
    )
    parser.add_argument(
        "--refresh-claims",
        action="store_true",
        help="Re-fingerprint and refresh stale evidence hashes after verifying claims",
    )
    args = parser.parse_args()

    repo_root = find_repo_root(args.root)
    if args.move:
        move_and_redirect_page(repo_root, args.move[0], args.move[1])
    return finalize_wiki(repo_root, actor=args.actor, refresh_claims=args.refresh_claims)


if __name__ == "__main__":
    sys.exit(main())

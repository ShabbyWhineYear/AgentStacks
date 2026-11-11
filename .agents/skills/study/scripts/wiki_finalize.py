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
import math
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
    MAX_SUBTREE_BYTES,
    TIERS,
    WikiPageInfo,
    discover_wiki_pages,
    find_repo_root,
    is_noise_cjk_token,
    legacy_raw_fragment_fingerprint,
    now_iso,
    parse_markdown,
    replace_managed_block,
    resolve_evidence_fingerprint,
    strip_deps_banner,
    surgical_update_frontmatter,
    wiki_rust_scripts,
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


CARGO_BINS_START = "# === WIKI-MANAGED-BINS:START ==="
CARGO_BINS_END = "# === WIKI-MANAGED-BINS:END ==="
_LEGACY_CARGO_MARKER = "# === AUTO-GENERATED BINARIES BY wiki_finalize.py (DO NOT EDIT BELOW) ==="


def sync_cargo_bins(cargo_toml_path: Path, repo_root: Path, rs_scripts: list[Path]) -> bool:
    """Register every wiki Rust script as a Cargo `[[bin]]` inside the managed marker block.

    Self-heals Cargo.toml files that only carry the legacy v2.0 marker (or none at all): previously the
    finalizer silently skipped them, so no verifier was ever compiled and `cargo test` passed vacuously.
    Duplicate stems are reported instead of producing an unbuildable manifest.
    """
    cargo_text = cargo_toml_path.read_text(encoding="utf-8")
    if CARGO_BINS_START not in cargo_text or CARGO_BINS_END not in cargo_text:
        base = cargo_text.replace(_LEGACY_CARGO_MARKER, "").rstrip()
        cargo_text = f"{base}\n\n{CARGO_BINS_START}\n{CARGO_BINS_END}\n"

    seen: dict[str, str] = {}
    bin_blocks: list[str] = [CARGO_BINS_START]
    for rs_p in rs_scripts:
        rel_rs = rs_p.relative_to(repo_root).as_posix()
        if rs_p.stem in seen:
            print(
                f"⚠️  Cargo bin name collision: '{rs_p.stem}' is used by both {seen[rs_p.stem]} and {rel_rs}; "
                "rename one script (bin names must be unique). Skipping the duplicate.",
                file=sys.stderr,
            )
            continue
        seen[rs_p.stem] = rel_rs
        bin_blocks.extend(["[[bin]]", f'name = "{rs_p.stem}"', f'path = "{rel_rs}"', ""])
    if bin_blocks[-1] == "":
        bin_blocks.pop()
    bin_blocks.append(CARGO_BINS_END)

    pre = cargo_text.split(CARGO_BINS_START)[0]
    post = cargo_text.split(CARGO_BINS_END, 1)[1]
    new_cargo = pre + "\n".join(bin_blocks) + post
    return write_text_if_changed(cargo_toml_path, new_cargo)


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

    # Rewrite references across all markdown files in wiki/ plus JSON sidecars that store paths
    # (`.claims/**` wiki:// evidence URIs and `.source-coverage.json` card bindings).
    rewrite_targets = list(wiki_root.rglob("*.md")) + list((wiki_root / ".claims").rglob("*.json"))
    cov_file = wiki_root / ".source-coverage.json"
    if cov_file.exists():
        rewrite_targets.append(cov_file)
    for f in rewrite_targets:
        text = f.read_text(encoding="utf-8")
        if old_rel in text:
            f.write_text(text.replace(old_rel, new_rel), encoding="utf-8")

    # The blanket rewrite above also rewrote the alias we just recorded in the moved page (old -> new),
    # which silently destroyed the redirect. Re-pin the alias list on the moved page.
    moved_raw = new_path.read_text(encoding="utf-8")
    moved_aliases = [a for a in aliases if a != new_rel]
    if parse_markdown(moved_raw).frontmatter.get("aliases") != moved_aliases:
        new_path.write_text(surgical_update_frontmatter(moved_raw, {"aliases": moved_aliases}), encoding="utf-8")

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
        claim_unverified = False
        for ev in evidences:
            res_uri = ev.get("resource", "")
            if res_uri and res_uri not in projected_sources:
                projected_sources.append(res_uri)
            exists, current_fp = resolve_evidence_fingerprint(repo_root, res_uri)
            recorded_fp = ev.get("fingerprint", "")
            if not exists:
                if res_uri.startswith(("script://", "wiki://")):
                    claim_stale = True
                    all_clean = False
                    stale_msgs.append(
                        f"[{page.rel_path}] Claim '{cid}' references missing resource '{res_uri}'"
                    )
                elif res_uri.startswith("raw://"):
                    # Neither the raw file nor its committed `.receipt.json` exists: the evidence cannot be
                    # verified (typo, never-ingested source, or a hallucinated citation). Never report it clean.
                    claim_unverified = True
            else:
                if recorded_fp and recorded_fp != current_fp and recorded_fp == legacy_raw_fragment_fingerprint(repo_root, res_uri):
                    recorded_fp = ""  # pre-v2.1 formula for the same bytes: migrate silently, not stale
                if not recorded_fp or refresh_claims:
                    ev["fingerprint"] = current_fp
                elif recorded_fp != current_fp:
                    claim_stale = True
                    all_clean = False
                    stale_msgs.append(
                        f"[{page.rel_path}] Claim '{cid}' is STALE: evidence '{res_uri}' changed "
                        f"({recorded_fp[:15]}... -> {current_fp[:15]}...)"
                    )
        claim["status"] = "stale" if claim_stale else ("unverified" if claim_unverified else "clean")

    sidecar_data["pageId"] = page.page_id
    sidecar_data["pageVersion"] = page.body_hash
    sidecar_data["updatedAt"] = now_iso()
    write_json_if_changed(sidecar_path, sidecar_data)
    return projected_sources, stale_msgs, all_clean



SCENE_BUNDLE_RULES: list[tuple[str, str, str, list[str], list[str]]] = []

# Sirchmunk (arXiv:2608.16185) KnowledgeEvolver L2 META Subtree Specifications
# Loaded dynamically from `wiki/.subtree-specs.json` (or auto-clustered when a domain has >=10 cards).
META_SUBTREE_SPECS: dict[str, list[dict[str, Any]]] = {}


_CLUSTER_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_\-]{2,24}|[\u4e00-\u9fff]{2,6}")
_INDEX_TERM_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_\-]{1,28}|[\u4e00-\u9fff]{2,8}")
_BILINGUAL_CJK_EN_RE = re.compile(
    r"([\u4e00-\u9fff][\u4e00-\u9fff0-9/\-·]{1,18})\s*[（(]([A-Za-z][A-Za-z0-9\s_\-/&]{2,38})[)）]"
)
_BILINGUAL_EN_CJK_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9\s_\-/&]{2,38})\s*[（(]([\u4e00-\u9fff][\u4e00-\u9fff0-9/\-·]{1,18})[)）]"
)

# Zero-cost Cross-Disciplinary & Colloquial-to-Technical Isomorphism Clusters
CROSS_DOMAIN_CONCEPT_SEEDS: tuple[tuple[str, ...], ...] = (
    (
        "开麦", "说话压低", "压低背景音乐", "人声避让", "语音闪避", "对白清晰",
        "ducking", "spectral-ducking", "频谱闪避", "频谱挖孔", "sidechain", "侧链压缩",
    ),
    (
        "打击感", "受击反馈", "刀刀到肉", "顿帧", "卡肉", "hitstop", "freeze-frame",
        "juiciness", "screen-shake", "震屏", "anticipation", "预备动作", "impact-frame", "黑白闪",
    ),
    (
        "地面预警", "红圈", "技能范围", "aoe预警", "攻击预警",
        "telegraph", "attack-telegraph", "sdf", "可读性", "战斗清晰度",
    ),
    (
        "压迫感", "恐怖氛围", "悬疑惊悚", "不安感", "窒息感",
        "chiaroscuro", "明暗对照", "dutch-angle", "倾斜镜头", "dissonance", "不协和", "sub-bass", "次低频",
    ),
    (
        "像素锯齿", "像素描边", "阶梯感", "像素平滑", "脏灰过渡",
        "manual-aa", "手工抗锯齿", "sel-out", "selective-outlining", "jaggies", "banding", "pillow-shading", "枕头阴影",
    ),
    (
        "赛璐珞", "二次元上色", "阴影发灰", "二分光影", "暗部死黑",
        "hue-shift", "色相偏移", "冷暖偏移", "terminator", "明暗交界线", "subsurface-scattering", "sss", "次表面散射",
    ),
    (
        "界面动效", "弹窗弹出", "按钮反馈", "丝滑过渡", "弹性动画",
        "easing", "overshoot", "回弹", "缓动曲线", "follow-through", "跟随动作", "视觉层级",
    ),
    (
        "掉帧卡顿", "发烫", "包体超限", "首屏加载", "内存暴涨",
        "drawcall", "batching", "合批", "frame-budget", "帧预算", "vfs", "data-locality", "数据局部性",
    ),
    (
        "倒字", "咬字", "洗脑副歌", "记忆点", "旋律上口",
        "prosody", "four-tones", "四声", "topline", "hook", "切分旋律", "motif", "动机发展",
    ),
    (
        "永久死亡", "permadeath", "纪念碑", "memorial", "战术小队", "squad",
        "叙事弧光", "arcs", "character-arc", "防崩盘", "anti-spiral",
    ),
    (
        "通货膨胀", "金币贬值", "金币回收", "回收漏斗", "经济膨胀",
        "mudflation", "sinks", "internal-economy",
    ),
    (
        "新手引导", "新手教程", "认知过载", "劝退", "教学脚手架",
        "onboarding", "cognitive-load", "affordances", "scaffolding", "usability",
    ),
    (
        "母带响度", "防爆音", "真峰值", "单声道低频",
        "lufs", "true-peak", "mastering", "mid-side", "compression",
    ),
    (
        "战斗喊话", "环境叙事", "对话树", "任务反转", "叙事和谐",
        "combat-barks", "environmental-storytelling", "dialogue-hubs", "ludonarrative", "systemic-vo",
    ),
)


def _auto_cluster_domain_subtrees(dom_name: str, dom_pages: list[WikiPageInfo]) -> list[dict[str, Any]]:
    """Deterministically partition a large domain (>=10 cards) into orthogonal IDF-weighted clusters."""
    n_docs = len(dom_pages)
    if n_docs < 10:
        return []
    k = max(2, min(20, math.ceil(n_docs / 22)))
    max_cluster_cap = math.ceil(n_docs / k) + 6

    page_vecs: list[dict[str, float]] = []
    df_counts: dict[str, int] = {}
    raw_term_bags: list[dict[str, int]] = []

    for p in dom_pages:
        bag: dict[str, int] = {}
        for t in p.tags:
            tl = t.lower().strip()
            if len(tl) >= 2:
                bag[tl] = bag.get(tl, 0) + 3
        for m in _CLUSTER_TOKEN_RE.finditer(f"{p.page_id} {p.title} {p.positive_description}"):
            tok = m.group(0).lower()
            if not tok.isdigit() and tok != dom_name.lower():
                bag[tok] = bag.get(tok, 0) + 1
        raw_term_bags.append(bag)
        for tok in bag:
            df_counts[tok] = df_counts.get(tok, 0) + 1

    for bag in raw_term_bags:
        vec: dict[str, float] = {}
        for tok, tf in bag.items():
            df = df_counts.get(tok, 1)
            if df > max(3, int(0.65 * n_docs)):
                continue
            idf = math.log((n_docs + 1.0) / (df + 0.5)) + 0.5
            vec[tok] = (1.0 + math.log(tf)) * idf
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        page_vecs.append({tok: v / norm for tok, v in vec.items()})

    def _dot(a: dict[str, float], b: dict[str, float]) -> float:
        if len(a) > len(b):
            a, b = b, a
        return sum(val * b.get(tok, 0.0) for tok, val in a.items())

    # Furthest-first deterministic seed selection for orthogonal cluster centers
    seed_indices: list[int] = [0]
    for _ in range(1, k):
        best_idx = -1
        best_min_sim = 2.0
        for idx in range(n_docs):
            if idx in seed_indices:
                continue
            max_sim_to_existing = max(_dot(page_vecs[idx], page_vecs[s_idx]) for s_idx in seed_indices)
            if max_sim_to_existing < best_min_sim:
                best_min_sim = max_sim_to_existing
                best_idx = idx
        if best_idx < 0:
            break
        seed_indices.append(best_idx)

    centroids: list[dict[str, float]] = [dict(page_vecs[idx]) for idx in seed_indices]
    clusters: list[list[int]] = [[] for _ in centroids]

    for idx in range(n_docs):
        ranked_c = sorted(
            range(len(centroids)),
            key=lambda c_i: (_dot(page_vecs[idx], centroids[c_i]) - 0.015 * len(clusters[c_i])),
            reverse=True,
        )
        chosen = ranked_c[0]
        for cand in ranked_c:
            if len(clusters[cand]) < max_cluster_cap:
                chosen = cand
                break
        clusters[chosen].append(idx)

    auto_specs: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    for c_idx, member_indices in enumerate(clusters, 1):
        if not member_indices:
            continue
        agg_scores: dict[str, float] = {}
        for m_i in member_indices:
            for tok, w in page_vecs[m_i].items():
                agg_scores[tok] = agg_scores.get(tok, 0.0) + w
        top_terms = [
            t for t, _ in sorted(agg_scores.items(), key=lambda x: (-x[1], x[0]))[:5]
        ] or [f"part_{c_idx}"]
        lead = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", top_terms[0]).strip("_")
        ascii_slug = re.sub(r"[^a-z0-9]+", "_", lead).strip("_")
        base_slug = f"cluster_{ascii_slug}" if ascii_slug else f"cluster_{c_idx:02d}"
        slug = base_slug if base_slug not in used_slugs else f"{base_slug}_{c_idx:02d}"
        used_slugs.add(slug)
        label_terms = " / ".join(top_terms[:3])
        auto_specs.append({
            "slug": slug,
            "title": f"{dom_name} — {label_terms}",
            "when": f"当处理 {dom_name} 中涉及 {label_terms} 的子任务时激活",
            "keywords": top_terms[:5],
            "script_keywords": [t for t in top_terms[:3] if re.fullmatch(r"[a-z0-9_\-]+", t)],
            "explicitCardIds": [dom_pages[m_i].page_id for m_i in member_indices],
        })
    return auto_specs


def _load_subtree_specs(repo_root: Path, dom_name: str, dom_pages: list[WikiPageInfo]) -> list[dict[str, Any]]:
    if not dom_pages:
        return []
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
    return _auto_cluster_domain_subtrees(dom_name, dom_pages)


def compute_weak_semantic_edges_and_subtrees(
    repo_root: Path,
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

    # 2. Build WeakSemanticEdges (depends_on + shared_script + ace_co_occur)
    adj: dict[str, dict[str, tuple[float, str]]] = {p.page_id: {} for p in pages}
    id_set = {p.page_id for p in pages}
    rel_to_id: dict[str, str] = {p.rel_path: p.page_id for p in pages}
    for p in pages:
        for alias in p.aliases:
            rel_to_id[alias] = p.page_id

    def _add_edge(u: str, v: str, w: float, src: str) -> None:
        if u == v or u not in adj or v not in adj:
            return
        prev_w, prev_src = adj[u].get(v, (0.0, src))
        new_w = min(1.0, round(prev_w + w, 3))
        best_src = src if w >= prev_w else prev_src
        adj[u][v] = (new_w, best_src)
        adj[v][u] = (new_w, best_src)

    # 2a. Direct depends_on edges (supports both page_id and wiki-relative path)
    for p in pages:
        for dep in p.depends_on:
            dep_raw = dep.split("#")[0].strip()
            dep_id = dep_raw if dep_raw in id_set else rel_to_id.get(dep_raw, "")
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

    # 4. Assign each card to an L2 META Subtree across ALL domains (manual specs + auto-clustered large domains)
    page_to_subtree: dict[str, str] = {}
    pages_by_domain: dict[str, list[WikiPageInfo]] = {}
    for p in pages:
        pages_by_domain.setdefault(p.domain, []).append(p)

    for dom_name, dom_pages in sorted(pages_by_domain.items()):
        specs = _load_subtree_specs(repo_root, dom_name, dom_pages)
        if not specs:
            continue
        explicit_lookup: dict[str, str] = {}
        for sp in specs:
            for eid in sp.get("explicitCardIds", []):
                explicit_lookup[str(eid)] = sp["slug"]

        unassigned: list[WikiPageInfo] = []
        for p in dom_pages:
            if p.page_id in explicit_lookup:
                page_to_subtree[p.page_id] = explicit_lookup[p.page_id]
                continue
            hay = f"{p.page_id} {p.title} {p.positive_description} {' '.join(p.tags)} {' '.join(s['path'] for s in p.scripts)}".lower()
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
    max_postings_per_term: int = 48,
) -> dict[str, list[str]]:
    """Sirchmunk CorpusTopicMap: Domain-stratified token-to-'cardId#nodeId' index with zero alphabetical starvation."""
    latin_re = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_\-]{2,28}")
    cjk_re = re.compile(r"[\u4e00-\u9fff]{2,36}")
    doc_freq: dict[str, set[str]] = {}
    domain_buckets: dict[str, dict[str, list[str]]] = {}
    seen: dict[str, set[str]] = {}

    def _extract_tokens(text: str) -> set[str]:
        toks: set[str] = set()
        for m in latin_re.finditer(text):
            tok = m.group(0).lower()
            if not tok.isdigit():
                toks.add(tok)
        for m in cjk_re.finditer(text):
            raw_run = m.group(0)
            for seg in re.split(r"[的了和与及把给在用让将或并而但对向从到被]", raw_run):
                if len(seg) < 2:
                    continue
                if len(seg) <= 8 and not is_noise_cjk_token(seg):
                    toks.add(seg)
                if len(seg) >= 3:
                    for w in (2, 3, 4):
                        if len(seg) < w:
                            continue
                        for idx in range(len(seg) - w + 1):
                            sub = seg[idx : idx + w]
                            if not is_noise_cjk_token(sub):
                                toks.add(sub)
        return toks

    def _record_ref(tok: str, dom: str, page_id: str, ref_str: str) -> None:
        doc_freq.setdefault(tok, set()).add(page_id)
        tok_seen = seen.setdefault(tok, set())
        if ref_str in tok_seen:
            return
        tok_seen.add(ref_str)
        domain_buckets.setdefault(tok, {}).setdefault(dom, []).append(ref_str)

    for p in pages:
        flat_nodes = p.doc_tree.all_flat_nodes() if p.doc_tree else []
        root_node_id = flat_nodes[0].node_id if flat_nodes else "N00"
        card_level_text = f"{p.title} {p.positive_description} {' '.join(p.tags)} {' '.join(p.aliases)}"
        for tok in _extract_tokens(card_level_text):
            _record_ref(tok, p.domain, p.page_id, f"{p.page_id}#{root_node_id}")

        for node in flat_nodes:
            ref_str = f"{p.page_id}#{node.node_id}"
            sec_text = f"{node.title} {node.summary[:120]}"
            for tok in _extract_tokens(sec_text):
                _record_ref(tok, p.domain, p.page_id, ref_str)

    total_docs = len(pages)
    cutoff = stop_document_fraction * total_docs if (total_docs >= min_documents_for_pruning and stop_document_fraction > 0) else float("inf")

    postings: dict[str, list[str]] = {}
    for tok, dom_map in domain_buckets.items():
        doc_set = doc_freq.get(tok, set())
        # Prune high-DF stopwords (>=35% corpus) and hapax >2-char CJK sliding windows (cannot bridge >=2 docs
        # and are already covered by 2-char bigrams + phrase BM25F, saving >85% index size at 5,000+ cards)
        is_cjk_long = bool(len(tok) >= 3 and "\u4e00" <= tok[0] <= "\u9fff")
        if len(doc_set) >= cutoff or (len(doc_set) <= 1 and (len(tok) > 12 or is_cjk_long)):
            continue
        # Stratified Domain Round-Robin: ensures giant domains never starve smaller domains
        ordered_domains = sorted(dom_map.keys())
        stratified: list[str] = []
        round_idx = 0
        while len(stratified) < max_postings_per_term:
            added_in_round = False
            for dom in ordered_domains:
                refs = dom_map[dom]
                if round_idx < len(refs):
                    stratified.append(refs[round_idx])
                    added_in_round = True
                    if len(stratified) >= max_postings_per_term:
                        break
            if not added_in_round:
                break
            round_idx += 1
        if stratified:
            postings[tok] = stratified

    return postings


def build_bilingual_and_cross_domain_concept_graph(
    pages: list[WikiPageInfo],
    ace_data: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Build a zero-API bilingual & cross-domain concept bridge graph for Recall-First expansion."""
    edges: dict[str, dict[str, tuple[float, str]]] = {}

    def _norm_concept(raw: str) -> str:
        s = re.sub(r"\s+", "-", raw.strip().lower())
        s = s.strip("-_·/()")
        return s

    def _link(a_raw: str, b_raw: str, weight: float, source: str) -> None:
        a = _norm_concept(a_raw)
        b = _norm_concept(b_raw)
        if not a or not b or a == b or len(a) < 2 or len(b) < 2 or len(a) > 36 or len(b) > 36:
            return
        prev_ab = edges.setdefault(a, {}).get(b, (0.0, source))
        if weight > prev_ab[0]:
            edges[a][b] = (round(weight, 3), source)
        prev_ba = edges.setdefault(b, {}).get(a, (0.0, source))
        if weight > prev_ba[0]:
            edges[b][a] = (round(weight, 3), source)

    # 1. Auto-mine bilingual parenthetical equivalents from card titles, section headings, and patterns
    for p in pages:
        texts = [p.title, *p.aliases, *p.patterns[:3]]
        if p.doc_tree:
            texts.extend(n.title for n in p.doc_tree.all_flat_nodes()[:12])
        for txt in texts:
            for m in _BILINGUAL_CJK_EN_RE.finditer(txt):
                cjk_term, en_term = m.group(1), m.group(2)
                _link(cjk_term, en_term, 0.85, "bilingual_heading")
                for sub_en in re.split(r"[/&,]+", en_term):
                    if len(sub_en.strip()) >= 3:
                        _link(cjk_term, sub_en, 0.80, "bilingual_heading")
            for m in _BILINGUAL_EN_CJK_RE.finditer(txt):
                en_term, cjk_term = m.group(1), m.group(2)
                _link(en_term, cjk_term, 0.85, "bilingual_heading")

    # 2. Inject curated cross-disciplinary & colloquial-to-technical isomorphism clusters
    for cluster in CROSS_DOMAIN_CONCEPT_SEEDS:
        for i in range(len(cluster)):
            for j in range(i + 1, len(cluster)):
                _link(cluster[i], cluster[j], 0.78, "cross_domain_seed")

    # 3. Mine cross-domain ACE trace co-occurrences (link distinctive lead tags across co-used domains)
    id_to_page = {p.page_id: p for p in pages}
    for tr in ace_data.get("recentTraces", []):
        h_cards = [id_to_page[c] for c in tr.get("helpful_cards", []) if c in id_to_page]
        for i in range(len(h_cards)):
            for j in range(i + 1, len(h_cards)):
                pa, pb = h_cards[i], h_cards[j]
                if pa.domain != pb.domain and pa.tags and pb.tags:
                    _link(pa.tags[0], pb.tags[0], 0.68, "ace_cross_domain")

    compact_graph: dict[str, list[dict[str, Any]]] = {}
    for term, nbrs in sorted(edges.items()):
        top_nbrs = sorted(nbrs.items(), key=lambda x: (-x[1][0], x[0]))[:8]
        compact_graph[term] = [
            {"term": n_term, "weight": w, "source": src}
            for n_term, (w, src) in top_nbrs
        ]
    return compact_graph


def extract_compact_body_term_freqs(clean_body: str, max_terms: int = 80) -> tuple[int, dict[str, int]]:
    """Precompute compact body token counts for millisecond BM25F scoring without reading .md files."""
    counts: dict[str, int] = {}
    total_tokens = 0
    for m in _INDEX_TERM_RE.finditer(clean_body):
        tok = m.group(0).lower()
        if tok.isdigit():
            continue
        total_tokens += 1
        counts[tok] = counts.get(tok, 0) + 1
        if re.fullmatch(r"[\u4e00-\u9fff]{3,8}", tok):
            for w in (2, 3):
                if len(tok) >= w:
                    for i in range(len(tok) - w + 1):
                        sub = tok[i : i + w]
                        counts[sub] = counts.get(sub, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], -len(x[0]), x[0]))[:max_terms]
    return max(1, total_tokens), dict(ranked)


def _render_single_subtree_md(
    dom_name: str,
    dom_dir: Path,
    slug: str,
    st_title: str,
    st_when: str,
    scoped_core: list[WikiPageInfo],
    matched_rows: list[dict[str, str]],
    action_pages: list[WikiPageInfo],
    deep_pages: list[WikiPageInfo],
    *,
    max_core_patterns: int,
    max_action_pat_chars: int,
    max_deep_sections: int,
    max_task_rows: int,
    max_action_items: int,
) -> str:
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
    for cp in scoped_core[:4]:
        rel_cp = "../" + cp.abs_path.relative_to(dom_dir).as_posix()
        st_lines.append(f"- **[`{cp.page_id}`]({rel_cp})** (*{cp.title}*)")
        for pat in cp.patterns[:max_core_patterns]:
            st_lines.append(f"  - 🎯 `Pattern`: {pat[:120]}")
        if max_core_patterns > 0:
            for con in cp.constraints[:1]:
                st_lines.append(f"  - 🚧 `Constraint`: {con['description'][:120]}")
    st_lines.append("")

    if matched_rows and max_task_rows > 0:
        st_lines.extend([
            "## 2. 子任务 $\\leftrightarrow$ 卡片与脚本精准联动矩阵 (Task-to-Card Matrix)",
            "",
            "| 具体子任务场景 | 必读技法卡 / 跨层绑定卡片 (`wiki_query.py read`) | 强制调用生成/校验脚本 (`scripts/`) |",
            "| :--- | :--- | :--- |",
        ])
        for mr in matched_rows[:max_task_rows]:
            t_str = mr["task"]
            if len(t_str) > 130:
                t_str = t_str[:127].rstrip() + "…**" if t_str.startswith("**") else t_str[:127].rstrip() + "…"
            st_lines.append(f"| {t_str} | {mr['cards']} | {mr['scripts']} |")
        st_lines.append("")

    st_lines.append("## 3. 本子树技法规则卡 (`01_craft_playbooks` & `02_style_specifics`)")
    st_lines.append("")
    if not action_pages:
        st_lines.append("- *(暂无行动卡)*")
    else:
        shown_actions = action_pages[:max_action_items]
        for ap in shown_actions:
            rel_ap = "../" + ap.abs_path.relative_to(dom_dir).as_posix()
            sc_str = ", ".join(f"`{Path(s['path']).name}`" for s in ap.scripts[:2])
            sc_note = f" | ⚙️ {sc_str}" if sc_str else ""
            pat_note = f" — *{ap.patterns[0][:max_action_pat_chars]}*" if (ap.patterns and max_action_pat_chars > 0) else ""
            st_lines.append(f"- **[`{ap.page_id}`]({rel_ap})** (*{ap.title}*){sc_note}{pat_note}")
        if len(action_pages) > len(shown_actions):
            rem = len(action_pages) - len(shown_actions)
            st_lines.append(
                f"- *(其余 {rem} 张同簇卡片已折叠防膨胀，请用 `wiki_query.py search \"<关键词>\" --domain {dom_name}` 精准召回)*"
            )
    st_lines.append("")

    st_lines.append("## 4. 深层理论与大师案例树索引 (`03_deep_concepts` DocumentTree `#section` Anchors)")
    st_lines.append("")
    if not deep_pages:
        st_lines.append("- *(暂无深层案例卡)*")
    else:
        for dp in deep_pages[:25]:
            rel_dp = "../" + dp.abs_path.relative_to(dom_dir).as_posix()
            st_lines.append(
                f"- **[`{dp.page_id}`]({rel_dp})** — *{dp.title}* (点读: `wiki_query.py read {dp.page_id}#<section>`)"
            )
            if dp.doc_tree and max_deep_sections > 0:
                ranked_nodes = sorted(
                    dp.doc_tree.nodes,
                    key=lambda n: (-n.quant_rule_count, n.line_range[0]),
                )[:max_deep_sections]
                ranked_nodes.sort(key=lambda n: n.line_range[0])
                for tn in ranked_nodes:
                    st_lines.append(
                        f"  - `#{tn.slug}` (`L{tn.line_range[0]}–{tn.line_range[1]}`, 规则:{tn.quant_rule_count}) — {tn.summary[:75]}"
                    )
    st_lines.append("")
    return "\n".join(st_lines)


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
    """Generate L2 `_subtrees/<slug>.md` files (hard-capped <= MAX_SUBTREE_BYTES) and compress L1 `quickstart.md`."""
    wiki_root = repo_root / "wiki"
    specs = _load_subtree_specs(repo_root, dom_name, dom_pages)
    if not specs:
        return []

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

    compaction_tiers = (
        (2, 105, 5, 18, 70),
        (1, 72, 3, 10, 50),
        (1, 36, 2, 5, 36),
        (0, 0, 1, 0, 26),
        (0, 0, 0, 0, 18),
    )

    for sp in specs:
        slug = sp["slug"]
        st_title = sp["title"]
        st_when = sp["when"]
        kws = [k.lower() for k in sp["keywords"]]
        skws = [k.lower() for k in sp.get("script_keywords", [])]

        st_pages = [p for p in dom_pages if page_to_subtree.get(p.page_id) == slug]
        scoped_core = [
            cp for cp in core_pages
            if page_to_subtree.get(cp.page_id) == slug
            or any(k in f"{cp.page_id} {cp.title} {cp.positive_description}".lower() for k in kws)
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

        st_page_ids = {p.page_id.lower() for p in st_pages}
        st_page_rels = {p.abs_path.relative_to(dom_dir).as_posix().lower() for p in st_pages}
        matched_rows = [
            r for r in preserved_rows
            if any(pid in r.get("cards", "").lower() for pid in st_page_ids)
            or any(prel in r.get("cards", "").lower() for prel in st_page_rels)
        ]

        action_pages = [p for p in st_pages if p.subdir in ("01_craft_playbooks", "02_style_specifics")]
        if not action_pages:
            action_pages = [p for p in st_pages if p.subdir not in ("00_core_aesthetics", "03_deep_concepts")]
        deep_pages = [p for p in st_pages if p.subdir == "03_deep_concepts"]

        rendered_st = ""
        for c_pat, a_chars, d_secs, t_rows, a_items in compaction_tiers:
            rendered_st = _render_single_subtree_md(
                dom_name,
                dom_dir,
                slug,
                st_title,
                st_when,
                scoped_core,
                matched_rows,
                action_pages,
                deep_pages,
                max_core_patterns=c_pat,
                max_action_pat_chars=a_chars,
                max_deep_sections=d_secs,
                max_task_rows=t_rows,
                max_action_items=a_items,
            )
            if len(rendered_st.encode("utf-8")) <= MAX_SUBTREE_BYTES - 128:
                break

        write_text_if_changed(subtrees_dir / f"{slug}.md", rendered_st)

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
            f"- **四路高召回检索 (`BM25F + ConceptGraph + 2-Hop`)**: `python3 .agents/skills/study/scripts/wiki_query.py search \"<关键词>\" --domain {dom_name}`",
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
    headings: list[str] = []
    for m in re.finditer(r"\*\*([^\*\n]{3,90})\*\*", page.clean_body):
        h = m.group(1).strip().rstrip("：:")
        if h and h not in headings and not h.startswith("跨层强绑定"):
            headings.append(h)
    claims_list: list[str] = []
    sidecar_candidates = (
        repo_root / "wiki" / ".claims" / Path(page.rel_path).with_suffix(".json"),
        page.abs_path.with_suffix(".claims.json"),
    )
    for cp in sidecar_candidates:
        if cp.exists():
            try:
                cdata = json.loads(cp.read_text(encoding="utf-8"))
                for c in cdata.get("claims", []):
                    st = str(c.get("statement") or c.get("text") or "").strip()
                    if st and st not in claims_list:
                        claims_list.append(st)
            except Exception:
                pass
    return headings[:18], claims_list[:24]

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
        if d.lower() in hay:
            matched.append(d)
    # Heuristic aliases
    if not matched:
        if any(k in hay for k in ("reaper", "exs", "sv2", "svr3", "Logic", "logic", "drum", "sampler")):
            matched.append("daw_music_prod")
        if any(k in hay for k in ("mandopop", "harmony", "voice_leading", "chord", "topline", "bgm")):
            matched.append("music_theory_acoustics")
        if any(k in hay for k in ("turnaround", "limb", "ankle", "foot", "head", "jaw", "bang", "dermal", "manga")):
            matched.append("visual_aesthetics")
        if any(k in hay for k in ("gpv3", "blender")):
            matched.append("blender_3d_layout")
        if any(k in hay for k in ("wx", "wechat", "minigame", "canvas")):
            matched.append("wechat_minigame_dev")
        if any(k in hay for k in ("boss", "ttk", "ludology", "combat")):
            matched.append("ludology_systems")
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
        repo_root, pages, ace_data
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

    # 4. Update `<!-- WIKI-MANAGED:START -->` across all Multi-Harness L0 Entrypoints:
    # - `GEMINI.md` (Gemini CLI / Google Antigravity)
    # - `CLAUDE.md` (Anthropic Claude Code)
    # - `AGENTS.md` (OpenAI Codex CLI / Cursor / Windsurf / Universal Agent Standard)
    # - `.cursor/rules/agentstacks.mdc` (Cursor IDE Native Rules)
    # IMPORTANT (Prefix-Cache Pinning + O(1) Scale Law):
    #   1) Never embed volatile card/script/ACE counters or timestamps in L0 entrypoints!
    #   2) Keep L0 `WIKI-MANAGED` strictly <= 2.5 KB (hard-capped <= 3.5 KB) via Tier-grouped module catalog,
    #      delegating the full Domain × L2 Subtree matrix to `wiki/index.md` & dynamic CLI tree/search routers.
    tier_labels = {
        "foundations": "Tier 1 · 底层基础学科 (`wiki/foundations/`)",
        "tools_craft": "Tier 2 · 软件工具技艺 (`wiki/tools_craft/`)",
        "workflows": "Tier 3 · 复合生产管线 (`wiki/workflows/`)",
    }
    managed_lines = [
        "## 6. 当前知识库全域路由地图（Auto-Managed by `wiki_finalize.py` — `AgentStacks` O(1) Prefix-Cache Pinned L0 Router）",
        "",
        "| 层级分馆 (`Tier`) | 已注册学科/工具模块 (`quickstart.md` 直达；带 `🌿` 模块含 L2 正交子树) |",
        "| :--- | :--- |",
    ]
    for tier_key in TIERS:
        t_doms = [d for d in domain_summaries if d["tier"] == tier_key]
        if not t_doms:
            cell = "*(暂无模块)*"
        else:
            shown = t_doms[:24]
            items = [
                f"`{d['domain']}`{'🌿' if d.get('subtrees') else ''}"
                for d in shown
            ]
            if len(t_doms) > len(shown):
                items.append(f"*(+{len(t_doms) - len(shown)} more in `wiki/index.md`)*")
            cell = " · ".join(items)
        managed_lines.append(f"| {tier_labels[tier_key]} | {cell} |")
    managed_lines.extend([
        "",
        "- **🗺️ 全馆完整路由大表 (`Domain × L2 Subtrees × requires_foundations`)**: [`wiki/index.md`](wiki/index.md) · **防踩坑账本**: [`wiki/GOTCHAS_AND_RETRY_SAVERS.md`](wiki/GOTCHAS_AND_RETRY_SAVERS.md) · **工作流记忆**: [`wiki/AWM_WORKFLOWS.md`](wiki/AWM_WORKFLOWS.md) · **RSI 记分板**: [`wiki/SCOREBOARD.md`](wiki/SCOREBOARD.md)",
        "- **🔍 统一四路高召回跨学科检索引擎 (`BM25F + ConceptGraph + GraphWalk + 2-Hop + NegativeGate`)**: `python3 .agents/skills/study/scripts/wiki_query.py search \"<任务关键词>\" [--domain <模块名>] [--limit 10] [--explain]`",
        "- **🌳 五级分形树与章节大纲导航 (`L0 -> L1 -> L2 Subtree -> PageIndex`)**: `python3 .agents/skills/study/scripts/wiki_query.py tree [<domain>[/<subtree>] | <card_id>]`",
        "- **📖 LENS 全链路硬字节预算读取器 (`lite <=6KB / medium <=20KB / heavy <=40KB`)**: `python3 .agents/skills/study/scripts/wiki_query.py read <card_id>[#<section>] [--mode {lite,medium,heavy}] [--subtree <domain>/<slug>]`",
        "- **🛡️ 领域联合考卷 / 黄金召回评测 / 覆盖率审计**: `python3 .agents/skills/study/scripts/study_runner.py {verify-suite --domain <模块名> | retrieval-bench | trace-diagnose | coverage-audit}`",
    ])
    managed_block_str = "\n".join(managed_lines)

    for l0_rel in ("GEMINI.md", "CLAUDE.md", "AGENTS.md", ".cursor/rules/agentstacks.mdc"):
        l0_path = repo_root / l0_rel
        if l0_path.exists():
            new_l0 = replace_managed_block(l0_path.read_text(encoding="utf-8"), managed_block_str)
            write_text_if_changed(l0_path, new_l0)

    # 4b. Auto-sync Cargo.toml [[bin]] targets for all wiki/**/*.rs scripts
    cargo_toml_path = repo_root / "Cargo.toml"
    rs_scripts = wiki_rust_scripts(wiki_root)
    if cargo_toml_path.exists():
        sync_cargo_bins(cargo_toml_path, repo_root, rs_scripts)

    # 5. Active Epistemic Curiosity / Curriculum Gap Analyzer
    update_curriculum_gaps(repo_root, pages, domain_summaries, stale_claim_issues)

    # 5b. Render Scoped Gotchas Tree, AWM Workflows, RSI Scoreboard, and Rolling Log Archive
    render_gotchas_and_workflows_and_scoreboard(
        repo_root, pages, domain_summaries, len(rs_scripts), len(stale_claim_issues), ace_cards, ace_scripts
    )

    # 5c. Build Sirchmunk DocumentTree Index (`wiki/.tree-index.json`) & v3 Hybrid Search Index (`wiki/.search-index.json`)
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
    concept_graph = build_bilingual_and_cross_domain_concept_graph(pages, ace_data)

    reverse_deps: dict[str, list[str]] = {p.page_id: [] for p in pages}
    normalized_deps_map: dict[str, list[str]] = {p.page_id: [] for p in pages}
    for p in pages:
        for dep in p.depends_on:
            dep_clean = dep.split("#")[0].strip()
            target_p = id_map.get(dep_clean) or rel_map.get(dep_clean)
            resolved_id = target_p.page_id if target_p else dep_clean
            if resolved_id not in normalized_deps_map[p.page_id]:
                normalized_deps_map[p.page_id].append(resolved_id)
            if target_p and p.page_id not in reverse_deps[target_p.page_id]:
                reverse_deps[target_p.page_id].append(p.page_id)

    search_entries: list[dict[str, Any]] = []
    total_body_len = 0
    for p in pages:
        r_headings, c_stmts = extract_rule_headings_and_claims(repo_root, p)
        body_len, body_terms = extract_compact_body_term_freqs(p.clean_body)
        total_body_len += body_len
        c_stat = ace_cards.get(p.page_id, {})
        scripts_bound = [
            {"role": str(s_item.get("role", "tool")), "path": str(s_item["path"])}
            for s_item in p.scripts
            if s_item.get("path")
        ]
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
            "bodyHash": p.body_hash,
            "type": p.page_type,
            "status": p.epistemic_status,
            "description": p.description,
            "positiveDescription": p.positive_description,
            "negativeScope": p.negative_scope,
            "tags": p.tags,
            "aliases": p.aliases,
            "patterns": p.patterns,
            "constraints": p.constraints,
            "sections": sections_compact,
            "ruleHeadings": r_headings,
            "claims": c_stmts,
            "bodyLen": body_len,
            "bodyTerms": body_terms,
            "dependsOn": normalized_deps_map.get(p.page_id, p.depends_on),
            "dependedBy": reverse_deps.get(p.page_id, []),
            "weakEdges": page_to_weak_edges.get(p.page_id, []),
            "historicalQueries": page_to_queries.get(p.page_id, []),
            "hotness": page_to_hotness.get(p.page_id, 0.2),
            "scripts": scripts_bound,
            "aceHelpful": int(c_stat.get("helpful", 0)),
            "aceHarmful": int(c_stat.get("harmful", 0)),
        })
    avg_body_len = round(total_body_len / max(1, len(search_entries)), 2)
    search_index_path = wiki_root / ".search-index.json"
    write_json_if_changed(
        search_index_path,
        {
            "schemaVersion": 3,
            "updatedAt": timestamp,
            "totalCards": len(search_entries),
            "corpusStats": {
                "avgBodyLen": avg_body_len,
            },
            "subtrees": all_subtree_summaries,
            "topicMap": topic_map,
            "conceptGraph": concept_graph,
            "cards": search_entries,
        },
        compact=True,
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

    # Refresh incremental `.git/agentstacks-parse-cache.json` after any frontmatter/banner updates
    discover_wiki_pages(repo_root, use_cache=True)

    if mermaid_issues:
        print("⚠️  Mermaid validation warnings:")
        for m in mermaid_issues:
            print(f"  - {m}")
    if stale_claim_issues:
        print("⚠️  Stale Grounded Claims detected (run /organize to reconcile):")
        for s in stale_claim_issues:
            print(f"  - {s}")

    print(
        f"✅ Finalized {len(pages)} wiki pages across {len(domain_summaries)} domains "
        f"({len(all_subtree_summaries)} L2 META Subtrees, {len(tree_index_entries)} DocumentTrees, "
        f"{len(topic_map)} Stratified TopicMap terms, {len(concept_graph)} ConceptGraph bridges)."
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

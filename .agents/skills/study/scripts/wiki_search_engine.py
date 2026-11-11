#!/usr/bin/env python3
"""AgentStacks v3.0 Unified Recall-First + Faceted-Precision Hybrid Search Engine.

Unifies `wiki_query.py search` and `study_runner.py search` onto a zero-API, millisecond-latency
two-stage retrieval pipeline designed for 5,000+ cards across 50+ domains:

Stage 1 — Recall-First Candidate Generation (Top-60 Pool):
  • Channel 1 (BM25F): 6-field Robertson-Sparck Jones BM25F (`id/tags/aliases`, `title`,
    `positiveDescription`, `patterns/ruleHeadings`, `claims/sections`, `clean_body`) with exact
    corpus IDF, field length normalization, exact phrase boost, and CJK root fallback.
  • Channel 2 (ConceptGraph): Bilingual parenthetical pairs + cross-disciplinary/colloquial
    isomorphism expansion (`说话压低 <-> spectral-ducking`, `打击感 <-> hitstop/anticipation`, etc.).
  • Channel 3 (1-Hop Graph Walk): Bidirectional traversal of `dependsOn` (pulls Foundation axioms),
    `dependedBy` (pulls Tool/Workflow playbooks), and `weakEdges` (`ACE co_occur` / `shared_script`).
  • Channel 4 (Cross-Domain SAG2 2-Hop Hyperedges): Domain-diversified seeds + adaptive target cap +
    1.6x cross-domain hyperedge bonus to break large-domain echo chambers.

Stage 2 — Precision & Faceted Diversity Reranker (Top-K):
  • Weighted Reciprocal Rank Fusion (RRF) + normalized channel score fusion.
  • Negative Scope Hard Gate (`不适用于...` / `Not for ...` -> 0.18x penalty when excluded).
  • Relevance-Gated Multiplicative ACE & `historicalQueries` boost (prevents Matthew-effect suppression
    of newly studied `ACE=0` cards).
  • Faceted Cross-Domain MMR slot allocation (guarantees multi-domain representation on composite tasks).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from wiki_core import (
    WikiPageInfo,
    discover_wiki_pages,
    is_noise_cjk_token,
    split_positive_and_negative_description,
)
from wiki_finalize import (
    CROSS_DOMAIN_CONCEPT_SEEDS,
    build_bilingual_and_cross_domain_concept_graph,
    build_corpus_topic_map,
    extract_compact_body_term_freqs,
    extract_rule_headings_and_claims,
)

_EN_WORD_RE = re.compile(r"[a-z0-9_+-]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_BRIDGE_STOPWORDS = frozenset({
    "rules", "rule", "overview", "summary", "patterns", "constraints", "core",
    "aesthetics", "playbook", "provisional", "canonical", "verified", "section",
    "design", "designing", "system", "systems", "cases", "case", "study",
    "guide", "manual", "principles", "theory", "method", "methods",
    "analysis", "models", "model", "engine", "engines", "framework",
    "pipeline", "workflow", "architecture", "introduction", "chapter",
    "part", "level", "phase", "step", "steps", "data", "code", "script",
    "概述", "规则", "总结", "核心", "基础", "进阶", "案例", "清单",
    "设计", "系统", "原理", "方法", "指南", "手册", "架构", "工作流",
    "流程", "分析", "模型", "引擎", "章节", "实战", "铁律", "标准",
    "模式", "规范", "法则", "点读", "诊断", "残差", "残差诊断", "残差诊断时点读",
    "时使用", "时激活", "实战手册", "全书拆解", "全书精读", "核心铁律",
})

_CORPUS_MEM_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any], dict[str, dict[str, Any]], dict[str, int], dict[str, float]]] = {}


def _stem_en(tok: str) -> str:
    if len(tok) >= 5 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
        return tok[:-1]
    return tok


def tokenize_query(text: str) -> list[str]:
    """Tokenize English terms (plus hyphen/underscore sub-parts) and non-noise CJK bigrams."""
    text_low = text.lower().strip()
    if not text_low:
        return []

    en_tokens: list[str] = []
    for raw_en in _EN_WORD_RE.findall(text_low):
        if raw_en not in en_tokens:
            en_tokens.append(raw_en)
        stemmed = _stem_en(raw_en)
        if len(stemmed) >= 2 and stemmed not in en_tokens:
            en_tokens.append(stemmed)
        if "-" in raw_en or "_" in raw_en:
            for part in re.split(r"[-_]+", raw_en):
                if len(part) >= 2 and not part.isdigit() and part not in en_tokens:
                    en_tokens.append(part)
                    sp = _stem_en(part)
                    if len(sp) >= 2 and sp not in en_tokens:
                        en_tokens.append(sp)

    cjk_tokens: list[str] = []
    for run in _CJK_RUN_RE.findall(text_low):
        bigrams = [run[i : i + 2] for i in range(len(run) - 1)]
        for bg in bigrams:
            if not is_noise_cjk_token(bg) and bg not in cjk_tokens:
                cjk_tokens.append(bg)

    if not cjk_tokens and not en_tokens:
        cjk_tokens = [c for c in re.findall(r"[\u4e00-\u9fff]", text_low) if not is_noise_cjk_token(c)]
    elif not cjk_tokens:
        single_cjk = [c for c in re.findall(r"[\u4e00-\u9fff]", text_low) if not is_noise_cjk_token(c)]
        for sc in single_cjk:
            if sc not in cjk_tokens:
                cjk_tokens.append(sc)

    return list(dict.fromkeys(en_tokens + cjk_tokens))


def extract_query_phrases(text: str) -> list[str]:
    """Extract high-specificity 3..6 char CJK compound phrases & hyphenated English phrases."""
    text_low = text.lower().strip()
    phrases: list[str] = []
    for m in re.finditer(r"[a-z0-9]+(?:[-_][a-z0-9]+)+", text_low):
        ph = m.group(0)
        if len(ph) >= 5 and ph not in phrases:
            phrases.append(ph)
    for run in _CJK_RUN_RE.findall(text_low):
        # Split run on obvious grammatical particles to isolate compound noun phrases
        sub_runs = re.split(r"[的了和与及把给在用让将或并而但对向从到被]", run)
        for sr in sub_runs:
            sr = sr.strip()
            if 3 <= len(sr) <= 6 and not is_noise_cjk_token(sr) and sr not in phrases:
                phrases.append(sr)
            elif len(sr) > 6:
                for w in (4, 3):
                    for i in range(len(sr) - w + 1):
                        seg = sr[i : i + w]
                        if not is_noise_cjk_token(seg) and seg not in phrases:
                            phrases.append(seg)
    return phrases[:16]


def _load_json_safe(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _tokenize_field_bag(text: str) -> tuple[int, dict[str, int]]:
    """Tokenize a metadata field into `(token_length, term_freq_dict)`."""
    if not text:
        return 0, {}
    text_l = text.lower()
    bag: dict[str, int] = {}
    length = 0
    for raw_en in _EN_WORD_RE.findall(text_l):
        length += 1
        bag[raw_en] = bag.get(raw_en, 0) + 1
        st = _stem_en(raw_en)
        if st != raw_en and len(st) >= 2:
            bag[st] = bag.get(st, 0) + 1
        if "-" in raw_en or "_" in raw_en:
            for p in re.split(r"[-_]+", raw_en):
                if len(p) >= 2 and not p.isdigit():
                    bag[p] = bag.get(p, 0) + 1
                    sp = _stem_en(p)
                    if sp != p and len(sp) >= 2:
                        bag[sp] = bag.get(sp, 0) + 1
    for run in _CJK_RUN_RE.findall(text_l):
        length += max(1, len(run) - 1)
        if 2 <= len(run) <= 6:
            bag[run] = bag.get(run, 0) + 1
        for w in (2, 3, 4):
            if len(run) >= w:
                for i in range(len(run) - w + 1):
                    seg = run[i : i + w]
                    bag[seg] = bag.get(seg, 0) + 1
        if len(run) == 1:
            bag[run] = bag.get(run, 0) + 1
    return max(1, length), bag


def _build_live_card_dict(repo_root: Path, p: WikiPageInfo, subtree: str = "core_default") -> dict[str, Any]:
    r_headings, c_stmts = extract_rule_headings_and_claims(repo_root, p)
    body_len, body_terms = extract_compact_body_term_freqs(p.clean_body)
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
    return {
        "id": p.page_id,
        "title": p.title,
        "tier": p.tier,
        "domain": p.domain,
        "subtree": subtree,
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
        "dependsOn": list(p.depends_on),
        "dependedBy": [],
        "weakEdges": [],
        "historicalQueries": [],
        "hotness": 0.35 if p.subdir == "00_core_aesthetics" else 0.20,
        "scripts": [{"role": str(s.get("role", "tool")), "path": str(s["path"])} for s in p.scripts if s.get("path")],
        "aceHelpful": 0,
        "aceHarmful": 0,
    }


_RAW_CORPUS_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}


def load_live_synced_search_corpus(repo_root: Path) -> dict[str, Any]:
    """Load `.search-index.json` and live-merge any unfinalized `.md` or ACE telemetry updates."""
    wiki_root = repo_root / "wiki"
    s_idx_file = wiki_root / ".search-index.json"
    ace_file = wiki_root / ".ace-telemetry.json"
    pages = discover_wiki_pages(repo_root, use_cache=True)

    raw_sig = (
        s_idx_file.stat().st_mtime_ns if s_idx_file.exists() else 0,
        ace_file.stat().st_mtime_ns if ace_file.exists() else 0,
        tuple((p.rel_path, p.page_id, p.body_hash, p.description) for p in pages),
    )
    repo_key = str(repo_root.resolve())
    cached_raw = _RAW_CORPUS_CACHE.get(repo_key)
    if cached_raw is not None and cached_raw[0] == raw_sig:
        return cached_raw[1]

    s_index = _load_json_safe(s_idx_file)
    ace_data = _load_json_safe(ace_file)
    ace_cards: dict[str, dict[str, Any]] = ace_data.get("cards", {})
    indexed_by_rel: dict[str, dict[str, Any]] = {
        str(c.get("relPath", "")): c for c in s_index.get("cards", []) if isinstance(c, dict)
    }

    cards: list[dict[str, Any]] = []
    dirty_pages = False
    id_map = {p.page_id: p for p in pages}
    rel_map = {p.rel_path: p for p in pages}
    for p in pages:
        for al in p.aliases:
            rel_map[al] = p

    for p in pages:
        existing = indexed_by_rel.get(p.rel_path)
        if (
            existing is not None
            and existing.get("id") == p.page_id
            and existing.get("bodyHash") == p.body_hash
            and existing.get("description") == p.description
            and "bodyTerms" in existing
            and "positiveDescription" in existing
        ):
            card_entry = dict(existing)
        else:
            dirty_pages = True
            st_hint = str(existing.get("subtree", "core_default")) if existing else "core_default"
            card_entry = _build_live_card_dict(repo_root, p, subtree=st_hint)
            if existing:
                card_entry["weakEdges"] = existing.get("weakEdges", [])
                card_entry["historicalQueries"] = existing.get("historicalQueries", [])

        # Normalize positiveDescription / negativeScope if index was written by v2
        if not card_entry.get("positiveDescription") and not card_entry.get("negativeScope") and card_entry.get("description"):
            pos_d, neg_s = split_positive_and_negative_description(str(card_entry["description"]))
            card_entry["positiveDescription"] = pos_d
            card_entry["negativeScope"] = neg_s

        # Overlay live ACE telemetry & trace queries immediately
        live_ace = ace_cards.get(p.page_id)
        if isinstance(live_ace, dict):
            card_entry["aceHelpful"] = int(live_ace.get("helpful", card_entry.get("aceHelpful", 0)))
            card_entry["aceHarmful"] = int(live_ace.get("harmful", card_entry.get("aceHarmful", 0)))
            hq = list(card_entry.get("historicalQueries", []))
            for q in live_ace.get("queries", []):
                if isinstance(q, str) and q.strip() and q.strip() not in hq:
                    hq.append(q.strip())
            last_t = str(live_ace.get("last_task", "")).strip()
            if last_t and last_t not in hq:
                hq.append(last_t)
            card_entry["historicalQueries"] = hq[-6:]

        cards.append(card_entry)

    if len(cards) != len(indexed_by_rel):
        dirty_pages = True

    # Ensure bidirectional `dependsOn` / `dependedBy` & live ACE `co_occur` weak edges are always up to date
    card_by_id = {c["id"]: c for c in cards}
    if dirty_pages:
        rev_deps: dict[str, list[str]] = {c["id"]: [] for c in cards}
        for c in cards:
            norm_deps: list[str] = []
            for dep in c.get("dependsOn", []):
                dep_clean = str(dep).split("#")[0].strip()
                tp = id_map.get(dep_clean) or rel_map.get(dep_clean)
                rid = tp.page_id if tp else dep_clean
                if rid not in norm_deps:
                    norm_deps.append(rid)
                if rid in rev_deps and c["id"] not in rev_deps[rid]:
                    rev_deps[rid].append(c["id"])
            c["dependsOn"] = norm_deps
        for c in cards:
            c["dependedBy"] = rev_deps.get(c["id"], [])

    # Overlay live ACE recentTraces co-occurrence edges if not yet finalized
    for tr in ace_data.get("recentTraces", []):
        h_ids = [str(x) for x in tr.get("helpful_cards", []) if str(x) in card_by_id]
        t_str = str(tr.get("task", "")).strip()
        for cid in h_ids:
            if t_str and t_str not in card_by_id[cid]["historicalQueries"]:
                card_by_id[cid]["historicalQueries"].append(t_str)
        for i in range(len(h_ids)):
            for j in range(i + 1, len(h_ids)):
                u, v = h_ids[i], h_ids[j]
                for src_id, dst_id in ((u, v), (v, u)):
                    we = list(card_by_id[src_id].get("weakEdges", []))
                    if not any(e.get("targetId") == dst_id for e in we):
                        we.append({"targetId": dst_id, "weight": 0.35, "source": "co_occur"})
                        card_by_id[src_id]["weakEdges"] = we[:8]

    topic_map = s_index.get("topicMap") if not dirty_pages and s_index.get("topicMap") else build_corpus_topic_map(pages)
    concept_graph = (
        s_index.get("conceptGraph")
        if not dirty_pages and s_index.get("conceptGraph")
        else build_bilingual_and_cross_domain_concept_graph(pages, ace_data)
    )
    avg_body_len = (
        float(s_index.get("corpusStats", {}).get("avgBodyLen", 0.0))
        if not dirty_pages
        else 0.0
    )
    if avg_body_len <= 0.0:
        avg_body_len = sum(int(c.get("bodyLen", 80)) for c in cards) / max(1, len(cards))

    result_corpus = {
        "cards": cards,
        "pagesById": id_map,
        "subtrees": s_index.get("subtrees", []),
        "topicMap": topic_map,
        "conceptGraph": concept_graph,
        "avgBodyLen": max(20.0, avg_body_len),
    }
    _RAW_CORPUS_CACHE[repo_key] = (raw_sig, result_corpus)
    return result_corpus


def _expand_query_via_concept_graph(
    raw_query: str,
    tokens: list[str],
    phrases: list[str],
    concept_graph: dict[str, list[dict[str, Any]]],
) -> list[tuple[str, float, str]]:
    """Return `(expanded_term, weight, trigger_term)` from the bilingual & cross-domain concept graph."""
    q_low = raw_query.lower().strip()
    q_norm = re.sub(r"\s+", "-", q_low)
    existing = set(tokens) | set(phrases)
    candidates: dict[str, tuple[float, str]] = {}

    probe_keys: list[str] = list(dict.fromkeys([*phrases, *tokens]))
    # Also scan concept graph keys that appear as substrings >= 2 chars inside raw_query
    for cg_key in concept_graph:
        if len(cg_key) >= 2 and (cg_key in q_low or cg_key in q_norm) and cg_key not in probe_keys:
            probe_keys.append(cg_key)

    # Also check built-in cross-domain seed phrases directly in case index is empty
    if not concept_graph:
        for cluster in CROSS_DOMAIN_CONCEPT_SEEDS:
            matched_triggers = [t for t in cluster if t.lower() in q_low]
            if matched_triggers:
                trig = matched_triggers[0]
                for peer in cluster:
                    pl = peer.lower()
                    if pl not in existing and pl != trig.lower():
                        candidates[pl] = (0.78, trig)

    for pk in probe_keys:
        nbrs = concept_graph.get(pk, [])
        for nb in nbrs:
            term = str(nb.get("term", "")).strip().lower()
            w = float(nb.get("weight", 0.7))
            if not term or term in existing or term == pk:
                continue
            prev = candidates.get(term)
            if prev is None or w > prev[0]:
                candidates[term] = (w, pk)

    ranked = sorted(candidates.items(), key=lambda x: (-x[1][0], x[0]))[:14]
    out: list[tuple[str, float, str]] = []
    for term, (w, trig) in ranked:
        out.append((term, w, trig))
        # If expanded term is hyphenated English, also include individual sub-tokens at slightly discounted weight
        if "-" in term and re.fullmatch(r"[a-z0-9\-]+", term):
            for part in term.split("-"):
                if len(part) >= 3 and part not in existing and part not in candidates:
                    out.append((part, round(w * 0.82, 3), trig))
    return out[:20]


def search_hybrid_ranked(
    repo_root: Path,
    query: str,
    tier_filter: str | None = None,
    domain_filter: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Core structured hybrid retrieval returning full ranked diagnostics & faceted groups."""
    tokens = tokenize_query(query)
    if not tokens:
        return {"error": "empty_query", "results": []}

    phrases = extract_query_phrases(query)
    corpus = load_live_synced_search_corpus(repo_root)
    all_cards: list[dict[str, Any]] = corpus["cards"]
    topic_map: dict[str, list[Any]] = corpus["topicMap"]
    concept_graph: dict[str, list[dict[str, Any]]] = corpus["conceptGraph"]
    subtrees_list: list[dict[str, Any]] = corpus["subtrees"]
    avg_body_len: float = corpus["avgBodyLen"]

    def _matches_filter(c: dict[str, Any]) -> bool:
        if tier_filter and c.get("tier") != tier_filter:
            return False
        if domain_filter:
            df_l = domain_filter.lower()
            if df_l != str(c.get("domain", "")).lower() and df_l not in f"{c.get('tier', '')}/{c.get('domain', '')}".lower():
                return False
        return True

    eligible_cards = [c for c in all_cards if _matches_filter(c)]
    if not eligible_cards:
        return {
            "query": query,
            "tokens": tokens,
            "matchedSubtrees": [],
            "conceptExpansions": [],
            "hop2Bridges": [],
            "primaryResults": [],
            "synergyResults": [],
            "results": [],
            "oneHopClosure": [],
            "boundVerifiers": [],
        }

    # Pre-tokenize fields for all corpus cards (cached in-memory per repo state for <10ms multi-query speed)
    n_corpus = max(1, len(all_cards))
    s_idx_p = repo_root / "wiki" / ".search-index.json"
    ace_p = repo_root / "wiki" / ".ace-telemetry.json"
    cache_sig = (
        s_idx_p.stat().st_mtime_ns if s_idx_p.exists() else 0,
        ace_p.stat().st_mtime_ns if ace_p.exists() else 0,
        len(all_cards),
        tuple((c["id"], c.get("bodyHash", ""), c.get("description", ""), c.get("aceHelpful", 0), c.get("aceHarmful", 0)) for c in all_cards),
    )
    repo_key = str(repo_root.resolve())
    cached_prep = _CORPUS_MEM_CACHE.get(repo_key)
    if cached_prep is not None and cached_prep[0] == cache_sig:
        _, _, prepared_all, doc_freq, avg_lens = cached_prep
    else:
        prepared_all = {}
        doc_freq = {}
        field_len_sums = {"id_tags": 0, "title": 0, "pos_desc": 0, "rules": 0, "claims_sec": 0}
        for c in all_cards:
            cid = c["id"]
            id_tags_txt = f"{cid} {' '.join(c.get('tags', []))} {' '.join(c.get('aliases', []))}"
            title_txt = str(c.get("title", ""))
            pos_desc_txt = str(c.get("positiveDescription", ""))
            neg_scope_txt = str(c.get("negativeScope", ""))
            rules_txt = " ".join(c.get("patterns", [])) + " " + " ".join(c.get("ruleHeadings", []))
            sec_txt = " ".join(f"{s.get('title', '')} {s.get('summary', '')}" for s in c.get("sections", []))
            claims_sec_txt = " ".join(c.get("claims", [])) + " " + sec_txt

            l_idt, b_idt = _tokenize_field_bag(id_tags_txt)
            l_ttl, b_ttl = _tokenize_field_bag(title_txt)
            l_pds, b_pds = _tokenize_field_bag(pos_desc_txt)
            l_rul, b_rul = _tokenize_field_bag(rules_txt)
            l_cls, b_cls = _tokenize_field_bag(claims_sec_txt)
            body_terms: dict[str, int] = c.get("bodyTerms", {})
            body_len = max(1, int(c.get("bodyLen", 80)))

            field_len_sums["id_tags"] += l_idt
            field_len_sums["title"] += l_ttl
            field_len_sums["pos_desc"] += l_pds
            field_len_sums["rules"] += l_rul
            field_len_sums["claims_sec"] += l_cls

            union_terms = set(b_idt) | set(b_ttl) | set(b_pds) | set(b_rul) | set(b_cls) | set(body_terms)
            for t in union_terms:
                doc_freq[t] = doc_freq.get(t, 0) + 1

            prepared_all[cid] = {
                "card": c,
                "bags": {
                    "id_tags": (l_idt, b_idt, id_tags_txt.lower()),
                    "title": (l_ttl, b_ttl, title_txt.lower()),
                    "pos_desc": (l_pds, b_pds, pos_desc_txt.lower()),
                    "rules": (l_rul, b_rul, rules_txt.lower()),
                    "claims_sec": (l_cls, b_cls, claims_sec_txt.lower()),
                },
                "bodyTerms": body_terms,
                "bodyLen": body_len,
                "negScopeLower": neg_scope_txt.lower(),
            }

        avg_lens = {k: max(2.0, v / n_corpus) for k, v in field_len_sums.items()}
        _CORPUS_MEM_CACHE[repo_key] = (cache_sig, corpus, prepared_all, doc_freq, avg_lens)

    def _idf(term: str) -> float:
        df = doc_freq.get(term, 0)
        return max(0.18, math.log(1.0 + (n_corpus - df + 0.5) / (df + 0.5)))

    # Rare CJK character fallback if ALL multi-char tokens missed the entire corpus
    active_tokens = list(tokens)
    if all(doc_freq.get(t, 0) == 0 for t in active_tokens):
        for ch in re.findall(r"[\u4e00-\u9fff]", query.lower()):
            if not is_noise_cjk_token(ch) and ch not in active_tokens:
                active_tokens.append(ch)

    # Phase 0: Match L2 META Subtrees
    matched_subtrees: list[tuple[float, dict[str, Any]]] = []
    for st in subtrees_list:
        if domain_filter and domain_filter.lower() not in str(st.get("domain", "")).lower():
            continue
        if tier_filter and st.get("tier") and st.get("tier") != tier_filter:
            continue
        st_hay = f"{st.get('slug', '')} {st.get('title', '')} {st.get('when', '')}".lower()
        st_sc = sum((_idf(tok) * 1.4 if len(tok) > 1 else 0.3) for tok in active_tokens if tok in st_hay)
        if st_sc >= 2.2:
            matched_subtrees.append((round(st_sc, 2), st))
    matched_subtrees.sort(key=lambda x: x[0], reverse=True)

    # CorpusTopicMap section-level boost & best anchor lookup
    topic_card_boost: dict[str, float] = {}
    topic_best_node: dict[str, tuple[float, str]] = {}
    for tok in active_tokens:
        if len(tok) < 2:
            continue
        postings = topic_map.get(tok, [])
        if not postings:
            continue
        idf_w = _idf(tok) * 0.85
        for post in postings:
            cid, nid = (post.split("#", 1) if isinstance(post, str) and "#" in post else ("", ""))
            if cid in prepared_all:
                topic_card_boost[cid] = topic_card_boost.get(cid, 0.0) + idf_w
                if idf_w > topic_best_node.get(cid, (0.0, ""))[0]:
                    topic_best_node[cid] = (idf_w, nid)

    # Helper: BM25F score of a term list against a prepared card
    field_specs = (
        ("id_tags", 4.2, 0.25),
        ("title", 3.8, 0.35),
        ("pos_desc", 3.0, 0.45),
        ("rules", 2.6, 0.50),
        ("claims_sec", 2.2, 0.50),
    )
    k1 = 1.2

    def _bm25_tf(tf: int, dl: float, avg_dl: float, b: float) -> float:
        if tf <= 0:
            return 0.0
        norm = 1.0 - b + b * (dl / max(1.0, avg_dl))
        return (tf * (k1 + 1.0)) / (tf + k1 * norm)

    # ---------------- CHANNEL 1: True BM25F + Exact Phrase Specificity ----------------
    ch1_scores: dict[str, float] = {}
    pos_subject_hits: dict[str, float] = {}
    pos_subject_tok_contribs: dict[str, dict[str, float]] = {}

    for c in eligible_cards:
        cid = c["id"]
        prep = prepared_all[cid]
        bags = prep["bags"]
        b_terms = prep["bodyTerms"]
        b_len = prep["bodyLen"]

        sc = topic_card_boost.get(cid, 0.0)
        subj_sc = 0.0
        subj_tok_map: dict[str, float] = {}

        for tok in active_tokens:
            idf_v = _idf(tok)
            len_mult = 1.0 if len(tok) > 1 else 0.30
            tok_field_sum = 0.0
            for f_name, f_w, f_b in field_specs:
                dl, f_bag, f_raw = bags[f_name]
                tf = f_bag.get(tok, 0)
                if tf == 0 and len(tok) >= 3 and tok in f_raw:
                    tf = 1
                if tf > 0:
                    contrib = f_w * _bm25_tf(tf, dl, avg_lens[f_name], f_b)
                    tok_field_sum += contrib
                    if f_name in ("id_tags", "title"):
                        subj_sc += idf_v * contrib
                        subj_tok_map[tok] = subj_tok_map.get(tok, 0.0) + idf_v * contrib

            btf = b_terms.get(tok, 0)
            if btf > 0:
                tok_field_sum += 1.15 * _bm25_tf(btf, b_len, avg_body_len, 0.65)

            sc += idf_v * len_mult * tok_field_sum

        # Exact multi-character compound phrase specificity boost
        for ph in phrases:
            ph_idf = max(_idf(ph), 1.8) + 0.35 * len(ph)
            if ph in bags["id_tags"][2] or ph in bags["title"][2]:
                sc += 3.2 * ph_idf
                subj_sc += 3.2 * ph_idf
                subj_tok_map[ph] = subj_tok_map.get(ph, 0.0) + 3.2 * ph_idf
            elif ph in bags["pos_desc"][2] or ph in bags["rules"][2]:
                sc += 2.0 * ph_idf
            elif ph in bags["claims_sec"][2] or ph in b_terms:
                sc += 1.1 * ph_idf

        if sc > 0.0:
            ch1_scores[cid] = sc
            pos_subject_hits[cid] = subj_sc
            pos_subject_tok_contribs[cid] = subj_tok_map

    # ---------------- CHANNEL 2: Bilingual & Cross-Domain Concept Graph ----------------
    concept_expansions = _expand_query_via_concept_graph(query, active_tokens, phrases, concept_graph)
    ch2_scores: dict[str, float] = {}
    concept_via_map: dict[str, list[str]] = {}

    if concept_expansions:
        for c in eligible_cards:
            cid = c["id"]
            prep = prepared_all[cid]
            bags = prep["bags"]
            b_terms = prep["bodyTerms"]
            c_sc = 0.0
            matched_pairs: list[str] = []
            for exp_term, edge_w, trig in concept_expansions:
                exp_idf = _idf(exp_term)
                hit_w = 0.0
                if exp_term in bags["id_tags"][1] or exp_term in bags["id_tags"][2]:
                    hit_w += 3.6
                if exp_term in bags["title"][1] or exp_term in bags["title"][2]:
                    hit_w += 3.2
                if exp_term in bags["pos_desc"][1] or exp_term in bags["pos_desc"][2]:
                    hit_w += 2.2
                if exp_term in bags["rules"][1] or exp_term in bags["rules"][2]:
                    hit_w += 1.8
                if exp_term in bags["claims_sec"][1] or exp_term in b_terms:
                    hit_w += 1.0
                if hit_w > 0.0:
                    c_sc += 0.65 * edge_w * exp_idf * hit_w
                    badge_pair = f"{trig}→{exp_term}"
                    if badge_pair not in matched_pairs:
                        matched_pairs.append(badge_pair)
            if c_sc > 0.0:
                ch2_scores[cid] = c_sc
                concept_via_map[cid] = matched_pairs[:2]

    # Select Domain-Diversified Seeds from Channel 1 + Channel 2 (max 2 per domain, up to 6 seeds)
    prelim_combined: list[tuple[float, dict[str, Any]]] = []
    eligible_by_id = {c["id"]: c for c in eligible_cards}
    all_by_id = {c["id"]: c for c in all_cards}
    for cid, c in eligible_by_id.items():
        s12 = ch1_scores.get(cid, 0.0) + 0.85 * ch2_scores.get(cid, 0.0)
        if s12 > 0.0:
            prelim_combined.append((s12, c))
    prelim_combined.sort(key=lambda x: x[0], reverse=True)

    diversified_seeds: list[tuple[float, dict[str, Any]]] = []
    seed_domain_counts: dict[str, int] = {}
    top_prelim = prelim_combined[0][0] if prelim_combined else 0.0
    for s_val, c_obj in prelim_combined:
        if len(diversified_seeds) >= 6:
            break
        if s_val < 0.18 * top_prelim:
            break
        dom = str(c_obj.get("domain", ""))
        if seed_domain_counts.get(dom, 0) >= 2:
            continue
        seed_domain_counts[dom] = seed_domain_counts.get(dom, 0) + 1
        diversified_seeds.append((s_val, c_obj))

    # ---------------- CHANNEL 3: 1-Hop Cross-Tier & Co-Occurrence Graph Walk ----------------
    ch3_scores: dict[str, float] = {}
    graph_walk_via: dict[str, list[str]] = {}
    for seed_sc, seed_c in diversified_seeds[:5]:
        sid = seed_c["id"]
        fwd_list = seed_c.get("dependsOn", [])
        fwd_damp = 1.0 / math.sqrt(max(1, len(fwd_list)))
        # 3a. Forward `dependsOn` (pulls Foundation core rules for matched Tool/Workflow cards)
        for dep_raw in fwd_list:
            dep_id = str(dep_raw).split("#")[0].strip()
            if dep_id in eligible_by_id and dep_id != sid:
                add_w = 0.34 * fwd_damp * seed_sc
                ch3_scores[dep_id] = max(ch3_scores.get(dep_id, 0.0), add_w)
                via_lbl = f"{sid}(dependsOn)"
                if via_lbl not in graph_walk_via.setdefault(dep_id, []):
                    graph_walk_via[dep_id].append(via_lbl)
        # 3b. Reverse `dependedBy` (degree-damped so hub axioms don't flood unrelated tool cards)
        rev_list = seed_c.get("dependedBy", [])
        rev_damp = 1.0 / math.sqrt(max(1, len(rev_list)))
        for rev_id in rev_list:
            if rev_id in eligible_by_id and rev_id != sid:
                add_w = 0.32 * rev_damp * seed_sc
                ch3_scores[rev_id] = max(ch3_scores.get(rev_id, 0.0), add_w)
                via_lbl = f"{sid}(dependedBy)"
                if via_lbl not in graph_walk_via.setdefault(rev_id, []):
                    graph_walk_via[rev_id].append(via_lbl)
        # 3c. Empirical `weakEdges` (`co_occur` & `shared_script`)
        for we in seed_c.get("weakEdges", []):
            tid = str(we.get("targetId", ""))
            ew = float(we.get("weight", 0.35))
            esrc = str(we.get("source", "co_occur"))
            if tid in eligible_by_id and tid != sid and esrc in ("co_occur", "shared_script"):
                cross_dom_mult = 1.25 if eligible_by_id[tid].get("domain") != seed_c.get("domain") else 1.0
                add_w = 0.38 * ew * cross_dom_mult * seed_sc
                ch3_scores[tid] = max(ch3_scores.get(tid, 0.0), add_w)
                via_lbl = f"{sid}({esrc})"
                if via_lbl not in graph_walk_via.setdefault(tid, []):
                    graph_walk_via[tid].append(via_lbl)

    # ---------------- CHANNEL 4: Cross-Domain SAG2 2-Hop Dynamic Bridge Hyperedges ----------------
    ch4_scores: dict[str, float] = {}
    hop2_page_via: dict[str, list[str]] = {}
    hop2_bridges_used: list[tuple[str, float, list[str], list[str]]] = []

    if diversified_seeds:
        hop2_seed_ids = {c_obj["id"] for _, c_obj in diversified_seeds[:5]}
        seed_domains = {eligible_by_id[sid]["domain"] for sid in hop2_seed_ids if sid in eligible_by_id}
        max_h2_targets = max(8, min(25, int(0.08 * max(1, len(eligible_cards)))))

        term_to_cids: dict[str, set[str]] = {}
        seed_tag_terms: set[str] = set()

        for bt_raw, plist in topic_map.items():
            bt = str(bt_raw).strip().lower()
            if len(bt) < 2 or bt.isdigit():
                continue
            for post in plist:
                cid = post.split("#", 1)[0] if isinstance(post, str) and "#" in post else ""
                if cid in eligible_by_id:
                    term_to_cids.setdefault(bt, set()).add(cid)

        for cid, c_obj in eligible_by_id.items():
            for tg in c_obj.get("tags", []):
                bt = str(tg).strip().lower()
                if len(bt) >= 2 and not bt.isdigit():
                    term_to_cids.setdefault(bt, set()).add(cid)
                    if cid in hop2_seed_ids:
                        seed_tag_terms.add(bt)

        q_tok_set = {t for t in active_tokens if len(t) >= 2}
        cand_bridges: list[tuple[float, str, list[str], list[str]]] = []
        for bt, cids in term_to_cids.items():
            if bt in _BRIDGE_STOPWORDS or bt in q_tok_set:
                continue
            if any(bt in qt or qt in bt for qt in q_tok_set):
                continue
            seed_hits = sorted(cids & hop2_seed_ids)
            hop2_targets = sorted(cids - hop2_seed_ids)
            if not seed_hits or not (1 <= len(hop2_targets) <= max_h2_targets):
                continue
            idf_bt = _idf(bt)
            tag_bonus = 2.0 if bt in seed_tag_terms else 0.0
            cross_domain_targets = [
                tid for tid in hop2_targets if eligible_by_id[tid].get("domain") not in seed_domains
            ]
            cross_mult = 1.6 if cross_domain_targets else 1.0
            bt_score = ((len(seed_hits) * 2.4) + idf_bt + tag_bonus + (0.5 if len(bt) >= 4 else 0.0)) * cross_mult
            cand_bridges.append((round(bt_score, 3), bt, seed_hits, hop2_targets))

        cand_bridges.sort(key=lambda x: (-x[0], -len(x[3]), x[1]))
        covered_h2: set[str] = set()
        seed_anchor_scale = max(4.0, top_prelim * 0.32)
        for bt_score, bt, s_hits, h2_targets in cand_bridges:
            if len(hop2_bridges_used) >= 5:
                break
            if covered_h2 and set(h2_targets).issubset(covered_h2):
                continue
            covered_h2.update(h2_targets)
            hop2_bridges_used.append((bt, bt_score, s_hits, h2_targets))
            hop2_w = round(0.12 * bt_score * (seed_anchor_scale / 4.0), 3)
            for tid in h2_targets:
                ch4_scores[tid] = ch4_scores.get(tid, 0.0) + hop2_w
                if bt not in hop2_page_via.setdefault(tid, []):
                    hop2_page_via[tid].append(bt)

    # ---------------- STAGE 2: Precision Reranker, Negative Gate & Faceted Diversity ----------------
    # Build per-channel ranks over Top-60 candidate union
    def _rank_map(sc_dict: dict[str, float]) -> dict[str, int]:
        ordered = sorted(sc_dict.items(), key=lambda x: x[1], reverse=True)[:60]
        return {cid: r_idx for r_idx, (cid, _) in enumerate(ordered, 1)}

    r1 = _rank_map(ch1_scores)
    r2 = _rank_map(ch2_scores)
    r3 = _rank_map(ch3_scores)
    r4 = _rank_map(ch4_scores)

    candidate_ids = set(r1) | set(r2) | set(r3) | set(r4)
    denom_ch1 = max(12.0, max(ch1_scores.values(), default=1.0))
    denom_ch2 = max(6.0, max(ch2_scores.values(), default=1.0))
    denom_ch3 = max(5.0, max(ch3_scores.values(), default=1.0))
    denom_ch4 = max(3.0, max(ch4_scores.values(), default=1.0))

    rrf_k = 35.0
    query_low = query.lower().strip()
    reranked_items: list[dict[str, Any]] = []

    for cid in candidate_ids:
        c = eligible_by_id[cid]
        prep = prepared_all[cid]

        s1_raw = ch1_scores.get(cid, 0.0)
        s2_raw = ch2_scores.get(cid, 0.0)
        s3_raw = ch3_scores.get(cid, 0.0)
        s4_raw = ch4_scores.get(cid, 0.0)

        # Normalized linear + strength-gated RRF hybrid relevance score in ~[0, 100]
        norm_linear = (
            66.0 * (s1_raw / denom_ch1)
            + 38.0 * (s2_raw / denom_ch2)
            + 9.5 * (s3_raw / denom_ch3)
            + 8.5 * (s4_raw / denom_ch4)
        )
        rrf_bonus = 350.0 * (
            (min(1.0, s1_raw / 6.0) / (rrf_k + r1[cid]) if cid in r1 else 0.0)
            + (0.85 * min(1.0, s2_raw / 4.0) / (rrf_k + r2[cid]) if cid in r2 else 0.0)
            + (0.32 * min(1.0, s3_raw / 3.0) / (rrf_k + r3[cid]) if cid in r3 else 0.0)
            + (0.30 * min(1.0, s4_raw / 2.0) / (rrf_k + r4[cid]) if cid in r4 else 0.0)
        )
        base_score = norm_linear + rrf_bonus
        final_score = base_score
        badges: list[str] = []

        # 1. Negative Scope Hard Gate ('不适用于平面图标' -> 0.18x penalty when query asks for '平面图标')
        neg_txt = prep["negScopeLower"]
        scope_excluded = False
        if neg_txt:
            # Strip negative lead marker to inspect actual excluded target nouns
            neg_core = re.sub(
                r"^(?:不适用于|不用于|禁止用于|不要用于|切勿用于|严禁用于|跳过|无需|not\s+for\s+|do\s+not\s+use\s+for\s+|skip\s+when\s+)",
                "",
                neg_txt,
            )
            neg_matched_toks = [tok for tok in active_tokens if len(tok) >= 2 and tok in neg_core]
            neg_hit_idf = sum(_idf(tok) for tok in neg_matched_toks)
            neg_phrase_hit = any(len(ph) >= 3 and ph in neg_core for ph in phrases)
            # Only positive subject matches NOT shared with the excluded target phrase can override exclusion
            non_neg_subj_strength = sum(
                val for k_tok, val in pos_subject_tok_contribs.get(cid, {}).items() if k_tok not in neg_core
            )
            if (neg_phrase_hit or len(neg_matched_toks) >= 2 or neg_hit_idf >= 1.5) and non_neg_subj_strength < max(4.0, neg_hit_idf * 3.5):
                final_score *= 0.18
                scope_excluded = True
                badges.append("⛔[Scope-Excluded]")

        # 2. Relevance-Gated Multiplicative ACE & Historical Query Memory (Anti-Matthew Effect)
        ace_h = int(c.get("aceHelpful", 0))
        ace_m = int(c.get("aceHarmful", 0))
        net_ace = ace_h - ace_m
        hist_queries = [str(q).lower().strip() for q in c.get("historicalQueries", []) if isinstance(q, str) and q.strip()]

        reused_q = False
        if not scope_excluded and ch1_scores.get(cid, 0.0) > 0:
            if len(query_low) >= 4 and any(len(hq) >= 4 and (query_low in hq or hq in query_low) for hq in hist_queries):
                final_score *= 1.08
                reused_q = True
            elif sum(1 for tok in active_tokens if len(tok) >= 2 and any(tok in hq for hq in hist_queries)) >= 2:
                final_score *= 1.04
                reused_q = True

        if net_ace > 0 and not scope_excluded and base_score >= 15.0:
            ace_mult = 1.0 + min(0.18, 0.045 * math.log1p(net_ace))
            final_score *= ace_mult
        elif net_ace < 0:
            harm_mult = max(0.35, 1.0 - 0.18 * math.log1p(-net_ace))
            final_score *= harm_mult

        # Attach provenance badges
        if reused_q:
            badges.append("♻️[Query-Memory]")
        if cid in concept_via_map:
            badges.append(f"🌐[Concept: {', '.join(concept_via_map[cid])}]")
        if cid in graph_walk_via and cid not in ch1_scores:
            badges.append(f"🔗[Graph-Walk via: {', '.join(graph_walk_via[cid][:2])}]")
        elif cid in graph_walk_via and ch3_scores.get(cid, 0.0) >= 0.4 * denom_ch3:
            badges.append(f"🔗[Graph-Walk via: {', '.join(graph_walk_via[cid][:1])}]")
        if cid in hop2_page_via:
            badges.append(f"🔗[2-Hop via: {', '.join(hop2_page_via[cid][:2])}]")

        # Locate best section anchor & line range
        best_anchor = ""
        best_line_info = ""
        best_sec_s = 0.0
        pref_nid = topic_best_node.get(cid, (0.0, ""))[1]
        probe_sec_terms = [t for t in active_tokens if len(t) > 1] + [e[0] for e in concept_expansions[:6]]
        for sec in c.get("sections", []):
            sec_hay = f"{sec.get('title', '')} {sec.get('summary', '')}".lower()
            s_sc = sum(2.5 for t in probe_sec_terms if t in sec_hay)
            if sec.get("nodeId") == pref_nid and pref_nid:
                s_sc += 3.0
            if s_sc > best_sec_s:
                best_sec_s = s_sc
                best_anchor = f"#{sec.get('slug', '')}"
                lr = sec.get("lineRange", [1, 1])
                best_line_info = f" (L{lr[0]}-{lr[1]})"

        # Matched rules preview
        matched_rules: list[str] = []
        for pat in c.get("patterns", []) + c.get("ruleHeadings", []):
            pl = pat.lower()
            if any(t in pl for t in probe_sec_terms):
                matched_rules.append(pat[:95])
                if len(matched_rules) >= 2:
                    break

        reranked_items.append({
            "id": cid,
            "tier": c.get("tier", ""),
            "domain": c.get("domain", ""),
            "card": c,
            "score": round(final_score, 3),
            "baseScore": round(base_score, 3),
            "channels": {
                "bm25f": round(ch1_scores.get(cid, 0.0), 2),
                "concept": round(ch2_scores.get(cid, 0.0), 2),
                "graphWalk": round(ch3_scores.get(cid, 0.0), 2),
                "hop2": round(ch4_scores.get(cid, 0.0), 2),
            },
            "scopeExcluded": scope_excluded,
            "badges": badges,
            "bestAnchor": best_anchor,
            "bestLineInfo": best_line_info,
            "matchedRules": matched_rules,
        })

    reranked_items.sort(key=lambda x: (x["score"], -x["card"].get("abstractionLevel", 2)), reverse=True)

    # ---------------- Faceted Cross-Domain Diversity Allocation ----------------
    eff_limit = max(1, limit)
    if domain_filter or eff_limit <= 3 or len(reranked_items) <= eff_limit:
        selected = reranked_items[:eff_limit]
        primary_results = selected
        synergy_results: list[dict[str, Any]] = []
    else:
        top_score = reranked_items[0]["score"]
        top_domain = reranked_items[0]["card"]["domain"]
        max_per_subtree = max(3, eff_limit // 2)

        # Find significant secondary domains so multi-domain queries NEVER get monopolized by 1 giant domain
        secondary_domain_leads: list[dict[str, Any]] = []
        seen_sec_domains: set[str] = {top_domain}
        for item in reranked_items[1:]:
            if item["scopeExcluded"]:
                continue
            dom = item["card"]["domain"]
            if dom in seen_sec_domains:
                continue
            has_direct = bool(item["channels"]["bm25f"] > 0 or item["channels"]["concept"] > 0)
            has_bridge = bool(item["channels"]["concept"] > 0 or item["channels"]["graphWalk"] > 0 or item["channels"]["hop2"] > 0)
            sig_thresh = 0.16 * top_score if (has_direct or has_bridge) else 0.24 * top_score
            if item["score"] >= sig_thresh:
                seen_sec_domains.add(dom)
                secondary_domain_leads.append(item)

        # Prefer secondary domains that matched direct query/concept terms over passive graph neighbors
        secondary_domain_leads.sort(
            key=lambda it: (1 if (it["channels"]["bm25f"] > 0 or it["channels"]["concept"] > 0) else 0, it["score"]),
            reverse=True,
        )
        reserved_synergy = secondary_domain_leads[: min(4, max(3, eff_limit // 2 - 1))]
        reserved_ids = {it["id"] for it in reserved_synergy}
        primary_budget = eff_limit - len(reserved_synergy)

        primary_results = []
        subtree_counts: dict[tuple[str, str], int] = {}
        overflow_pool: list[dict[str, Any]] = []

        for item in reranked_items:
            if item["id"] in reserved_ids:
                continue
            if len(primary_results) >= primary_budget:
                overflow_pool.append(item)
                continue
            st_key = (item["card"]["domain"], item["card"].get("subtree", "core_default"))
            if subtree_counts.get(st_key, 0) >= max_per_subtree and not item["scopeExcluded"]:
                overflow_pool.append(item)
                continue
            subtree_counts[st_key] = subtree_counts.get(st_key, 0) + 1
            primary_results.append(item)

        for item in overflow_pool:
            if len(primary_results) >= primary_budget:
                break
            primary_results.append(item)

        synergy_results = reserved_synergy
        selected = primary_results + synergy_results
        selected.sort(key=lambda x: x["score"], reverse=True)

    # Collect 1-Hop Co-Retrieval Closure & Bound Verifier Scripts across selected hits
    selected_ids = {it["id"] for it in selected}
    one_hop_closure: list[dict[str, str]] = []
    seen_closure: set[str] = set()
    bound_verifiers: list[str] = []

    for it in selected:
        c = it["card"]
        for dep in c.get("dependsOn", []):
            dep_id = str(dep).split("#")[0].strip()
            if dep_id not in selected_ids and dep_id not in seen_closure and dep_id in all_by_id:
                seen_closure.add(dep_id)
                dc = all_by_id[dep_id]
                one_hop_closure.append({
                    "id": dep_id,
                    "title": dc["title"],
                    "tierDomain": f"{dc['tier']}/{dc['domain']}",
                })
        for sc_item in c.get("scripts", []):
            sp = str(sc_item.get("path", ""))
            if sp and sp not in bound_verifiers:
                bound_verifiers.append(sp)

    return {
        "query": query,
        "tokens": active_tokens,
        "matchedSubtrees": [
            {
                "domain": st["domain"],
                "slug": st["slug"],
                "title": st["title"],
                "when": st["when"],
                "cardCount": st["cardCount"],
                "score": sc,
            }
            for sc, st in matched_subtrees[:3]
        ],
        "conceptExpansions": [
            {"term": t, "weight": w, "trigger": trig}
            for t, w, trig in concept_expansions[:8]
        ],
        "hop2Bridges": [
            {"term": bt, "score": bsc, "seeds": s_hits, "targets": h2_t}
            for bt, bsc, s_hits, h2_t in hop2_bridges_used
        ],
        "primaryResults": primary_results,
        "synergyResults": synergy_results,
        "results": selected,
        "oneHopClosure": one_hop_closure[:6],
        "boundVerifiers": bound_verifiers[:8],
    }


def _format_hit_line(rank: int, item: dict[str, Any], explain: bool = False) -> str:
    c = item["card"]
    cid = item["id"]
    st_slug = c.get("subtree", "")
    st_badge = f" | 🌿`{st_slug}`" if st_slug and st_slug != "core_default" else ""
    ace_net = int(c.get("aceHelpful", 0)) - int(c.get("aceHarmful", 0))
    ace_badge = f" | ⭐ACE:{ace_net:+d}" if ace_net != 0 else ""
    prov_badges = (" " + " ".join(item["badges"])) if item["badges"] else ""
    anchor = item["bestAnchor"]
    line_info = item["bestLineInfo"]
    read_cmd = f"`wiki_query.py read {cid}{anchor}`" if anchor else f"`wiki_query.py read {cid}`"

    lines = [
        f"{rank}. [{c['tier']}/{c['domain']}{st_badge}] {c['title']} "
        f"(id: `{cid}`, L{c.get('abstractionLevel', 2)}, {c.get('status', 'canonical')}, score={item['score']:.1f}{ace_badge}){prov_badges}",
        f"   Path: wiki/{c['relPath']}{anchor}{line_info}  -->  Drill-down: {read_cmd}",
        f"   Description: {c.get('description', '')}",
    ]
    if item["matchedRules"]:
        lines.append(f"   Matched Rules: {' | '.join(item['matchedRules'])}")
    if explain:
        ch = item["channels"]
        lines.append(
            f"   🔬 Explain: base={item['baseScore']:.1f} | BM25F={ch['bm25f']:.1f}, "
            f"Concept={ch['concept']:.1f}, GraphWalk={ch['graphWalk']:.1f}, 2-Hop={ch['hop2']:.1f}"
        )
    return "\n".join(lines)


def execute_hybrid_search(
    repo_root: Path,
    query: str,
    tier_filter: str | None = None,
    domain_filter: str | None = None,
    limit: int = 10,
    as_json: bool = False,
    explain: bool = False,
) -> int:
    """CLI entrypoint shared by both `wiki_query.py search` and `study_runner.py search`."""
    payload = search_hybrid_ranked(
        repo_root,
        query=query,
        tier_filter=tier_filter,
        domain_filter=domain_filter,
        limit=limit,
    )
    if payload.get("error") == "empty_query":
        print("Empty query.")
        return 1

    if as_json:
        json_out = {
            k: v
            for k, v in payload.items()
            if k not in ("primaryResults", "synergyResults", "results")
        }
        json_out["results"] = [
            {
                "id": it["id"],
                "title": it["card"]["title"],
                "tier": it["card"]["tier"],
                "domain": it["card"]["domain"],
                "subtree": it["card"].get("subtree", ""),
                "relPath": it["card"]["relPath"],
                "score": it["score"],
                "scopeExcluded": it["scopeExcluded"],
                "badges": it["badges"],
                "bestAnchor": it["bestAnchor"],
                "channels": it["channels"],
            }
            for it in payload["results"]
        ]
        print(json.dumps(json_out, ensure_ascii=False, indent=2))
        return 0

    results = payload["results"]
    if not results:
        print(f"No wiki pages matched query: '{query}'")
        return 0

    if payload["matchedSubtrees"]:
        print("🌿 [Phase 0 — Matched L2 META Subtrees (Sirchmunk Community Router)]:")
        for st in payload["matchedSubtrees"]:
            print(
                f"  • `{st['domain']}/{st['slug']}` ({st['cardCount']} cards, match={st['score']:.1f}) — {st['title']}\n"
                f"    Activate Subtree: `python3 .agents/skills/study/scripts/wiki_query.py read --subtree {st['domain']}/{st['slug']}`"
            )
        print("")

    if payload["conceptExpansions"]:
        exp_preview = ", ".join(f"`{e['trigger']}→{e['term']}`" for e in payload["conceptExpansions"][:5])
        print(f"🌐 [Channel 2 — Bilingual & Cross-Domain Concept Bridges]: {exp_preview}")

    if payload["hop2Bridges"]:
        b_preview = ", ".join(f"`{b['term']}` ({len(b['targets'])} cards)" for b in payload["hop2Bridges"][:4])
        print(f"🔗 [Channel 4 — SAG2 Cross-Domain 2-Hop Hyperedges]: {b_preview}")

    if payload["conceptExpansions"] or payload["hop2Bridges"]:
        print("")

    synergy_ids = {it["id"] for it in payload["synergyResults"]}
    primary_list = [it for it in results if it["id"] not in synergy_ids]
    synergy_list = [it for it in results if it["id"] in synergy_ids]

    print(f"=== 🎯 Primary Hits (Top {len(primary_list)} of {len(results)} for '{query}') ===")
    rank = 1
    for it in primary_list:
        print(_format_hit_line(rank, it, explain=explain) + "\n")
        rank += 1

    if synergy_list:
        print(f"=== 🌉 Cross-Domain & Cross-Tier Synergy ({len(synergy_list)} Companion Lead(s)) ===")
        for it in synergy_list:
            print(_format_hit_line(rank, it, explain=explain) + "\n")
            rank += 1

    if payload["oneHopClosure"]:
        closure_str = ", ".join(f"`{d['id']}` ({d['tierDomain']})" for d in payload["oneHopClosure"])
        print(f"🔗 [1-Hop Co-Retrieval Prerequisites (`depends_on`)]: {closure_str}")

    if payload["boundVerifiers"]:
        ver_str = ", ".join(
            f"`{vp}`" if str(vp).startswith("wiki/") else f"`wiki/{vp}`"
            for vp in payload["boundVerifiers"][:5]
        )
        print(f"🛡️ [Bound Executable Scripts / Rust Verifiers]: {ver_str}")

    return 0

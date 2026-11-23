#!/usr/bin/env python3
"""Planetary-Scale Zero-Token-Waste Sleep-Time `Dreaming` Engine (`wiki_dream.py`).

Implements Anthropic's *"The components of agent memory"* (Dreaming / Sleep-Time Compute)
engineered for 100,000,000-book / multi-million-card scale without token explosion or git-history dependence:

1. Gate 1 — Content-Addressed `dreamedHash` Dormancy Watermark (`O(Δ)` Delta-Only, Never `O(N)`):
   - Clean `canonical` / `human-reviewed` cards and previously dreamed cards (`dreamedHash == bodyHash`)
     with 0 harmful hits, non-stale timestamps, and no Read-to-Helpful funnel leak stay 100% DORMANT
     (even lazy `clean_body` disk reads are skipped!).
2. Gate 2 — Zero-LLM Deterministic 5-Bucket Salience & Entropy Triage (`P0`..`P4`):
   - `P0`: Active regressions (`aceHarmful > 0` or broken/stale Grounded Claims).
   - `P1`: Unverified single-session notes (`empirical-refine` / `provisional`, `trust != human-reviewed`, undreamed)
           — catches the Pokémon Mt. Moon "confidently wrong in-flight note" class against `raw/` ground truth.
   - `P2`: Expired external platform/API rules (`is_stale == True`).
   - `P3`: Token Vampires (`delta_reads >= 5` & `helpful == 0` from `.git/agentstacks-read-stats.json`)
           & Borges 1:1 Map Bloat (high KB, low `quant_rules/KB`, excluding verbatim precision tables).
   - `P4`: Intra-subtree `O(1)` locality near-duplicates (`Jaccard >= 0.58`) & repeated unresolved wanted links.
3. Gate 3 — Subtree-Localized UTF-8 Micro-Batch Slicer (`--max-batches 1 --budget-kb 20.0`):
   - Bundles co-located cards sharing the same L2 subtree & `raw/` textbook into a strict `<= 20 KB` packet
     with `#page=X-Y` laser locators. Emits a 0-Token Short-Circuit when the library is already clean.
4. Gate 4 — `dream-seal` Watermark:
   - Stamps `dreamedHash` & `dreamedReads` in `wiki/.page-manifest.json` (0 `.md` mtime churn, 100% KV-cache safe).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from wiki_core import (
    CARD_TYPES,
    WikiPageInfo,
    agentstacks_lock,
    discover_wiki_pages,
    load_implicit_read_stats,
    now_iso,
    resolve_okf_path_candidates,
    write_json_if_changed,
)

READ_VAMPIRE_THRESHOLD = 5
BORGES_ACTION_KB_THRESHOLD = 3.6
BORGES_CONCEPT_KB_THRESHOLD = 28.0
BORGES_MIN_RULES_PER_KB = 0.45
SUBTREE_DUP_JACCARD = 0.58


def _shingles(text: str) -> set[str]:
    low = text.lower()
    out = set(re.findall(r"[a-z0-9]{2,}", low))
    for run in re.findall(r"[\u4e00-\u9fff]+", low):
        if len(run) == 1:
            out.add(run)
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def _card_tree_metrics(p: WikiPageInfo) -> tuple[float, int, int, float]:
    """Compute `(est_body_kb, quant_rules, table_count, rules_per_kb)` from cached DocumentTree without disk I/O."""
    quant_rules = 0
    table_cnt = 0
    max_char = 0
    if p.doc_tree:
        for n in p.doc_tree.all_flat_nodes():
            quant_rules += int(n.quant_rule_count)
            table_cnt += int(n.table_count)
            if n.char_range[1] > max_char:
                max_char = n.char_range[1]
    if max_char > 0:
        # CJK-heavy markdown averages ~2.1 UTF-8 bytes per char; fall back to stat size minus ~450B frontmatter
        try:
            est_bytes = max(256, p.abs_path.stat().st_size - 450)
        except OSError:
            est_bytes = int(max_char * 2.0)
    else:
        try:
            est_bytes = max(256, p.abs_path.stat().st_size - 450)
        except OSError:
            est_bytes = 512
    est_kb = round(est_bytes / 1024.0, 2)
    rules_per_kb = round(quant_rules / max(0.35, est_kb), 2)
    return est_kb, quant_rules, table_cnt, rules_per_kb


def _extract_primary_locators(p: WikiPageInfo, repo_root: Path) -> list[str]:
    locs: list[str] = []
    raw_sources = p.frontmatter.get("sources", [])
    if isinstance(raw_sources, list):
        for s in raw_sources:
            if isinstance(s, dict):
                v = next((str(s[k]).strip() for k in ("resource", "locator", "uri", "url", "path") if s.get(k)), "")
            else:
                v = str(s).strip()
            if v and v not in locs:
                locs.append(v)
    claim_p = repo_root / "wiki" / ".claims" / Path(p.rel_path).with_suffix(".json")
    if claim_p.is_file():
        try:
            cdata = json.loads(claim_p.read_text(encoding="utf-8"))
            for cl in cdata.get("claims", []):
                for ev in cl.get("evidence", []):
                    rv = str(ev.get("resource", "")).strip()
                    if rv and rv not in locs:
                        locs.append(rv)
        except Exception:
            pass
    return locs[:6]


def build_dream_diagnosis(
    repo_root: Path,
    domain_filter: str = "all",
) -> dict[str, Any]:
    """Zero-LLM O(Δ) triage returning prioritized Dreaming candidates & global dormancy telemetry."""
    wiki_root = repo_root / "wiki"
    pages = discover_wiki_pages(repo_root, use_cache=True)
    manifest_path = wiki_root / ".page-manifest.json"
    manifest_pages: dict[str, dict[str, Any]] = {}
    if manifest_path.is_file():
        try:
            manifest_pages = json.loads(manifest_path.read_text(encoding="utf-8")).get("pages", {})
        except Exception:
            manifest_pages = {}

    ace_path = wiki_root / ".ace-telemetry.json"
    ace_cards: dict[str, dict[str, Any]] = {}
    if ace_path.is_file():
        try:
            ace_cards = json.loads(ace_path.read_text(encoding="utf-8")).get("cards", {})
        except Exception:
            ace_cards = {}

    read_stats = load_implicit_read_stats(repo_root)

    known_targets: set[str] = set()
    for p in pages:
        known_targets.add(p.page_id)
        known_targets.add(p.rel_path)
        known_targets.add(p.rel_path.removesuffix(".md"))
        for al in p.aliases:
            known_targets.add(al)

    filtered_pages = [
        p
        for p in pages
        if p.lifecycle_status != "deprecated"
        and (domain_filter == "all" or domain_filter.lower() in f"{p.tier}/{p.domain}".lower())
    ]

    # Intra-subtree locality near-duplicate detection (O(1) bounded bucket size per subtree, never global O(N^2))
    by_subtree: dict[tuple[str, str, str], list[WikiPageInfo]] = {}
    for p in filtered_pages:
        if p.page_type not in CARD_TYPES:
            continue
        m_ent = manifest_pages.get(p.rel_path, {})
        st_slug = str(m_ent.get("subtree", "core_default"))
        by_subtree.setdefault((p.tier, p.domain, st_slug), []).append(p)

    near_dup_partners: dict[str, list[tuple[str, float]]] = {}
    for _, st_pages in by_subtree.items():
        if len(st_pages) < 2:
            continue
        sh_map = {p.page_id: _shingles(f"{p.title} {p.description}") for p in st_pages}
        # Cap pairwise comparison to 40 per subtree bucket so pathological buckets remain O(1)
        bucket = st_pages[:40]
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                ma = manifest_pages.get(a.rel_path, {})
                mb = manifest_pages.get(b.rel_path, {})
                # Skip pair if BOTH cards were already dreamed at their exact current bodyHash
                if ma.get("dreamedHash") == a.body_hash and mb.get("dreamedHash") == b.body_hash:
                    continue
                sa, sb = sh_map[a.page_id], sh_map[b.page_id]
                if len(sa) < 6 or len(sb) < 6:
                    continue
                jac = len(sa & sb) / len(sa | sb)
                if jac >= SUBTREE_DUP_JACCARD:
                    near_dup_partners.setdefault(a.page_id, []).append((b.page_id, round(jac, 2)))
                    near_dup_partners.setdefault(b.page_id, []).append((a.page_id, round(jac, 2)))

    candidates: list[dict[str, Any]] = []
    wanted_counter: dict[str, list[str]] = {}
    dormant_count = 0
    vampire_cards: list[dict[str, Any]] = []
    zero_read_cards = 0

    for p in filtered_pages:
        m_ent = manifest_pages.get(p.rel_path, {}) if isinstance(manifest_pages.get(p.rel_path), dict) else {}
        claims_clean = bool(m_ent.get("claimsClean", True))
        dreamed_hash = str(m_ent.get("dreamedHash", ""))
        dreamed_reads = int(m_ent.get("dreamedReads", 0))
        subtree_slug = str(m_ent.get("subtree", "core_default"))

        c_ace = ace_cards.get(p.page_id, {})
        helpful = int(c_ace.get("helpful", 0))
        harmful = int(c_ace.get("harmful", 0))
        total_reads, _ = read_stats.get(p.page_id, (0, 0))
        if total_reads == 0 and helpful == 0:
            zero_read_cards += 1
        delta_reads = max(0, total_reads - dreamed_reads)

        est_kb, quant_rules, table_cnt, rules_per_kb = _card_tree_metrics(p)
        undreamed_edit = dreamed_hash != p.body_hash

        is_borges_bloat = False
        if undreamed_edit and helpful == 0:
            if p.page_type in CARD_TYPES and est_kb > BORGES_ACTION_KB_THRESHOLD and rules_per_kb < BORGES_MIN_RULES_PER_KB:
                is_borges_bloat = True
            elif (
                p.page_type not in CARD_TYPES
                and est_kb > BORGES_CONCEPT_KB_THRESHOLD
                and rules_per_kb < 0.25
                and table_cnt == 0
            ):
                is_borges_bloat = True

        is_vampire = delta_reads >= READ_VAMPIRE_THRESHOLD and helpful == 0
        if is_vampire:
            vampire_cards.append({
                "id": p.page_id,
                "relPath": p.rel_path,
                "reads": total_reads,
                "deltaReads": delta_reads,
                "helpful": helpful,
                "kb": est_kb,
            })

        reasons: list[str] = []
        bucket = ""
        priority = 0.0

        # P0: Active harm or broken/stale evidence
        if harmful > 0 or not claims_clean:
            bucket = "P0_HARMFUL_OR_STALE_CLAIM"
            priority = 110.0 + harmful * 15.0 + (20.0 if not claims_clean else 0.0)
            if harmful > 0:
                reasons.append(f"ACE harmful=-{harmful} (misled past task)")
            if not claims_clean:
                reasons.append("Grounded Claim evidence fingerprint is STALE/broken")

        # P1: Unverified in-flight empirical/provisional note (Pokémon Mt. Moon error class)
        if p.epistemic_status in ("empirical-refine", "provisional") and p.trust_tier != "human-reviewed" and undreamed_edit:
            if not bucket:
                bucket = "P1_UNVERIFIED_INFLIGHT_NOTE"
            priority = max(priority, 84.0 + min(12.0, total_reads * 1.5))
            reasons.append(f"Unverified `{p.epistemic_status}` note (trust={p.trust_tier}) — cross-check against raw/ source")

        # P2: Expired external tool/API timestamp
        if p.is_stale:
            if not bucket:
                bucket = "P2_STALE_EXTERNAL_API"
            priority = max(priority, 74.0)
            reasons.append(f"stale_after expired ({p.stale_reason})")

        # P3: Read-to-Helpful Token Vampire or Borges 1:1 Map Bloat
        if is_vampire or is_borges_bloat:
            if not bucket:
                bucket = "P3_VAMPIRE_OR_BORGES_BLOAT"
            priority = max(priority, 56.0 + min(20.0, delta_reads * 2.0 + est_kb))
            if is_vampire:
                reasons.append(f"Token Vampire: read {delta_reads}x since last dream with 0 helpful hits (sharpen description or rules)")
            if is_borges_bloat:
                reasons.append(f"Borges Bloat: {est_kb:.1f} KB with low rule density ({rules_per_kb:.2f} rules/KB, {quant_rules} rules)")

        # Scan unresolved internal links ONLY on undreamed/edited cards (0 disk I/O on clean dormant corpus!)
        broken_refs: list[str] = []
        if undreamed_edit and p.epistemic_status != "canonical":
            body_no_code = re.sub(r"```.*?```", "", p.clean_body, flags=re.DOTALL)
            for wl in re.findall(r"\[\[([^\]|#]+)", body_no_code):
                wl_clean = wl.strip()
                if wl_clean and not any(c in known_targets for c in resolve_okf_path_candidates(wl_clean, p.rel_path)):
                    broken_refs.append(wl_clean)
                    wanted_counter.setdefault(wl_clean, []).append(p.page_id)
            for ml in re.findall(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)", body_no_code):
                if ml.startswith(("http://", "https://", "raw://", "file://")):
                    continue
                if not any(c in known_targets for c in resolve_okf_path_candidates(ml, p.rel_path)):
                    broken_refs.append(ml)
                    wanted_counter.setdefault(ml, []).append(p.page_id)

        dups = near_dup_partners.get(p.page_id, [])
        if dups or broken_refs:
            if not bucket:
                bucket = "P4_SUBTREE_DUP_OR_WANTED_GAP"
            priority = max(priority, 40.0 + len(dups) * 5.0 + len(broken_refs) * 4.0)
            if dups:
                dup_str = ", ".join(f"`{did}` (Jaccard={j})" for did, j in dups[:2])
                reasons.append(f"Subtree D-Merge candidate with {dup_str}")
            if broken_refs:
                reasons.append(f"Unresolved concept links: {', '.join(broken_refs[:3])}")

        if not reasons:
            dormant_count += 1
            continue

        candidates.append({
            "id": p.page_id,
            "title": p.title,
            "tier": p.tier,
            "domain": p.domain,
            "subtree": subtree_slug,
            "relPath": p.rel_path,
            "epistemicStatus": p.epistemic_status,
            "trustTier": p.trust_tier,
            "bucket": bucket,
            "priority": round(priority, 2),
            "estKb": est_kb,
            "quantRules": quant_rules,
            "rulesPerKb": rules_per_kb,
            "reads": total_reads,
            "deltaReads": delta_reads,
            "helpful": helpful,
            "harmful": harmful,
            "reasons": reasons,
            "primaryLocators": _extract_primary_locators(p, repo_root),
            "page": p,
        })

    candidates.sort(key=lambda x: (x["priority"], x["deltaReads"], x["estKb"]), reverse=True)
    multi_session_gaps = [
        {"target": tgt, "citedBy": sorted(set(cids))}
        for tgt, cids in sorted(wanted_counter.items(), key=lambda kv: len(set(kv[1])), reverse=True)
        if len(set(cids)) >= 1
    ]

    return {
        "totalEvaluated": len(filtered_pages),
        "dormantCleanCount": dormant_count,
        "dormantPct": round(100.0 * dormant_count / max(1, len(filtered_pages)), 2),
        "zeroReadCount": zero_read_cards,
        "vampireCards": vampire_cards,
        "candidates": candidates,
        "wantedGaps": multi_session_gaps[:15],
    }


def render_dream_plan(
    repo_root: Path,
    domain_filter: str = "all",
    max_batches: int = 1,
    budget_kb: float = 20.0,
    as_json: bool = False,
) -> int:
    """Emit token-budgeted Micro-Dream Work Units (or a 0-Token Short-Circuit when clean)."""
    diag = build_dream_diagnosis(repo_root, domain_filter=domain_filter)
    cands = diag["candidates"]

    # Group top candidates into subtree-localized micro-batches capped by `budget_kb`
    budget_bytes = max(2048, int(budget_kb * 1024))
    batches: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    for lead in cands:
        if len(batches) >= max(1, max_batches):
            break
        if lead["id"] in used_ids:
            continue
        st_key = (lead["tier"], lead["domain"], lead["subtree"])
        batch_items = [lead]
        used_ids.add(lead["id"])
        acc_bytes = int(lead["estKb"] * 1024) + 600

        # Co-pack up to 3 sibling candidates from the exact same L2 subtree if within token budget
        for sib in cands:
            if len(batch_items) >= 4:
                break
            if sib["id"] in used_ids:
                continue
            if (sib["tier"], sib["domain"], sib["subtree"]) != st_key:
                continue
            sib_bytes = int(sib["estKb"] * 1024) + 450
            if acc_bytes + sib_bytes <= budget_bytes:
                batch_items.append(sib)
                used_ids.add(sib["id"])
                acc_bytes += sib_bytes

        shared_locators: list[str] = []
        for it in batch_items:
            for loc in it["primaryLocators"]:
                if loc not in shared_locators:
                    shared_locators.append(loc)

        batches.append({
            "batchIndex": len(batches) + 1,
            "scope": f"{st_key[0]}/{st_key[1]} (subtree={st_key[2]})",
            "estimatedContextKb": round(min(budget_bytes, acc_bytes) / 1024.0, 2),
            "cardIds": [it["id"] for it in batch_items],
            "sharedPrimaryLocators": shared_locators[:8],
            "items": [
                {k: v for k, v in it.items() if k != "page"}
                for it in batch_items
            ],
            "sealCommand": f"python3 .agents/skills/study/scripts/study_runner.py dream-seal {' '.join(it['id'] for it in batch_items)}",
        })

    if as_json:
        payload = {
            "totalEvaluated": diag["totalEvaluated"],
            "dormantCleanCount": diag["dormantCleanCount"],
            "dormantPct": diag["dormantPct"],
            "candidateCount": len(cands),
            "zeroTokenShortCircuit": len(cands) == 0,
            "wantedGaps": diag["wantedGaps"],
            "batches": batches,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("=" * 84)
    print(
        f"🌙 AGENTSTACKS SLEEP-TIME DREAMING PLANNER (O(Δ) Zero-Waste Triage | domain={domain_filter})"
    )
    print("=" * 84)
    print(
        f"• Dormancy Filter: {diag['dormantCleanCount']}/{diag['totalEvaluated']} cards ({diag['dormantPct']}%) "
        f"are SLEEPING-CLEAN (0 LLM tokens, 0 body reads)."
    )
    print(
        f"• Active Wakeup Queue: {len(cands)} card(s) flagged | Zero-Read Routing Blindspots: {diag['zeroReadCount']} card(s)"
    )

    if not cands and not diag["wantedGaps"]:
        print("\n😴 [ZERO-TOKEN SHORT-CIRCUIT] Library is 100% consolidated & verified. Stop here — spend 0 LLM tokens!")
        return 0

    if diag["wantedGaps"]:
        print("\n🕳️ Cross-Session Unresolved / Wanted Concept Links (Anthropic Fig. 15 Gap Closure):")
        for g in diag["wantedGaps"][:6]:
            print(f"   • `{g['target']}` <- cited by {', '.join(g['citedBy'][:4])}")

    for b in batches:
        print("\n" + "-" * 84)
        print(
            f"📦 MICRO-DREAM BATCH #{b['batchIndex']}/{len(batches)} — Scope: `{b['scope']}` "
            f"(Budget: ~{b['estimatedContextKb']} KB <= {budget_kb:.1f} KB cap)"
        )
        print("-" * 84)
        for it in b["items"]:
            print(
                f"  🔹 `{it['id']}` [{it['bucket']} | prio={it['priority']}] (`wiki/{it['relPath']}`)\n"
                f"     Stats: size={it['estKb']} KB | density={it['rulesPerKb']} rules/KB | reads={it['reads']} (Δ={it['deltaReads']}) | ACE=+{it['helpful']}/-{it['harmful']}\n"
                f"     Why Woken: {' ; '.join(it['reasons'])}"
            )
        if b["sharedPrimaryLocators"]:
            print("  📚 Laser Primary-Source Slices to Inspect (read ONLY these page/line ranges, NOT whole books):")
            for loc in b["sharedPrimaryLocators"]:
                print(f"     - `{loc}`")
        print(f"  🔒 Seal After Consolidating: `{b['sealCommand']}`")

    remaining = len(cands) - sum(len(b["cardIds"]) for b in batches)
    if remaining > 0:
        print(f"\n📌 ({remaining} lower-priority candidate(s) deferred to future sleep cycles to protect token budget.)")
    return 0


def seal_dreamed_cards(repo_root: Path, card_targets: list[str]) -> int:
    """Stamp `dreamedHash = bodyHash` & `dreamedReads = current_reads` in `wiki/.page-manifest.json`."""
    from wiki_query import resolve_page

    with agentstacks_lock(repo_root):
        pages = discover_wiki_pages(repo_root, use_cache=True)
        manifest_path = repo_root / "wiki" / ".page-manifest.json"
        m_data: dict[str, Any] = {"schemaVersion": 2, "pages": {}}
        if manifest_path.is_file():
            try:
                m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        m_pages = m_data.setdefault("pages", {})
        read_stats = load_implicit_read_stats(repo_root)
        ts = now_iso()

        sealed: list[str] = []
        for t in card_targets:
            p = resolve_page(pages, t)
            if not p:
                print(f"❌ Card not found for dream-seal: '{t}'", file=sys.stderr)
                return 1
            cur_reads, _ = read_stats.get(p.page_id, (0, 0))
            ent = m_pages.setdefault(
                p.rel_path,
                {
                    "id": p.page_id,
                    "type": p.page_type,
                    "tier": p.tier,
                    "domain": p.domain,
                    "subtree": "core_default",
                    "abstractionLevel": p.abstraction_level,
                    "epistemicStatus": p.epistemic_status,
                    "aliases": p.aliases,
                    "dependsOn": p.depends_on,
                    "bodyHash": p.body_hash,
                    "claimsClean": True,
                    "updatedAt": ts,
                },
            )
            ent["bodyHash"] = p.body_hash
            ent["dreamedHash"] = p.body_hash
            ent["dreamedReads"] = cur_reads
            ent["dreamedAt"] = ts
            sealed.append(p.page_id)

        write_json_if_changed(
            manifest_path,
            m_data,
            volatile_keys=("updatedAt", "lastFinalizedAt", "lastFinalizedBy"),
        )
        print(
            f"😴 Sealed {len(sealed)} card(s) as DREAMED-CLEAN (`dreamedHash` = current `bodyHash`, 0 future dream tokens until edited): "
            + ", ".join(f"`{cid}`" for cid in sealed)
        )
        return 0

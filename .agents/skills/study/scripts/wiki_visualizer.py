#!/usr/bin/env python3
"""Zero-Backend, Zero-CDN Single-File Interactive Knowledge Graph Visualizer (`viz.html`).

Inspired by OKF's `visualize` tool, upgraded for AgentStacks (500–2,000+ cards, offline air-gapped ready):
- Pure HTML5 2D Canvas force-clustered constellation graph (zero external CDN dependencies).
- Visualizes 3 Orthogonal Tiers (`foundations` / `tools_craft` / `workflows`), L2 Subtrees, ACE Hotness,
  OKF v0.3 Trust Tiers (`human-reviewed` / `machine-confirmed` / `unverified`), `stale_after` warnings,
  `depends_on` directed edges, cross-domain bridges, and computed Backlinks (`Cited by`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from wiki_core import find_repo_root, now_iso, wiki_rust_scripts
from wiki_search_engine import load_live_synced_search_corpus


def build_visualization_payload(repo_root: Path, domain_filter: str | None = None) -> dict[str, Any]:
    """Assemble compact graph payload from live-synced AgentStacks corpus."""
    corpus = load_live_synced_search_corpus(repo_root)
    all_cards: list[dict[str, Any]] = corpus.get("cards", [])
    subtrees: list[dict[str, Any]] = corpus.get("subtrees", [])

    if domain_filter and domain_filter != "all":
        df_l = domain_filter.lower()
        cards = [
            c
            for c in all_cards
            if df_l == str(c.get("domain", "")).lower()
            or df_l in f"{c.get('tier', '')}/{c.get('domain', '')}".lower()
        ]
    else:
        cards = list(all_cards)

    card_ids = {c["id"] for c in cards}
    id_or_path_to_id: dict[str, str] = {}
    for c in all_cards:
        cid = c["id"]
        id_or_path_to_id[cid] = cid
        rel = str(c.get("relPath", ""))
        if rel:
            id_or_path_to_id[rel] = cid
            if rel.endswith(".md"):
                id_or_path_to_id[rel[:-3]] = cid
        for al in c.get("aliases", []):
            id_or_path_to_id.setdefault(str(al), cid)

    backlinks: dict[str, list[str]] = {c["id"]: [] for c in cards}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    card_by_id = {c["id"]: c for c in cards}

    for c in cards:
        src_id = c["id"]
        src_dom = c.get("domain", "")
        for raw_dep in c.get("dependsOn", []):
            dep_clean = str(raw_dep).split("#", 1)[0].strip()
            dst_id = id_or_path_to_id.get(dep_clean, dep_clean)
            if dst_id in card_ids and dst_id != src_id:
                ekey = (src_id, dst_id, "depends_on")
                if ekey not in seen_edges:
                    seen_edges.add(ekey)
                    dst_dom = card_by_id[dst_id].get("domain", "")
                    edges.append({
                        "source": src_id,
                        "target": dst_id,
                        "kind": "cross_domain" if src_dom != dst_dom else "depends_on",
                    })
                if src_id not in backlinks[dst_id]:
                    backlinks[dst_id].append(src_id)

        for we in c.get("weakEdges", [])[:4]:
            dst_id = str(we.get("target", ""))
            if dst_id in card_ids and dst_id != src_id:
                a, b = (src_id, dst_id) if src_id < dst_id else (dst_id, src_id)
                ekey = (a, b, "weak")
                if ekey not in seen_edges and (src_id, dst_id, "depends_on") not in seen_edges and (dst_id, src_id, "depends_on") not in seen_edges:
                    seen_edges.add(ekey)
                    edges.append({
                        "source": src_id,
                        "target": dst_id,
                        "kind": "weak",
                    })

    nodes: list[dict[str, Any]] = []
    domains_seen: set[str] = set()
    for c in cards:
        cid = c["id"]
        dom = str(c.get("domain", ""))
        domains_seen.add(dom)
        raw_sources = c.get("sources", [])
        fmt_sources: list[str] = []
        if isinstance(raw_sources, list):
            for s in raw_sources[:8]:
                if isinstance(s, dict):
                    sid = f"[{s['id']}] " if s.get("id") else ""
                    ttl = f" ({s['title']})" if s.get("title") else ""
                    res_loc = next((str(s[k]).strip() for k in ("resource", "locator", "uri", "url", "path") if s.get(k)), "")
                    fmt_sources.append(f"{sid}{res_loc}{ttl}")
                else:
                    fmt_sources.append(str(s))

        constraints_fmt: list[str] = []
        for cs in c.get("constraints", [])[:5]:
            if isinstance(cs, dict):
                d = str(cs.get("description", "")).strip()
                if d:
                    constraints_fmt.append(d)
            elif isinstance(cs, str) and cs.strip():
                constraints_fmt.append(cs.strip())

        deps_resolved = []
        for raw_dep in c.get("dependsOn", []):
            dep_clean = str(raw_dep).split("#", 1)[0].strip()
            dst_id = id_or_path_to_id.get(dep_clean, dep_clean)
            if dst_id in card_ids and dst_id not in deps_resolved:
                deps_resolved.append(dst_id)

        nodes.append({
            "id": cid,
            "title": c.get("title", cid),
            "tier": c.get("tier", "foundations"),
            "domain": dom,
            "subtree": c.get("subtree", "core_default"),
            "subdir": c.get("subdir", ""),
            "relPath": c.get("relPath", ""),
            "type": c.get("type", "craft-card"),
            "epistemicStatus": c.get("epistemicStatus", c.get("status", "canonical")),
            "lifecycleStatus": c.get("lifecycleStatus", "stable"),
            "supersededBy": c.get("supersededBy", ""),
            "staleAfter": c.get("staleAfter", ""),
            "isStale": bool(c.get("isStale", False)),
            "trustTier": c.get("trustTier", "unverified"),
            "description": c.get("description", ""),
            "tags": c.get("tags", []),
            "patterns": c.get("patterns", [])[:5],
            "constraints": constraints_fmt,
            "scripts": [f"{s.get('role', 'script')}: {s.get('path', '')}" for s in c.get("scripts", [])],
            "sources": fmt_sources,
            "aceHelpful": int(c.get("aceHelpful", 0)),
            "aceHarmful": int(c.get("aceHarmful", 0)),
            "dependsOn": deps_resolved,
            "citedBy": backlinks.get(cid, []),
        })

    rs_count = len(wiki_rust_scripts(repo_root / "wiki")) if (repo_root / "wiki").is_dir() else 0
    return {
        "okfVersion": "0.3",
        "generatedAt": now_iso(),
        "repoName": repo_root.name,
        "stats": {
            "totalCards": len(nodes),
            "totalEdges": len(edges),
            "domains": sorted(domains_seen),
            "subtrees": len(subtrees),
            "rustScripts": rs_count,
            "humanReviewed": sum(1 for n in nodes if n["trustTier"] == "human-reviewed"),
            "machineConfirmed": sum(1 for n in nodes if n["trustTier"] == "machine-confirmed"),
            "unverified": sum(1 for n in nodes if n["trustTier"] == "unverified"),
            "stale": sum(1 for n in nodes if n["isStale"]),
            "deprecated": sum(1 for n in nodes if n["lifecycleStatus"] == "deprecated"),
        },
        "nodes": nodes,
        "edges": edges,
    }


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AgentStacks Knowledge Star-Map (OKF v0.3)</title>
<style>
  :root {
    --bg: #0b1020;
    --panel: rgba(16, 23, 42, 0.94);
    --border: rgba(148, 163, 184, 0.22);
    --text: #e2e8f0;
    --muted: #94a3b8;
    --foundations: #38bdf8;
    --tools: #a78bfa;
    --workflows: #34d399;
    --gold: #fbbf24;
    --danger: #f87171;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: radial-gradient(circle at 30% 20%, #131c36 0%, #070b16 100%);
    color: var(--text);
    height: 100vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  header {
    padding: 10px 16px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-wrap: wrap;
    gap: 10px 14px;
    align-items: center;
    justify-content: space-between;
    z-index: 10;
  }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 15px; }
  .badge-okf {
    font-size: 11px; padding: 2px 7px; border-radius: 999px;
    background: rgba(56, 189, 248, 0.16); color: var(--foundations); border: 1px solid rgba(56, 189, 248, 0.4);
  }
  .kpis { display: flex; gap: 12px; font-size: 12px; color: var(--muted); flex-wrap: wrap; }
  .kpis strong { color: var(--text); }
  .controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  input, select, button {
    background: #0f172a; color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 9px; font-size: 12px; outline: none;
  }
  input:focus, select:focus { border-color: var(--foundations); }
  button { cursor: pointer; transition: background 0.15s; }
  button:hover { background: #1e293b; }
  main { flex: 1; position: relative; display: flex; min-height: 0; }
  #graph-wrap { flex: 1; position: relative; overflow: hidden; }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  canvas:active { cursor: grabbing; }
  .legend {
    position: absolute; left: 14px; bottom: 14px; background: var(--panel);
    border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
    font-size: 11px; color: var(--muted); display: flex; flex-direction: column; gap: 5px;
    pointer-events: none;
  }
  .leg-row { display: flex; align-items: center; gap: 7px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
  #drawer {
    width: 390px; max-width: 45vw; background: var(--panel);
    border-left: 1px solid var(--border); padding: 16px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 12px;
  }
  #drawer h2 { font-size: 16px; line-height: 1.35; color: #f8fafc; }
  .meta-tags { display: flex; flex-wrap: wrap; gap: 6px; }
  .pill {
    font-size: 11px; padding: 2px 8px; border-radius: 999px;
    background: rgba(148, 163, 184, 0.14); color: var(--text);
  }
  .pill.human { background: rgba(52, 211, 153, 0.2); color: #6ee7b7; border: 1px solid rgba(52, 211, 153, 0.45); }
  .pill.machine { background: rgba(56, 189, 248, 0.16); color: #7dd3fc; }
  .pill.stale, .pill.deprecated { background: rgba(248, 113, 113, 0.2); color: #fca5a5; border: 1px solid rgba(248, 113, 113, 0.45); }
  .sec-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); margin-bottom: 4px; }
  .box { background: rgba(15, 23, 42, 0.72); border: 1px solid var(--border); border-radius: 7px; padding: 9px 11px; font-size: 12px; line-height: 1.5; }
  .box ul { padding-left: 16px; display: flex; flex-direction: column; gap: 4px; }
  .link-chip {
    display: inline-block; margin: 2px 4px 2px 0; padding: 2px 7px; border-radius: 5px;
    background: rgba(56, 189, 248, 0.13); color: #7dd3fc; font-size: 11px; cursor: pointer;
    border: 1px solid rgba(56, 189, 248, 0.28);
  }
  .link-chip:hover { background: rgba(56, 189, 248, 0.26); }
  .empty-hint { color: var(--muted); font-size: 13px; line-height: 1.6; margin-top: 24px; text-align: center; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span>📚 AgentStacks 书栈星图</span>
    <span class="badge-okf" id="okf-badge">OKF v0.3</span>
    <div class="kpis" id="kpi-bar"></div>
  </div>
  <div class="controls">
    <input type="search" id="q" placeholder="搜索 ID / 标题 / 标签 / 边界…" style="width:200px">
    <select id="tier-sel">
      <option value="all">全部分馆 (All Tiers)</option>
      <option value="foundations">Tier 1 · Foundations</option>
      <option value="tools_craft">Tier 2 · Tools Craft</option>
      <option value="workflows">Tier 3 · Workflows</option>
    </select>
    <select id="dom-sel"><option value="all">全部学科 (All Domains)</option></select>
    <select id="trust-sel">
      <option value="all">全部状态 (All Cards)</option>
      <option value="human-reviewed">👤 Human-Reviewed</option>
      <option value="machine-confirmed">🤖 Machine-Confirmed</option>
      <option value="provisional">⚠️ Provisional 二手碎片</option>
      <option value="stale">⏰ Stale 待复核</option>
      <option value="deprecated">⛔ Deprecated</option>
      <option value="orphan">🏝️ 零连接孤岛卡</option>
    </select>
    <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:4px;cursor:pointer">
      <input type="checkbox" id="weak-chk"> 弱共现边
    </label>
    <button id="reset-btn">重置视图</button>
  </div>
</header>
<main>
  <div id="graph-wrap">
    <canvas id="cv"></canvas>
    <div class="legend">
      <div class="leg-row"><span class="dot" style="background:#38bdf8"></span> Tier 1 · Foundations (底层公理)</div>
      <div class="leg-row"><span class="dot" style="background:#a78bfa"></span> Tier 2 · Tools Craft (软件手艺)</div>
      <div class="leg-row"><span class="dot" style="background:#34d399"></span> Tier 3 · Workflows (复合管线)</div>
      <div class="leg-row"><span class="dot" style="background:#fbbf24"></span> 跨领域桥接边 / Human-Reviewed 金环</div>
      <div class="leg-row">滚轮缩放 · 拖拽平移 · 点击节点查看契约与反链</div>
    </div>
  </div>
  <aside id="drawer">
    <div class="empty-hint" id="empty-msg">
      点击左侧任意知识卡节点，<br>检视其 <strong>OKF v0.3 信任等级</strong>、定量规则摘要、<br>绑定 Rust 脚本、一手教材溯源与正反向引用链。
    </div>
    <div id="detail" style="display:none;flex-direction:column;gap:11px"></div>
  </aside>
</main>
<script id="graph-data" type="application/json">__GRAPH_JSON__</script>
<script>
(() => {
  const DATA = JSON.parse(document.getElementById('graph-data').textContent);
  const st = DATA.stats;
  document.getElementById('kpi-bar').innerHTML =
    `<span>卡片 <strong>${st.totalCards}</strong></span>` +
    `<span>学科 <strong>${st.domains.length}</strong></span>` +
    `<span>L2子树 <strong>${st.subtrees}</strong></span>` +
    `<span>Rust脚本 <strong>${st.rustScripts}</strong></span>` +
    `<span>👤亲审 <strong>${st.humanReviewed}</strong></span>` +
    `<span>🤖机审 <strong>${st.machineConfirmed}</strong></span>` +
    (st.stale ? `<span style="color:#fca5a5">⏰待复核 <strong>${st.stale}</strong></span>` : '');

  const domSel = document.getElementById('dom-sel');
  st.domains.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d; opt.textContent = d;
    domSel.appendChild(opt);
  });

  const TIER_COLOR = { foundations: '#38bdf8', tools_craft: '#a78bfa', workflows: '#34d399' };
  const domAngle = {};
  st.domains.forEach((d, i) => { domAngle[d] = (2 * Math.PI * i) / Math.max(1, st.domains.length); });

  const nodeMap = new Map();
  const nodes = DATA.nodes.map((n, idx) => {
    const ang = domAngle[n.domain] || 0;
    const ring = n.tier === 'foundations' ? 210 : (n.tier === 'tools_craft' ? 360 : 470);
    const jitterA = ((idx * 137.5) % 360) * (Math.PI / 180) * 0.14;
    const jitterR = ((idx * 53) % 90) - 45;
    const deg = (n.dependsOn?.length || 0) + (n.citedBy?.length || 0);
    const r = Math.min(14, 4.8 + Math.sqrt(Math.max(0, n.aceHelpful) * 2 + deg * 0.9));
    const obj = {
      ...n,
      x: Math.cos(ang + jitterA) * (ring + jitterR),
      y: Math.sin(ang + jitterA) * (ring + jitterR),
      vx: 0, vy: 0, r, deg, visible: true
    };
    nodeMap.set(n.id, obj);
    return obj;
  });

  const edges = DATA.edges.map(e => ({
    source: nodeMap.get(e.source),
    target: nodeMap.get(e.target),
    kind: e.kind
  })).filter(e => e.source && e.target);

  const cv = document.getElementById('cv');
  const ctx = cv.getContext('2d');
  let W = 800, H = 600, dpr = window.devicePixelRatio || 1;
  let camX = 0, camY = 0, camK = 0.85;
  let hovered = null, selected = null, dragging = null, panning = false, lastMX = 0, lastMY = 0;
  let alpha = 1.0;

  function resize() {
    const rect = cv.parentElement.getBoundingClientRect();
    W = rect.width; H = rect.height;
    cv.width = W * dpr; cv.height = H * dpr;
    draw();
  }
  window.addEventListener('resize', resize);

  function applyFilters() {
    const q = document.getElementById('q').value.trim().toLowerCase();
    const tier = document.getElementById('tier-sel').value;
    const dom = domSel.value;
    const trust = document.getElementById('trust-sel').value;
    for (const n of nodes) {
      let ok = true;
      if (tier !== 'all' && n.tier !== tier) ok = false;
      if (dom !== 'all' && n.domain !== dom) ok = false;
      if (trust === 'human-reviewed' && n.trustTier !== 'human-reviewed') ok = false;
      else if (trust === 'machine-confirmed' && n.trustTier !== 'machine-confirmed') ok = false;
      else if (trust === 'provisional' && n.epistemicStatus !== 'provisional') ok = false;
      else if (trust === 'stale' && !n.isStale) ok = false;
      else if (trust === 'deprecated' && n.lifecycleStatus !== 'deprecated') ok = false;
      else if (trust === 'orphan' && n.deg > 0) ok = false;
      if (ok && q) {
        const hay = `${n.id} ${n.title} ${n.description} ${(n.tags || []).join(' ')}`.toLowerCase();
        if (!hay.includes(q)) ok = false;
      }
      n.visible = ok;
    }
    alpha = Math.max(alpha, 0.25);
    draw();
  }

  ['q', 'tier-sel', 'dom-sel', 'trust-sel', 'weak-chk'].forEach(id => {
    document.getElementById(id).addEventListener('input', applyFilters);
    document.getElementById(id).addEventListener('change', applyFilters);
  });
  document.getElementById('reset-btn').addEventListener('click', () => {
    camX = 0; camY = 0; camK = 0.85; selected = null;
    document.getElementById('detail').style.display = 'none';
    document.getElementById('empty-msg').style.display = 'block';
    draw();
  });

  function stepPhysics() {
    if (alpha < 0.004) return;
    const vis = nodes.filter(n => n.visible);
    const nLen = vis.length;
    for (let i = 0; i < nLen; i++) {
      const a = vis[i];
      const ang = domAngle[a.domain] || 0;
      const targetR = a.tier === 'foundations' ? 200 : (a.tier === 'tools_craft' ? 355 : 465);
      a.vx += (Math.cos(ang) * targetR - a.x) * 0.012 * alpha;
      a.vy += (Math.sin(ang) * targetR - a.y) * 0.012 * alpha;
    }
    const stride = nLen > 450 ? 2 : 1;
    for (let i = 0; i < nLen; i++) {
      const a = vis[i];
      for (let j = i + 1; j < nLen; j += stride) {
        const b = vis[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy + 1;
        if (d2 > 22000) continue;
        const f = (420 * stride * alpha) / d2;
        a.vx -= dx * f; a.vy -= dy * f;
        b.vx += dx * f; b.vy += dy * f;
      }
    }
    for (const e of edges) {
      if (!e.source.visible || !e.target.visible) continue;
      if (e.kind === 'weak') continue;
      const dx = e.target.x - e.source.x, dy = e.target.y - e.source.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const k = (d - 95) * 0.006 * alpha;
      const fx = (dx / d) * k, fy = (dy / d) * k;
      e.source.vx += fx; e.source.vy += fy;
      e.target.vx -= fx; e.target.vy -= fy;
    }
    for (const n of vis) {
      if (n === dragging) continue;
      n.vx *= 0.78; n.vy *= 0.78;
      n.x += n.vx; n.y += n.vy;
    }
    alpha *= 0.965;
  }

  function screenToWorld(sx, sy) {
    return [(sx - W / 2 - camX) / camK, (sy - H / 2 - camY) / camK];
  }

  function pickNode(sx, sy) {
    const [wx, wy] = screenToWorld(sx, sy);
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (!n.visible) continue;
      const hitR = n.r + 4 / camK;
      const dx = wx - n.x, dy = wy - n.y;
      if (dx * dx + dy * dy <= hitR * hitR) return n;
    }
    return null;
  }

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  window.__jumpCard = (id) => {
    const target = nodeMap.get(id);
    if (!target) return;
    target.visible = true;
    selected = target;
    camX = -target.x * camK;
    camY = -target.y * camK;
    renderDrawer(target);
    draw();
  };

  function renderDrawer(n) {
    document.getElementById('empty-msg').style.display = 'none';
    const box = document.getElementById('detail');
    box.style.display = 'flex';
    const trustCls = n.trustTier === 'human-reviewed' ? 'human' : 'machine';
    const trustIcon = n.trustTier === 'human-reviewed' ? '👤 ' : (n.trustTier === 'machine-confirmed' ? '🤖 ' : '⚪ ');
    const depsHtml = (n.dependsOn || []).map(id => `<span class="link-chip" onclick="__jumpCard('${esc(id)}')">→ ${esc(id)}</span>`).join('') || '<span style="color:var(--muted)">无</span>';
    const backHtml = (n.citedBy || []).map(id => `<span class="link-chip" onclick="__jumpCard('${esc(id)}')">← ${esc(id)}</span>`).join('') || '<span style="color:var(--muted)">无</span>';
    const patHtml = (n.patterns || []).length ? `<ul>${n.patterns.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : '<span style="color:var(--muted)">—</span>';
    const srcHtml = (n.sources || []).length ? `<ul>${n.sources.map(s => `<li><code>${esc(s)}</code></li>`).join('')}</ul>` : '<span style="color:var(--muted)">—</span>';
    const scrHtml = (n.scripts || []).length ? `<ul>${n.scripts.map(s => `<li><code>${esc(s)}</code></li>`).join('')}</ul>` : '<span style="color:var(--muted)">—</span>';

    box.innerHTML = `
      <div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:4px"><code>${esc(n.id)}</code> · <code>wiki/${esc(n.relPath)}</code></div>
        <h2>${esc(n.title)}</h2>
      </div>
      <div class="meta-tags">
        <span class="pill">${esc(n.tier)}/${esc(n.domain)}</span>
        <span class="pill ${trustCls}">${trustIcon}${esc(n.trustTier)}</span>
        <span class="pill">${esc(n.epistemicStatus)}</span>
        <span class="pill">ACE +${n.aceHelpful}/-${n.aceHarmful}</span>
        ${n.isStale ? `<span class="pill stale">⏰ Stale (${esc(n.staleAfter || 'check')})</span>` : ''}
        ${n.lifecycleStatus === 'deprecated' ? `<span class="pill deprecated">⛔ Deprecated</span>` : ''}
      </div>
      <div>
        <div class="sec-title">适用边界 (Routing Description)</div>
        <div class="box">${esc(n.description)}</div>
      </div>
      <div>
        <div class="sec-title">核心定量规则摘要 (Patterns)</div>
        <div class="box">${patHtml}</div>
      </div>
      <div>
        <div class="sec-title">前置依赖 (Depends On: ${(n.dependsOn || []).length})</div>
        <div class="box">${depsHtml}</div>
      </div>
      <div>
        <div class="sec-title">反向引用 (Cited By: ${(n.citedBy || []).length})</div>
        <div class="box">${backHtml}</div>
      </div>
      <div>
        <div class="sec-title">绑定可执行脚本 (Rust / Python)</div>
        <div class="box">${scrHtml}</div>
      </div>
      <div>
        <div class="sec-title">一手源材料溯源 (OKF v0.3 Sources)</div>
        <div class="box">${srcHtml}</div>
      </div>
    `;
  }

  cv.addEventListener('mousedown', e => {
    const rect = cv.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    lastMX = sx; lastMY = sy;
    const hit = pickNode(sx, sy);
    if (hit) {
      dragging = hit;
      selected = hit;
      renderDrawer(hit);
    } else {
      panning = true;
    }
    draw();
  });
  window.addEventListener('mousemove', e => {
    const rect = cv.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    if (dragging) {
      const [wx, wy] = screenToWorld(sx, sy);
      dragging.x = wx; dragging.y = wy;
      alpha = Math.max(alpha, 0.08);
      draw();
    } else if (panning) {
      camX += sx - lastMX; camY += sy - lastMY;
      lastMX = sx; lastMY = sy;
      draw();
    } else {
      const hit = pickNode(sx, sy);
      if (hit !== hovered) { hovered = hit; cv.style.cursor = hit ? 'pointer' : 'grab'; draw(); }
    }
  });
  window.addEventListener('mouseup', () => { dragging = null; panning = false; });
  cv.addEventListener('wheel', e => {
    e.preventDefault();
    const rect = cv.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    const [wx, wy] = screenToWorld(sx, sy);
    const factor = e.deltaY < 0 ? 1.12 : 0.89;
    camK = Math.max(0.18, Math.min(4.5, camK * factor));
    camX = sx - W / 2 - wx * camK;
    camY = sy - H / 2 - wy * camK;
    draw();
  }, { passive: false });

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.save();
    ctx.translate(W / 2 + camX, H / 2 + camY);
    ctx.scale(camK, camK);

    const focus = hovered || selected;
    const focusNeighbors = new Set();
    if (focus) {
      focusNeighbors.add(focus.id);
      (focus.dependsOn || []).forEach(id => focusNeighbors.add(id));
      (focus.citedBy || []).forEach(id => focusNeighbors.add(id));
    }

    const showWeak = document.getElementById('weak-chk').checked;
    for (const e of edges) {
      if (!e.source.visible || !e.target.visible) continue;
      if (e.kind === 'weak' && !showWeak) continue;
      const hi = focus && (e.source === focus || e.target === focus);
      if (focus && !hi) ctx.globalAlpha = 0.08;
      else ctx.globalAlpha = e.kind === 'cross_domain' ? 0.55 : (e.kind === 'weak' ? 0.22 : 0.34);

      ctx.beginPath();
      if (e.kind === 'weak') ctx.setLineDash([4, 4]); else ctx.setLineDash([]);
      ctx.strokeStyle = e.kind === 'cross_domain' ? '#fbbf24' : '#94a3b8';
      ctx.lineWidth = hi ? 1.8 / camK : 1.0 / camK;
      ctx.moveTo(e.source.x, e.source.y);
      ctx.lineTo(e.target.x, e.target.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    for (const n of nodes) {
      if (!n.visible) continue;
      const dimmed = focus && !focusNeighbors.has(n.id);
      ctx.globalAlpha = dimmed ? 0.16 : 1.0;

      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = TIER_COLOR[n.tier] || '#38bdf8';
      ctx.fill();

      if (n.trustTier === 'human-reviewed') {
        ctx.strokeStyle = '#fbbf24';
        ctx.lineWidth = 2.0 / camK;
        ctx.stroke();
      } else if (n.isStale || n.lifecycleStatus === 'deprecated') {
        ctx.strokeStyle = '#f87171';
        ctx.lineWidth = 2.0 / camK;
        ctx.stroke();
      } else if (n === selected || n === hovered) {
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2.0 / camK;
        ctx.stroke();
      }

      if (camK > 1.05 || n === hovered || n === selected || (focus && focusNeighbors.has(n.id))) {
        ctx.font = `${Math.max(9, 11 / camK)}px -apple-system, sans-serif`;
        ctx.fillStyle = '#f1f5f9';
        ctx.fillText(n.title.slice(0, 22), n.x + n.r + 4 / camK, n.y + 3 / camK);
      }
    }
    ctx.restore();
  }

  function loop() {
    if (alpha >= 0.004) { stepPhysics(); draw(); }
    requestAnimationFrame(loop);
  }
  resize();
  loop();
})();
</script>
</body>
</html>
"""


def render_wiki_visualization(
    repo_root: Path,
    out_path: Path | None = None,
    domain_filter: str | None = None,
) -> Path:
    """Generate a self-contained `viz.html` file and return its path."""
    payload = build_visualization_payload(repo_root, domain_filter=domain_filter)
    target = out_path or (repo_root / "scratch" / "agentstacks_viz.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    safe_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_out = _HTML_TEMPLATE.replace("__GRAPH_JSON__", safe_json)
    target.write_text(html_out, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate zero-backend OKF v0.3 interactive HTML graph (`viz.html`).")
    parser.add_argument("--out", type=Path, default=None, help="Output HTML path (default: scratch/agentstacks_viz.html)")
    parser.add_argument("--domain", type=str, default="all", help="Optional domain filter")
    args = parser.parse_args()
    repo_root = find_repo_root()
    out = render_wiki_visualization(repo_root, out_path=args.out, domain_filter=args.domain)
    print(f"✅ Generated interactive AgentStacks star-map (`viz.html`): {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

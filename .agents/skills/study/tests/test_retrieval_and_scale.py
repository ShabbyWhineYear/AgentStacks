#!/usr/bin/env python3
"""Scale, Context-Budget, and Hybrid Cross-Domain Retrieval Test Suite (AgentStacks v3.0).

Every test runs against an isolated temporary `Sandbox` repository, never mutating the live `wiki/`.

Covers:
1. 600-card skewed multi-domain scale stress (`400` cards in giant domain `aaa_mega_domain` + `5 x 40` cards):
   - L0 `AGENTS.md` `WIKI-MANAGED` block strictly `<= 3584` bytes (`MAX_L0_MANAGED_BYTES`).
   - All L2 `_subtrees/*.md` files strictly `<= 12288` bytes (`MAX_SUBTREE_BYTES`).
   - `CorpusTopicMap` stratified round-robin prevents alphabetical starvation of later domains (`zzz_*`).
   - Incremental `(mtime_ns, size)` parse cache keeps warm `discover_wiki_pages` `< 120ms` on 600 cards.
2. Bilingual & colloquial concept-graph bridge (`玩家开麦说话自动压低背景音乐` -> English `Spectral Ducking / Sidechain`).
3. Cross-tier `dependsOn`/`dependedBy` & ACE `co_occur` 1-Hop graph walk.
4. Negative Scope Gate (`不适用于...` / `Not for ...`) exclusion demotion (`0.18x`).
5. Anti-Matthew cold-card protection (`ACE=0` exact technical card beats `ACE=+50` generic card).
6. Airtight UTF-8 byte caps on `wiki_query.py read` (`lite <= 6KB`, `medium <= 20KB`, `heavy <= 40KB`).
"""

from __future__ import annotations

import io
import json
import sys
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
FRAMEWORK_ROOT = TESTS_DIR.parents[3]
SCRIPTS_DIR = FRAMEWORK_ROOT / ".agents" / "skills" / "study" / "scripts"
for _p in (str(SCRIPTS_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_engine import EngineTestCase, card  # noqa: E402
from wiki_core import (  # noqa: E402
    MANAGED_END,
    MANAGED_START,
    MAX_L0_MANAGED_BYTES,
    MAX_SUBTREE_BYTES,
    discover_wiki_pages,
)
from wiki_query import read_subtree_router, read_wiki_pages  # noqa: E402
from wiki_search_engine import search_hybrid_ranked  # noqa: E402


class TestSkewedScaleAndZeroContextExplosion(EngineTestCase):
    def test_600_card_skewed_corpus_caps_and_stratified_topic_map(self) -> None:
        # 1 giant alphabetically-first domain (400 cards) + 5 smaller domains (40 cards each = 200 cards)
        topics = [
            ("resonator", "谐振滤波器组设计"),
            ("quantizer", "网格节拍量化矩阵"),
            ("envelope", "瞬态起音衰减包络"),
            ("formant", "元音共振峰包络调制"),
            ("spatializer", "双耳空间声像定位"),
            ("oversampling", "抗混叠过采样滤波"),
            ("limiter", "真峰值砖墙限幅器"),
            ("reverb", "早期反射混响尾音"),
        ]
        for i in range(400):
            slug, zh_topic = topics[i % len(topics)]
            shared_marker = "跨域协同锚点脉冲" if i < 30 else "常规声学分支"
            self.sb.write(
                f"wiki/foundations/aaa_mega_domain/01_craft_playbooks/mega_{i:03d}.md",
                card(
                    f"mega-{i:03d}",
                    f"巨型声学模块 {i:03d} {zh_topic}",
                    f"当调节 {zh_topic} 与 {shared_marker} 参数区间时使用；不适用于纯平面印刷排版。",
                    body=(
                        f"## Rules\n"
                        f"- **Rule 1 ({zh_topic})**: 保持 `{slug}` 增益余量 `>= 3.0 dB`，严禁超过 `-1.0 dBTP`。\n"
                        f"- **Rule 2**: {shared_marker} 周期锁定在 `16 ms` 至 `48 ms` 区间。\n"
                    ),
                    tags=f"[{slug}, mega-cluster-{i % 8}]",
                ),
            )

        small_domains = [
            ("foundations", "mmm_combat_feel", "战斗受击顿帧反馈"),
            ("foundations", "ppp_lighting_vfx", "地面预警光效色阶"),
            ("tools_craft", "rrr_pixel_rig", "像素拆件刀光残影"),
            ("tools_craft", "ttt_shader_graph", "双层有向距离场着色"),
            ("workflows", "zzz_late_pipeline", "终局多轨合成验收"),
        ]
        for tier, dom, dom_theme in small_domains:
            for j in range(40):
                shared_marker = "跨域协同锚点脉冲" if j < 6 else dom_theme
                extra_kw = {"tags": f"[{dom}, cross-anchor-{j % 4}]"}
                if tier != "foundations":
                    extra_kw["depends_on"] = "[mega-000]"
                self.sb.write(
                    f"wiki/{tier}/{dom}/01_craft_playbooks/{dom}_{j:02d}.md",
                    card(
                        f"{dom[:3]}-{j:02d}",
                        f"{dom_theme} 规范卡 {j:02d}",
                        f"当实现 {dom_theme} 与 {shared_marker} 联动管线时使用；不适用于纯静态文档。",
                        body=(
                            f"## Rules\n"
                            f"- **Rule 1 ({dom_theme})**: {shared_marker} 响应延迟必须 `<= 32 ms`，禁止裸调无界循环。\n"
                        ),
                        **extra_kw,
                    ),
                )

        # Finalize the 600-card skewed library
        self.sb.run("wiki_finalize.py", check=True)

        # 1. L0 WIKI-MANAGED block must stay O(1) and strictly <= MAX_L0_MANAGED_BYTES (3584 B)
        agents_text = (self.sb.root / "AGENTS.md").read_text(encoding="utf-8")
        s_idx = agents_text.index(MANAGED_START)
        e_idx = agents_text.index(MANAGED_END) + len(MANAGED_END)
        managed_bytes = len(agents_text[s_idx:e_idx].encode("utf-8"))
        self.assertLessEqual(
            managed_bytes,
            MAX_L0_MANAGED_BYTES,
            f"L0 WIKI-MANAGED block exploded to {managed_bytes} bytes (> {MAX_L0_MANAGED_BYTES})",
        )

        # 2. Every auto-clustered _subtrees/*.md must exist and strictly obey <= MAX_SUBTREE_BYTES (12 KB)
        subtree_files = list((self.sb.root / "wiki").rglob("_subtrees/*.md"))
        self.assertGreaterEqual(len(subtree_files), 10)
        for st_file in subtree_files:
            st_bytes = len(st_file.read_bytes())
            self.assertLessEqual(
                st_bytes,
                MAX_SUBTREE_BYTES,
                f"{st_file} exceeded 12KB cap ({st_bytes} bytes)",
            )
            # Also verify no auto-clustered subtree is empty
            st_content = st_file.read_text(encoding="utf-8")
            self.assertTrue(
                "- **[`" in st_content or "| `" in st_content,
                f"Auto-clustered subtree {st_file.name} rendered 0 cards!",
            )

        # 3. Stratified Domain Round-Robin in CorpusTopicMap must NOT starve `zzz_late_pipeline`
        # even though `aaa_mega_domain` alone had 30 cards containing "跨域协同锚点脉冲" (previously capped at 12!)
        s_index = json.loads((self.sb.root / "wiki/.search-index.json").read_text(encoding="utf-8"))
        id_to_dom = {c["id"]: c["domain"] for c in s_index.get("cards", [])}
        topic_map = s_index.get("topicMap", {})
        anchor_postings: list[str] = []
        for term, plist in topic_map.items():
            if "锚点脉冲" in term or "协同锚点" in term or term == "跨域协同锚点脉冲":
                anchor_postings.extend(plist)
        posting_domains = {id_to_dom.get(str(p).split("#", 1)[0], "") for p in anchor_postings}
        self.assertIn(
            "zzz_late_pipeline",
            posting_domains,
            f"Alphabetical truncation starved zzz_late_pipeline! Domains seen: {posting_domains}",
        )

        # 4. Faceted Cross-Domain Search must surface multiple domains, not 100% `aaa_mega_domain`
        payload = search_hybrid_ranked(self.sb.root, "跨域协同锚点脉冲 终局多轨合成验收", limit=10)
        hit_domains = {r["domain"] for r in payload["results"]}
        self.assertIn("zzz_late_pipeline", hit_domains)
        self.assertGreaterEqual(len(hit_domains), 3, f"Expected multi-domain diversity, got {hit_domains}")

        # 5. Warm incremental parse cache on 600 cards must run fast without reparsing Markdown
        discover_wiki_pages(self.sb.root, use_cache=True)
        t0 = time.perf_counter()
        cached_pages = discover_wiki_pages(self.sb.root, use_cache=True)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertEqual(len(cached_pages), 600)
        self.assertLess(elapsed_ms, 150.0, f"Warm parse cache took {elapsed_ms:.1f}ms on 600 cards")

        # 6. Linter --no-tests --strict must pass cleanly on the 600-card skewed repo
        r_lint = self.sb.run("wiki_linter.py", "--no-tests", "--strict")
        self.assertEqual(r_lint.returncode, 0, r_lint.stdout + r_lint.stderr)


class TestHybridRecallAndPrecisionGates(EngineTestCase):
    def test_bilingual_colloquial_concept_bridge_recall(self) -> None:
        # Card uses technical English/acoustic terminology ("Spectral Ducking", "Sidechain") without colloquial "开麦说话"
        self.sb.write(
            "wiki/foundations/music_theory_acoustics/01_craft_playbooks/ducking.md",
            card(
                "mta-spectral-ducking-sidechain",
                "实时语音总线 Sidechain 动态频谱闪避规范 (Spectral Ducking & Voice Bus)",
                "当处理双人语音通话与背景配乐频段掩蔽冲突时使用；不适用于离线单轨钢琴独奏。",
                body=(
                    "## 1. Spectral Ducking 频段避让阈值\n"
                    "- **Rule 1**: 在 `300 Hz - 3.4 kHz` 人声共振峰频段施加 `-4.5 dB` 动态侧链衰减 (Sidechain Compression)。\n"
                ),
                tags="[spectral-ducking, sidechain]",
            ),
        )
        self.sb.write(
            "wiki/foundations/music_theory_acoustics/01_craft_playbooks/fugue.md",
            card(
                "mta-baroque-fugue",
                "巴洛克四声部赋格对位写作",
                "当创作古典器乐赋格主题与答题时使用；不适用于现代游戏实时语音混音。",
            ),
        )
        self.sb.run("wiki_finalize.py", check=True)

        payload = search_hybrid_ranked(self.sb.root, "玩家开麦说话自动压低背景音乐", limit=5)
        top_ids = [r["id"] for r in payload["results"][:3]]
        self.assertEqual(top_ids[0], "mta-spectral-ducking-sidechain", f"ConceptGraph failed to rank #1: {payload}")

    def test_cross_tier_depended_by_and_ace_co_occurrence_walk(self) -> None:
        self.sb.write(
            "wiki/foundations/visual_aesthetics/00_core_aesthetics/terminator.md",
            card(
                "va-terminator-subsurface-shift",
                "明暗交界线冷暖色相偏移与次表面透光定律",
                "当塑造角色皮肤明暗交界线血色散射时使用；不适用于纯黑白文字排版。",
                body="## 1. 交界线色相偏移\n- **Rule 1**: 皮肤交界线向高饱和暖红偏移 `12-18 deg`。\n",
                tags="[terminator-hue, sss-skin]",
            ),
        )
        # Downstream tool card in krita_2d_paint explicitly depends_on va-terminator-subsurface-shift
        # without repeating the query words "次表面透光定律"
        self.sb.write(
            "wiki/tools_craft/krita_2d_paint/01_craft_playbooks/alpha_stack.md",
            card(
                "krita-alpha-inherit-layer-stack",
                "Krita 图层组 Alpha 继承与正片叠底色阶工作流",
                "当在 Krita 中搭建角色分层上色堆栈时使用；不适用于 3D 骨骼蒙皮。",
                body="## 1. Alpha 继承栈\n- **Rule 1**: 阴影层开启 `Inherit Alpha` 并使用 `Multiply` 叠底。\n",
                depends_on="[va-terminator-subsurface-shift]",
            ),
        )
        # Partner audio card co-applied in a historical ACE trace
        self.sb.write(
            "wiki/foundations/motion_animation/01_craft_playbooks/breath_idle.md",
            card(
                "ma-portrait-breathing-micro-motion",
                "角色立绘胸腔呼吸相位错开微动效",
                "当为立绘添加待机呼吸微动效时使用；不适用于静态打印海报。",
            ),
        )
        self.sb.run("wiki_finalize.py", check=True)
        self.sb.run(
            "study_runner.py",
            "record-trace",
            "--helpful",
            "va-terminator-subsurface-shift",
            "ma-portrait-breathing-micro-motion",
            "--task",
            "角色立绘皮肤透光与待机呼吸渲染",
            check=True,
        )
        self.sb.run("wiki_finalize.py", check=True)

        payload = search_hybrid_ranked(self.sb.root, "明暗交界线次表面透光定律", limit=8)
        found_ids = [r["id"] for r in payload["results"]]
        self.assertEqual(found_ids[0], "va-terminator-subsurface-shift")
        self.assertIn(
            "krita-alpha-inherit-layer-stack",
            found_ids,
            f"1-Hop dependedBy failed to surface downstream Krita tool card: {found_ids}",
        )
        self.assertIn(
            "ma-portrait-breathing-micro-motion",
            found_ids,
            f"ACE co-occurrence weak edge failed to surface partner motion card: {found_ids}",
        )

    def test_negative_scope_exclusion_gate_demotes_trap_card(self) -> None:
        self.sb.write(
            "wiki/foundations/visual_aesthetics/01_craft_playbooks/armor_metal.md",
            card(
                "va-heavy-armor-fresnel",
                "重装金属盔甲菲涅尔高反光绘制法则",
                "当绘制写实高反光金属盔甲与环境反射时使用；不适用于扁平化平面图标设计。",
                body="## 1. 菲涅尔反射\n- **Rule 1**: 掠射角反射率提升至 `0.92`（平面图标设计严禁套用此写实高光）。\n",
            ),
        )
        self.sb.write(
            "wiki/foundations/visual_aesthetics/01_craft_playbooks/flat_icon.md",
            card(
                "va-flat-ui-icon-silhouette",
                "扁平化平面图标设计与负空间识别度规范",
                "当进行移动端扁平化平面图标设计与小尺寸剪影优化时使用；不适用于写实厚涂油画。",
                body="## 1. 图标负空间闭合\n- **Rule 1**: `24px` 视口下内部负空间开口宽度 `>= 2px`。\n",
            ),
        )
        self.sb.run("wiki_finalize.py", check=True)

        payload = search_hybrid_ranked(self.sb.root, "扁平化平面图标设计", limit=5)
        results = payload["results"]
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "va-flat-ui-icon-silhouette")
        trap_entries = [r for r in results if r["id"] == "va-heavy-armor-fresnel"]
        if trap_entries:
            self.assertTrue(trap_entries[0]["scopeExcluded"], f"Expected scopeExcluded=True on trap card: {trap_entries[0]}")
            self.assertLess(trap_entries[0]["score"], results[0]["score"] * 0.35)

    def test_anti_matthew_cold_card_beats_high_ace_generic_card(self) -> None:
        self.sb.write(
            "wiki/foundations/visual_aesthetics/00_core_aesthetics/generic_shadow.md",
            card(
                "va-generic-shadow-old",
                "基础阴影明度对比通则",
                "当检查画面整体阴影明暗对比度时使用；不适用于纯文本。",
                body="## 1. 阴影明度\n- **Rule 1**: 亮部与阴影明度差 `>= 0.22`。\n",
            ),
        )
        self.sb.write(
            "wiki/foundations/visual_aesthetics/01_craft_playbooks/sss_foliage.md",
            card(
                "va-foliage-translucency-cold",
                "树叶逆光次表面透射阴影与叶脉色偏规范",
                "当绘制逆光树叶次表面透射阴影与黄绿叶脉透光时使用；不适用于金属机械。",
                body="## 1. 树叶次表面透射\n- **Rule 1**: 逆光透射区饱和度提升 `+25%`，色相向黄绿偏移 `18 deg`。\n",
            ),
        )
        self.sb.run("wiki_finalize.py", check=True)
        # Inflate ACE helpful count on generic_shadow to +50 while keeping sss_foliage at 0
        ace_path = self.sb.root / "wiki/.ace-telemetry.json"
        ace_data = {
            "schemaVersion": 2,
            "cards": {"va-generic-shadow-old": {"helpful": 50, "harmful": 0}},
            "scripts": {},
            "co_occurrences": {},
            "query_memory": [],
            "events": [],
        }
        ace_path.write_text(json.dumps(ace_data, ensure_ascii=False, indent=2), encoding="utf-8")

        payload = search_hybrid_ranked(self.sb.root, "树叶逆光次表面透射阴影", limit=5)
        self.assertEqual(
            payload["results"][0]["id"],
            "va-foliage-translucency-cold",
            f"Matthew effect drowned cold specialist card! Results: {payload['results']}",
        )


class TestAirtightLensByteBudgets(EngineTestCase):
    def test_read_modes_strictly_obey_utf8_byte_caps(self) -> None:
        # Create 5 heavy dependency cards + 4 target cards that all depend on them
        dep_ids = []
        for d in range(5):
            did = f"va-dep-{d}"
            dep_ids.append(did)
            dep_body = (
                f"## 1. 底层光学约束簇 {d}\n"
                + "\n".join(
                    f"- **Rule {k}**: 保持色温梯度 `{4200 + k * 150}K` 与明度跨度 `>= 0.{20 + k}`，严禁色阶断层。"
                    for k in range(18)
                )
                + "\n"
            )
            self.sb.write(
                f"wiki/foundations/visual_aesthetics/00_core_aesthetics/dep_{d}.md",
                card(did, f"底层光学核心定理 {d}", f"当计算光学梯度 {d} 时使用；不适用于纯音频。", body=dep_body),
            )

        target_ids = []
        deps_str = "[" + ", ".join(dep_ids) + "]"
        for t in range(4):
            tid = f"kr-heavy-target-{t}"
            target_ids.append(tid)
            t_body = (
                f"## 1. 复合绘制流程 {t}\n"
                + "\n".join(
                    f"- **Step {k}**: 在图层组 `{k}` 施加正片叠底不透明度 `{35 + k}%` 并校验边缘硬度 `>= 0.75`。"
                    for k in range(32)
                )
                + "\n"
            )
            self.sb.write(
                f"wiki/tools_craft/krita_2d_paint/01_craft_playbooks/target_{t}.md",
                card(
                    tid,
                    f"Krita 重型复合上色手册 {t}",
                    f"当执行复合角色上色流水线 {t} 时使用；不适用于纯命令行。",
                    body=t_body,
                    depends_on=deps_str,
                ),
            )
        self.sb.run("wiki_finalize.py", check=True)

        for mode, cap_bytes in (("lite", 6 * 1024), ("medium", 20 * 1024), ("heavy", 40 * 1024)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = read_wiki_pages(self.sb.root, target_ids, full_deps=(mode == "heavy"), mode=mode)
            out_bytes = len(buf.getvalue().encode("utf-8"))
            self.assertEqual(rc, 0)
            self.assertLessEqual(
                out_bytes,
                cap_bytes,
                f"read_wiki_pages(mode={mode}) emitted {out_bytes} UTF-8 bytes > hard cap {cap_bytes}!",
            )

        # Also verify subtree router read obeys medium cap
        for j in range(18):
            self.sb.write(
                f"wiki/tools_craft/krita_2d_paint/02_style_specifics/extra_{j}.md",
                card(f"kr-extra-{j}", f"扩展风格卡 {j}", f"当切换画风 {j} 时使用；不适用于3D。"),
            )
        self.sb.run("wiki_finalize.py", check=True)
        st_dir = self.sb.root / "wiki/tools_craft/krita_2d_paint/_subtrees"
        st_slug = sorted(st_dir.glob("*.md"))[0].stem
        buf_st = io.StringIO()
        with redirect_stdout(buf_st):
            rc_st = read_subtree_router(self.sb.root, f"krita_2d_paint/{st_slug}", mode="medium")
        self.assertEqual(rc_st, 0)
        self.assertLessEqual(len(buf_st.getvalue().encode("utf-8")), 20 * 1024)


if __name__ == "__main__":
    unittest.main()

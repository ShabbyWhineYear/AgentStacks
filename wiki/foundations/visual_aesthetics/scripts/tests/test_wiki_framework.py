#!/usr/bin/env python3
"""Self-test for the LLM-Wiki 2.0 × Sirchmunk learning framework."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPTS_DIR = REPO_ROOT / ".agents" / "skills" / "study" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from wiki_core import build_document_tree, extract_card_patterns_and_constraints  # noqa: E402


class TestWikiFramework(unittest.TestCase):
    def test_sirchmunk_document_tree_builder(self) -> None:
        sample_md = """## 1. Core Quantitative Rules
- **Rule A**: Keep luminance contrast `>= 0.35` across primary silhouettes; 严禁低于 `0.20`.
- **Rule B**: Apply warm/cool hue shift `15-30 deg` across the terminator boundary.

### 1.1 Sub-Section Verification
- Verify via `cargo test --quiet`.
"""
        tree = build_document_tree("sample-card", "foundations/visual_aesthetics/sample.md", sample_md, "sha256:000")
        flat = tree.all_flat_nodes()
        self.assertEqual(len(flat), 2)
        self.assertEqual(flat[0].node_id, "N01")
        self.assertGreaterEqual(flat[0].quant_rule_count, 1)

        patterns, constraints, level = extract_card_patterns_and_constraints(
            {},
            "Use when designing cel-shaded lighting; 不适用于无影平面图标。",
            sample_md,
            "craft-card",
            "foundations",
            "01_craft_playbooks",
        )
        self.assertEqual(level, 2)
        self.assertGreaterEqual(len(patterns), 1)
        self.assertGreaterEqual(len(constraints), 1)

    def test_sidecars_exist(self) -> None:
        self.assertTrue((REPO_ROOT / "wiki" / ".tree-index.json").exists())
        self.assertTrue((REPO_ROOT / "wiki" / ".search-index.json").exists())
        s_data = json.loads((REPO_ROOT / "wiki" / ".search-index.json").read_text(encoding="utf-8"))
        self.assertIn("cards", s_data)
        self.assertIn("topicMap", s_data)

    def test_harnesspro_and_prefix_cache_pinning(self) -> None:
        import subprocess
        import time

        r_diag = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "study_runner.py"), "trace-diagnose"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("HARNESSPRO (arXiv:2610.26770)", r_diag.stdout)
        self.assertIn("--mode lite", r_diag.stdout)

        r_cov = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "study_runner.py"), "coverage-audit"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("WHOLE-SOURCE EXHAUSTIVE COVERAGE", r_cov.stdout)

        # Verify Anti-Cherry-Picking Guard rejects <70% reusable units
        r_cherry = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "study_runner.py"),
                "register-source",
                "--source",
                "raw/foundations/visual_aesthetics/sample.pdf",
                "--title",
                "Sample Book",
                "--units-json",
                '[{"unit_id":"ch1","title":"Ch1","reusable":true},{"unit_id":"ch2","title":"Ch2","reusable":false}]',
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(r_cherry.returncode, 0)
        self.assertIn("Anti-Cherry-Picking Guard", r_cherry.stderr)

        # Verify Prefix-Cache Pinning: running wiki_finalize.py when nothing changed touches 0 files on disk
        tracked = [REPO_ROOT / "GEMINI.md", REPO_ROOT / "SCHEMA.md"] + [
            p for p in (REPO_ROOT / "wiki").rglob("*") if p.is_file()
        ]
        mtimes_before = {p: p.stat().st_mtime_ns for p in tracked}
        time.sleep(0.05)
        subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "wiki_finalize.py")],
            cwd=str(REPO_ROOT),
            check=True,
        )
        mutated = [p.name for p in tracked if p.stat().st_mtime_ns != mtimes_before[p]]
        self.assertEqual(mutated, [], f"Idempotent finalizer mutated files: {mutated}")

    def test_sag2_two_hop_bridge_term_expansion(self) -> None:
        import io
        import tempfile
        from contextlib import redirect_stdout
        from wiki_query import search_wiki

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            c_dir = tmp_root / "wiki" / "foundations" / "visual_aesthetics" / "01_craft_playbooks"
            c_dir.mkdir(parents=True, exist_ok=True)
            # Card A matches user query "赛博朋克霓虹" and carries bridge term `oklab-gamut-clipping`
            (c_dir / "card-a.md").write_text(
                "---\n"
                "id: va-cyberpunk-neon-seed\n"
                "type: craft-card\n"
                "title: 赛博朋克霓虹灯牌色彩控制\n"
                "description: 当设计赛博朋克霓虹发光字与暗部环境色对比时使用；不适用于黑白线稿。\n"
                "epistemic_status: canonical\n"
                "tags: [cyberpunk-neon, oklab-gamut-clipping]\n"
                "depends_on: []\n"
                "scripts: []\n"
                "---\n\n"
                "## 1. 霓虹发光色相铁律\n"
                "- **Rule 1**: 霓虹核心亮度保持 `L >= 0.88`，外发光色相偏移 `15-25 deg`。\n",
                encoding="utf-8",
            )
            # Card B has ZERO lexical overlap with "赛博朋克霓虹", but shares the bridge hyperedge `oklab-gamut-clipping`
            (c_dir / "card-b.md").write_text(
                "---\n"
                "id: va-oklab-hdr-tone-mapping\n"
                "type: craft-card\n"
                "title: Oklab 广色域高光防过曝软膝映射\n"
                "description: 当处理高饱和度光源防溢色裁剪时使用；不适用于纯文本排版。\n"
                "epistemic_status: canonical\n"
                "tags: [oklab-gamut-clipping, hdr-tone-mapping]\n"
                "depends_on: []\n"
                "scripts: []\n"
                "---\n\n"
                "## 1. Oklab Gamut Clipping 软膝阈值\n"
                "- **Rule 1**: 当色度 `C > 0.26` 时必须启用 `oklab-gamut-clipping` 保持感知色相恒定。\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = search_wiki(tmp_root, "赛博朋克霓虹")
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("Phase 2: SAG2 2-Hop Dynamic Bridge-Term Expansion", out)
            self.assertIn("oklab-gamut-clipping", out)
            self.assertIn("va-oklab-hdr-tone-mapping", out)
            self.assertIn("2-Hop via: oklab-gamut-clipping", out)


if __name__ == "__main__":
    unittest.main()

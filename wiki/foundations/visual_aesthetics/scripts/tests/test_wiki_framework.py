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

    def test_harnesspro_and_harness2_commands(self) -> None:
        import subprocess
        r_diag = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "study_runner.py"), "trace-diagnose"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("HARNESSPRO (arXiv:2610.26770)", r_diag.stdout)
        self.assertIn("--mode lite", r_diag.stdout)


if __name__ == "__main__":
    unittest.main()

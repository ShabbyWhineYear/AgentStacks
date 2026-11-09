#!/usr/bin/env python3
"""Regression suite for the AgentStacks engine (`.agents/skills/study/scripts`).

Every test runs against a throw-away copy of the framework in a temp directory, never against the
user's live `wiki/`. (v2.0 kept its self-test inside `wiki/foundations/visual_aesthetics/scripts/tests/`;
it asserted Day-0-only output, so the linter — and therefore every `commit` — failed as soon as the
first real card existed.)

Run:  python3 -m unittest discover -s .agents/skills/study/tests -v
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = FRAMEWORK_ROOT / ".agents" / "skills" / "study" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import wiki_core  # noqa: E402
from wiki_core import build_document_tree, extract_card_patterns_and_constraints  # noqa: E402

GIT_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def card(cid: str, title: str, desc: str, body: str = "## Rules\n1. 主体与背景明度差至少 20%。\n", **extra: str) -> str:
    fm = [f"id: {cid}", "type: craft-card", f"title: {title}", f"description: {desc}", "epistemic_status: canonical"]
    fm += [f"{k}: {v}" for k, v in extra.items()]
    return "---\n" + "\n".join(fm) + "\n---\n\n" + body


class Sandbox:
    """A temp copy of the framework (scripts + schema + Cargo.toml) with its own git repo."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "stacks"
        self.root.mkdir()
        for name in ("SCHEMA.md", "AGENTS.md", "Cargo.toml"):
            shutil.copy2(FRAMEWORK_ROOT / name, self.root / name)
        shutil.copytree(FRAMEWORK_ROOT / "src", self.root / "src")
        (self.root / "wiki").mkdir()
        (self.root / "raw").mkdir()
        self.git("init", "-q")
        self.git("checkout", "-q", "-b", "main")

    def close(self) -> None:
        self.tmp.cleanup()

    def git(self, *args: str, check: bool = True) -> str:
        r = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, env=GIT_ENV)
        if check and r.returncode != 0:
            raise AssertionError(f"git {args} failed: {r.stderr}")
        return r.stdout.strip()

    def write(self, rel: str, text: str | bytes) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
        return p

    def run(self, script: str, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        r = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script), *args], cwd=self.root, capture_output=True, text=True, env=GIT_ENV
        )
        if check and r.returncode != 0:
            raise AssertionError(f"{script} {args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def commit_all(self, msg: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-qm", msg)


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)


class TestDocumentTreeAndContracts(unittest.TestCase):
    def test_sirchmunk_document_tree_builder(self) -> None:
        sample_md = textwrap.dedent(
            """\
            ## 1. Core Quantitative Rules
            - **Rule A**: Keep luminance contrast `>= 0.35` across primary silhouettes; 严禁低于 `0.20`.
            - **Rule B**: Apply warm/cool hue shift `15-30 deg` across the terminator boundary.

            ### 1.1 Sub-Section Verification
            - Verify via `cargo test --quiet`.
            """
        )
        tree = build_document_tree("sample-card", "foundations/visual_aesthetics/sample.md", sample_md, "sha256:000")
        flat = tree.all_flat_nodes()
        self.assertEqual(len(flat), 2)
        self.assertEqual(flat[0].node_id, "N01")
        self.assertGreaterEqual(flat[0].quant_rule_count, 1)
        patterns, constraints, level = extract_card_patterns_and_constraints(
            {}, "Use when designing cel-shaded lighting; 不适用于无影平面图标。", sample_md, "craft-card", "foundations", "01_craft_playbooks"
        )
        self.assertEqual(level, 2)
        self.assertGreaterEqual(len(patterns), 1)
        self.assertGreaterEqual(len(constraints), 1)

    def test_cli_help_does_not_crash(self) -> None:
        for script in ("study_runner.py", "wiki_query.py", "wiki_linter.py", "wiki_finalize.py"):
            r = subprocess.run([sys.executable, str(SCRIPTS_DIR / script), "--help"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{script} --help crashed:\n{r.stderr}")

    def test_query_tokenizer_drops_particles_and_filler(self) -> None:
        from study_runner import _tokenize_query
        from wiki_query import tokenize_query

        toks = tokenize_query("帮我给角色的阴影上色")
        self.assertIn("阴影", toks)
        self.assertIn("角色", toks)
        for noise in ("的", "我", "帮我", "我给", "色的", "的阴"):
            self.assertNotIn(noise, toks)
        self.assertEqual(tokenize_query("色"), ["色"])  # single-char query still searchable
        # Domain terms containing polysemous root characters ("着", "过", "了") must NEVER be dropped
        for domain_term in ("着色", "过渡", "过门", "过曝", "过载", "明了"):
            self.assertIn(domain_term, tokenize_query(domain_term))
            self.assertIn(domain_term, _tokenize_query(domain_term))
        self.assertIn("着色", tokenize_query("卡通着色器"))
        self.assertIn("过门", tokenize_query("鼓组过门"))
        self.assertIn("过渡", tokenize_query("段落过渡"))
        self.assertIn("过曝", tokenize_query("高光过曝"))


class TestFinalizeAndCargo(EngineTestCase):
    def test_rust_scripts_are_registered_and_linted(self) -> None:
        self.sb.write("wiki/foundations/visual_aesthetics/scripts/verifiers/check_demo.rs", "fn main() {}\n")
        # Simulate a v2.0 checkout that only has the legacy marker comment.
        cargo = self.sb.root / "Cargo.toml"
        text = cargo.read_text(encoding="utf-8")
        start, end = text.index("# === WIKI-MANAGED-BINS:START ==="), text.index("# === WIKI-MANAGED-BINS:END ===")
        cargo.write_text(
            text[:start] + "# === AUTO-GENERATED BINARIES BY wiki_finalize.py (DO NOT EDIT BELOW) ===\n" + text[end + 31 :],
            encoding="utf-8",
        )
        self.sb.run("wiki_finalize.py", check=True)
        new_text = cargo.read_text(encoding="utf-8")
        self.assertIn('path = "wiki/foundations/visual_aesthetics/scripts/verifiers/check_demo.rs"', new_text)
        self.assertNotIn("AUTO-GENERATED BINARIES", new_text)
        # Removing the registration again must be caught by the linter (no more vacuous `cargo test`).
        cargo.write_text(new_text.replace('name = "check_demo"', "").replace(
            'path = "wiki/foundations/visual_aesthetics/scripts/verifiers/check_demo.rs"', ""), encoding="utf-8")
        from wiki_linter import lint_wiki

        errors, _ = lint_wiki(self.sb.root, run_tests=True)
        self.assertTrue(any("not registered as [[bin]]" in e for e in errors), errors)

    def test_finalize_is_idempotent(self) -> None:
        self.sb.write("wiki/foundations/visual_aesthetics/01_craft_playbooks/a.md",
                      card("va-a", "明度结构", "当需要决定明暗层次时使用；不适用于纯线稿。"))
        self.sb.run("wiki_finalize.py", check=True)
        tracked = [p for p in self.sb.root.rglob("*") if p.is_file() and ".git" not in p.parts]
        before = {p: p.stat().st_mtime_ns for p in tracked}
        self.sb.run("wiki_finalize.py", check=True)
        mutated = [p.name for p in tracked if p.stat().st_mtime_ns != before[p]]
        self.assertEqual(mutated, [], f"finalize mutated files on a no-op run: {mutated}")

    def test_move_keeps_alias_redirect(self) -> None:
        self.sb.write("wiki/foundations/visual_aesthetics/01_craft_playbooks/old.md",
                      card("va-old", "旧卡", "当测试移动时使用；不适用于其他场景。"))
        self.sb.write("wiki/tools_craft/krita_2d_paint/01_craft_playbooks/k.md",
                      card("kr-k", "引用卡", "当测试引用时使用；不适用于其他场景。",
                           depends_on="[foundations/visual_aesthetics/01_craft_playbooks/old.md]"))
        self.sb.run("wiki_finalize.py", "--move", "foundations/visual_aesthetics/01_craft_playbooks/old.md",
                    "foundations/visual_aesthetics/01_craft_playbooks/new.md", check=True)
        moved = wiki_core.parse_markdown(
            (self.sb.root / "wiki/foundations/visual_aesthetics/01_craft_playbooks/new.md").read_text(encoding="utf-8"))
        self.assertEqual(moved.frontmatter.get("aliases"), ["foundations/visual_aesthetics/01_craft_playbooks/old.md"])
        ref = (self.sb.root / "wiki/tools_craft/krita_2d_paint/01_craft_playbooks/k.md").read_text(encoding="utf-8")
        self.assertIn("01_craft_playbooks/new.md", ref)


class TestLinterRules(EngineTestCase):
    def lint(self) -> tuple[list[str], list[str]]:
        from wiki_linter import lint_wiki

        self.sb.run("wiki_finalize.py", check=True)
        return lint_wiki(self.sb.root, run_tests=False)

    def test_description_shape_and_narrative_rules(self) -> None:
        base = "wiki/foundations/visual_aesthetics/01_craft_playbooks/"
        self.sb.write(base + "folded.md", card("va-folded", "折行", ">-\n  第一行\n  第二行"))
        self.sb.write(base + "hash.md", card("va-hash", "注释", "当需要原理时按 #section 点读；日常不需要。"))
        self.sb.write(base + "story.md", card("va-story", "叙事", "当测试叙事时使用；不适用于其他。",
                                              body="## Rules\n1. 模型写了过多高光，阴影偏移至少 10 度。\n"))
        self.sb.write(base + "quoted.md", card("va-quoted", "引号", '"当需要原理时按 #section 点读；日常不需要。"'))
        errors, _ = self.lint()
        joined = "\n".join(errors)
        self.assertIn("folded.md] 'description' uses a YAML block scalar", joined)
        self.assertIn("hash.md] 'description' contains an unquoted ' #'", joined)
        self.assertIn("story.md] 禁止在规则卡中记录训练期", joined)
        self.assertNotIn("quoted.md]", joined)

    def test_near_duplicate_and_one_sided_description_are_advice_only(self) -> None:
        base = "wiki/foundations/visual_aesthetics/01_craft_playbooks/"
        self.sb.write(base + "a.md", card("va-a", "赛璐珞明暗交界线冷暖色相偏移", "当进行角色二分光影塑造、选择阴影色相偏移方向时使用；不适用于平面图标。"))
        self.sb.write(base + "b.md", card("va-b", "赛璐珞阴影冷暖色相偏移", "当进行角色二分光影塑造、选择阴影色相偏移方向时使用；不适用于平面图标设计。"))
        self.sb.write(base + "c.md", card("va-c", "单边描述", "当在绘画软件中选择笔刷类型与笔刷尺寸时使用。"))
        errors, warnings = self.lint()
        self.assertEqual(errors, [])
        self.assertTrue(any("[near-duplicate]" in w and "va-a" in w and "va-b" in w for w in warnings), warnings)
        self.assertTrue(any("c.md] 'description' states when to read but not when to skip" in w for w in warnings), warnings)
        r = self.sb.run("wiki_linter.py", "--no-tests", "--strict")
        self.assertEqual(r.returncode, 0, r.stdout)  # advice never fails --strict

    def test_unverifiable_raw_source_is_flagged(self) -> None:
        self.sb.write("wiki/foundations/visual_aesthetics/01_craft_playbooks/src.md",
                      card("va-src", "来源", "当测试来源时使用；不适用于其他。",
                           sources="[raw://foundations/visual_aesthetics/ghost_book.pdf#page=3]"))
        _, warnings = self.lint()
        self.assertTrue(any("Unverifiable primary source" in w for w in warnings), warnings)


class TestRawMediaSafety(EngineTestCase):
    def test_prune_only_deletes_audio_video(self) -> None:
        d = "raw/foundations/visual_aesthetics/"
        self.sb.write(d + "lecture.mp4", os.urandom(2_000_000))
        self.sb.write(d + "board.psd", os.urandom(1_500_000))
        self.sb.write(d + "contact_sheet.jpg", os.urandom(1_200_000))
        self.sb.write(d + "lecture.en.vtt", "WEBVTT\n")
        self.sb.run("study_runner.py", "prune-raw", check=True)
        names = {p.name for p in (self.sb.root / d).iterdir()}
        self.assertNotIn("lecture.mp4", names)
        self.assertIn("lecture.mp4.receipt.json", names)
        for kept in ("board.psd", "contact_sheet.jpg", "lecture.en.vtt"):
            self.assertIn(kept, names)

    def test_prune_keeps_media_of_in_progress_source_only(self) -> None:
        self.sb.write("raw/foundations/motion_animation/course/ep1.mp4", os.urandom(10_000))
        self.sb.write("raw/foundations/visual_aesthetics/done.mp4", os.urandom(10_000))
        self.sb.run("study_runner.py", "register-source", "--source", "raw/foundations/motion_animation/course",
                    "--title", "Course", "--units-json", '[{"unit_id":"u1","title":"U1"}]', check=True)
        self.sb.run("study_runner.py", "prune-raw", check=True)
        self.assertTrue((self.sb.root / "raw/foundations/motion_animation/course/ep1.mp4").exists())
        self.assertFalse((self.sb.root / "raw/foundations/visual_aesthetics/done.mp4").exists())

    def test_claim_fingerprint_survives_prune(self) -> None:
        self.sb.write("raw/foundations/visual_aesthetics/lecture.mp4", os.urandom(50_000))
        rel = "foundations/visual_aesthetics/01_craft_playbooks/c.md"
        self.sb.write("wiki/" + rel, card("va-c", "声明", "当测试声明时使用；不适用于其他。"))
        self.sb.write("wiki/.claims/" + rel[:-3] + ".json", json.dumps({
            "schemaVersion": 1, "pageId": "va-c", "claims": [
                {"id": "c1", "text": "x", "evidence": [{"resource": "raw://foundations/visual_aesthetics/lecture.mp4#t=01:00-02:00"}]}
            ]}))
        self.sb.run("wiki_finalize.py", check=True)
        self.sb.run("study_runner.py", "prune-raw", check=True)
        self.sb.run("wiki_finalize.py", check=True)
        data = json.loads((self.sb.root / "wiki/.claims" / (rel[:-3] + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(data["claims"][0]["status"], "clean")

    def test_commit_stages_receipts_and_transcripts(self) -> None:
        self.sb.write(".gitignore", "raw/**/*.mp4\n")
        self.sb.write("raw/foundations/visual_aesthetics/lecture.mp4", os.urandom(1000))
        self.sb.write("raw/foundations/visual_aesthetics/lecture.en.vtt", "WEBVTT\n")
        self.sb.commit_all("init")
        self.sb.run("study_runner.py", "prune-raw", check=True)
        r = self.sb.run("study_runner.py", "commit", "--verb", "stacks", "--domain", "t", "--summary", "s")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = self.sb.git("ls-files", "raw").splitlines()
        self.assertIn("raw/foundations/visual_aesthetics/lecture.mp4.receipt.json", files)
        self.assertIn("raw/foundations/visual_aesthetics/lecture.en.vtt", files)


class TestGitOperations(EngineTestCase):
    def make_history(self, n: int) -> None:
        for i in range(n):
            self.sb.write("wiki/log.md", "x\n" * (i + 1))
            self.sb.commit_all(f"c{i}")

    def test_compact_git_really_rewrites_and_backs_up(self) -> None:
        self.make_history(20)
        tree_before = self.sb.git("rev-parse", "HEAD^{tree}")
        backup_dir = self.sb.root.parent / "backups"
        env_backup = os.environ.get("AGENTSTACKS_BACKUP_DIR")
        os.environ["AGENTSTACKS_BACKUP_DIR"] = str(backup_dir)
        try:
            r = subprocess.run([sys.executable, str(SCRIPTS_DIR / "study_runner.py"), "compact-git", "--keep", "5"],
                               cwd=self.sb.root, capture_output=True, text=True, env={**GIT_ENV, "AGENTSTACKS_BACKUP_DIR": str(backup_dir)})
        finally:
            if env_backup is None:
                os.environ.pop("AGENTSTACKS_BACKUP_DIR", None)
            else:
                os.environ["AGENTSTACKS_BACKUP_DIR"] = env_backup
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.sb.git("--no-replace-objects", "rev-list", "--count", "HEAD"), "6")
        self.assertEqual(self.sb.git("for-each-ref", "refs/replace/"), "")
        self.assertEqual(self.sb.git("rev-parse", "HEAD^{tree}"), tree_before)
        self.assertEqual(self.sb.git("log", "-1", "--format=%s"), "c19")
        self.assertEqual(len(list(backup_dir.glob("*.bundle"))), 1)

    def test_compact_git_refuses_to_rewrite_pushed_history(self) -> None:
        self.make_history(8)
        remote = self.sb.root.parent / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.sb.git("remote", "add", "origin", str(remote))
        self.sb.git("push", "-q", "-u", "origin", "main")
        r = self.sb.run("study_runner.py", "compact-git", "--keep", "3", "--no-backup")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("force", r.stderr)
        self.assertEqual(self.sb.git("rev-list", "--count", "HEAD"), "8")

    def test_rollback_leaves_clean_tree(self) -> None:
        self.sb.write("wiki/foundations/visual_aesthetics/01_craft_playbooks/a.md",
                      card("va-a", "明度结构", "当需要决定明暗层次时使用；不适用于纯线稿。"))
        self.sb.run("wiki_finalize.py", check=True)
        self.sb.commit_all("base")
        self.sb.write("wiki/foundations/visual_aesthetics/01_craft_playbooks/b.md",
                      card("va-b", "色相偏移", "当选择阴影色相时使用；不适用于平面图标。"))
        self.sb.run("wiki_finalize.py", check=True)
        self.sb.commit_all("add b")
        self.sb.run("study_runner.py", "rollback", check=True)
        self.assertEqual(self.sb.git("status", "--porcelain"), "")
        self.assertFalse((self.sb.root / "wiki/foundations/visual_aesthetics/01_craft_playbooks/b.md").exists())


class TestQueryAndTelemetry(EngineTestCase):
    def setUp(self) -> None:
        super().setUp()
        base = "wiki/foundations/visual_aesthetics/"
        long_body = "## 概述\n" + "推导段落。\n" * 50 + "\n## 色温与白平衡\n色温 6500K 为日光标准。\n"
        self.sb.write(base + "03_deep_concepts/sci.md", "---\nid: va-sci\ntype: concept-doc\ntitle: 色彩科学\n"
                      "description: \"当需要色温原理时按 #section 点读；日常上色不需要。\"\nepistemic_status: canonical\n---\n\n" + long_body)
        self.sb.write(base + "01_craft_playbooks/a.md", card("va-a", "阴影色相偏移", "当选择阴影色相时使用；不适用于平面图标。"))
        self.sb.run("wiki_finalize.py", check=True)

    def test_read_with_shell_split_section_anchor(self) -> None:
        r = self.sb.run("wiki_query.py", "read", "va-sci", "#色温与白平衡", check=True)
        self.assertIn("6500K", r.stdout)
        self.assertNotIn("va-a", r.stdout)

    def test_record_trace_normalizes_paths_and_penalizes_harmful(self) -> None:
        self.sb.run("study_runner.py", "record-trace", "--harmful",
                    "foundations/visual_aesthetics/01_craft_playbooks/a.md", "--task", "t", check=True)
        ace = json.loads((self.sb.root / "wiki/.ace-telemetry.json").read_text(encoding="utf-8"))
        self.assertIn("va-a", ace["cards"])
        self.assertNotIn("foundations/visual_aesthetics/01_craft_playbooks/a.md", ace["cards"])

    def test_gotcha_root_cause_is_searchable(self) -> None:
        self.sb.run("study_runner.py", "record-gotcha", "--id", "g1", "--scope", "krita_2d_paint", "--symptom", "阴影发灰",
                    "--root-cause", "默认混合模式是正常而不是正片叠底", "--recipe", "改 Multiply", check=True)
        r = self.sb.run("wiki_query.py", "gotchas", "--query", "正片叠底", check=True)
        self.assertIn("g1", r.stdout)

    def test_sag2_two_hop_bridge_term_expansion(self) -> None:
        from wiki_query import search_wiki

        c_dir = self.sb.root / "wiki" / "foundations" / "visual_aesthetics" / "01_craft_playbooks"
        (c_dir / "card-a.md").write_text(card(
            "va-cyberpunk-neon-seed", "赛博朋克霓虹灯牌色彩控制", "当设计赛博朋克霓虹发光字与暗部环境色对比时使用；不适用于黑白线稿。",
            body="## 1. 霓虹发光色相铁律\n- **Rule 1**: 霓虹核心亮度保持 `L >= 0.88`。\n",
            tags="[cyberpunk-neon, oklab-gamut-clipping]"), encoding="utf-8")
        (c_dir / "card-b.md").write_text(card(
            "va-oklab-hdr-tone-mapping", "Oklab 广色域高光防过曝软膝映射", "当处理高饱和度光源防溢色裁剪时使用；不适用于纯文本排版。",
            body="## 1. Oklab Gamut Clipping 软膝阈值\n- **Rule 1**: 当色度 `C > 0.26` 时启用。\n",
            tags="[oklab-gamut-clipping, hdr-tone-mapping]"), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = search_wiki(self.sb.root, "赛博朋克霓虹")
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("2-Hop via: oklab-gamut-clipping", out)
        self.assertIn("va-oklab-hdr-tone-mapping", out)


if __name__ == "__main__":
    unittest.main()

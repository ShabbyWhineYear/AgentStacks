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

    def test_link_harnesses_copy_and_symlink_modes(self) -> None:
        for skill in ("study", "stacks", "organize"):
            dst = self.sb.root / ".agents" / "skills" / skill
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(FRAMEWORK_ROOT / ".agents" / "skills" / skill / "SKILL.md", dst / "SKILL.md")
            src_yaml = FRAMEWORK_ROOT / ".agents" / "skills" / skill / "agents" / "openai.yaml"
            if src_yaml.exists():
                (dst / "agents").mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_yaml, dst / "agents" / "openai.yaml")

        # Ensure WIKI-MANAGED markers exist on AGENTS.md before wiki_finalize.py runs
        agents_text = (self.sb.root / "AGENTS.md").read_text(encoding="utf-8")
        self.sb.write("CLAUDE.md", agents_text)
        self.sb.write("GEMINI.md", agents_text)

        # 1. Windows-safe --copy mode & parity --check
        self.sb.run("study_runner.py", "link-harnesses", "--copy", check=True)
        self.assertFalse((self.sb.root / "CLAUDE.md").is_symlink())
        self.assertFalse((self.sb.root / "GEMINI.md").is_symlink())
        self.assertTrue((self.sb.root / ".claude" / "commands" / "study.md").is_file())
        self.assertTrue((self.sb.root / ".cursor" / "rules" / "agentstacks.mdc").is_file())
        self.sb.run("study_runner.py", "link-harnesses", "--copy", "--check", check=True)
        self.sb.run("study_runner.py", "link-harnesses", "--check", check=True)

        # 2. Drift detection in --copy mode returns exit 1
        (self.sb.root / ".claude" / "commands" / "study.md").write_text("# drifted\n", encoding="utf-8")
        drifted = self.sb.run("study_runner.py", "link-harnesses", "--check")
        self.assertNotEqual(drifted.returncode, 0)

        # 3. POSIX symlink mode cleans duplicate .claude/commands and preserves symlinks across wiki_finalize.py
        self.sb.run("study_runner.py", "link-harnesses", check=True)
        self.assertTrue((self.sb.root / "CLAUDE.md").is_symlink())
        self.assertTrue((self.sb.root / "GEMINI.md").is_symlink())
        self.assertTrue((self.sb.root / ".claude" / "skills").is_symlink())
        self.assertTrue((self.sb.root / ".codex" / "skills").is_symlink())
        self.assertFalse((self.sb.root / ".claude" / "commands" / "study.md").exists())
        self.sb.run("wiki_finalize.py", check=True)
        self.assertTrue((self.sb.root / "CLAUDE.md").is_symlink())
        self.assertTrue((self.sb.root / "GEMINI.md").is_symlink())
        self.sb.run("study_runner.py", "link-harnesses", "--check", check=True)


class TestOkfV03Upgrades(EngineTestCase):
    def test_okf_v03_trust_tiers_and_human_review_downgrade(self) -> None:
        from wiki_core import compute_trust_tier, parse_markdown

        rel = "wiki/foundations/visual_aesthetics/01_craft_playbooks/trust.md"
        self.sb.write(rel, card("va-trust", "冷暖交界线控制", "当塑造角色明暗交界线冷暖过渡时使用；不适用于纯文字排版。"))
        self.sb.run("wiki_finalize.py", check=True)

        fm1 = parse_markdown((self.sb.root / rel).read_text(encoding="utf-8")).frontmatter
        self.assertEqual(compute_trust_tier(fm1), "machine-confirmed")
        self.assertTrue(any(ev.get("by", "").startswith("process:") for ev in fm1.get("verified", [])))

        # Stamp human review -> upgrades to human-reviewed and survives idempotent finalize
        self.sb.run("study_runner.py", "verify-card", "va-trust", "--by", "human:li", check=True)
        self.sb.run("wiki_finalize.py", check=True)
        fm2 = parse_markdown((self.sb.root / rel).read_text(encoding="utf-8")).frontmatter
        self.assertEqual(compute_trust_tier(fm2), "human-reviewed")
        r_read = self.sb.run("wiki_query.py", "read", "va-trust", check=True)
        self.assertIn("Trust: human-reviewed", r_read.stdout)

        # Agent modifies body after human review -> automatically downgrades back to machine-confirmed while keeping history
        raw_after = (self.sb.root / rel).read_text(encoding="utf-8") + "\n2. 阴影交界处饱和度提升 8%–12%。\n"
        (self.sb.root / rel).write_text(raw_after, encoding="utf-8")
        self.sb.run("wiki_finalize.py", check=True)
        fm3 = parse_markdown((self.sb.root / rel).read_text(encoding="utf-8")).frontmatter
        self.assertEqual(compute_trust_tier(fm3), "machine-confirmed")
        self.assertTrue(any(ev.get("by") == "human:li" for ev in fm3.get("verified", [])))

    def test_okf_v03_lifecycle_deprecated_superseded_and_stale_after(self) -> None:
        base = "wiki/foundations/visual_aesthetics/01_craft_playbooks/"
        self.sb.write(
            base + "old_api.md",
            card(
                "va-old-xenon-lut",
                "旧版氙气色彩查找表规范",
                "当配置氙气查找表参数时使用；不适用于黑白线稿。",
                status="deprecated",
                superseded_by="va-new-oklab-lut",
            ),
        )
        self.sb.write(
            base + "new_api.md",
            card(
                "va-new-oklab-lut",
                "新版 Oklab 色彩查找表规范",
                "当配置色彩查找表与广色域映射时使用；不适用于黑白线稿。",
                stale_after='"2020-01-01"',
            ),
        )
        self.sb.run("wiki_finalize.py", check=True)
        self.sb.run("wiki_linter.py", "--strict", "--no-tests", check=True)

        # Search for term only in deprecated card ('氙气') redirects to replacement & shows Stale badge
        r_s = self.sb.run("wiki_query.py", "search", "氙气查找表", check=True)
        self.assertIn("va-new-oklab-lut", r_s.stdout)
        self.assertIn("Supersedes: va-old-xenon-lut", r_s.stdout)
        self.assertIn("Stale: check again", r_s.stdout)

        r_r = self.sb.run("wiki_query.py", "read", "va-new-oklab-lut", check=True)
        self.assertIn("STALE REVIEW DUE", r_r.stdout)

        # Malformed stale_after is caught by linter
        self.sb.write(
            base + "bad_stale.md",
            card("va-bad-stale", "错误日期测试", "当测试日期校验时使用；不适用于生产。", stale_after='"not-a-valid-date"'),
        )
        r_lint = self.sb.run("wiki_linter.py", "--strict", "--no-tests")
        self.assertNotEqual(r_lint.returncode, 0)
        self.assertIn("stale_after", r_lint.stdout + r_lint.stderr)

    def test_okf_v03_structured_sources_and_footnote_attribution(self) -> None:
        from wiki_core import parse_markdown

        rel = "wiki/foundations/visual_aesthetics/01_craft_playbooks/fn.md"
        content = (
            "---\n"
            "id: va-gurney-fn\n"
            "type: craft-card\n"
            "title: 漫反射天光色温偏移\n"
            "description: 当计算室外阴影天光色温偏移时使用；不适用于纯UI图标。\n"
            "epistemic_status: canonical\n"
            "sources:\n"
            "  - id: gurney-ch4\n"
            "    resource: https://okf.md/examples/color-and-light\n"
            "    title: Color and Light Ch.4\n"
            "---\n\n"
            "## Rules\n"
            "1. 晴天背光面色温向 9000K–12000K 蓝紫偏移。[^gurney-ch4]\n"
        )
        self.sb.write(rel, content)
        self.sb.run("wiki_finalize.py", check=True)
        self.sb.run("wiki_linter.py", "--strict", "--no-tests", check=True)

        fm = parse_markdown((self.sb.root / rel).read_text(encoding="utf-8")).frontmatter
        self.assertIsInstance(fm["sources"][0], dict)
        self.assertEqual(fm["sources"][0]["id"], "gurney-ch4")

        # Undefined footnote reference is rejected by linter
        (self.sb.root / rel).write_text(content + "2. 未定义脚注测试。[^missing-src]\n", encoding="utf-8")
        r_bad = self.sb.run("wiki_linter.py", "--strict", "--no-tests")
        self.assertNotEqual(r_bad.returncode, 0)
        self.assertIn("missing-src", r_bad.stdout + r_bad.stderr)

    def test_okf_v03_attested_verifier_receipt_and_tamper_detection(self) -> None:
        v_rel = "wiki/foundations/visual_aesthetics/scripts/verifiers/check_palette_contrast.py"
        self.sb.write(
            v_rel,
            "import json, sys\n"
            "data = json.loads(open(sys.argv[1], encoding='utf-8').read())\n"
            "sys.exit(0 if data.get('contrast', 0) >= 0.2 else 1)\n",
        )
        c_rel = "wiki/foundations/visual_aesthetics/01_craft_playbooks/contrast.md"
        self.sb.write(
            c_rel,
            "---\n"
            "id: va-contrast-att\n"
            "type: craft-card\n"
            "title: 明度对比硬校验\n"
            "description: 当校验角色前景与背景明度差时使用；不适用于纯音频工程。\n"
            "epistemic_status: canonical\n"
            "scripts:\n"
            f"  - role: verifier\n    path: {v_rel}\n"
            "---\n\n## Rules\n1. 前景与背景明度差 >= 0.20。\n",
        )
        art = self.sb.root / "palette_artifact.json"
        art.write_text('{"contrast": 0.35}\n', encoding="utf-8")
        self.sb.run("wiki_finalize.py", check=True)

        self.sb.run(
            "study_runner.py",
            "verify-suite",
            "--domain",
            "visual_aesthetics",
            "--artifact",
            str(art),
            "--attest",
            check=True,
        )
        self.sb.run("study_runner.py", "attest-check", str(art), check=True)

        # Tamper with artifact after verification -> attest-check MUST fail
        art.write_text('{"contrast": 0.05}\n', encoding="utf-8")
        r_tampered = self.sb.run("study_runner.py", "attest-check", str(art))
        self.assertNotEqual(r_tampered.returncode, 0)
        self.assertIn("Artifact modified after verification", r_tampered.stderr)

    def test_okf_v03_zero_cdn_visualizer(self) -> None:
        base = "wiki/foundations/visual_aesthetics/01_craft_playbooks/"
        self.sb.write(base + "a.md", card("va-root", "基础明度公理", "当规划画面明度层级时使用；不适用于纯文本。"))
        self.sb.write(base + "b.md", card("va-child", "边缘光塑造", "当为主体添加轮廓光时使用；不适用于平面图标。", depends_on="[va-root]"))
        self.sb.run("wiki_finalize.py", check=True)

        out_html = self.sb.root / "scratch" / "viz.html"
        self.sb.run("study_runner.py", "visualize", "--out", str(out_html), check=True)
        self.assertTrue(out_html.is_file())
        html_txt = out_html.read_text(encoding="utf-8")
        self.assertNotIn("https://", html_txt)
        self.assertIn('"va-root"', html_txt)
        self.assertIn('"va-child"', html_txt)
        self.assertIn('"citedBy": ["va-child"]', html_txt.replace('["va-child"]', '["va-child"]') if '"citedBy":["va-child"]' not in html_txt else html_txt)
        self.assertIn("va-child", html_txt)


class TestDreamingPrefixCacheAndDiskCaps(EngineTestCase):
    def test_prefix_cache_pinning_invariants(self) -> None:
        base = "wiki/foundations/visual_aesthetics/01_craft_playbooks/"
        self.sb.write(base + "a.md", card("va-cache-a", "明度分层法则", "当规划前景与背景明度层次时使用；不适用于纯音频。"))
        agents_text = (self.sb.root / "AGENTS.md").read_text(encoding="utf-8")
        self.sb.write("GEMINI.md", agents_text)
        self.sb.write("CLAUDE.md", agents_text)
        self.sb.run("wiki_finalize.py", check=True)

        # Snapshot every workspace file outside .git/ before mid-turn search & read calls
        ws_files = [p for p in self.sb.root.rglob("*") if p.is_file() and ".git" not in p.parts]
        mtimes_before = {p: p.stat().st_mtime_ns for p in ws_files}
        l0_bytes_before = {
            name: (self.sb.root / name).read_bytes()
            for name in ("AGENTS.md", "GEMINI.md", "CLAUDE.md")
        }

        # 1. Mid-turn search + read MUST mutate ZERO workspace files (read stats live inside .git/)
        self.sb.run("wiki_query.py", "search", "明度分层", check=True)
        self.sb.run("wiki_query.py", "read", "va-cache-a", check=True)
        ws_after_read = [p for p in self.sb.root.rglob("*") if p.is_file() and ".git" not in p.parts]
        self.assertEqual(set(ws_files), set(ws_after_read))
        mutated_on_read = [p.name for p in ws_files if p.stat().st_mtime_ns != mtimes_before[p]]
        self.assertEqual(mutated_on_read, [], f"mid-turn search/read mutated workspace files: {mutated_on_read}")
        self.assertTrue((self.sb.root / ".git" / "agentstacks-read-stats.json").is_file())

        # 2. Adding a card in an existing domain + record-trace + finalize MUST keep Token-0 L0 files 100% byte-identical
        self.sb.write(base + "b.md", card("va-cache-b", "冷暖色温对比", "当搭配冷暖光源对比时使用；不适用于纯线稿。"))
        self.sb.run("study_runner.py", "record-trace", "--helpful", "va-cache-a", "--task", "test cache", check=True)
        for name, old_b in l0_bytes_before.items():
            self.assertEqual((self.sb.root / name).read_bytes(), old_b, f"Token-0 L0 file {name} mutated on card/ACE update!")

    def test_sleep_time_dream_plan_zero_waste_and_dream_seal(self) -> None:
        base = "wiki/foundations/visual_aesthetics/01_craft_playbooks/"
        self.sb.write(base + "clean.md", card("va-clean", "标准黄金分割", "当规划构图焦点位置时使用；不适用于纯文本。"))
        self.sb.run("wiki_finalize.py", check=True)

        # 1. Clean canonical library triggers 0-Token Short-Circuit immediately
        r0 = self.sb.run("study_runner.py", "dream-plan", "--json", check=True)
        d0 = json.loads(r0.stdout)
        self.assertTrue(d0["zeroTokenShortCircuit"])
        self.assertEqual(d0["candidateCount"], 0)

        # 2. Unverified empirical-refine note (P1) + Token Vampire read 5x with 0 helpful (P3) wake up in micro-batch
        self.sb.write(
            base + "emp.md",
            card(
                "va-emp-note",
                "实战临时边缘光经验",
                "当快速补画角色轮廓高光时使用；不适用于UI平面按钮。",
                epistemic_status="empirical-refine",
            ).replace("epistemic_status: canonical\n", ""),
        )
        self.sb.run("wiki_finalize.py", check=True)
        for _ in range(5):
            self.sb.run("wiki_query.py", "read", "va-clean", check=True)

        r1 = self.sb.run("study_runner.py", "dream-plan", "--max-batches", "1", "--budget-kb", "20", "--json", check=True)
        d1 = json.loads(r1.stdout)
        self.assertFalse(d1["zeroTokenShortCircuit"])
        woken_ids = {it["id"] for b in d1["batches"] for it in b["items"]}
        self.assertIn("va-emp-note", woken_ids)
        self.assertIn("va-clean", woken_ids)
        self.assertLessEqual(d1["batches"][0]["estimatedContextKb"], 20.0)

        # 3. Sealing dreamed cards puts them back to 0-Token sleep WITHOUT mutating .md card mtimes
        emp_mtime = (self.sb.root / (base + "emp.md")).stat().st_mtime_ns
        self.sb.run("study_runner.py", "dream-seal", "va-emp-note", "va-clean", check=True)
        self.assertEqual((self.sb.root / (base + "emp.md")).stat().st_mtime_ns, emp_mtime)

        r2 = self.sb.run("study_runner.py", "dream-plan", "--json", check=True)
        d2 = json.loads(r2.stdout)
        self.assertTrue(d2["zeroTokenShortCircuit"])
        self.assertEqual(d2["candidateCount"], 0)

    def test_auto_rolling_git_cap_and_backup_bundle_rotation(self) -> None:
        backup_dir = self.sb.root.parent / "rot_backups"
        env = {
            **GIT_ENV,
            "AGENTSTACKS_BACKUP_DIR": str(backup_dir),
            "AGENTSTACKS_MAX_GIT_COMMITS": "6",
            "AGENTSTACKS_KEEP_GIT_COMMITS": "4",
        }
        # Seed 6 plain commits first
        for i in range(6):
            self.sb.write("wiki/log.md", f"# Log\nentry {i}\n")
            self.sb.commit_all(f"seed-{i}")
        self.sb.write(
            "wiki/foundations/visual_aesthetics/01_craft_playbooks/c1.md",
            card("va-c1", "规则一", "当测试自动滚动压缩时使用；不适用于其他场景。"),
        )
        # Mock linter test runner off inside nested commit? No, commit runs lint_wiki; skip recursive pytest recursion via fast commit or direct compact check
        # First verify compact-git rotates bundles (`--keep-backups 1`) across two compactions
        r_c1 = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "study_runner.py"), "compact-git", "--keep", "4", "--keep-backups", "1"],
            cwd=self.sb.root, capture_output=True, text=True, env=env,
        )
        self.assertEqual(r_c1.returncode, 0, r_c1.stdout + r_c1.stderr)
        self.assertEqual(len(list(backup_dir.glob("*.bundle"))), 1)

        for i in range(6, 9):
            self.sb.write("wiki/log.md", f"# Log\nentry {i}\n")
            self.sb.commit_all(f"seed-{i}")
        r_c2 = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "study_runner.py"), "compact-git", "--keep", "4", "--keep-backups", "1"],
            cwd=self.sb.root, capture_output=True, text=True, env=env,
        )
        self.assertEqual(r_c2.returncode, 0, r_c2.stdout + r_c2.stderr)
        # Must STILL have only 1 bundle (the older bundle was auto-pruned!) and 5 commits (1 squashed root + 4 kept)
        self.assertEqual(len(list(backup_dir.glob("*.bundle"))), 1)
        self.assertEqual(self.sb.git("--no-replace-objects", "rev-list", "--count", "HEAD"), "5")

    def test_concurrent_multi_agent_record_trace_lock(self) -> None:
        self.sb.write(
            "wiki/foundations/visual_aesthetics/01_craft_playbooks/lock.md",
            card("va-lock", "并发锁测试卡", "当验证多进程并发记账安全时使用；不适用于纯音频。"),
        )
        self.sb.run("wiki_finalize.py", check=True)
        procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "study_runner.py"),
                    "record-trace",
                    "--helpful",
                    "va-lock",
                    "--task",
                    f"worker-{i}",
                ],
                cwd=self.sb.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=GIT_ENV,
            )
            for i in range(4)
        ]
        for p in procs:
            out, err = p.communicate(timeout=20)
            self.assertEqual(p.returncode, 0, out + err)
        ace = json.loads((self.sb.root / "wiki" / ".ace-telemetry.json").read_text(encoding="utf-8"))
        self.assertEqual(ace["cards"]["va-lock"]["helpful"], 4)
        self.assertEqual(len(ace["recentTraces"]), 4)

    def test_commit_with_child_domain_test_calling_wiki_query_read_never_deadlocks(self) -> None:
        self.sb.write(
            "wiki/foundations/visual_aesthetics/01_craft_playbooks/reentrant.md",
            card("va-reentrant", "子进程锁重入测试卡", "当验证 commit 内嵌 domain 测试调用 wiki_query read 不死锁时使用；不适用于音频。"),
        )
        query_script = str(SCRIPTS_DIR / "wiki_query.py")
        self.sb.write(
            "wiki/foundations/visual_aesthetics/scripts/tests/test_child_read.py",
            f"""import subprocess, sys, unittest
class TestChildRead(unittest.TestCase):
    def test_read_during_commit_lint(self):
        res = subprocess.run([sys.executable, {query_script!r}, "read", "va-reentrant"], capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, res.stderr)
""",
        )
        res = self.sb.run(
            "study_runner.py",
            "commit",
            "--verb",
            "refine",
            "--domain",
            "foundations/visual_aesthetics",
            "--summary",
            "verify child subprocess read during commit never deadlocks",
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class TestEmpiricalAuditV34Fixes(EngineTestCase):
    def test_read_funnel_tracks_one_hop_deps_and_subtree_core_cards(self) -> None:
        from wiki_core import load_implicit_read_stats

        base = "wiki/foundations/visual_aesthetics/"
        self.sb.write(
            base + "00_core_aesthetics/core_law.md",
            card("va-core-law", "明度对比核心红线", "当校验主次明度对比时使用；不适用于纯文本。"),
        )
        for i in range(10):
            self.sb.write(
                f"{base}01_craft_playbooks/c{i}.md",
                card(
                    f"va-c{i}",
                    f"色彩技法 {i}",
                    f"当绘制色彩练习 {i} 时使用；不适用于纯线稿。",
                    depends_on="[va-core-law]" if i == 0 else "[]",
                    tags="[color-cluster]",
                ),
            )
        self.sb.run("wiki_finalize.py", check=True)

        # 1. Reading va-c0 also emits 1-Hop Contract Digest of va-core-law -> BOTH must be recorded in read stats!
        self.sb.run("wiki_query.py", "read", "va-c0", check=True)
        stats1 = load_implicit_read_stats(self.sb.root)
        self.assertEqual(stats1.get("va-c0", (0, 0))[0], 1)
        self.assertEqual(stats1.get("va-core-law", (0, 0))[0], 1)

        # 2. Reading an L2 subtree emits Scoped 00_Core Contract Digests -> increments va-core-law too!
        s_idx = json.loads((self.sb.root / "wiki" / ".search-index.json").read_text(encoding="utf-8"))
        st_slug = s_idx["subtrees"][0]["slug"]
        self.sb.run("wiki_query.py", "read", "--subtree", f"visual_aesthetics/{st_slug}", check=True)
        stats2 = load_implicit_read_stats(self.sb.root)
        self.assertEqual(stats2.get("va-core-law", (0, 0))[0], 2)

        # 3. Search output prints explicit python3 wiki_query.py read command
        r_s = self.sb.run("wiki_query.py", "search", "色彩技法", check=True)
        self.assertIn("python3 .agents/skills/study/scripts/wiki_query.py read", r_s.stdout)

    def test_reconcile_telemetry_script_dedup_alias_migration_and_trace_archive(self) -> None:
        self.sb.write(
            "wiki/foundations/visual_aesthetics/01_craft_playbooks/merged.md",
            card(
                "va-canonical-merged",
                "合并后的透视法则",
                "当检查透视灭点时使用；不适用于纯音频。",
                aliases="[va-legacy-old-id]",
            ),
        )
        self.sb.run("wiki_finalize.py", check=True)
        self.sb.run(
            "study_runner.py",
            "record-gotcha",
            "--id",
            "gotcha-sample",
            "--scope",
            "visual_aesthetics",
            "--symptom",
            "s",
            "--root-cause",
            "r",
            "--recipe",
            "fix",
            check=True,
        )

        # Seed polluted legacy .ace-telemetry.json (split .rs script keys, old alias, misplaced gotcha, dead key, >40 traces)
        ace_path = self.sb.root / "wiki" / ".ace-telemetry.json"
        polluted = {
            "schemaVersion": 1,
            "cards": {
                "va-canonical-merged": {"helpful": 3, "harmful": 0, "last_used_at": "2026-11-20", "last_task": "t1", "queries": ["q1"]},
                "va-legacy-old-id": {"helpful": 5, "harmful": 1, "last_used_at": "2026-11-21", "last_task": "t2", "queries": ["q2"]},
                "gotcha-sample": {"helpful": 2, "harmful": 0},
                "totally-retired-card": {"helpful": 4, "harmful": 0},
            },
            "scripts": {
                "reaper_rpp_render": {"helpful": 10, "harmful": 0, "last_used_at": "2026-11-20", "last_task": "s1"},
                "reaper_rpp_render.rs": {"helpful": 4, "harmful": 0, "last_used_at": "2026-11-22", "last_task": "s2"},
            },
            "recentTraces": [{"at": f"2026-11-01T00:00:{i:02d}Z", "task": f"old-{i}"} for i in range(43)],
        }
        ace_path.write_text(json.dumps(polluted, ensure_ascii=False, indent=2), encoding="utf-8")

        self.sb.run("study_runner.py", "reconcile-telemetry", check=True)
        cleaned = json.loads(ace_path.read_text(encoding="utf-8"))
        self.assertEqual(list(cleaned["cards"].keys()), ["va-canonical-merged"])
        self.assertEqual(cleaned["cards"]["va-canonical-merged"]["helpful"], 8)
        self.assertEqual(cleaned["cards"]["va-canonical-merged"]["harmful"], 1)
        self.assertEqual(list(cleaned["scripts"].keys()), ["reaper_rpp_render"])
        self.assertEqual(cleaned["scripts"]["reaper_rpp_render"]["helpful"], 14)
        self.assertEqual(len(cleaned["recentTraces"]), 40)
        self.assertTrue((self.sb.root / "wiki" / ".archive" / "ace_traces_history.jsonl").is_file())
        self.assertTrue((self.sb.root / "wiki" / ".archive" / "ace_retired_keys.json").is_file())

        # Subsequent record-trace with `.rs` suffix folds cleanly into stem without splitting
        self.sb.run(
            "study_runner.py",
            "record-trace",
            "--helpful",
            "va-legacy-old-id",
            "--scripts",
            "wiki/tools_craft/daw_music_prod/scripts/generators/reaper_rpp_render.rs",
            "--task",
            "new-run",
            check=True,
        )
        after = json.loads(ace_path.read_text(encoding="utf-8"))
        self.assertEqual(after["cards"]["va-canonical-merged"]["helpful"], 9)
        self.assertEqual(after["scripts"]["reaper_rpp_render"]["helpful"], 15)
        self.assertNotIn("reaper_rpp_render.rs", after["scripts"])

    def test_record_gap_and_project_gotcha_isolation_and_byte_cap(self) -> None:
        (self.sb.root / "wiki" / "foundations" / "visual_aesthetics").mkdir(parents=True, exist_ok=True)
        # 1. record-gap works without requiring --books (Observe Mode)
        self.sb.write("wiki/CURRICULUM_GAPS.md", "# Gaps\n\n<!-- WIKI-MANAGED:START -->\n<!-- WIKI-MANAGED:END -->\n")
        self.sb.run(
            "study_runner.py",
            "record-gap",
            "--domain",
            "foundations/embodied_physics",
            "--topic",
            "MuJoCo 接触动力学与多室树突脉冲网络",
            "--reason",
            "具身仿真任务缺少接触力与主动树突教材",
            check=True,
        )
        gaps_md = (self.sb.root / "wiki" / "CURRICULUM_GAPS.md").read_text(encoding="utf-8")
        self.assertIn("MuJoCo 接触动力学与多室树突脉冲网络", gaps_md)

        # 2. Project-scoped gotcha routes to _project_and_sim_gotchas.md, NOT visual_aesthetics/_domain_gotchas.md
        self.sb.run(
            "study_runner.py",
            "record-gotcha",
            "--id",
            "proj-atom-head-offset",
            "--scope",
            "projects/atom / mocap head",
            "--project",
            "atom",
            "--symptom",
            "head offset",
            "--root-cause",
            "constant spine",
            "--recipe",
            "lock 0.468",
            check=True,
        )
        self.assertTrue((self.sb.root / "wiki" / "_project_and_sim_gotchas.md").is_file())
        va_dg = (self.sb.root / "wiki" / "foundations" / "visual_aesthetics" / "_domain_gotchas.md").read_text(encoding="utf-8")
        self.assertNotIn("proj-atom-head-offset", va_dg)

        # 3. 30 verbose domain gotchas still obey <= 12 KB cap on _domain_gotchas.md
        for i in range(30):
            self.sb.run(
                "study_runner.py",
                "record-gotcha",
                "--id",
                f"va-gotcha-{i:02d}",
                "--scope",
                "visual_aesthetics",
                "--symptom",
                "症状描述" * 45,
                "--root-cause",
                "根因描述" * 45,
                "--recipe",
                "标准解法" * 55,
                check=True,
            )
        va_dg_bytes = len((self.sb.root / "wiki" / "foundations" / "visual_aesthetics" / "_domain_gotchas.md").read_bytes())
        self.assertLessEqual(va_dg_bytes, 12288)

    def test_tier_inversion_advice_detected_by_linter(self) -> None:
        self.sb.write(
            "wiki/tools_craft/krita_2d_paint/01_craft_playbooks/tool.md",
            card("krita-tool", "Krita 图层工具", "当操作 Krita 图层时使用；不适用于音频。", depends_on="[va-base]"),
        )
        self.sb.write(
            "wiki/foundations/visual_aesthetics/00_core_aesthetics/base.md",
            card("va-base", "底层色彩法则", "当判断色彩冷暖时使用；不适用于纯文本。", depends_on="[krita-tool]"),
        )
        self.sb.run("wiki_finalize.py", check=True)
        r = self.sb.run("wiki_linter.py", "--no-tests", check=True)
        self.assertIn("[tier-inversion]", r.stdout)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Domain & Tool Directory Scaffolder for the 3-Tier LLM Wiki.

Initializes `INSTRUCTIONS.md` (human-owned brief) and `quickstart.md` (Task-Routing Map
with `requires_foundations` matrix) for all initial domains, and can be called anytime
to scaffold a new foundation, tool_craft, or workflow module.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wiki_core import find_repo_root

INITIAL_MODULES = [
    # Tier 1: Foundations
    {
        "tier": "foundations",
        "name": "visual_aesthetics",
        "title": "视觉美学与色彩光影科学 (Visual Aesthetics)",
        "desc": "色彩与光影理论、明度对比、色相冷暖偏移、构图视线引导、透视空间与造型剪影公理。",
        "requires": [],
        "matrix": [],
    },
    {
        "tier": "foundations",
        "name": "cinematography_staging",
        "title": "视听语言与分镜调度 (Cinematography & Staging)",
        "desc": "镜头焦段心理、机位高度与透视张力、180度轴线法则、画面景别与蒙太奇节奏。",
        "requires": ["foundations/visual_aesthetics"],
        "matrix": [],
    },
    {
        "tier": "foundations",
        "name": "motion_animation",
        "title": "运动规律与原画表演 (Motion & Animation Principles)",
        "desc": "动画十二法则、关键帧动态线(Line of Action)、预备与缓冲、时间间距(Timing & Spacing)。",
        "requires": ["foundations/visual_aesthetics"],
        "matrix": [],
    },
    {
        "tier": "foundations",
        "name": "music_theory_acoustics",
        "title": "乐理、和声与声学心理 (Music Theory & Acoustics)",
        "desc": "调式音阶、功能和声与次属张力、四部声部连接法则、配器频段掩蔽与混音声学标准。",
        "requires": [],
        "matrix": [],
    },
    {
        "tier": "foundations",
        "name": "narrative_dramaturgy",
        "title": "叙事学与戏剧冲突结构 (Narrative & Dramaturgy)",
        "desc": "戏剧冲突密度、情绪起伏弧线、潜台词设计、漫画/剧本分场张力控制。",
        "requires": [],
        "matrix": [],
    },
    {
        "tier": "foundations",
        "name": "ludology_systems",
        "title": "游戏机制与数值系统理论 (Ludology & Game Systems)",
        "desc": "30秒核心心流循环、风险回报博弈、数值成长曲线、经济系统均衡与关卡拓扑设计。",
        "requires": [],
        "matrix": [],
    },
    # Tier 2: Tools Craft
    {
        "tier": "tools_craft",
        "name": "aseprite_pixel",
        "title": "Aseprite 像素美术与动画技艺 (Aseprite Pixel Craft)",
        "desc": "受限索引色板管理、像素手工抗锯齿(AA)、抖动纹理(Dithering)、精灵表导出与 Lua/CLI 自动化。",
        "requires": ["foundations/visual_aesthetics", "foundations/motion_animation"],
        "matrix": [],
    },
    {
        "tier": "tools_craft",
        "name": "krita_2d_paint",
        "title": "Krita 原画关键帧、清线与赛璐珞上色 (Krita 2D Paint)",
        "desc": "漫画转动画关键帧(LO)、矢量化线稿清理、Alpha继承分层上色、Colorize Mask与Krita Python脚本。",
        "requires": [
            "foundations/visual_aesthetics",
            "foundations/cinematography_staging",
            "foundations/motion_animation",
        ],
        "matrix": [],
    },
    {
        "tier": "tools_craft",
        "name": "blender_3d_layout",
        "title": "Blender 3D 场景搭建、透视与分镜相机 (Blender 3D Layout)",
        "desc": "3D辅助2D透视白模搭建、摄像机焦段与景深绑定、NPR三渲二灯光节点、bpy自动化布局脚本。",
        "requires": ["foundations/visual_aesthetics", "foundations/cinematography_staging"],
        "matrix": [],
    },
    {
        "tier": "tools_craft",
        "name": "ae_compositing",
        "title": "After Effects 摄影后期合成与调色 (AE Compositing)",
        "desc": "动画摄影表合成、空气透视光效(Para/Flare)、Z-Depth景深模糊、色指定微调与自动化合成脚本。",
        "requires": ["foundations/visual_aesthetics", "foundations/cinematography_staging"],
        "matrix": [],
    },
    {
        "tier": "tools_craft",
        "name": "daw_music_prod",
        "title": "DAW 编曲宿主工程、配器与混音自动化 (DAW Music Production)",
        "desc": "MIDI轨道编排、合成器音色调制、EQ/压缩动态处理、母带响度控制与MIDI生成脚本。",
        "requires": ["foundations/music_theory_acoustics"],
        "matrix": [],
    },
    # Tier 3: Workflows
    {
        "tier": "workflows",
        "name": "2d_game_production",
        "title": "2D 游戏全流程资产与系统生产管线 (2D Game Production)",
        "desc": "串联游戏机制设计(ludology)、视觉美学标准、Aseprite像素资产管线与游戏配乐音效。",
        "requires": [
            "foundations/ludology_systems",
            "foundations/visual_aesthetics",
            "foundations/motion_animation",
            "foundations/music_theory_acoustics",
        ],
        "matrix": [],
    },
    {
        "tier": "workflows",
        "name": "manga_to_anime_pipeline",
        "title": "漫改动画全流程生产管线 (Manga-to-Anime Pipeline: Blender -> Krita -> AE)",
        "desc": "从漫画分镜解析 -> Blender 3D场景透视与机位 -> Krita原画关键帧、清线与赛璐珞上色 -> AE摄影后期合成。",
        "requires": [
            "foundations/narrative_dramaturgy",
            "foundations/cinematography_staging",
            "foundations/visual_aesthetics",
            "foundations/motion_animation",
        ],
        "matrix": [],
    },
]


def scaffold_module(
    repo_root: Path,
    tier: str,
    name: str,
    title: str,
    desc: str,
    requires: list[str],
    matrix: list[tuple[str, str, str]],
) -> None:
    mod_dir = repo_root / "wiki" / tier / name
    for sub in ("00_core_aesthetics", "01_craft_playbooks", "02_style_specifics", "03_deep_concepts"):
        (mod_dir / sub).mkdir(parents=True, exist_ok=True)
    for sc_sub in ("generators", "analyzers", "verifiers", "tests"):
        (mod_dir / "scripts" / sc_sub).mkdir(parents=True, exist_ok=True)
    (repo_root / "raw" / tier / name).mkdir(parents=True, exist_ok=True)

    inst_path = mod_dir / "INSTRUCTIONS.md"
    if not inst_path.exists():
        inst_lines = [
            "---",
            "type: orientation-guide",
            f'title: "{title} — 人类钦定宪章 (Human Instructions)"',
            f'description: "Human-owned scope, aesthetic priorities, and non-negotiable guardrails for {tier}/{name}."',
            "---",
            "",
            f"# {title} — 人类最高宪章 (`INSTRUCTIONS.md`)",
            "",
            "> [!IMPORTANT]",
            "> 本文件由用户拥有所有权，Agent 在 `/study`、`wiki-refine` 和日常执行中**必须首先读取并遵守本文件**，但未经用户明确授意**严禁覆写本文件**。",
            "",
            "## 核心关注方向与审美优先级",
            f"- **模块定位**: {desc}",
            "- **质量原则**: 优先提炼可复用的结构化方法论与可量化的 Verifier/Generator 脚本，拒绝空洞的形容词堆砌。",
        ]
        if requires:
            inst_lines.append(
                f"- **跨层强制联动**: 执行本模块任何任务时，必须同步加载关联底层学科：{', '.join(f'`{r}`' for r in requires)}。"
            )
        inst_path.write_text("\n".join(inst_lines) + "\n", encoding="utf-8")

    qs_path = mod_dir / "quickstart.md"
    req_yaml = (
        "requires_foundations:\n" + "\n".join(f"  - {r}" for r in requires)
        if requires
        else "requires_foundations: []"
    )
    qs_lines = [
        "---",
        "type: orientation-guide",
        f'title: "{title} — 快速任务路由 (Quickstart)"',
        f'description: "{desc}"',
        req_yaml,
        "---",
        "",
        f"# {title} — 任务路由地图 (`quickstart.md`)",
        "",
        f"**职责边界**：{desc}",
        "",
    ]
    if requires:
        qs_lines.extend(
            [
                "## 1. 跨层强依赖底层学科 (`requires_foundations`)",
                "凡进入本模块执行任务，**严禁只读本模块卡片**，必须根据子任务同步加载以下底层基础学科：",
            ]
        )
        for r in requires:
            qs_lines.append(f"- [`wiki/{r}/quickstart.md`](../../{r}/quickstart.md) (`{r}`)")
        qs_lines.append("")

    qs_lines.extend(
        [
            "## 2. 子任务 $\\leftrightarrow$ 卡片与脚本联动矩阵 (Task-to-Foundation Matrix)",
            "",
            "| 具体子任务场景 | 必读技法卡 / 跨层绑定卡片 (`wiki_query.py read`) | 强制调用生成/校验脚本 (`scripts/`) |",
            "| :--- | :--- | :--- |",
        ]
    )
    if matrix:
        for task_name, cards_ref, script_ref in matrix:
            qs_lines.append(f"| **{task_name}** | `{cards_ref}` | `{script_ref}` |")
    else:
        qs_lines.append("| *(待通过 `/study` 导入首部教材)* | — | — |")
    qs_lines.append("")
    qs_lines.append("## 3. 全量目录索引")
    qs_lines.append("- 参见自动生成的 [_domain_index.md](_domain_index.md)")
    qs_path.write_text("\n".join(qs_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold initial or new 3-Tier LLM Wiki modules.")
    parser.add_argument("--add", nargs=3, metavar=("TIER", "NAME", "TITLE"), help="Add a custom module")
    parser.add_argument("--desc", default="Custom domain module", help="Description when using --add")
    parser.add_argument("--requires", nargs="*", default=[], help="Required foundation domains when using --add")
    args = parser.parse_args()

    repo_root = find_repo_root()
    if args.add:
        tier, name, title = args.add
        scaffold_module(repo_root, tier, name, title, args.desc, args.requires, [])
        print(f"✅ Scaffolded new module wiki/{tier}/{name}")
        return 0

    for mod in INITIAL_MODULES:
        scaffold_module(
            repo_root,
            mod["tier"],
            mod["name"],
            mod["title"],
            mod["desc"],
            mod["requires"],
            mod["matrix"],
        )
    print(f"✅ Scaffolded {len(INITIAL_MODULES)} initial modules across Foundations, Tools Craft, and Workflows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

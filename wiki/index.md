---
okf_version: "0.2"
title: Three-Tier LLM Wiki Root Index
description: "Root MoE & Sirchmunk Tree Router across Foundations, Tools Craft, and Composite Workflows."
---

# LLM Wiki 全域总索引 (Root Tree Router — `LLM-Wiki × Sirchmunk`)

- **RSI 自进化记分板与 ACE 命中看板**: [SCOREBOARD.md](SCOREBOARD.md)
- **排错防踩坑分级树账本 (Scoped Rules That Save Retries)**: [GOTCHAS_AND_RETRY_SAVERS.md](GOTCHAS_AND_RETRY_SAVERS.md)
- **AWM 参数化工作流记忆库 (Workflow Memory)**: [AWM_WORKFLOWS.md](AWM_WORKFLOWS.md)
- **知识缺口与主动求知榜单**: [CURRICULUM_GAPS.md](CURRICULUM_GAPS.md)
- **全局演进审计日志**: [log.md](log.md)

## Tier 1: 底层基础学科层 (`wiki/foundations/`) — 跨工具权威美学与科学公理

| 领域/模块 | L1/L2 树形入口路由 | 词条数 (`Canonical` / `Provisional`) | 脚本数 | 强制关联底层学科 (`requires_foundations`) | 职责边界说明 |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **`cinematography_staging`** | [quickstart](foundations/cinematography_staging/quickstart.md) · [index](foundations/cinematography_staging/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/visual_aesthetics` | 镜头焦段心理、机位高度与透视张力、180度轴线法则、画面景别与蒙太奇节奏。 |
| **`ludology_systems`** | [quickstart](foundations/ludology_systems/quickstart.md) · [index](foundations/ludology_systems/_domain_index.md) | 0 (`0` / `0`) | 0 | — | 30秒核心心流循环、风险回报博弈、数值成长曲线、经济系统均衡与关卡拓扑设计。 |
| **`motion_animation`** | [quickstart](foundations/motion_animation/quickstart.md) · [index](foundations/motion_animation/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/visual_aesthetics` | 动画十二法则、关键帧动态线(Line of Action)、预备与缓冲、时间间距(Timing & Spacing)。 |
| **`music_theory_acoustics`** | [quickstart](foundations/music_theory_acoustics/quickstart.md) · [index](foundations/music_theory_acoustics/_domain_index.md) | 0 (`0` / `0`) | 0 | — | 调式音阶、功能和声与次属张力、四部声部连接法则、配器频段掩蔽与混音声学标准。 |
| **`narrative_dramaturgy`** | [quickstart](foundations/narrative_dramaturgy/quickstart.md) · [index](foundations/narrative_dramaturgy/_domain_index.md) | 0 (`0` / `0`) | 0 | — | 戏剧冲突密度、情绪起伏弧线、潜台词设计、漫画/剧本分场张力控制。 |
| **`visual_aesthetics`** | [quickstart](foundations/visual_aesthetics/quickstart.md) · [index](foundations/visual_aesthetics/_domain_index.md) | 0 (`0` / `0`) | 0 | — | 色彩与光影理论、明度对比、色相冷暖偏移、构图视线引导、透视空间与造型剪影公理。 |

## Tier 2: 软件工具与媒介技艺层 (`wiki/tools_craft/`) — 软件实操与自动化脚本

| 领域/模块 | L1/L2 树形入口路由 | 词条数 (`Canonical` / `Provisional`) | 脚本数 | 强制关联底层学科 (`requires_foundations`) | 职责边界说明 |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **`ae_compositing`** | [quickstart](tools_craft/ae_compositing/quickstart.md) · [index](tools_craft/ae_compositing/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/visual_aesthetics`, `foundations/cinematography_staging` | 动画摄影表合成、空气透视光效(Para/Flare)、Z-Depth景深模糊、色指定微调与自动化合成脚本。 |
| **`aseprite_pixel`** | [quickstart](tools_craft/aseprite_pixel/quickstart.md) · [index](tools_craft/aseprite_pixel/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/visual_aesthetics`, `foundations/motion_animation` | 受限索引色板管理、像素手工抗锯齿(AA)、抖动纹理(Dithering)、精灵表导出与 Lua/CLI 自动化。 |
| **`blender_3d_layout`** | [quickstart](tools_craft/blender_3d_layout/quickstart.md) · [index](tools_craft/blender_3d_layout/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/visual_aesthetics`, `foundations/cinematography_staging` | 3D辅助2D透视白模搭建、摄像机焦段与景深绑定、NPR三渲二灯光节点、bpy自动化布局脚本。 |
| **`daw_music_prod`** | [quickstart](tools_craft/daw_music_prod/quickstart.md) · [index](tools_craft/daw_music_prod/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/music_theory_acoustics` | MIDI轨道编排、合成器音色调制、EQ/压缩动态处理、母带响度控制与MIDI生成脚本。 |
| **`krita_2d_paint`** | [quickstart](tools_craft/krita_2d_paint/quickstart.md) · [index](tools_craft/krita_2d_paint/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/visual_aesthetics`, `foundations/cinematography_staging`, `foundations/motion_animation` | 漫画转动画关键帧(LO)、矢量化线稿清理、Alpha继承分层上色、Colorize Mask与Krita Python脚本。 |

## Tier 3: 复合生产管线层 (`wiki/workflows/`) — 多工具跨学科协同流水线

| 领域/模块 | L1/L2 树形入口路由 | 词条数 (`Canonical` / `Provisional`) | 脚本数 | 强制关联底层学科 (`requires_foundations`) | 职责边界说明 |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **`2d_game_production`** | [quickstart](workflows/2d_game_production/quickstart.md) · [index](workflows/2d_game_production/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/ludology_systems`, `foundations/visual_aesthetics`, `foundations/motion_animation`, `foundations/music_theory_acoustics` | 串联游戏机制设计(ludology)、视觉美学标准、Aseprite像素资产管线与游戏配乐音效。 |
| **`manga_to_anime_pipeline`** | [quickstart](workflows/manga_to_anime_pipeline/quickstart.md) · [index](workflows/manga_to_anime_pipeline/_domain_index.md) | 0 (`0` / `0`) | 0 | `foundations/narrative_dramaturgy`, `foundations/cinematography_staging`, `foundations/visual_aesthetics`, `foundations/motion_animation` | 从漫画分镜解析 -> Blender 3D场景透视与机位 -> Krita原画关键帧、清线与赛璐珞上色 -> AE摄影后期合成。 |

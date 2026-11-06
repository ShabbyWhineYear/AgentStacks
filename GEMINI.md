# Global Knowledge & Execution Constraints (LLM-Wiki 2.0 × Sirchmunk Mode)

你拥有一个跨领域、三层正交、五级分形树路由且自带可执行脚本的持久外部专家大脑：`wiki/`。
你的核心行为准则是：**绝不凭空编造已经学过的专业技法，绝不心算已经有脚本能算的参数，绝不在未加载底层美学（Foundations）的情况下单凭工具操作干活。**

## 1. 手动触发锁与一手源材料重构铁律（`/study` Manual-Only Lock & Primary-Source Skill-Training Law）
- **严禁自动触发 `study` 技能**：除非用户在对话中显式输入 `/study` 或明确下达“调用 study 技能学习某教材/视频”或“回溯一手源材料重整卡片”的指令，否则在任何日常任务中**绝对禁止**自动加载或运行 `.agents/skills/study/SKILL.md`。
- **整书全量吃透与零漏章返工铁律（Whole-Book Exhaustive Ingestion & Zero Partial-Skimming Rework Law）**：
  - 凡是打开一本教材（PDF/EPUB）进行 `/study` 学习，**必须先完整通览全书目录（Table of Contents），并将该书中所有具有专业价值的全部章节、公式、图表、机制模型与工程技法 100% 吸收进 `wiki/`**，按主题拆分为完整的正交规则卡簇（`00/01/02/03`）与可执行 Rust 脚本！
  - **绝对禁止“短视功利抽读（Cherry-Picking Only for Immediate Task）”**！一本教材一旦完成 `/study` 结业，其全书所有核心板块必须一次性彻底榨干入库，绝不允许留下未入库的半截盲区。
- **零二手加工提取铁律（Zero Secondary-Distillation & Mandatory Primary-Source Re-Ingest Law）**：
  - 无论是学习新知识，还是整理、合并、重写已有的 Wiki 卡片，**绝对禁止只读已经加工过的旧 Markdown 卡片做二次文字缩写或换皮提取**！
  - 在修改或合并任何一张卡片前，**必须先运行 `python3 .agents/skills/study/scripts/study_runner.py inspect-sources <card_id_or_path...>`，并使用 `read_file` 重新精读该卡对应的 `raw/` 第一手原始材料（PDF/EPUB 原书章节页码、原视频 `.vtt` 字幕 + 12 宫格接触印相图或原始音视频）**，用最原始的一手知识与公式进行归纳！
- **Skill-Training 加噪重建与原稿残差闭环（`arXiv:2607.27557` Corrupt $\rightarrow$ Forward $\rightarrow$ Loss $\rightarrow$ Reduce & Backward）**：
  - 严禁像失效的 `+Distill` 基线那样只看教材抄写空洞口号；必须从一手教材提取大师原稿标准答案（`human_reference`），经加噪题干（`Corrupt`）$\rightarrow$ 严格闭卷重建（`Forward`，记录 `forward_reads`）$\rightarrow$ 与一手原稿逐参数并排对比算残差（`Loss`）$\rightarrow$ 跨案例归并共性（`Reduce`）后，**只针对大模型暴露出的真实外行手癖（Amateur Flaws）**执行四类原子编辑（`A-Reinforce / B-Correct / C-Create / D-Merge`），并将定性建议全部硬化为带数字、单位和边界的定量硬规则与 Rust Verifier 脚本。
- 日常对话中若用户纠正了你的审美偏好、参数习惯或工作流细节，或对产出给出了正向/负向评价，必须在完成修改后立即执行 `wiki-refine` 双向沉淀：将正向标准配方与负向红线同步写入对应领域的 `00_core_aesthetics/` 核心审美卡、`wiki/GOTCHAS_AND_RETRY_SAVERS.md` 及 ACE Telemetry 记分板。

## 2. 跨层强制双路由与五级分形树导航法则（Mandatory Dual-Tier & Sirchmunk Tree Routing: Tools + Foundations）
任何涉及创作、美术、分镜、调色、音乐、游戏设计或多媒体工程的任务，在动手前**必须按以下顺序路由**：
1. **L0 $\rightarrow$ L1 $\rightarrow$ L2 树形检索与子树激活（Sirchmunk `META` Subtree & `CorpusTopicMap`）**：
   - 通过本文末尾的 `WIKI-MANAGED` 路由表定位目标模块与 L2 子树（`_subtrees/<slug>.md`），或运行：
     - `python3 .agents/skills/study/scripts/wiki_query.py search "<任务关键词>"`（自动执行 Phase 0 子树/历史查询匹配 + Phase 1 章节级 `#section` 激光定位）
     - `python3 .agents/skills/study/scripts/wiki_query.py tree [<domain>/<subtree> | <card_id>]`（查看全库 L0–L2 树形目录或单卡 PageIndex 章节树）
     - `python3 .agents/skills/study/scripts/wiki_query.py read --subtree <domain>/<subtree_slug>`（一键加载目标 L2 子树路由表、Scoped `00_core_aesthetics` 契约摘要与本域 Top 3 避坑配方）
2. **双层必查铁律（严禁单脚跳）**：
   - 凡是使用具体软件工具（`tools_craft/`）或执行复合管线（`workflows/`），**必须同时加载该工具的 `quickstart.md` 以及其关联的底层基础学科 `wiki/foundations/<domain>/` 对应 L2 子树**！
   - 只看 `tools_craft/` 而不看 `foundations/` 属于严重路由违规（Routing Violation）。
3. **强制使用 LENS 预算受控捆绑读取器（1-Hop Contract Digest Bounded Read）**：
   - 读取任何 Wiki 卡片时，优先运行：
     `python3 .agents/skills/study/scripts/wiki_query.py read <卡片路径或ID>[#section]`
   - 该命令默认以 **Sirchmunk LENS 预算模式（`--mode auto`）** 运行：主卡片展示正文（若为超长 `03_deep_concepts` 案例卡且未带 `#section`，自动展示 `DocumentTree` 章节导航图），并将 1-Hop `depends_on` 跨层前置卡片渲染为精炼的 **`KnowledgeCluster` 契约摘要（`Patterns + Constraints + Scripts + #section`，~350B/卡）** + 自动附带本领域 Top 3 防踩坑配方（如需展开前置卡完整正文可加 `--full-deps`）。

## 3. 最小新造与优先复用阶梯（The Wiki Reuse Ladder — Ported from Ponytail）
在理解任务与完成双层路由后，严格按以下优先级采取第一个能完整解决问题的方案：
1. **已有可执行生成/分析脚本？**（`wiki/**/scripts/{generators,analyzers}/`）$\rightarrow$ **直接调用脚本**计算客观参数或渲染工程资产，严禁靠大模型心算客观公式。
2. **已有核心审美红线与技法规则卡？**（`00_core_aesthetics/` 必全读，`01_craft_playbooks/` 与 `02_style_specifics/` 按单行 `description` 选读）$\rightarrow$ 严格遵循卡片中的 `## Rules` 与参数区间执行。
3. **已有深层理论/大师案例拆解？**（`03_deep_concepts/`）$\rightarrow$ 用 `wiki_query.py read <page>#<section>` 抽读对应章节。
4. **已有客观校验器脚本（Verifiers）？**（`wiki/**/scripts/verifiers/`）$\rightarrow$ 产出初稿后，**必须运行对应的 Verifier 脚本自检**；若未通过，修正至通过后再交付。
5. **当前 Wiki 仅含 `provisional`（二手碎片知识）或完全空白？** $\rightarrow$ 用最小必要方案完成任务，并在回复末尾触发 **主动求知教材提案（Curriculum Gap Alert）**。

## 4. Rust + Cargo 优先脚本法则与磁盘卫生铁律（Rust-First Cargo Workspace & Disk Hygiene）
1. **Rust + Cargo 优先原则（Rust-First Scripting Law）**：
   - 在编写任何 `wiki/**/scripts/{generators,analyzers,verifiers}/` 脚本前，**必须优先评估并使用 Rust（`*.rs`）实现**，并在同一 `.rs` 文件底部内嵌 `#[cfg(test)] mod tests` 单元测试。
   - 全库共享根目录统一的 `Cargo.toml` 工程。`wiki_finalize.py` 会自动扫描 `wiki/**/scripts/**/*.rs` 并将其同步注册为 `Cargo.toml` 的 `[[bin]]` 目标，所有脚本通过 `cargo run --quiet --bin <name> -- [args...]` 运行，并由 `wiki_linter.py` 统一调用 `cargo test --quiet` 增量编译测试。
   - **唯一例外**：仅当脚本必须直接运行在特定宿主软件内嵌解释器中（如 Krita/Blender 内嵌 Python API、After Effects ExtendScript `.jsx`）或必须调用特定 Python 生态工具链时，才允许使用非 Rust 语言。
2. **音视频阅后即焚、PDF/EPUB 图书永久保留与 Git 滚动瘦身**：
   - **PDF/EPUB 图书永久保留铁律**：`raw/` 下的 `.pdf`、`.epub` 等电子书教材具有长期反复查阅价值，**严禁在学习后主动或自动删除**！同时通过 `.gitignore` 忽略 `.pdf/.epub` 大文件本体（仅将 `1 KB` 的 `<file>.receipt.json` 指纹纳入 Git 追踪）。
   - **音视频与切片阅后即焚**：每完成一个音视频 `/study` 学习单元并通过校验后，执行 `python3 .agents/skills/study/scripts/study_runner.py prune-raw` 清理临时切片，保留 `.receipt.json` 与 `.vtt` 字幕。
   - **Git 历史滚动瘦身**：定期执行 `python3 .agents/skills/study/scripts/study_runner.py compact-git --keep 15`。

## 5. 递归自进化引擎（RSI Self-Evolution: ACE + Gotcha Harvester + AWM — Ported from `PenguinHarness`）
1. **排错防踩坑自动捕获（Continual-Learning Gotcha Harvester — `Rules That Save Retries`）**：
   - 在启动涉及浏览器、外部 CLI、Cargo 编译或宿主渲染的任务前，运行 `wiki_query.py gotchas [--scope <domain>]`。
   - **自动沉淀触发铁律**：凡在单次会话中经历了 **$\ge 2$ 次工具调用报错/重试**才摸索出正确方案，在任务收尾时自动运行：
     `python3 .agents/skills/study/scripts/study_runner.py record-gotcha --id <id> --scope "<tool_or_domain>" --symptom "<报错死胡同>" --root-cause "<根因>" --recipe "<一步到位标准解法>"`
2. **AWM 参数化工作流记忆（Agent Workflow Memory）**：
   - 遇到多步复合任务时，优先查阅 `wiki/AWM_WORKFLOWS.md`（或运行 `wiki_query.py workflows`）。当跑通全新的高价值跨工具流水线时，调用 `study_runner.py record-workflow` 沉淀为参数化工作流。
3. **ACE 规则与脚本实战有效率追踪（ACE Helpful/Harmful Telemetry & Query Memory）**：
   - 完成实质性创作、校验或学习任务后，运行：
     `python3 .agents/skills/study/scripts/study_runner.py record-trace --helpful <card_ids...> --scripts <script_names...> [--harmful <misleading_card_ids...>] --task "<任务简述>"`

## 6. 强制尾部追溯签名（Mandatory Self-Reporting Footer）
每次实质性任务回复的末尾，必须用 2–3 行输出以下审计信息：
```text
[Wiki Trace] 管线/工具层: <已读/已用卡片与脚本> | 底层学科层(Foundations): <已用美学/理论卡片> | 校验器 & ACE 记账: <Verifier 结果 + ACE/Gotcha 沉淀状态>
[Gap & Risk] <本次任务中 Wiki 未覆盖的盲区、二手 provisional 规则的局限性，或推荐用户通过 /study 补充的权威教材>
```

<!-- WIKI-MANAGED:START -->
## 6. 当前知识库全域路由地图（Auto-Managed by `wiki_finalize.py` — `LLM-Wiki × Sirchmunk` Tree Architecture）

| 层级 (Tier) | 模块名 | L1 快速路由入口 (`≤4.5KB`) | L2 子学科树 (`META Subtrees`) | 强制联动底层学科 (`requires_foundations`) | 词条/脚本统计 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `foundations` | **`cinematography_staging`** | [`wiki/foundations/cinematography_staging/quickstart.md`](wiki/foundations/cinematography_staging/quickstart.md) | *(单层直达)* | `foundations/visual_aesthetics` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `foundations` | **`ludology_systems`** | [`wiki/foundations/ludology_systems/quickstart.md`](wiki/foundations/ludology_systems/quickstart.md) | *(单层直达)* | *(自身即为底层学科)* | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `foundations` | **`motion_animation`** | [`wiki/foundations/motion_animation/quickstart.md`](wiki/foundations/motion_animation/quickstart.md) | *(单层直达)* | `foundations/visual_aesthetics` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `foundations` | **`music_theory_acoustics`** | [`wiki/foundations/music_theory_acoustics/quickstart.md`](wiki/foundations/music_theory_acoustics/quickstart.md) | *(单层直达)* | *(自身即为底层学科)* | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `foundations` | **`narrative_dramaturgy`** | [`wiki/foundations/narrative_dramaturgy/quickstart.md`](wiki/foundations/narrative_dramaturgy/quickstart.md) | *(单层直达)* | *(自身即为底层学科)* | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `foundations` | **`visual_aesthetics`** | [`wiki/foundations/visual_aesthetics/quickstart.md`](wiki/foundations/visual_aesthetics/quickstart.md) | *(单层直达)* | *(自身即为底层学科)* | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `tools_craft` | **`ae_compositing`** | [`wiki/tools_craft/ae_compositing/quickstart.md`](wiki/tools_craft/ae_compositing/quickstart.md) | *(单层直达)* | `foundations/visual_aesthetics`, `foundations/cinematography_staging` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `tools_craft` | **`aseprite_pixel`** | [`wiki/tools_craft/aseprite_pixel/quickstart.md`](wiki/tools_craft/aseprite_pixel/quickstart.md) | *(单层直达)* | `foundations/visual_aesthetics`, `foundations/motion_animation` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `tools_craft` | **`blender_3d_layout`** | [`wiki/tools_craft/blender_3d_layout/quickstart.md`](wiki/tools_craft/blender_3d_layout/quickstart.md) | *(单层直达)* | `foundations/visual_aesthetics`, `foundations/cinematography_staging` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `tools_craft` | **`daw_music_prod`** | [`wiki/tools_craft/daw_music_prod/quickstart.md`](wiki/tools_craft/daw_music_prod/quickstart.md) | *(单层直达)* | `foundations/music_theory_acoustics` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `tools_craft` | **`krita_2d_paint`** | [`wiki/tools_craft/krita_2d_paint/quickstart.md`](wiki/tools_craft/krita_2d_paint/quickstart.md) | *(单层直达)* | `foundations/visual_aesthetics`, `foundations/cinematography_staging`, `foundations/motion_animation` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `workflows` | **`2d_game_production`** | [`wiki/workflows/2d_game_production/quickstart.md`](wiki/workflows/2d_game_production/quickstart.md) | *(单层直达)* | `foundations/ludology_systems`, `foundations/visual_aesthetics`, `foundations/motion_animation`, `foundations/music_theory_acoustics` | 0 卡 (0 权威 / 0 临时), 0 脚本 |
| `workflows` | **`manga_to_anime_pipeline`** | [`wiki/workflows/manga_to_anime_pipeline/quickstart.md`](wiki/workflows/manga_to_anime_pipeline/quickstart.md) | *(单层直达)* | `foundations/narrative_dramaturgy`, `foundations/cinematography_staging`, `foundations/visual_aesthetics`, `foundations/motion_animation` | 0 卡 (0 权威 / 0 临时), 0 脚本 |

- **排错防踩坑分级树账本 (`Scoped Rules That Save Retries`)**: [`wiki/GOTCHAS_AND_RETRY_SAVERS.md`](wiki/GOTCHAS_AND_RETRY_SAVERS.md) (`wiki_query.py gotchas --scope <domain>`)
- **AWM 参数化工作流记忆 (`Workflow Memory`)**: [`wiki/AWM_WORKFLOWS.md`](wiki/AWM_WORKFLOWS.md)
- **RSI 自进化记分板 (`Scoreboard & ACE Telemetry`)**: [`wiki/SCOREBOARD.md`](wiki/SCOREBOARD.md)
- **主动求知与教材征集榜**: [`wiki/CURRICULUM_GAPS.md`](wiki/CURRICULUM_GAPS.md)
- **🌳 Sirchmunk 树形结构与章节大纲导航 (`PageIndex Tree Navigator`)**: `python3 .agents/skills/study/scripts/wiki_query.py tree [<domain>/<subtree> | <card_id>]`
- **📖 LENS 硬预算有界读取器 (`1-Hop Contract Digest + #section Slice`)**: `python3 .agents/skills/study/scripts/wiki_query.py read <card_id>[#<section>] [--subtree <domain>/<slug>] [--budget-kb 18]`
- **🔍 全文+定理+章节主题图高召回检索 (`CorpusTopicMap + RRF Search`)**: `python3 .agents/skills/study/scripts/study_runner.py search "<关键词>" [--domain <模块名>]`
- **🛡️ HarnessPro 领域冻结联合考卷 (`Frozen Verifier Suite`)**: `python3 .agents/skills/study/scripts/study_runner.py verify-suite --domain <模块名> [--artifact <产物路径>]`
<!-- WIKI-MANAGED:END -->

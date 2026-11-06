# Phase 4 Prompt Guide: Cross-Work Reduce, Backward Edits, Memory Regularization & Back-Wiring (Ported from `skill-training` `reduce.md` + `backward.md` + `memory_policy.md`)

本阶段严格复刻论文 *Skill Training with Corruption and Reconstruction Loop (arXiv:2607.27557)* 的 `s4_backward.py`、`prompts/reduce.md`、`prompts/backward.md` 与 `prompts/memory_policy.md`，并结合一手教材重整铁律与跨层反向挂钩。

## 1. 跨案例共性归并（Mini-Batch `Reduce` — 源自 `prompts/reduce.md`）

> *"You see all of this step's works/cases together so that you generalize rather than patch. One work's quirk is not a law."*

在修改任何规则卡前，先对本批次（$\ge 2$ 个一手教材谱例/案例或作品）的 Loss 报告执行归并（Reduce）：
1. **通用底线与核心技法（`00_core_aesthetics/` & `01_craft_playbooks/`）**：
   - 只有在 **$\ge 2$ 个案例/作品中共同暴露的差距**，或一手权威教材明确声明的**普适学科公理**，才允许进入 `00` 或 `01`。
   - `00_core_aesthetics/` 是生成时**每次 100% 必全读**的全局宪法，**每个领域严格控制在 $\le 5$ 张卡、每张 $\le 45$ 行**！凡回答“具体某个组件怎么写（How to write X）”的技法，哪怕普遍适用，也必须放在 `01_craft_playbooks/`，绝不塞满 `00`！
2. **特定流派/曲风/画风专属规则（`02_style_specifics/`）**：
   - 仅在某一特定子流派（如 Bossa Nova、Funk Slap、国风战歌、赛博朋克霓虹调色）下成立的规律，归入 `02_style_specifics/`，绝不污染全局规则。
3. **单一案例偶然细节（One-Story Specifics）**：
   - 仅属于某一道例题或某一首歌的局部特殊处理，只保留在 `03_deep_concepts/*-cases.md` 案例档案中，**严禁写进 `00/01/02` 规则卡**！

## 2. 卡片结构契约与严苛容量正则化（Memory Policy & Hard Regularizer）

每张行动规则卡（`00_core_aesthetics/`、`01_craft_playbooks/`、`02_style_specifics/`）必须严格遵守以下结构契约（由 `wiki_linter.py` 强制门禁）：
1. **单行 `description` 只写触发边界（Boundary Only, Never Craft Advice）**：
   - 生成 Agent 在挑选卡片时**只看得到 `description`**。
   - 因此 `description` **必须且只能用单行写明“何时必读这张卡（Use when...），以及相邻的什么场景不读它（Not for...）”**！严禁在 `description` 里罗列书名或报菜名。
2. **正文 `## Rules` 只写定量、防手癖的可执行指令（Quantitative & Error-Driven Imperatives）**：
   - **定量硬约束优先（论文 Section 4.3 Table 5）**：每条规则必须包含明确的定量参数或硬边界（如音程半音数、力度区间 `[a, b]`、每小节最少/最多命中数、明度差阈值、`严禁/不得超过`），专门掐死大模型在 Phase 2 暴露出的业余手癖！
   - **严禁出现训练复盘叙事（Forbidden Post-Mortem Phrasing）**：卡片是面向下一次创作的法则，不是上次犯错的检讨书。严禁出现 `"the model wrote X, the human wrote Y"`、`"在刚才的重建中模型..."` 等字样。
   - **硬性行数上限**：`core-rule`（`00`）正文 $\le 45$ 行；`craft-card`（`01`）与 `style-card`（`02`）正文 $\le 60$ 行、$\le 4800$ 字符。超出上限时**严禁继续追加行**，必须压缩提纯或将案例数据移入 `03_deep_concepts/`！

## 3. 四种原子编辑与一手源材料回溯重整法则（The Four Atomic Edits + Primary-Source Consolidation）

对照归因结果，执行以下四类原子编辑（论文 Figure 6b 证明：成熟技能库 **80% 以上的更新应为 `B-Correct` 重写与 `D-Merge` 合并，而非无节制新增卡片**）：
- **A — Reinforce（强化硬化）**：卡片方向正确但表述太软 $\rightarrow$ 结合一手教材原件，将其收紧为带具体数值和正反例边界的定量规则，并同步强化绑定的 Rust Verifier。
- **B — Correct（纠偏或修路由）**：
  - 若规则过宽/误导 $\rightarrow$ 缩小 `description` 适用边界或在 `## Rules` 中写明例外条件；
  - 若卡片存在但 Phase 2 未被打开（Routing Failure）$\rightarrow$ **严禁新建重复卡！** 立即重写该卡的 `description`（写清触发场景词），并在关联卡片的 `depends_on` 与 `_domain_index.md` 必读簇中补上路由指针。
- **C — Create（审慎新增）**：仅当确认整个领域的 `_domain_index.md` 中完全没有覆盖该核心模块的卡片时，才允许新建卡片及配套 Rust 脚本。
- **D — Merge（回溯一手原件合并去重 — 治理碎片化的核心武器）**：
  - 当发现同一领域下存在多张同源碎片卡（例如同一门课程或同一乐器被拆成了十几张小卡）时，必须执行 **`D — Merge`**：
    1. **先运行 `study_runner.py inspect-sources <待合并的所有卡片ID>`**，用 `read_file` **把这些卡片对应的全部 `raw/` 一手原书章节或 `.vtt` 原始字幕重新完整精读一遍**（严禁只看着旧卡片的二手文字做拼贴缩写！）；
    2. 从一手原始知识出发，按正交功能模块将其重构融合为 1 张体系严密、定量参数完备、查表即用的高密度主干卡片；
    3. 将被合并删除的旧卡片 `id` 和相对路径全部写入保留卡的 `aliases:` 列表中（或使用 `wiki_finalize.py --move`），同步合并 `.claims/` 中的一手证据 URI，确保全库零断链、零一手知识丢失！

## 4. 跨层反向挂钩铁律（Mandatory Cross-Tier Back-Wiring）

每当更新或合并了 `wiki/foundations/<domain>/` 下的底层卡片或 Verifier 脚本后，必须扫描 `wiki/tools_craft/` 与 `wiki/workflows/`，将底层卡片 `id` 注入相关工具卡片的 `depends_on` 与 `quickstart.md` 中，确保“先学工具、后学基础学科”时也能 100% 自动联动激活！

---
schema_version: 2
okf_version: "0.2"
title: LLM Wiki 2.0 × Sirchmunk Governance & Hierarchical Tree Architecture Schema
description: Authoritative specification for the 3-tier orthogonal LLM Wiki fused with Sirchmunk (arXiv:2608.16185) PageIndex DocumentTrees, L2 META Subtrees, KnowledgeCluster Contracts, CorpusTopicMap, and LENS Token-Budgeted Reading.
---

# LLM Wiki Governance & Schema Specification (`v2.0` — `LLM-Wiki × Sirchmunk`)

本规范融合了 **Karpathy LLM Wiki**、**ModelScope Sirchmunk (`arXiv:2608.16185` — PageIndex Tree, KnowledgeCluster, KnowledgeEvolver & LENS Context Budget)**、**LangChain OpenWiki (OKF v0.2 & Grounded Claims)**、**Skill Training (`arXiv:2607.27557`)**、**Prime Agent (Executable Skills & Refine)** 与 **Ponytail (Reuse Decision Ladder)** 的核心工程逻辑。

---

## 1. 三维正交知识矩阵与五级分形树路由（3-Tier Matrix × 5-Level Fractal Tree Router）

为防止跨场景知识孤岛、概念同名污染以及随着卡片增长导致的**初始上下文爆炸（Context Explosion）**，`wiki/` 严格按横轴“三层抽象正交”与纵轴“五级分形树按需展开（L0 $\rightarrow$ L4）”组织：

1. **Tier 1 — `wiki/foundations/<domain>/`（底层基础学科层）**
   - **定义**：跨软件、跨媒介的底层科学、美学与理论公理体系（如 `visual_aesthetics` 视觉美学、`cinematography_staging` 视听分镜、`motion_animation` 运动规律、`music_theory_acoustics` 乐理声学、`narrative_dramaturgy` 叙事戏剧、`ludology_systems` 游戏机制）。
   - **原则**：禁止包含特定软件菜单/快捷键操作；其规则与 Verifier 脚本对所有上层工具通用。
2. **Tier 2 — `wiki/tools_craft/<tool>/`（软件工具与媒介技艺层）**
   - **定义**：特定软件或媒介的操作方法、工程规范与自动化脚本（如 `aseprite_pixel`、`krita_2d_paint`、`blender_3d_layout`、`ae_compositing`、`daw_music_prod`）。
   - **原则**：必须通过 `requires_foundations`（目录级）和 `depends_on`（卡片级）显式挂载 Tier 1 的底层学科卡片。
3. **Tier 3 — `wiki/workflows/<pipeline>/`（复合生产管线层）**
   - **定义**：串联多个工具与多个底层学科的端到端业务流水线（如 `2d_game_production`、`manga_to_anime_pipeline`）。
   - **原则**：负责跨工具数据流转契约、阶段验收标准与顶层编排脚本，不重复抄写底层理论。

### 每个领域/模块目录的标准五级树形内部结构
```text
<tier>/<domain_or_tool>/
├── INSTRUCTIONS.md         # [人类钦定/只读] 该领域的最高审美偏好、关注重点与红线（Agent 绝不可覆写）
├── quickstart.md           # [L1 领域树干入口 <= 9 KB] L1 -> L2 META 子树路由表 + 跨层强依赖矩阵
├── _subtrees/              # [L2 子学科路由层 | Sirchmunk KnowledgeEvolver 自动生成]
│   └── <subtree_slug>.md   # 按图社区聚类拆分的正交子学科路由表（Scoped 00_core + 任务联动矩阵 + 章节锚点）
├── _domain_gotchas.md      # [L2 领域专属防踩坑账本] 从全局 .gotchas.json 自动过滤出的本领域踩坑配方
├── _domain_index.md        # [Finalizer 自动生成] 本目录全量子树、卡片与脚本树形目录
├── 00_core_aesthetics/     # [L3 必读核心区 | AbstractionLevel: L5 PHILOSOPHY] 不可违背的审美底线 (正文 <= 45 行)
├── 01_craft_playbooks/     # [L3 按需技法卡 | AbstractionLevel: L2 PATTERN] 可直接指导操作的技法规则卡 (正文 <= 60 行)
├── 02_style_specifics/     # [L3 子流派规则 | AbstractionLevel: L1 TECHNIQUE] 特定题材/流派专属规则卡 (正文 <= 60 行)
├── 03_deep_concepts/       # [L4 深层理论与案例 | AbstractionLevel: L4 DETAIL] 课本推导、大师拆解 (通过 .tree-index.json #section 点读)
└── scripts/                # [Prime Agent 可执行层]
    ├── generators/         # 生成器/计算器/工程转换脚本 (严禁 LLM 心算)
    ├── analyzers/          # 多模态特征提取与诊断脚本
    ├── verifiers/          # 审美与规范客观校验脚本 (产出后强制运行自检)
    └── tests/              # 每个脚本必须配套的单元测试
```

### Sirchmunk 五大上下文防爆核心机制（`arXiv:2608.16185`）
1. **PageIndex 文档内章节树索引（`wiki/.tree-index.json`）**：`wiki_finalize.py` 为全库每张卡片自动构建 H2/H3 章节树（`TreeNode`：含 `node_id`、`#slug`、`summary <= 240 chars`、`line_range`、`char_range`、`quant_rule_count`）。读取大型 `03_deep_concepts` 卡片时默认展示 `DocumentTree` 章节导航图，按需通过 `wiki_query.py read <id>#<slug>` 激光点读目标小节。
2. **`KnowledgeCluster` 契约摘要与 1-Hop 有界依赖捆绑（Contract Digest）**：每张卡片自动提取 `patterns`（3–5 条核心定量铁律）、`constraints`（适用屏障与红线）与 `abstraction_level`（`L1–L5`）。运行 `wiki_query.py read <id>` 时，仅展开 1-Hop 直接前置卡片的 **Contract Digest（~350B/卡）** 并以单行指针列出 2-Hop 传递依赖，彻底终结历史无限 BFS 传递展开导致的 `476 KB` 上下文爆炸（如需全量前置正文可传 `--full-deps`）。
3. **图社区演化 `META` 子树路由（`KnowledgeEvolver` + `WeakSemanticEdge`）**：基于 `depends_on`、共享脚本（`shared_script`）与 ACE 历史同现轨迹（`ace_co_occur`）构建弱语义边，将大型学科（如 113 卡的 `music_theory_acoustics`、28 卡的 `visual_aesthetics`、23 卡的 `ludology_systems`）自动聚类为 `L2 _subtrees/<slug>.md`，使 L1 `quickstart.md` 永久保持在 `4–9 KB`。
4. **文档频率（DF）自适应修剪 `CorpusTopicMap` 与查询记忆（`historicalQueries` + `Hotness`）**：在 `wiki/.search-index.json` 中构建 DF 修剪（剔除 >35% 高频泛化词）的章节级倒排索引，并在 `wiki/.ace-telemetry.json` 中记录每张卡片最近 5 条真实任务查询（`queries`）与热度分（`hotness`），实现 Phase 0 子树/历史查询秒级命中。
5. **作用域分片防踩坑账本（Scoped Gotchas）与滚动日志归档（Rolling Log Archive）**：根级 `wiki/GOTCHAS_AND_RETRY_SAVERS.md` 永久保持为 `~6 KB` 的 L1 分域路由表 + 最新 12 条高频速查，完整条目按领域自动分流至 `_domain_gotchas.md` 并在 `wiki_query.py read` 时自动附带 Top 3 本域避坑配方；`wiki/log.md` 仅保留最近 35 条审计记录，历史自动归档至 `wiki/.archive/log_history.md`。

---

## 2. 双层页面契约与 OKF v0.2 Frontmatter 规范

### 2.1 标准 YAML Frontmatter
所有知识页面必须包含以下 YAML Frontmatter（区分 **Agent 维护字段** 与 **Finalizer 自动维护字段**）：

```yaml
---
id: va-cel-terminator-hue-shift          # [必填] 全局唯一稳定短 ID (用于移动/重命名时自动重定向链接)
type: craft-card                         # [必填] core-rule | craft-card | style-card | concept-doc | reference-case | orientation-guide
title: 赛璐珞明暗交界线与冷暖色相偏移法则    # [必填] 人类可读标题
description: 当进行角色或场景的二分光影塑造、选择阴影与受光面色相偏移方向时使用；不适用于纯无影平面 Icon 设计。 # [必填] 单行路由边界 (<= 800 字符，写明何时用、何时不用)
epistemic_status: canonical              # [必填] canonical (权威教材体系) | provisional (工具课顺带提取的二手碎片) | empirical-refine (实战微调经验)
tags: [lighting, color-theory, cel-shading, hue-shift]
aliases: []                              # [可选] 历史相对路径列表，用于移动或拆分目录后的无损跳转
depends_on:                              # [跨层强绑定] 读取本卡时由 wiki_query.py 自动捆绑注入的前置卡片 ID 或相对路径
  - va-color-value-baseline
scripts:                                 # [可执行脚本绑定]
  - role: generator
    path: wiki/foundations/visual_aesthetics/scripts/generators/palette_hue_shifter.py
  - role: verifier
    path: wiki/foundations/visual_aesthetics/scripts/verifiers/check_value_contrast.py
# === 以下字段由 wiki_finalize.py 自动从 .claims/ 与文件指纹同步，无需手写 ===
sources:
  - raw://foundations/visual_aesthetics/color_and_light.pdf#page=42-48
generated: { by: "study-skill/1.0", at: "2026-10-14T07:30:00-07:00" }
verified:
  - { by: "wiki-linter/1.0", at: "2026-10-14T07:30:00-07:00" }
---
```

### 2.2 硬性 Linter 约束（由 `wiki_linter.py` 强制执行，照抄 `skill-training`）
1. **`description` 路由纯粹性**：必须位于**单行**（禁止多行折行），长度 $\le 800$ 字符，只声明**触发边界（何时打开、何时跳过）**，不塞具体技法细节。
2. **行动卡（`core-rule` / `craft-card` / `style-card`）长度硬上限**：
   - `core-rule` 正文（不含 YAML 头与自动生成的依赖横幅）$\le 45$ 行且 $\le 3600$ 字符；
   - `craft-card` / `style-card` 正文 $\le 60$ 行且 $\le 4800$ 字符；
   - 超出上限时严禁强行堆砌：必须**提炼压缩（Compress）**、**拆分为两张职责正交的卡片（Split）**、**将长篇原理解析下沉至 `03_deep_concepts/` 并用 `#section` 链接引用**，或**将确定性步骤封装为 `scripts/` 脚本**。
3. **禁止叙事废话**：卡片内严禁出现“在刚才的第 3 集练习中，模型写了 X 而人类写了 Y”之类的训练过程复盘；只写面向下一次任务的普适规则。
4. **理论文档（`concept-doc` / `reference-case`）结构约束**：不限总行数，但必须包含清晰的 `##` 二级标题，且鼓励使用语法合法的 `mermaid` 图表表达状态机、管线流与结构关系。

---

## 3. Grounded Claims（多模态与脚本证据锚点 Sidecar）

照抄并扩展 LangChain OpenWiki 的 `.claims/` 机制：
- 路径映射：`wiki/<tier>/<domain>/<subdir>/<page>.md` 的断言存储在 `wiki/.claims/<tier>/<domain>/<subdir>/<page>.json`。
- **重要度门槛（Materiality Test）**：只有当一条规则/命题“若为假，会实质改变 Agent 的审美判断、工具操作或脚本参数”时才记录为 Claim。
- **三类标准证据 URI**：
  1. `raw://<tier>/<domain>/<file>.pdf#page=10-15` 或 `raw://<tier>/<domain>/<video>.mp4#t=04:12-06:30`
  2. `script://wiki/<tier>/<domain>/scripts/<role>/<script>.py#L10-L45`（`wiki_finalize.py` 会自动计算该脚本对应行区间的 SHA-256 指纹；**一旦脚本代码被修改，关联该脚本的 Claim 自动变为 `stale`，触发复核提醒！**）
  3. `wiki://<tier>/<domain>/03_deep_concepts/<doc>.md#<section>`

---

## 4. 认知权威度定级与主动求知引擎（Epistemic Grading & Curriculum Gaps）

1. **工具教程中的美学剥离法则（Triage Rule）**：
   - 当在 `/study` 学习工具教程（如 Aseprite、Krita、Blender 实操课）遇到跨工具通用的底层学科知识（如色彩冷暖、构图、镜头透视、运动节奏）时：
   - **必须将工具操作留在 `tools_craft/<tool>/`**，将底层原理剥离写入 `foundations/<domain>/`，并在工具卡 `depends_on` 中建立引用；
   - 但因为工具课讲的底层原理往往是局部二手经验，**必须将该底层卡片标记为 `epistemic_status: provisional`**，并在正文中标注 `> [!WARNING] **[Provisional 二手碎片知识]**` 适用范围警告。
2. **主动求知触发器（`wiki/CURRICULUM_GAPS.md`）**：
   - 当某底层学科的 `provisional` 卡片堆积（或缺少 `canonical` 权威卡片）、存在未解析的 `[[wanted-links]]`、或在 `Skill Training` 闭卷重建 Loss 中暴露出底层功底缺失时，`wiki_finalize.py` 与 Agent 会将其登记入 `wiki/.gaps.json` 及 `wiki/CURRICULUM_GAPS.md`，主动向用户推荐并索要权威教材以启动 `/study` 升级为 `canonical`。

---

## 5. 四类原子更新法则与跨层反向挂钩（Backward & Cross-Tier Back-Wiring）

在 `/study` 的 `Backward` 阶段及 `/wiki-refine`、`/wiki-lint` 中，所有对知识库的修改严格遵循 `skill-training` 的四种原子操作与 `OpenWiki` 的稀疏断言调和：
- **A — Reinforce（强化）**：规则/脚本有效但边界或参数不够锐利 $\rightarrow$ 收紧表述或优化脚本参数，确认（`confirm`）Claim。
- **B — Correct（纠偏与修路由）**：
  - 规则误导了创作 $\rightarrow$ 修正正文或脚本逻辑，更新（`update`）Claim；
  - 规则明明存在但重建时 Agent 没打开它 $\rightarrow$ 这是**路由失败（Routing Failure）**，重写该卡片的 `description`、`quickstart.md` 联动矩阵或补充 `depends_on`，**严禁重复创建第二份副本**。
- **C — Create（新增）**：现有卡片与脚本均未覆盖的空白技法 $\rightarrow$ 创建新卡片与配套 Generator/Verifier 脚本。
- **D — Merge（合并去重）**：两张卡片职责重叠 $\rightarrow$ 将有效规则融合进覆盖面更优的一张，将其旧路径加入 `aliases`，删除冗余卡并运行 `wiki_finalize.py` 自动重定向全库引用。
- **Cross-Tier Back-Wiring（跨层反向挂钩铁律）**：每当在 `foundations/` 新增或升级一张卡片/脚本时，必须扫描 `tools_craft/` 与 `workflows/`，将新卡片 ID 自动挂载到相关工具卡片（如 Krita、Aseprite、Blender、AE）的 `depends_on` 和 `quickstart.md` 联动表中！

---

## 6. 五大后期补救与无损演进渠道（Remediation & Evolution Channels）

1. **渠道 1（目录重构与移动零断链）**：每张卡片拥有全局唯一 `id` 与 `aliases`。运行 `python3 .agents/skills/study/scripts/wiki_finalize.py --move <old_path> <new_path>` 可自动移动文件并重写全库所有 `depends_on`、正文链接与 `.claims/` Sidecar。
2. **渠道 2（Schema 外科手术式自动升级）**：修改本规范升级 `schema_version` 后，通过 `wiki_finalize.py --migrate` 在字节级保留正文的前提下批量升级存量卡片 YAML 头。
3. **渠道 3（基于 `raw://` 收据锚点的定向重编译与素材阅后即焚）**：已学完的大体积音视频通过 `study_runner.py prune-raw` 自动清理并留下 `<file>.receipt.json`（含原始文件 SHA-256 指纹与体积）及轻量 `.vtt` 字幕，`wiki_core.py` 直接读取收据指纹验证 `.claims/`，释放 99% 以上的媒体空间；如需重编译直接基于字幕或按 URL 重新拉取。
4. **渠道 4（Git 原子事务回滚与滚动压缩 `compact-git`）**：每次 `/study` 章节完成、`wiki-refine` 微调或 `wiki-lint` 重构均生成带前缀的独立 Git Commit，可通过 `python3 .agents/skills/study/scripts/study_runner.py rollback` 一键精准回退；同时支持 `study_runner.py compact-git --keep 15` 滚动压缩旧历史，彻底杜绝长期学习导致的 `.git/` 磁盘膨胀。
5. **渠道 5（制度与数据物理解耦）**：`GEMINI.md` 与 `quickstart.md` 中的自动生成区统一包裹在 `<!-- WIKI-MANAGED:START -->` 块内，修改全局制度或重跑脚本绝不破坏人类手写规则。
6. **渠道 6（Rust + Cargo 优先可执行层）**：所有 `wiki/**/scripts/` 下的生成器、分析器与校验器优先采用 Rust（`*.rs`）编写，由根目录统一的 `Cargo.toml` 托管（共享 `serde`、`serde_json`、`hound`、`rustfft` 等外部依赖 Crate 与增量编译缓存），`wiki_finalize.py` 自动同步 `[[bin]]` 列表，`wiki_linter.py` 通过 `cargo test --quiet` 统一执行单元测试。



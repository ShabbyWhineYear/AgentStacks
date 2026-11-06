# 📚 AgentStacks（书栈）：自进化个人 Agent 图书馆框架

<p align="center">
  <strong>简体中文</strong> | <a href="README_EN.md">English</a>
</p>

> **给你的 Coding 与多模态创作 Agent 建一座「零上下文爆炸、五级分形书架、帕累托按需借阅、自带可执行脚本」的私人藏书楼（Personal Agent Library）。**
>
> **为什么叫 `AgentStacks`（书栈）？**
> 在图书馆学中，**Stacks** 指层层分级、按索书号井然排列的**「藏书架层」**；在软件工程中，**Stack** 指**「技术栈与调用栈」**。**书栈（`AgentStacks`）** 将你喂给 Agent 的每一本一手原典教材（PDF/EPUB）与课程，转化为层层索引的分形书架（`L0–L4`）与可执行 Rust 校验工具栈——让 Agent 真正拥有一座越读书越博学、却永不撑爆上下文窗口的私人图书馆。
>
> 深度融合 **Karpathy LLM Wiki**、**ModelScope Sirchmunk (`arXiv:2608.16185`)**、**Google Research Harness² (`google-research/harness2`)**、**清华唐杰团队 HarnessPro (`arXiv:2610.26770`)**、**DeepSeek `dsh-superagent-prototype`**、**闭卷加噪重建训练 (`Skill Training, arXiv:2607.27557`)**、**Prime Agent (`PrimeIntellect-ai/prime-agent` & `verifiers`)**、**PenguinHarness (`ACE + AWM + OPRO + Gotcha Harvester`)** 与 **LangChain OpenWiki (`OKF v0.2`)**。

---

## 🏛️ 「书栈 (`AgentStacks`)」的个人图书馆空间映射

| 图书馆空间隐喻 | 对应 AgentStacks 架构层级 | 上下文开销 | 核心职责 |
| :--- | :--- | :--- | :--- |
| **📖 原典恒温藏书室**<br>*(Primary Source Vault)* | `raw/<tier>/<domain>/`<br>+ `<book>.receipt.json` | `0 KB`<br>*(按需翻阅)* | 永久珍藏一手 PDF/EPUB 原书（Git 仅追踪 `1 KB` 的 `.receipt.json` 数字藏书票 SHA-256 印章，零仓库膨胀）。 |
| **🗺️ L0 总馆目录厅**<br>*(Main Catalog Hall)* | [`GEMINI.md`](GEMINI.md) & [`wiki/index.md`](wiki/index.md) | `~3 KB` | 常驻馆长总目，一眼看清整座书栈有哪些分馆、多少册权威法典与专题书架。 |
| **🚪 L1 分馆阅览室**<br>*(Reading Rooms)* | `wiki/<tier>/<domain>/quickstart.md` | `≤ 4.5–14 KB` | 三大独立分馆：**底层科学馆 (`foundations`)**、**工具手艺馆 (`tools_craft`)**、**复合管线馆 (`workflows`)**。 |
| **🗄️ L2 专题书架层**<br>*(Subject Stacks)* | `_subtrees/<slug>.md`<br>& `_domain_gotchas.md` | `~2.5 KB` | 基于 `Sirchmunk` 弱语义边自动聚类的专题书架索书表 + 本馆高频排错备忘录。 |
| **📜 L3 活体法典与三档借阅**<br>*(Action Folios & Lending Modes)* | `00_core / 01_craft / 02_style`<br>`--mode {lite,medium,heavy}` | `≤ 6 / 18 / 40 KB` | 经闭卷默写提炼的定量规则册，支持 `HarnessPro` 帕累托三档借阅（摘要卡 `lite` / 平衡拐点 `medium` / 全架联读 `heavy`）。 |
| **🔖 L4 单册书签与仪器室**<br>*(Section Bookmarks & Lab)* | `03_deep_concepts/#section`<br>+ `wiki/**/scripts/*.rs` | `~2–5 KB` | `PageIndex` 章节级 `#section` 激光翻页 + 统一 Cargo Rust 工作区的可执行生成器、分析器与校验器。 |

---

## ✨ 为什么需要「书栈 (`AgentStacks`)」？

传统的 Agent 记忆库、RAG 向量检索或扁平 Markdown 知识库，在持续学习专业教材与视频课程时会迅速撞上四大瓶颈：

1. **上下文爆炸与成本失控（Context & Cost Explosion）**：随着规则卡片与跨领域依赖增加，一次性加载全量卡片及其传递依赖动辄膨胀至 `100 KB–400 KB+`，不仅挤爆上下文窗口，还会导致大模型注意力稀释与 Token 成本飙升。
2. **浅层抄书陷阱（`+Distill` Trap）**：直接让大模型阅读教材并总结笔记，只会产出上百张写满“注意光影对比”、“保持声部平滑”的空洞口号卡——因为**大师教材的正面范例中本就不包含外行错误**，模型在实战时依然会犯原生手癖。
3. **软件操作与底层学科耦合（Tool-vs-Foundation Coupling）**：将具体软件按键/API（如 Blender、Krita、REAPER、引擎操作）与普适底层理论（如解剖透视、色彩光学、声学乐理、数值平衡）混写在一起，导致换一个工具就要重写整套知识。
4. **心算幻觉与子 Agent 传话筒劣化（Mental Arithmetic & Subagent Degradation）**：让大模型靠概率生成去“心算”客观声学/透视/数值公式，或把主干需求经有损缩写传话交给失忆的子 Agent 代写，导致产出质量严重退化。

本仓库提供一套 **100% 纯净、零预置知识、从 0 起步的「个人 Agent 图书馆躯壳」**。你可以将任何领域的 PDF/EPUB 权威教材、论文或视频教程喂给 Agent（`/study`），从零生长出结构化、可校验、永不撑爆上下文的持久专家大脑。

---

## 🏗️ 核心架构（5 级分形书架 × 三层正交矩阵 × 八大前沿引擎）

```mermaid
flowchart TD
    L0["L0 总馆目录厅: GEMINI.md & wiki/index.md (~3 KB)"] --> L1["L1 分馆阅览室: 各模块 quickstart.md (<=14 KB)"]
    L1 --> L2["L2 专题书架层: _subtrees/<slug>.md (Sirchmunk META 子树) & _domain_gotchas.md"]
    L2 --> L3["L3 活体法典册: 行动规则卡 (00/01/02) + HarnessPro 三档借阅 (Lite/Medium/Heavy)"]
    L3 --> L4["L4 单册书签: 03_deep_concepts/ 大师案例 + PageIndex DocumentTree (#section 激光切片)"]
    L3 --> RS["⚙️ 精密仪器室: Cargo Rust 统一工作区 (Prime Agent Generators / Analyzers / Verifiers)"]
    RS --> RSI["🧬 自进化馆长: Harness² (Propose-Probe-Compose) × PenguinHarness (ACE/AWM/OPRO)"]
```

### 1. 三层正交知识矩阵（3-Tier Orthogonal Matrix）
- **Tier 1 — 底层通用科学/美学公理层（`wiki/foundations/<domain>/`）**
  存放跨工具普适的学科公理（如 `visual_aesthetics` 视觉美学与解剖、`cinematography_staging` 镜头调度、`motion_animation` 运动规律、`music_theory_acoustics` 乐理与声学、`narrative_dramaturgy` 叙事剧作、`ludology_systems` 游戏机制与数值平衡）。**严禁出现任何特定软件的菜单或快捷键**。
- **Tier 2 — 媒介与工具手艺层（`wiki/tools_craft/<tool>/`）**
  存放具体软件与工程 API 的实操配方（如 `aseprite_pixel`、`krita_2d_paint`、`blender_3d_layout`、`ae_compositing`、`daw_music_prod`），通过 `requires_foundations` 与卡片级 `depends_on` 强制联动 Tier 1 底层学科（**严禁只看工具层不看底层学科单脚跳**）。
- **Tier 3 — 端到端复合工作流层（`wiki/workflows/<pipeline>/`）**
  串联多个工具与多个底层学科的工业级跨软件流水线（如 `2d_game_production`、`manga_to_anime_pipeline`）。

### 2. Sirchmunk (`arXiv:2608.16185`) × 唐杰团队 `HarnessPro` (`arXiv:2610.26770`) 帕累托预算引擎
结合 ModelScope **Sirchmunk** 的分级树索引与清华唐杰团队 **HarnessPro** 的性能–成本帕累托前沿优化（Pareto Optimization）：
- **PageIndex 章节大纲树（`wiki/.tree-index.json`）**：[`wiki_finalize.py`](.agents/skills/study/scripts/wiki_finalize.py) 自动将每张卡片的 `H2/H3` 标题解析为分级节点（含 `nodeId`、`#slug`、`summary <= 240 chars`、`lineRange`、`charRange`、`quantRuleCount`）。读取超长 `03_deep_concepts` 案例卡时默认展示 `DocumentTree` 导航图，支持 `wiki_query.py read <id>#<section>` 激光切片精读。
- **`KnowledgeCluster` 契约摘要与 `HarnessPro` 帕累托三档运行模式（`--mode {lite,medium,heavy}`）**：
  - **`Lite` 模式（`--mode lite`, `<= 6 KB`）**：面向常规轻量任务，仅输出目标卡片的 `KnowledgeCluster` 契约摘要（`Patterns + Constraints + Scripts`）+ 最多 3 张 1-Hop 前置契约 + Top 2 避坑配方，节省 **~80%** 上下文开销。
  - **`Medium` / `auto` 模式（`--mode medium`, `<= 18 KB`）**：帕累托平衡拐点（默认），输出目标卡完整正文 + 1-Hop 前置契约摘要（`~350 B/卡`）+ 2-Hop 单行指针 + Top 3 避坑配方。
  - **`Heavy` / `full` 模式（`--mode heavy`, `<= 40 KB`）**：面向复杂跨系统合成任务，输出目标卡完整正文 + 1-Hop 前置卡完整正文 + Top 5 避坑配方 + 绑定 Rust 校验器套件。
- **`KnowledgeEvolver` L2 子学科树（`_subtrees/*.md`）与 DF 剪枝 `CorpusTopicMap`**：基于 `WeakSemanticEdge`（`depends_on`、`shared_script`、`ace_co_occur`）将大模块自动拆分为正交 L2 子树；在 [`wiki/.search-index.json`](wiki/.search-index.json) 中构建自动剪枝高频泛化词（`DF > 35%`）的章节级倒排索引。

### 3. Google Research `Harness²` (`google-research/harness2`) 双轨递归与八维可编辑空间
借鉴 Google Research **Harness²** (*Recursive Agent Harnessing for an Open World*)：
- **八维可编辑 Harness 空间（8-Component Editable Space）**：将 Agent 外部脚手架正交划分为 `system_prompt`、`guardrails`、`memory`、`skills`、`tool_guidance`、`scripts`、`subagents`、`middleware` 八个组件。
- **双轨 `Propose–Probe–Compose` 循环（`study_runner.py propose-probe`）**：每次故障复盘或 `/wiki-refine` 时，强制将改进提案拆分为：
  1. **跨领域不变量候选（`Invariant Candidate`）**：修复跨学科通用的规划顺序、工具委托或全局 Guardrails；
  2. **领域专属候选（`Domain-Specific Candidate`）**：修复具体学科的 `00–03` 规则卡片或 Rust 校验器；
  经对比验证（`Probe`）通过后合入持久基线（`Compose`）。

### 4. DeepSeek `dsh-superagent-prototype` 主从路由、零转述指针直传与注意力守恒
融合 DeepSeek Harness 团队 **`dsh-superagent-prototype`** 的核心工程哲学（见 [`GEMINI.md`](GEMINI.md) 第 8 节）：
- **`000-Philosophy.md` 注意力守恒与默认观察模式（Observe-by-Default）**：*“Act only when asked, or when a written rule allows it. Speak only when a person must act or can use a result.”* 未经请求的旁支发现默认静默记入 [`wiki/.gaps.json`](wiki/.gaps.json)，绝不擅自打断用户主线。
- **`dispatch.md` 零转述源指针直传（Zero-Paraphrase Source-Pointer Law）**：唤醒子 Agent（Worker）时严禁仅靠主 Agent 二次缩写传话，必须直传用户原始需求原文 + 一手源文件路径指针（`raw/...`、`ANCHOR.md`）+ 强制子 Agent 第一步自行调用 `wiki_query.py read` 加载底层卡片。
- **`verify.md` 出箱前确定性硬门禁（Pre-Outbox Gate）**：任何产物合入或交付前，必须通过 `study_runner.py verify-suite` 与 `wiki_linter.py --strict`。

### 5. 闭卷加噪重建训练（`Skill Training, arXiv:2607.27557`）× `Prime Agent` × `PenguinHarness`
- **闭卷加噪重建（`/study`）**：提取一手教材大师原稿（`human_reference`）$ightarrow$ 剥离手艺生成题干（`Corrupt`）$ightarrow$ 仅凭现有卡片闭卷重建（`Closed-Book Forward`）$ightarrow$ 并排对比算残差（`Ground-Truth Loss`）$ightarrow$ 跨案例归并共性（`Reduce`）并执行四类原子更新（`A-Reinforce / B-Correct / C-Create / D-Merge`）。
- **Prime Agent 不可变基座与可执行校验器（`PrimeIntellect-ai/prime-agent` & `verifiers`）**：`INSTRUCTIONS.md` 为只读不可变基座，客观计算与审美红线全部硬化为 `wiki/**/scripts/` 下的 Rust 生成器与校验器，支持 `study_runner.py rollback` 一键回滚。
- **PenguinHarness 递归自进化全家桶（`Prism-Shadow/penguin-harness`）**：内置 **Gotcha Harvester**（[`wiki/GOTCHAS_AND_RETRY_SAVERS.md`](wiki/GOTCHAS_AND_RETRY_SAVERS.md)）、**AWM 工作流记忆**（[`wiki/AWM_WORKFLOWS.md`](wiki/AWM_WORKFLOWS.md)）、**ACE 有效率追踪**与 **OPRO 单位体积效用评分**（`study_runner.py trace-diagnose`）。

---

## 🚀 快速开始（从 0 构建你的专家大脑）

### 环境要求
- **Python 3.9+**（核心 Wiki 引擎仅依赖 Python 标准库，零外部 pip 依赖即可运行）
- **Rust & Cargo**（`rustup`，用于编译和运行 `wiki/**/scripts/` 下的确定性生成与校验脚本）
- *(可选)* `ffmpeg` / `yt-dlp` / `pdfinfo`（用于多模态音视频切片与 PDF 教材预处理）

### 1. 克隆并校验纯净框架
```bash
git clone https://github.com/ShabbyWhineYear/AgentStacks.git
cd AgentStacks

# 运行确定性终结器：同步全库树形索引、Manifest 与 L0/L1/L2 路由表
python3 .agents/skills/study/scripts/wiki_finalize.py

# 运行严格健康门禁：检查 OKF Frontmatter、跨层依赖、Python 与 Cargo 单元测试
python3 .agents/skills/study/scripts/wiki_linter.py --strict
```

### 2. 查看分级树结构与扩展新学科模块
```bash
# 查看全局 L0 -> L1 -> L2 树形目录
python3 .agents/skills/study/scripts/wiki_query.py tree

# 一键脚手架新增自定义基础学科 (foundations)、工具 (tools_craft) 或管线 (workflows)
python3 .agents/skills/study/scripts/scaffold_domains.py \
  --add foundations shader_math "实时着色器数学与光学 (Shader Math & Optics)" \
  --desc "BRDF 光照模型、SDF 距离场数学与实时程序化噪声公理。"
```

### 3. 启动第一次学习（`/study`）与日常帕累托检索
将你需要学习的 PDF/EPUB 教材或视频放入 `raw/<tier>/<domain>/`，在支持 `GEMINI.md` 与 `.agents/skills/` 的 Agent 环境中输入：
```text
/study 学习 raw/foundations/visual_aesthetics/<你的教材.pdf>
```
常用命令行工具速查：
```bash
# 1. 将长 PDF 教材或长视频切分为可消化的学习单元 (Study Units)
python3 .agents/skills/study/scripts/study_runner.py prepare <教材或视频路径> \
  --tier foundations --domain visual_aesthetics

# 2. 混合检索（Phase 0 子树/历史查询匹配 + Phase 1 章节级 CorpusTopicMap + RRF 融合检索）
python3 .agents/skills/study/scripts/wiki_query.py search "<关键词>"

# 3. HarnessPro × Sirchmunk 帕累托三档受控读取（lite <=6KB | medium <=18KB | heavy <=40KB）
python3 .agents/skills/study/scripts/wiki_query.py read <card_id>[#section] --mode medium

# 4. 读取指定 L2 子学科树路由表
python3 .agents/skills/study/scripts/wiki_query.py read --subtree <domain>/<subtree_slug>

# 5. 运行 HarnessPro × OPRO 轨迹诊断与帕累托前沿审计
python3 .agents/skills/study/scripts/study_runner.py trace-diagnose --domain all

# 6. 记录一次 Google Research Harness² 双轨自进化 (Invariant vs Domain-Specific)
python3 .agents/skills/study/scripts/study_runner.py propose-probe \
  --task "<任务或故障简述>" --invariant-component tool_guidance \
  --invariant-edit "<跨领域通用改进>" --domain-scope "foundations/visual_aesthetics" \
  --domain-edit "<领域卡片或校验器改进>" --probe-contrast "<对比验证结果>"

# 7. 记录一次排错防踩坑配方 (Gotcha)
python3 .agents/skills/study/scripts/study_runner.py record-gotcha \
  --id "<slug>" --scope "<domain_or_tool>" \
  --symptom "<报错表象>" --root-cause "<底层根因>" --recipe "<一步到位标准解法>"

# 8. 原子提交学习成果（自动运行 Finalizer + Linter + Scoreboard 快照 + Git Commit）
python3 .agents/skills/study/scripts/study_runner.py commit --verb study --domain <domain> --summary "完成第 1 章闭卷重建与校验"
```

---

## 📜 理论致谢与参考项目矩阵

| 参考项目 / 论文 | 机构 / 作者 | 在本框架中的核心落地机制 |
| :--- | :--- | :--- |
| **Karpathy's LLM Wiki** | Andrej Karpathy | 由 LLM 持续维护、复利演进的纯文本 Markdown 知识库范式 |
| **Sirchmunk** (`arXiv:2608.16185`) | ModelScope 团队 | `PageIndex` 文档章节树、`KnowledgeCluster` 1-Hop 契约摘要、`KnowledgeEvolver` L2 子树、DF 剪枝 `CorpusTopicMap` 与 `LENS` 预算控制 |
| **Harness²** ([`google-research/harness2`](https://github.com/google-research/harness2)) | Google Research & Northwestern | 八维可编辑 Harness 空间、双轨 `Propose–Probe–Compose`（`Invariant Candidate` vs `Domain Candidate`）与双层递归自进化 |
| **HarnessPro** (`arXiv:2610.26770`, [`pzs19/HarnessPro`](https://github.com/pzs19/HarnessPro)) | 清华大学唐杰团队 & 智谱 AI | 性能–成本帕累托前沿优化、三档运行模式（`Lite / Medium / Heavy`）、`trace-diagnose` 轨迹诊断与无标签合成测试 |
| **Superagent** ([`deepseek-ai/dsh-superagent-prototype`](https://github.com/deepseek-ai/dsh-superagent-prototype)) | DeepSeek Harness 团队 | `000-Philosophy.md` 注意力守恒与默认观察模式、`dispatch.md` 零转述源指针直传、`verify.md` 出箱前确定性硬门禁 |
| **Skill Training** (`arXiv:2607.27557`) | Skill-Training Authors | `Corrupt -> Closed-Book Forward -> Ground-Truth Loss -> Reduce & Backward` 闭卷加噪重建与容量正则化 |
| **Prime Agent & Verifiers** ([`PrimeIntellect-ai/prime-agent`](https://github.com/PrimeIntellect-ai/prime-agent)) | Prime Intellect | 不可变基座（`INSTRUCTIONS.md`）与补充状态分离、`/refine` 轨迹沉淀、Cargo Rust 可执行校验器套件 |
| **PenguinHarness** ([`Prism-Shadow/penguin-harness`](https://github.com/Prism-Shadow/penguin-harness)) | Prism-Shadow | `ACE` 有效率账本、`AWM` 参数化工作流记忆、`OPRO` (`arXiv:2309.03409`) 单位体积效用评分与 `Continual-Learning` 防踩坑捕获 |
| **OpenWiki (`OKF v0.2`) & Ponytail** | LangChain & Google Research | 确定性 `wiki_finalize.py` 编译器、SHA-256 Grounded Claims 证据锚定与最小新造优先复用阶梯 |

## 📄 开源协议
本项目基于 [MIT License](LICENSE) 开源。

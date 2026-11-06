---
description: "🧹 全馆重整 (/organize) — Full-Library Primary-Source D-Merge & Pareto Bloat Audit"
---

User Request / Target Arguments: $ARGUMENTS

# `/organize` — 全量重新整理书栈 (`AgentStacks` Full-Library Primary-Source Reorganization & Pareto Audit)

本技能用于对 **`AgentStacks`（书栈 · 个人 Agent 图书馆）** 执行全馆或指定分馆的深度盘点、碎片卡回溯原书合并（`D-Merge`）、帕累托低效卡瘦身（`HarnessPro × OPRO`）与五级分形书架重排。

> [!CAUTION]
> **1. 手动触发锁（Manual-Only Lock）**：本技能带有 `disable-model-invocation: true`，仅在用户显式输入 `/organize` 或明确下达“全量重新整理书栈/重整某领域卡片”指令时运行，日常对话中绝不自动触发，确保零额外上下文开销。
> **2. 整理与合并卡片的第一铁律（Mandatory Primary-Source Re-Ingest Law）**：
> 在对任何现有卡片执行重构、提纯或 `D-Merge` 合并时，**绝对禁止只读旧 Markdown 卡片的二手文字做拼贴缩写！**
> - 必须先运行 `python3 .agents/skills/study/scripts/study_runner.py inspect-sources <card_id_or_path...>` 定位每张卡片背后的 `raw/` 一手源文件（PDF/EPUB 原书章节页码、原视频 `.en.vtt` 字幕、12 宫格接触印相图 `.jpg`）；
> - 必须使用 `read_file` **亲自重新阅读原始教材/原视频字幕与谱例**，用最原始的一手知识重新审视、校对并归纳；
> - 结合 `Skill Training (arXiv:2607.27557)` 的 **Corrupt $\rightarrow$ Closed-Book Forward $\rightarrow$ Ground-Truth Loss $\rightarrow$ Backward** 闭环，检验合并后的卡片与 Rust Verifier 能否真正拦截大模型的原生手癖并复现一手教材的大师水准！

---

## 核心四步整理工作流（4-Step Full Reorganization）

### Step 1：全馆健康度体检、帕累托前沿与全书覆盖率审计（Full-Stacks & Coverage Diagnostic）
依次运行以下确定性诊断脚本，获取当前书栈的树形结构、OPRO 单位体积效用分布、全书/全片 100% 吸收覆盖率与过期断言报告：
```bash
python3 .agents/skills/study/scripts/wiki_finalize.py
python3 .agents/skills/study/scripts/wiki_linter.py --strict
python3 .agents/skills/study/scripts/study_runner.py trace-diagnose --domain all
python3 .agents/skills/study/scripts/study_runner.py coverage-audit --domain all
python3 .agents/skills/study/scripts/study_runner.py inspect-sources [--domain <target_domain>]
python3 .agents/skills/study/scripts/wiki_query.py scoreboard
```
- **重点排查历史局部抽读漏章（Anti-Partial-Skimming Audit）**：检查 `coverage-audit` 输出中是否存在未完成章节的 `in_progress` 源材料，或仅被单张卡片窄范围引用的 `raw/` 教材，若有则立即补齐剩余章节的跨领域卡片沉淀，彻底杜绝二次返工。

### Step 2：回溯一手原书的碎片卡合并与低效卡瘦身（Primary-Source `D-Merge` & `B-Correct`）
针对 `trace-diagnose` 识别出的**臃肿低效卡（`> 3.8 KB` 且净命中为 0）**或同一领域内过度碎片化的卡片簇，按**一手教材/知识支柱（Primary Source Pillars）**分批重整：
1. **回溯原件**：运行 `study_runner.py inspect-sources <待重整卡片ID...>`，用 `read_file` 重新精读这批卡片对应的全部 `raw/` 一手 `.pdf/.epub` 章节或 `.vtt` 字幕 + 接触印相图。
2. **校正大师案例库（`03_deep_concepts/`）**：确认一手原书中的完整谱例、公式推导与参数表已完整保存在 `03_deep_concepts/*-cases.md`（供 `#section` 激光点读）。
3. **合并与硬化行动规则卡（`00_core / 01_craft / 02_style`）**：
   - 单行 `description` 明确写出触发屏障（`Use when...; 不适用于...`）；
   - `## Rules` 全部硬化为带数字、单位和正反例边界的定量硬指令（`00_core` $\le 45$ 行，`01/02` $\le 60$ 行）；
   - 将被合并旧卡的 `id` 与相对路径记入保留卡的 `aliases:` 列表，合并 `wiki/.claims/.../*.json` 证据链后删除冗余碎片卡。
4. **升级 Rust 校验器并跑通门禁**：同步升级 `scripts/{generators,verifiers}/*.rs` 并运行 `study_runner.py verify-suite --domain <domain>` 验证通过。

### Step 3：无损书架移位、过期断言调和与缺失溯源补齐（Shelf Re-Indexing & Claims Reconciliation）
1. **零断链移动/重命名**：若需调整卡片所属目录，使用：
   ```bash
   python3 .agents/skills/study/scripts/wiki_finalize.py --move <旧相对路径> <新相对路径>
   ```
2. **补齐缺失的一手证据绑定**：若 `inspect-sources` 报告某卡缺少 `raw/` 一手 URI，立即回查 `raw/` 原件补齐精确页码/时间戳。
3. **刷新过期断言（Stale Claims）**：核对修改过的脚本或卡片后，运行 `python3 .agents/skills/study/scripts/wiki_finalize.py --refresh-claims`。

### Step 4：回合末尾原子提交与 Git 历史滚动瘦身（Turn-End Atomic Commit）
每完成一个分馆或全馆的整理并通过 `wiki_linter.py --strict` 后，在收尾阶段一次性提交：
```bash
python3 .agents/skills/study/scripts/study_runner.py commit \
  --verb organize --domain "<tier>/<domain_or_all>" \
  --summary "<本批次基于一手原件重整/合并的书架模块与验证摘要>"
python3 .agents/skills/study/scripts/study_runner.py compact-git --keep 15
```

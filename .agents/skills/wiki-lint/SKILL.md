---
name: wiki-lint
description: >-
  Audit, refactor, consolidate fragmented cards from their primary raw sources, reconcile stale Grounded Claims,
  and evolve the 3-Tier LLM Wiki architecture using the Skill-Training (arXiv:2607.27557) memory regularization
  and primary-source re-ingest laws. Use when the user invokes /wiki-lint, asks to check wiki health, wants to
  consolidate/merge cards from original textbooks/videos, or wants to review curriculum gaps.
---

# `wiki-lint` — Primary-Source Consolidation, Memory Regularization & Wiki Health Audit

本技能融合了 `Skill Training (arXiv:2607.27557)` 的 **Memory Regularization & `D-Merge` 反膨胀机制**、**零二手加工提取铁律（Zero Secondary-Distillation Law）** 与 `LangChain OpenWiki` 的 Stale Claims 调和。

> [!CAUTION]
> **整理与合并卡片的第一铁律（Mandatory Primary-Source Re-Ingest Law）**：
> 在对任何现有卡片（如 `music_theory_acoustics` 的 106 张卡片）执行重构、提纯或 `D-Merge` 合并时，**绝对禁止只读旧 Markdown 卡片的二手文字做拼贴缩写！**
> 1. 必须先运行 `python3 .agents/skills/study/scripts/study_runner.py inspect-sources <card_id_or_path...>` 定位每张卡片背后的 `raw/` 一手源文件（PDF/EPUB 原书章节页码、原视频 `.en.vtt` 字幕、12 宫格谱例/画面接触印相图 `.jpg` 或原始 URL）；
> 2. 必须使用 `read_file` **亲自重新阅读原始教材/原视频字幕与谱例**，用最原始的一手知识重新审视、校对并归纳；
> 3. 结合 `skill-training` 的 **Corrupt $\rightarrow$ Closed-Book Forward $\rightarrow$ Ground-Truth Loss $\rightarrow$ Backward** 闭环，检验合并后的卡片与 Rust Verifier 能否真正拦截大模型的原生手癖并复现一手教材的大师水准！

---

## 核心工作流

### 1. 运行全量确定性诊断与一手源溯源审计
首先运行以下诊断命令获取当前 Wiki 的结构、卡片密度分布、一手源绑定状态、Cargo 单元测试与 ACE 记分板报告：
```bash
python3 .agents/skills/study/scripts/wiki_finalize.py
python3 .agents/skills/study/scripts/wiki_linter.py --strict
python3 .agents/skills/study/scripts/study_runner.py trace-diagnose --domain <target_domain>
python3 .agents/skills/study/scripts/study_runner.py inspect-sources --domain <target_domain>
python3 .agents/skills/study/scripts/wiki_query.py scoreboard
```

### 2. 五项深度语义重整与修复（Primary-Source Refactoring & Regularization）

1. **回溯一手教材的模块化重整与 `D — Merge` 合并（Primary-Source Consolidation）**：
   - 当某领域卡片过度碎片化（如同一门课或同一教材体系被拆成数十张碎片卡，导致生成时触发 `Skill Activation Failure` 漏读）时，按**一手教材/知识支柱（Primary Source Pillars）**分批重整：
     1. **Step A（回溯原件）**：运行 `study_runner.py inspect-sources <待重整卡片ID...>`，用 `read_file` 重新精读这批卡片对应的全部 `raw/` 一手 `.pdf/.epub` 章节或 `.en.vtt` 字幕 + 接触印相图；
     2. **Step B（校正案例库 `03_deep_concepts/`）**：核对一手原书/原视频中的大师谱例、公式与参数是否完整准确地保存在对应的 `03_deep_concepts/*-cases.md`（作为 `human_reference` 标准答案档案）；
     3. **Step C（重写与合并行动卡 `00/01/02`）**：基于一手原件与大模型易犯手癖（Amateur Flaws），将同源碎片卡融合重构为高内聚、定量化的主干卡片：
        - 单行 `description` 只写清晰的触发边界（`Use when... Not for...`）；
        - `## Rules` 全部采用带数字、带单位、带正反例边界的定量硬指令（Quantitative Hard Rules）；
        - 将被合并旧卡的 `id` 与相对路径全部记入保留卡的 `aliases:` 列表，并合并 `wiki/.claims/.../*.json` 中的一手证据链，删除冗余碎片文件并清理旧 `.claims` Sidecar。
     4. **Step D（升级 Rust 脚本与闭卷验证）**：检查绑定的 `scripts/{generators,verifiers}/*.rs` 是否完整覆盖了一手教材中的计算公式与禁忌红线，运行 `study_runner.py verify-suite --domain <domain>` 验证通过。

2. **补齐缺失的一手证据绑定（Zero Unsourced Card Gate）**：
   - 若 `inspect-sources` 或 `wiki_linter.py` 报告某张卡片缺少 `raw/` 一手证据绑定，必须立即回查 `raw/` 下的对应教材/视频字幕，重新核实内容并在卡片 `sources:` 及 `wiki/.claims/.../*.json` 中补齐精确的 `raw://...#page=X` 或 `raw://...#t=MM:SS` 证据。

3. **过期断言复核（Stale Grounded Claims Reconciliation）**：
   - 检查 `wiki_finalize.py` 报告的所有 `STALE` 断言，对照一手源材料与最新 Rust 脚本刷新 `fingerprint` 与 `status: "clean"`。

4. **无损移动、重命名与目录拆分（Remediation Channel 1）**：
   - 移动文件时使用零断链命令：
     ```bash
     python3 .agents/skills/study/scripts/wiki_finalize.py --move <旧相对路径> <新相对路径>
     ```

5. **错误提交精准回滚（Remediation Channel 4）**：
   - 若发现某次历史提交污染了规则，运行 `study_runner.py rollback --commit <commit_hash>`。

### 3. 原子提交重整结果
每完成一个知识支柱（Pillar）的一手回溯重整与验证后，脚踏实地执行一次原子提交：
```bash
python3 .agents/skills/study/scripts/study_runner.py commit --verb lint --domain "<tier>/<domain>" --summary "<本批次基于一手原件重整/合并的知识模块与验证摘要>"
```

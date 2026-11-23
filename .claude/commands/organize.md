---
disable-model-invocation: true
description: >-
  MANUAL INVOCATION ONLY. Full-library reorganization, primary-source card consolidation (D-Merge),
  HarnessPro x OPRO Pareto bloat diagnosis, L2 subtree re-clustering, and stale Grounded Claims
  reconciliation for AgentStacks (书栈). Activate ONLY when the user explicitly types "/organize"
  or explicitly commands to reorganize/consolidate the full AgentStacks library.
---

# `/organize` — 全量重新整理书栈 (`AgentStacks` Full-Library Primary-Source Reorganization & Pareto Audit)

本技能用于对 **`AgentStacks`（书栈 · 个人 Agent 图书馆）** 执行全馆或指定分馆的深度盘点、碎片卡回溯原书合并（`D-Merge`）、帕累托低效卡瘦身（`HarnessPro × OPRO`）与五级分形书架重排。

> [!CAUTION]
> **1. 手动触发锁（Manual-Only Lock）**：本技能带有 `disable-model-invocation: true`，仅在用户显式输入 `/organize` 或明确下达“全量重新整理书栈/重整某领域卡片”指令时运行，日常对话中绝不自动触发，确保零额外上下文开销。
> **2. 整理与合并卡片的第一铁律（Mandatory Primary-Source Re-Ingest Law）**：
> 在对任何现有卡片执行重构、提纯或 `D-Merge` 合并时，**绝对禁止只读旧 Markdown 卡片的二手文字做拼贴缩写！**
> - 必须先运行 `python3 .agents/skills/study/scripts/study_runner.py inspect-sources <card_id_or_path...>` 定位每张卡片背后的 `raw/` 一手源文件（PDF/EPUB 原书章节页码、原视频 `.en.vtt` 字幕、12 宫格接触印相图 `.jpg`）；
> - 必须使用宿主原生读文件/多模态工具（如 `read_file` / `Read`）**亲自重新阅读原始教材/原视频字幕与谱例**，用最原始的一手知识重新审视、校对并归纳；
> - 结合 `Skill Training (arXiv:2607.27557)` 的 **Corrupt $\rightarrow$ Closed-Book Forward $\rightarrow$ Ground-Truth Loss $\rightarrow$ Backward** 闭环，检验合并后的卡片与 Rust Verifier 能否真正拦截大模型的原生手癖并复现一手教材的大师水准！

---

## 核心四步整理工作流（4-Step Full Reorganization）

### Step 1：零浪费离线做梦分诊（`O(Δ)` Sleep-Time Dreaming Triage）、帕累托与覆盖率审计
依次运行以下零大模型成本（`0 LLM Token`）的确定性诊断脚本，优先筛出真正存在熵增/错误的微批次（Micro-Batch `<= 20 KB`），**严禁在海量休眠干净卡（`SLEEPING-CLEAN`）上盲目空耗 Token**：
```bash
python3 .agents/skills/study/scripts/wiki_finalize.py
python3 .agents/skills/study/scripts/wiki_linter.py --strict
python3 .agents/skills/study/scripts/study_runner.py dream-plan --domain all --max-batches 1 --budget-kb 20
python3 .agents/skills/study/scripts/study_runner.py trace-diagnose --domain all
python3 .agents/skills/study/scripts/study_runner.py coverage-audit --domain all
python3 .agents/skills/study/scripts/study_runner.py inspect-sources [--domain <target_domain>]
python3 .agents/skills/study/scripts/wiki_query.py scoreboard
```
- **零浪费短路法则（Zero-Token Short-Circuit）**：若 `dream-plan` 输出 `😴 [ZERO-TOKEN SHORT-CIRCUIT]` 且 `coverage-audit` 无未结项源材料，立即停止整理，绝不重读已休眠的干净卡片！
- **按 `P0 -> P4` 优先级处理唤醒微批次**：重点核验 `P0`（`harmful > 0` 或断言过期）、`P1`（单次会话临时记下的 `empirical-refine` 未审笔记，对照 `raw/` 指定 `#page` 页码核查防记反）、`P2`（`stale_after` 到期工具卡）、`P3`（`reads >= 5` 却 `0 helpful` 的占坑吸血鬼卡 & 博尔赫斯低密度膨胀卡）与 `P4`（同子树近重复卡与跨会话悬空链接）。
- **重点排查历史局部抽读漏章（Anti-Partial-Skimming Audit）**：检查 `coverage-audit` 输出中是否存在未完成章节的 `in_progress` 源材料，或仅被单张卡片窄范围引用的 `raw/` 教材，若有则立即补齐剩余章节的跨领域卡片沉淀，彻底杜绝二次返工。

### Step 2：回溯一手原书的碎片卡合并与低效卡瘦身（Primary-Source `D-Merge` & `B-Correct`）
针对 `trace-diagnose` 识别出的**臃肿低效卡（`> 3.8 KB` 且净命中为 0）**或同一领域内过度碎片化的卡片簇，按**一手教材/知识支柱（Primary Source Pillars）**分批重整：
1. **回溯原件**：运行 `study_runner.py inspect-sources <待重整卡片ID...>`，使用宿主原生读文件工具（如 `read_file` / `Read`）重新精读这批卡片对应的全部 `raw/` 一手 `.pdf/.epub` 章节或 `.vtt` 字幕 + 接触印相图。
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
3. **刷新过期断言并加盖休眠封印（Claims Reconciliation & `dream-seal`）**：核对修改过的脚本或复核完 `dream-plan` 微批次卡片后，运行：
   ```bash
   python3 .agents/skills/study/scripts/wiki_finalize.py --refresh-claims
   python3 .agents/skills/study/scripts/study_runner.py dream-seal <已复核确认无误的卡片ID...>
   ```
   使已复核卡片进入 `dreamedHash == bodyHash` 休眠态（下轮做梦 0 Token 跳过，且不改动 `.md` 文件时间戳，100% 保护 KV 前缀缓存）。

### Step 4：回合末尾原子提交与 Git 历史滚动瘦身（Turn-End Atomic Commit）
每完成一个分馆或全馆的整理并通过 `wiki_linter.py --strict` 后，在收尾阶段一次性提交：
```bash
python3 .agents/skills/study/scripts/study_runner.py commit \
  --verb organize --domain "<tier>/<domain_or_all>" \
  --summary "<本批次基于一手原件重整/合并的书架模块与验证摘要>"
python3 .agents/skills/study/scripts/study_runner.py compact-git --keep 15
```

---
name: wiki-refine
description: >-
  Persist empirical lessons, user aesthetic corrections, retry-saving environment gotchas,
  parameterized AWM workflows, or ACE rule effectiveness counters from a working trajectory into
  the LLM Wiki. Activate automatically when the user corrects a judgment/parameter, when >= 2
  tool retries were needed to solve an environment/CLI gotcha, or when the user invokes /wiki-refine.
---

# `wiki-refine` — Continual Harness Trajectory Refinement (`Prime Agent` + `HarnessPro` + `PenguinHarness RSI`)

本技能融合了 **`PrimeIntellect-ai/prime-agent` (`/refine` 不可变基座与补充状态分离)**、**唐杰团队 `HarnessPro` (`arXiv:2610.26770` 轨迹诊断与帕累托成本优化)** 以及 **`Prism-Shadow/penguin-harness` (`ACE` + `AWM` + `OPRO` + `Continual-Learning` 单假设原子编辑)**，用于在日常实战与长任务收尾时，以**零多余候选 Token 消耗、100% 保护 KV 前缀缓存（Prefix-Cache Pinning）**的方式，将**用户审美纠偏、排错防踩坑配方（Rules That Save Retries）与参数化工作流（AWM Workflows）**自动沉淀回 `wiki/`。

> [!IMPORTANT]
> **KV 前缀缓存保活铁律（Prefix-Cache Preservation Law）**：
> 1. **严禁在多步任务中途执行写库/记账命令**：所有 `record-trace`、`record-gotcha`、`record-workflow` 与 `commit` 必须延迟到**当前回合主任务交付就绪后的最后一步（Turn-End）**一次性批量执行，绝不在读取/生成/验证中途触发文件重算。
> 2. **零多候选 Token 浪费**：严格执行 `PenguinHarness` 单假设原子修订（Single-Hypothesis Atomic Edit），直接定位根因修改目标卡片或 Rust 校验器，严禁并发生成多份候选 Prompt 跑对比消耗 Token。

## 触发场景（When to Activate Automatically）

1. **用户实时纠偏（User Correction in Flight）**：当用户明确纠正了审美判断、配器/声部习惯或工具参数时，**先完成当前交付物的修改与验证**，随后在回合收尾阶段运行本技能将经验写入 Wiki，并对刚才误导的旧卡片记一次 `record-trace --harmful <card_id>`。
2. **排错踩坑自动捕获（Gotcha & Retry Harvester — $\ge 2$ 次重试自动触发）**：当你在一次任务中遇到了环境限制、CLI 参数陷阱、浏览器鉴权拦截或编译器报错，经历了 $\ge 2$ 次尝试才跑通正确解法时，在任务收尾前**无需用户提醒**，自动调用 `study_runner.py record-gotcha` 记入 [`wiki/GOTCHAS_AND_RETRY_SAVERS.md`](../../wiki/GOTCHAS_AND_RETRY_SAVERS.md)。
3. **成功复合轨迹抽象（AWM Workflow Induction）**：当你首次跑通一套多步骤跨工具协同流水线时，剥离具体文件名/ID 等实例值（替换为 `{parameter}` 占位符），调用 `study_runner.py record-workflow` 沉淀为 `Observation -> Reasoning -> Action` 标准子例程。
4. **手动触发（Explicit `/wiki-refine`）**：用户主动指示沉淀当前会话摸索出的脚本、参数或工作流。

## 执行步骤（Turn-End Batched Procedure）

1. **定位目标载体与层级**：
   - **环境/CLI/工具防踩坑配方（Gotchas）** $\rightarrow$ 运行：
     ```bash
     python3 .agents/skills/study/scripts/study_runner.py record-gotcha \
       --id <gotcha-id> --scope "<tool_or_domain>" \
       --symptom "<报错现象与死胡同>" --root-cause "<底层根因>" \
       --recipe "<一步到位标准解法>"
     ```
   - **可复用跨工具子例程（AWM Workflows）** $\rightarrow$ 运行：
     ```bash
     python3 .agents/skills/study/scripts/study_runner.py record-workflow \
       --id <wf-id> --title "<工作流标题>" --applies-when "<适用场景>" \
       --params-json '{"param1": "说明"}' \
       --steps-json '[{"observation": "...", "reasoning": "...", "action": "..."}]'
     ```
   - **领域审美/技法规则修订** $\rightarrow$ 使用 `wiki_query.py search` 定位卡片：
     - 个人最高审美底线 $\rightarrow$ `00_core_aesthetics/*.md`
     - 软件实操与脚本 $\rightarrow$ `wiki/tools_craft/<tool>/01_craft_playbooks/` 或 `scripts/`
     - 跨工具通用规律 $\rightarrow$ `wiki/foundations/<domain>/`（单次实战摸索设为 `epistemic_status: empirical-refine`，绝不冒充 `canonical`；若暴露系统理论盲区则运行 `study_runner.py add-gap`）。
2. **更新 ACE 规则与脚本命中账本**：
   - 运行 `study_runner.py record-trace --helpful <card_ids...> --scripts <script_names...> [--harmful <old_card_ids...>] --task "<任务摘要>"`。
3. **脚本与测试同步更新（Rust 优先）**：
   - 若该经验可转化为确定性校验指标，将其写入 `scripts/verifiers/*.rs` 并补充 `#[test]` 单元测试。
4. **确定性收尾、Scoreboard 快照与原子提交**：
   ```bash
   python3 .agents/skills/study/scripts/study_runner.py commit --verb refine --domain "<tier>/<domain>" --summary "<一句话总结本次沉淀的经验、Gotcha 或 AWM 工作流>"
   ```

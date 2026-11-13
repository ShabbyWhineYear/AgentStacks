# Phase 1 Prompt Guide: Primary-Source Ingest, Orthogonal Triage & Ground-Truth Extraction

## 1. 零二手加工提取铁律（Zero Secondary-Distillation & Primary-Source Law）

在新建、修改、合并或重构任何一张 Wiki 卡片前，**绝对禁止直接拿已有的旧 Markdown 卡片做二次缩写或表面润色**！
1. **强制回溯一手原件**：
   - 运行 `python3 .agents/skills/study/scripts/study_runner.py inspect-sources <card_id_or_domain>`，获取卡片绑定的 `raw/` 一手原书（`.pdf` / `.epub`）或原视频（`.en.vtt` 字幕 + 12 宫格接触印相图 `.jpg` / 原始 URL）。
   - 使用宿主原生读文件工具（如 `read_file` / `Read`）亲自打开并精读一手原书对应章节页码或原视频字幕/谱例画面，确保每一个概念、公式、谱例小节和定量参数都 100% 源自第一手权威教材，零信息失真、零二道贩子衰减！
2. **严禁 `+Distill` 直接抄书入卡**：
   - 论文 *Skill Training (arXiv:2607.27557)* 证明，直接从教材归纳规则（`+Distill`）只会写出充满教科书口号却无法纠正大模型原生手癖（Amateur Flaws）的无效卡片。
   - 因此，Phase 1 的任务**不是直接去写 `00/01/02` 规则卡**，而是**从一手原件中提取 100% 忠实的大师原稿标准答案（`human_reference`）与客观计算/校验算法**，供 Phase 2–4 的 `Corrupt -> Forward -> Loss -> Backward` 闭环提炼真正的实战规则！

## 2. 全书/全片穷尽吸收与跨领域正交分流（Exhaustive Full-Source Coverage & Cross-Domain Triage）

1. **严禁因“当前项目暂时不用”而跳章漏学（Zero Task-Biased Cherry-Picking）**：
   - 历史重大教训：如果第一次打开一本教材或教学视频时只学了当前项目关心的那几页，等到开发下一个相似项目时，发现急需的知识就在以前读过的教材里，会导致同一份教材反复返工重读！
   - **铁律**：凡进入 `/study` 的教材或视频，**必须通过 `study_runner.py register-source` 将全书所有章节或全片所有知识片段 100% 立项登记**；若某章节的知识不属于当前主领域（例如在读游戏架构书时遇到音频引擎、骨骼动画数学或叙事状态机章节），**绝对禁止跳过**，必须将其分流（`target_domain`）至对应的正交领域（如 `foundations/music_theory_acoustics`、`foundations/motion_animation`、`foundations/narrative_dramaturgy`）并完整沉淀为规则卡与脚本！
2. **“换一个宿主软件（如从 Aseprite 换到 Krita，或从 REAPER 换到 FL Studio），这条规律还成立吗？”**
   - **不成立**（软件特定快捷键、API、轨道块语法） $\rightarrow$ 归入 `wiki/tools_craft/<tool>/`。
   - **依然成立**（和声声部连接、鼓组四肢音量独立性、明暗交界线色相偏移、分镜 180 度轴线） $\rightarrow$ **必须归入 `wiki/foundations/<domain>/`**！
3. **权威度定级（Epistemic Grading）**：
   - **一手权威专著/大师专业拆解** $\rightarrow$ `epistemic_status: canonical`，并主动吞并升级旧的 `provisional` 卡片。
   - **工具教程顺带提及的局部经验** $\rightarrow$ `epistemic_status: provisional`（正文顶部加 `[!WARNING]` 二手碎片提示，并登记 `study_runner.py add-gap`）。

## 3. 提取一手大师原稿档案（Lock `human_reference` into `03_deep_concepts/`）

从当前精读的一手教材/视频中，提取 **$\ge 2$ 个（Mini-Batch）原汁原味的大师成品范例（`human_reference`）**，将其完整精确的数据（如原书谱例的调性、BPM、逐小节音符/和弦 Voicing/鼓组点位、原书公式推导、原画光影色值）归档到 `03_deep_concepts/<topic>-cases.md`（每个案例一个清晰的 `## Case N: ...` 章节，标注精确的原书页码 `raw://...#page=X` 或视频时间戳 `raw://...#t=MM:SS`）。

## 4. Rust + Cargo 原生脚本化契约（Rust-First Scripting Contract）

凡一手教材中涉及客观计算或可量化红线的知识，**必须优先使用 Rust（`*.rs`）编写或升级脚本**（并在同文件底部内嵌 `#[cfg(test)] mod tests` 单元测试）：
1. **Generators / Analyzers (`scripts/generators/*.rs`, `scripts/analyzers/*.rs`)**：数学公式、音程/频率/声位计算、色彩空间换算、透视网格、工程渲染器。
2. **Verifiers (`scripts/verifiers/*.rs`)**：将一手教材中的禁忌红线与大模型常见手癖转化为确定性校验器（返回码 `0` 通过，非 `0` 报错并指出具体违例小节/参数及对应的 Wiki 卡片 ID）。

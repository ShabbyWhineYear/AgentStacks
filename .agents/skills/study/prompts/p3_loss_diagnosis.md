# Phase 3 Prompt Guide: Side-by-Side Ground-Truth Loss & Credit Assignment (Ported from `skill-training` `loss.md` + `memory_policy.md`)

本阶段严格复刻论文 *Skill Training with Corruption and Reconstruction Loop (arXiv:2607.27557)* 的 `s3_loss.py`（一手原稿与模型重建稿并排对比）与 `memory_policy.md` 的逐卡归因法则。

> *"Two artifacts were created from the same outline. One is by a human master (`human_reference` from the primary textbook/video), the other by a model (`model_reconstruction`). Both cover the same skeleton, so skeleton differences are not interesting. What matters is everything the two creators did differently with the same material."*

## 1. 并排对比四大铁律（Side-by-Side Comparison Rules）

将 Phase 2 闭卷生成的 `model_reconstruction` 与 Phase 1 锁定的第一手教材/视频原稿 `human_reference` 并排放置（同时运行 `study_runner.py verify-suite --domain <domain> --artifact <path>` 并通过宿主读文件/检视工具如 `read_file` / `Read` 亲自试听 `.wav` 音频或检视 `.png` 画稿）：
1. **先客观描述两边各自做了什么，再评判高下（Describe Both First, Then Judge）**：
   - 谁的声部更密/更疏？谁的律动切分更连续？谁的动态起伏更大？谁更早收束？
2. **双向捕捉差距（Both Directions Matter Equally）**：
   - **模型写多了什么外行赘余（Over-generation / Amateur Habits）？** 例如：每 4 小节机械乱砸 Crash 镲片、滥用复杂变化音爵士和弦导致浑浊、无休止的碎音堆砌或冗余旁白。
   - **模型写少了什么大师精髓（Under-generation / Missing Craft）？** 例如：踩镲缺少连续 8 分音符 Shank-Tip 强弱呼吸、军鼓缺少 Ghost Notes、中音区（`C3–C4`）缺少内声部支撑、声部连接缺少反行与导音解决、旋律缺少五声性动机呼吸留白。
3. **必须具体引用原书/原视频与模型草稿（Get Concrete — Quote Both）**：
   - **严禁写“律动需要打磨”、“和声需要更丰富”这种毫无用处的废话口号！**
   - 必须逐条引用：
     - **`[Human Original]`**：引用一手原书（页码/谱例小节号）或原视频（时间戳）的具体音符、Voicing 排列、力度区间或参数数值；
     - **`[Model Draft]`**：引用模型重建稿在同一小节/同一位置实际写出的具体音符、力度或参数；
     - **`[Nature of the Gap]`**：明确指出这是模型不知道的行业规范（Convention）、缺乏的节奏/空间本能（Instinct），还是模型自带的业余坏习惯（Amateur Habit）。

## 2. 对照 `forward_reads` 进行逐卡信用归因（Credit Assignment）

把刚才产出的 Loss 报告与 Phase 2 的 `forward_reads`（实际打开了哪些卡片）放在一起，对每一处发现的差距或亮点做严格归因：
1. **`[It Helped]`（有效规则）**：
   - 重建稿在某维度与一手原稿高度一致，且正是因为打开并遵循了某张已读卡片 $\rightarrow$ 标记该卡 `+helpful`。
2. **`[It Misled / Too Vague]`（误导或过于空泛）**：
   - 重建时明明打开了某张卡片，但模型依然犯错！原因只有两个：
     - 要么卡片写成了软绵绵的定性建议（缺少带数字、单位和“严禁/必须”的定量硬边界，见论文 Section 4.3 Table 5）；
     - 要么卡片适用范围写得太宽/太死板，在不该用的时候硬套了规则。
3. **`[Routing Failure]`（路由断联：有卡却没打开）**：
   - 检查领域 `_domain_index.md` 完整头列表：库里其实早就有一张讲这件事的卡，但 Phase 2 重建时**根本没有打开它**！
   - **铁律**：这属于路由失败，**在 Phase 4 中绝对禁止新建第二张重复卡片**，必须去修改那张旧卡的单行 `description`（触发边界）或补齐 `depends_on`！
4. **`[Missing Rule / Verifier Deficit]`（规则空白或校验器漏检）**：
   - 一手原书/原视频中的这项关键手艺在当前库中确实空白，或者模型犯了明显的客观违例（如越界半音碰撞、踩镲断流、调性偏离）却未被 `scripts/verifiers/*.rs` 拦截 $\rightarrow$ 必须在 Phase 4 中写入规则并升级 Rust Verifier！

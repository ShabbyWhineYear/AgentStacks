# Phase 2 Prompt Guide: Corrupt & Closed-Book Reconstruct (Ported from `skill-training` `diffuse_heavy.md` + `forward.md`)

本阶段严格复刻论文 *Skill Training with Corruption and Reconstruction Loop (arXiv:2607.27557)* 的 `s1_diffuse.py`（加噪去手艺）与 `s2_forward.py`（闭卷重建与读取轨迹记录）。

## Step 2A: Corrupt（去手艺留题干 — 对应 `prompts/diffuse_heavy.md`）

> *"This is a corruption step. The goal is not a good piece of writing -- it is to strip out everything a master contributes, so that whoever reads your summary later has to reinvent all of it. Take the craft away and leave the skeleton."*

针对 Phase 1 从一手教材/视频中锁定的每个大师原稿（`human_reference`，建议每批次 $\ge 2$ 个案例组成 Mini-Batch），将其剥离专业手艺细节，生成**加噪重建题干（`corrupted_outline`）**：
- **必须保留（Keep）**：
  - **音乐场景**：曲目的真实调性（Key）、速度（BPM）、拍号、段落结构、基础主旋律简谱/歌词或基础罗马数字功能骨架；
  - **视觉/分镜/动画场景**：故事事件因果链、角色关系、场景环境与镜头情绪目标；
  - **游戏/系统工程场景**：核心玩法循环目标、业务约束与输入输出接口定义。
- **必须彻底抹除（Remove — 迫使重建者自己做决定）**：
  - **音乐场景**：抹除大师原本的具体声部排列（Voicing/Drop-2/UST）、内声部对位线条、经过音/副属插音、贝斯律动切分、鼓组 Shank-Tip 力度包络/幽灵音/过门加花及分轨混音参数；
  - **视觉/分镜/动画场景**：抹除大师原本的具体机位焦段、构图三分/透视灭点坐标、明暗交界线冷暖偏移色值与缓动曲线控制点；
  - **绝不泄露答案**：题干中严禁出现任何提示原稿答案的术语或参数。

## Step 2B: Closed-Book Forward（严格闭卷重建 — 对应 `prompts/forward.md` & `s2_forward.py`）

> *"This is a closed-book exercise. These outlines were made by stripping the craft out of an artifact somebody else created, and that artifact is the answer key. Do not open or copy the answer key during reconstruction."*

1. **模拟路由读取（Routing View Only）**：
   - 先查看目标领域的 `_domain_index.md`（仅含每张卡片的 `path` 与单行 `description`，等同于 `skill-training` 的 `memory.headers(pool)`）：
   - **必全读 `00_core_aesthetics/`**：读取该目录下全部核心铁律卡（如同 `skill-training` 中每次必全读 `00_general_rules/`）；
   - **按单行 `description` 选读 `01_craft_playbooks/` 与 `02_style_specifics/`**：仅打开 `description` 明确匹配当前 `corrupted_outline` 需求的卡片（通过 `wiki_query.py read <card_id>` 自动连带加载 `depends_on` 跨层依赖）。**全读或全不读均视为路由失败！**
2. **闭卷生成重建稿（`model_reconstruction`）**：
   - 在**严禁偷看 `human_reference` 答案细节**的前提下，仅凭已打开的规则卡与绑定的 Generator 脚本，将 `corrupted_outline` 重建为完整的工程产物（如完整多轨总谱 `.json` + 渲染 `.wav`、画稿 `.png` 或系统配置）。
   - 如果当前库里还没有相关规则（或旧规则写得含糊），**不要靠偷看原书答案来掩饰**——诚实地按当前规则写出重建稿，让大模型原生的外行手癖（Amateur Flaws）充分暴露，这正是 Phase 3 最宝贵的训练信号！
3. **记录读取轨迹（`forward_reads`）**：
   - 忠实记录本次闭卷重建实际打开了哪些卡片 ID、调用了哪些脚本，供 Phase 3–4 进行精确的信用归因（Credit Assignment）。

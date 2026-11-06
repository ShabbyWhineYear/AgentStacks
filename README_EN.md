# 🧠 Self-Evolving LLM-Wiki × Sirchmunk Learning Framework

<p align="center">
  <a href="README.md">简体中文</a> | <strong>English</strong>
</p>

> **A Zero-Context-Explosion, 3-Tier Orthogonal, Pareto-Budgeted, Self-Evolving External Brain for Coding & Creative Agents.**
>
> Fuses **Karpathy's LLM Wiki**, **ModelScope Sirchmunk (`arXiv:2608.16185`)**, **Google Research Harness² (`google-research/harness2`)**, **Tsinghua Prof. Jie Tang Team's HarnessPro (`arXiv:2610.26770`)**, **DeepSeek `dsh-superagent-prototype`**, **Closed-Book Skill Training (`arXiv:2607.27557`)**, **Prime Agent (`PrimeIntellect-ai/prime-agent` & `verifiers`)**, **PenguinHarness (`ACE + AWM + OPRO + Gotcha Harvester`)**, and **LangChain OpenWiki (`OKF v0.2`)**.

---

## ✨ Why This Framework?

Traditional agent memory systems and flat Markdown wikis fail as knowledge grows:
1. **Context & Cost Explosion**: Dumping full cards and transitive dependencies into the prompt quickly exceeds `100 KB–400 KB+`, degrading LLM reasoning and exhausting token budgets.
2. **Shallow Textbook Parroting (`+Distill` Trap)**: Summarizing textbooks directly produces vague, qualitative platitudes instead of fixing the model's actual amateur habits.
3. **Tool-vs-Foundation Coupling**: Mixing software button-clicks (e.g., Blender/Krita/REAPER) with universal domain theory (e.g., anatomy, color science, music theory) creates siloed, non-reusable rules.
4. **Mental Arithmetic & Subagent Paraphrase Degradation**: Letting LLMs mentally guess domain math or delegating core synthesis via lossy paraphrased prompts to cold-start subagents.

This repository provides a **clean, Day-0 empty learning framework shell** ready to ingest textbooks, papers, and video courses from scratch (`/study`) without ever suffering context explosion.

---

## 🏗️ Core Architecture Pillars

1. **3-Tier Orthogonal Matrix**: `wiki/foundations/<domain>/` (universal axioms), `wiki/tools_craft/<tool>/` (software craft), and `wiki/workflows/<pipeline>/` (end-to-end pipelines).
2. **Sirchmunk (`arXiv:2608.16185`) × HarnessPro (`arXiv:2610.26770`) Pareto Context Engine**:
   - **PageIndex `DocumentTree` (`wiki/.tree-index.json`)** for `#section` laser reading.
   - **3 Pareto Operating Modes (`wiki_query.py read --mode {lite,medium,heavy}`)**: `Lite (<=6KB)` Contract Digests only, `Medium (<=18KB)` primary prose + 1-Hop Contract Digests, and `Heavy (<=40KB)` full 1-Hop expansion.
   - **Trace Diagnosis & OPRO Utility-per-KB Audit (`study_runner.py trace-diagnose`)**.
3. **Google Research `Harness²` (`google-research/harness2`) Dual-Candidate Recursion**:
   - Models the 8-component editable harness space (`system_prompt`, `guardrails`, `memory`, `skills`, `tool_guidance`, `scripts`, `subagents`, `middleware`) and separates **Invariant Candidates** from **Domain-Specific Candidates** via `study_runner.py propose-probe`.
4. **DeepSeek `dsh-superagent-prototype` Router-Worker & Attention Conservation**:
   - Enforces `000-Philosophy.md` Observe-by-Default, `dispatch.md` Zero-Paraphrase Source-Pointer Law, and `verify.md` Pre-Outbox Deterministic Verification Gate.
5. **Closed-Book Skill Training (`arXiv:2607.27557`) × Prime Agent × PenguinHarness**:
   - `Corrupt -> Closed-Book Forward -> Ground-Truth Loss -> Reduce & Backward`, paired with Cargo Rust verifiers, Scoped Gotchas, AWM Workflows, and ACE/OPRO Scoreboard.

---

## 🚀 Quickstart (From 0 Knowledge)

```bash
git clone https://github.com/ShabbyWhineYear/llm-wiki-sirchmunk.git
cd llm-wiki-sirchmunk

# 1. Sync tree indexes & run strict linter + unit tests
python3 .agents/skills/study/scripts/wiki_finalize.py
python3 .agents/skills/study/scripts/wiki_linter.py --strict

# 2. Inspect tree & Pareto trace diagnostics
python3 .agents/skills/study/scripts/wiki_query.py tree
python3 .agents/skills/study/scripts/study_runner.py trace-diagnose --domain all

# 3. Read cards with HarnessPro Pareto modes (lite | medium | heavy)
python3 .agents/skills/study/scripts/wiki_query.py read <card_id>[#section] --mode medium
```

## 📄 License
MIT License. See [LICENSE](LICENSE).

# 📚 AgentStacks (书栈): The Self-Evolving Personal Agent Library

<p align="center">
  <a href="README.md">简体中文</a> | <strong>English</strong>
</p>

> **A Zero-Context-Explosion, 5-Level Fractal Stacks, Pareto-Budgeted, Prefix-Cache-Pinned Personal Library for Coding & Creative Agents.**
>
> **Why `AgentStacks` (书栈)?**
> In library science, **Stacks** are the multi-tiered bookshelves organized by call numbers; in software engineering, a **Stack** is your executable technology and call stack. **AgentStacks (书栈)** turns every primary-source textbook (PDF/EPUB) and course you study into a 5-level fractal bookshelf (`L0–L4`) backed by deterministic Rust verifiers—giving your agent a personal library that grows infinitely wiser without ever overflowing its context window or invalidating its KV prefix cache.
>
> Fuses **Karpathy's LLM Wiki**, **ModelScope Sirchmunk (`arXiv:2608.16185`)**, **Tsinghua Prof. Jie Tang Team's HarnessPro (`arXiv:2610.26770`)**, **DeepSeek `dsh-superagent-prototype`**, **Closed-Book Skill Training (`arXiv:2607.27557`)**, **Prime Agent (`PrimeIntellect-ai/prime-agent` & `verifiers`)**, **PenguinHarness (`ACE + AWM + OPRO + Gotcha Harvester`)**, and **LangChain OpenWiki (`OKF v0.2`)**.

---

## 🏛️ The Personal Agent Library Metaphor (`AgentStacks`)

| Library Space Metaphor | AgentStacks Layer | Context Cost | Role |
| :--- | :--- | :--- | :--- |
| **📖 Primary Source Vault** | `raw/<tier>/<domain>/` + `*.receipt.json` | `0 KB` (on-demand) | Permanent primary-source PDF/EPUB archive (Git tracks only `1 KB` SHA-256 Ex-Libris `.receipt.json` stamps). |
| **🗺️ L0 Main Catalog Hall** | [`GEMINI.md`](GEMINI.md) & [`wiki/index.md`](wiki/index.md) | `~3 KB` | Byte-frozen master catalog at Token 0 (preserves 100% KV Prefix Cache across card/trace updates). |
| **🚪 L1 Reading Rooms** | `wiki/<tier>/<domain>/quickstart.md` | `≤ 4.5–14 KB` | 3 Orthogonal Wings: **Foundations (`foundations`)**, **Tools Craft (`tools_craft`)**, and **Workflows (`workflows`)**. |
| **🗄️ L2 Subject Stacks** | `_subtrees/<slug>.md` & `_domain_gotchas.md` | `~2.5 KB` | `Sirchmunk` weak-semantic-edge subject clusters + scoped domain troubleshooting notes. |
| **📜 L3 Action Folios & Lending Modes** | `00_core / 01_craft / 02_style` (`--mode {lite,medium,heavy}`) | `≤ 6 / 18 / 40 KB` | Quantitative rules distilled via closed-book reconstruction, read via `HarnessPro` Pareto lending modes. |
| **🔖 L4 Section Bookmarks & Lab** | `03_deep_concepts/#section` + `wiki/**/scripts/*.rs` | `~2–5 KB` | `PageIndex` `#section` laser reading + unified Cargo Rust generators, analyzers, and verifiers. |

---

## ✨ Why AgentStacks?

Traditional agent memory systems and flat Markdown wikis fail as knowledge grows:
1. **Context Explosion & Prefix-Cache Invalidation**: Dumping full cards into the prompt exceeds `100 KB–400 KB+`, while mutating dynamic counters in `GEMINI.md` or rewriting file timestamps mid-task invalidates the LLM's Token-0 KV Prefix Cache.
2. **Shallow Textbook Parroting (`+Distill` Trap)**: Summarizing textbooks directly produces vague, qualitative platitudes instead of fixing the model's actual amateur habits.
3. **Tool-vs-Foundation Coupling**: Mixing software button-clicks (e.g., Blender/Krita/REAPER) with universal domain theory (e.g., anatomy, color science, music theory) creates siloed, non-reusable rules.
4. **Mental Arithmetic & Subagent Paraphrase Degradation**: Letting LLMs mentally guess domain math or delegating core synthesis via lossy paraphrased prompts to cold-start subagents.

This repository provides a **clean, Day-0 empty Personal Agent Library shell** ready to ingest textbooks, papers, and video courses from scratch (`/study`) without ever suffering context explosion.

---

## 🏗️ Core Architecture Pillars

1. **3-Tier Orthogonal Matrix**: `wiki/foundations/<domain>/` (universal axioms), `wiki/tools_craft/<tool>/` (software craft), and `wiki/workflows/<pipeline>/` (end-to-end pipelines).
2. **Sirchmunk (`arXiv:2608.16185`) × HarnessPro (`arXiv:2610.26770`) Pareto Context Engine**:
   - **PageIndex `DocumentTree` (`wiki/.tree-index.json`)** for `#section` laser reading.
   - **3 Pareto Operating Modes (`wiki_query.py read --mode {lite,medium,heavy}`)**: `Lite (<=6KB)` Contract Digests only, `Medium (<=18KB)` primary prose + 1-Hop Contract Digests, and `Heavy (<=40KB)` full 1-Hop expansion.
   - **Trace Diagnosis & OPRO Utility-per-KB Audit (`study_runner.py trace-diagnose`)**—zero multi-candidate token waste.
3. **KV Prefix-Cache Pinning & Turn-End Deferred Write Engine**:
   - Removes volatile card/script/ACE counters from `GEMINI.md` and `_domain_index.md`, enforces idempotent `write_text_if_changed` / `write_json_if_changed` (`0` file writes when unchanged), and defers all telemetry/commits to Turn-End.
4. **DeepSeek `dsh-superagent-prototype` Router-Worker & Attention Conservation**:
   - Enforces `000-Philosophy.md` Observe-by-Default, `dispatch.md` Zero-Paraphrase Source-Pointer Law, and `verify.md` Pre-Outbox Deterministic Verification Gate.
5. **Closed-Book Skill Training (`arXiv:2607.27557`) × Prime Agent × PenguinHarness**:
   - `Corrupt -> Closed-Book Forward -> Ground-Truth Loss -> Reduce & Backward`, paired with Cargo Rust verifiers, Scoped Gotchas, AWM Workflows, and ACE/OPRO Scoreboard.

---

## 🚀 Quickstart (From 0 Knowledge)

```bash
git clone https://github.com/ShabbyWhineYear/AgentStacks.git
cd AgentStacks

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

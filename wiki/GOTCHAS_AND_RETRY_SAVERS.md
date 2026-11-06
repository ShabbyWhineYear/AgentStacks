---
okf_version: "0.2"
title: 排错防踩坑分级树账本 (Scoped Rules That Save Retries — PenguinHarness × Sirchmunk)
description: "Hierarchical L1 router for 110+ retry-saving environment, compiler, DAW, and domain gotchas."
---

# 排错防踩坑分级树账本 (`Rules That Save Retries` — L1 Scoped Router)

> [!IMPORTANT]
> **零重试复发与作用域屏障铁律（`PenguinHarness` Continual-Learning × `Sirchmunk` Barrier Edges）**：
> 1. 为防止全量 115+ 条跨领域踩坑记录撑爆上下文，本根账本采用 **L1 作用域树形索引（Scoped Tree Router）**，完整症状/根因/解法已按领域分发至各模块的 `_domain_gotchas.md`。
> 2. **推荐按需精准检索**：运行 `python3 .agents/skills/study/scripts/wiki_query.py gotchas --scope <domain_or_tool>`（或 `--query <关键词>`）；同时 `wiki_query.py read <card>` 会自动附带当前领域的 Top 防踩坑提示！
> 3. 凡在单次会话中经历了 $\ge 2$ 次工具调用报错/重试才摸索出正确方案，收尾时必须运行 `python3 .agents/skills/study/scripts/study_runner.py record-gotcha` 记入本账本。

## 1. L1 领域/工具防踩坑分册路由表 (Scoped Gotcha Sub-Ledgers)

| 作用域 (`Scope / Domain`) | 条目数 | 分册详情入口 (`Full Recipes`) | 一键按作用域检索命令 |
| :--- | :---: | :--- | :--- |
| **`_env_and_cli`** *(Chrome DevTools / macOS / PDF / SSH)* | **0** | [`_env_and_cli_gotchas.md`](_env_and_cli_gotchas.md) | `wiki_query.py gotchas --scope chrome` |

## 2. 高频必知 Top 12 跨领域防踩坑速查摘要 (High-Hotness Digest)

| ID | 作用域 (`scope`) | 核心一步到位标准解法摘要 (`1-Line Recipe Digest`) | ACE |
| :--- | :--- | :--- | :---: |

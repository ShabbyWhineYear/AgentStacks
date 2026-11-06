---
okf_version: "0.2"
title: RSI 自进化记分板与 ACE 命中看板 (Evolution Scoreboard)
description: "Versioned quantitative scoreboard tracking Wiki canonical maturity, Cargo verifier coverage, and ACE rule effectiveness."
---

# RSI 自进化记分板与 ACE 实战命中看板 (`SCOREBOARD`)

## 1. 当前实时基线指标 (Live Reference State)

| 指标维度 | 当前数值 | 状态说明 |
| :--- | :---: | :--- |
| **知识卡总数 (`Total Cards`)** | **0** | 权威卡 `0` 张 (`0.0%`) / 临时卡 `0` 张 |
| **Cargo Rust 脚本数 (`[[bin]]`)** | **0** | 全量纳入 `cargo test --quiet` 确定性门禁 |
| **密码学证据链健康度 (`Claims Clean`)** | **0/0** | 过期断言数: `0` |
| **防踩坑配方数 (`Gotchas`) & AWM 工作流** | **0 / 0** | 已沉淀的零重试经验与参数化子例程 |
| **ACE 累计实战有效命中 (`Total Helpful`)** | **`+0`** | 区分『仅读取』与『实战真正应用且通过校验』 |

## 2. ACE 规则卡与 Rust 脚本实战命中排行榜 (Top Applied Assets)

| 类型 | 资产 ID / 脚本名 | `Helpful` (`+`) | `Harmful` (`-`) | 最近一次实战应用任务 (`last_task`) |
| :---: | :--- | :---: | :---: | :--- |
| — | *(暂无实战命中记录)* | 0 | 0 | — |

## 3. 版本演进历史轨迹 (`scoreboard.json` History)

| 版本 (`vN`) | 时间 (`UTC/ISO`) | 动作 (`verb`) | 权威卡占比 (`Canonical`) | Rust 脚本数 | ACE 累计有效命中 | 演进摘要 (`summary`) |
| :---: | :--- | :---: | :---: | :---: | :---: | :--- |
| **`v1`** | `2026-01-01T00:00:00` | `init(framework)` | `0/0` (`0.0%`) | `0` | `+0` | Initialized clean Day-0 LLM-Wiki 2.0 × Sirchmunk self-evolving learning framework. |

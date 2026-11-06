---
okf_version: "0.2"
title: AWM 参数化工作流记忆库 (Agent Workflow Memory — Ported from PenguinHarness)
description: "Reusable parameterized sub-routines (Observation -> Reasoning -> Action) induced from successful trajectories."
---

# AWM 参数化工作流记忆库 (`Agent Workflow Memory`)

> [!TIP]
> **参数化工作流复用铁律（Ported from `PenguinHarness` `awm-rsi-toolkit`）**：
> 每个工作流均从真实成功轨迹中剥离具体实例值（抽离为 `{parameter}` 占位符），固化为 `Observation -> Reasoning -> Action` 标准序列。遇到同类任务时直接绑定参数执行，严禁重新试探。

- *(暂无已归纳的参数化工作流)*
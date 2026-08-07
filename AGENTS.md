# Repository Rules

These rules apply to the entire repository.

## Non-negotiable publication gate

Every record written to the Pages payload must have positive evidence for:

1. a wall-mounted (壁挂式) or floor-standing (立柜式) unit type — central,
   mobile, and unknown-type AC units are never published;
2. inverter (变频) operation — 定频/unknown inverter fails closed;
3. an APF value meeting the tier minimum: 壁挂式 ≥ 5.0 (preferred ≥ 5.3),
   立柜式 ≥ 4.2 (preferred ≥ 4.5).  Unknown APF fails closed.

Unknown values fail closed.  `throttle_type` (节流装置: 电子膨胀阀/毛细管) and
`coil_rows` (外机铜管排数: 单排/1.6排/双排) are NOT hard gates, but every
published record must carry both fields (unknown allowed and marked in the
UI).  The frontend may hide more records, but it must never turn a rejected
raw record into a published record.

## Hardware facts (铜管排数 / 节流装置)

Spec tables on PConline/ZOL/JD do not carry these fields.  They are filled
by `scripts/ai_extract_hardware.py`, which requires a positive evidence URL
for every claimed value; unverifiable facts stay "未知" (fail closed).
Do not weaken this policy in crawler, merge, audit, test, or UI code.

## Data and source integrity

- Keep source-local product IDs as evidence, never as cross-source identity.
- Deduplicate by the normalized AC model identity (KFR-XX.../...).
- Store canonical atomic sources in `atomic_source_names`; aliases normalize
  to `PConline`, `ZOL`, or `JD`.
- Preserve the last published eligible baseline and reject publication shrink.
- A crawler artifact with fewer than 50 records is incomplete and must fail.
- Crawlers must run behind the airport-subscription proxy with per-request
  node rotation (`scripts/node_rotator.py`); blocked nodes are blacklisted at
  runtime and re-probed on the next run.

## Engineering workflow

- Run `python -m pytest tests/ -v` before committing.
- Keep crawler network parsing separate from deterministic merge and audit logic.
- Add fixtures/tests when changing source parsing or publication semantics.
- Use `actions/checkout@main` and `actions/setup-python@main`; do not pin action
  commit SHAs in this repository.
- Git commits must use `Fatty911 <xuerui911@gmail.com>`, never a bot identity.

## Git 提交作者身份规则（Fatty911 全局要求，2026-08-04）

本仓库所有 Git 提交必须遵守以下作者命名规则：

1. **全局兜底身份**：`Fatty911 <xuerui911@gmail.com>`。禁止使用 `bot@users.noreply.github.com` 邮箱（该邮箱关联 GitHub 用户名 `bot`，网页端会显示纯 `bot`）。
2. **Agent 工具显式提交**：使用动态格式 `<实际工具名>-<实际模型>`。工具名 = 实际执行提交的 Agent 工具（如 hermes-agent / codex / opencode / openclaw / mimocode / qoder）。模型名 = 本次实际处理会话的模型 ID 的小写紧凑写法（如 GLM-5.2 → `glm5.2`、GPT-5.6-Sol → `gpt5.6sol`、Kimi-K3 → `kimi-k3`、DeepSeek-V4-Flash → `deepseek-v4-flash`）。示例：`opencode-kimi-k3`、`hermes-agent-glm5.2`、`codex-gpt5.5`。
3. 禁止纯 `bot` 名称或系统 bot 身份冒充源码/文档提交；`github-actions[bot]` 仅限数据/进度自动提交。
4. 邮箱一律使用 `xuerui911@gmail.com`。

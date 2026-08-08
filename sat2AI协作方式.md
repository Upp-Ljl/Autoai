# SAT2 AI 协作方式

> 本文件是 SAT2 科研协作制度在 Relay 实现仓库中的维护镜像。科研任务、PR、任务 YAML、精确 SHA、实验和证据仍以 `Upp-Ljl/sat2` 为权威事实。
>
> **Relay 2.2.2 聚焦覆盖规则。** 本文件原有的科学治理、证据边界、任务范围和人工高风险门继续有效；仅协作消息机制更新为 Relay 2.2.2。任何要求 Mentor/Worker Session 手写、校验或发布 Relay YAML、SHA、`parent_event_id`、actor、target、event ID 或 timestamp 的旧规则均停止使用。
>
> 收到 Relay Capsule 的 Session 只输出正常科研报告，并在末尾提交一个带当前 `delivery_token` 的简短 Decision JSON。本地 Relay 负责角色、状态、PR head、parent、correlation、SHA、路由、YAML 序列化、GitHub 评论发布、去重与恢复。完整规则见 [`docs/SAT2_CHAT_RELAY_PROTOCOL.md`](docs/SAT2_CHAT_RELAY_PROTOCOL.md)。
>
> Windows 继续采用按需启动：登录时不启动 Relay、不监听 8765、不注册登录计划任务。完成一次 extension ID 与受限 Native Messaging host 的本地配对后，日常直接在浏览器扩展点击“一键启动协作”，由插件启动本地 Relay、开启自动推进并执行首轮 heartbeat / poll / delivery。桌面 `SAT2 Relay - Start or Repair` 只保留为故障后备。目标 Session 暂时未绑定或离线时，消息保持等待，不视为科研失败。
>
> 完整且可执行的 Mentor task 文档本身就是正常任务授权；Relay 自动生成兼容性的根控制事件并投递指定 Worker，不再要求用户重复点击“授权任务”。Mentor 按同一份冻结 task contract 审查并给出 `MENTOR_ACCEPTED` 后，Relay 校验通过即自动进入 `COMPLETE`，不再增加机械二次确认。只有 task 文档明确列出的 human gates、不可判定冲突或 `TASK_BLOCKED` 才暂停。Relay 仍不得自动 merge、mark ready、dispatch workflow、运行 qualification/formal experiment、修改 registry/seed/accepted evidence 或论文数字与 claim。
>
> 扩展的“绑定 Session 回复完成提醒”独立于自动推进：自动推进关闭、甚至本地 daemon 停止时，只要具体 ChatGPT conversation 已绑定且扩展运行，新的 assistant 回复完成后仍可弹出浏览器通知；该提醒不产生 Relay 控制事件，也不改变任务状态。
>
> 当前 Relay 2.2.2 状态为“源码已实现、待真实 Windows + ChatGPT 闭环验收”；在完成一次 `插件一键启动 → Mentor 文档自动 dispatch → Worker checkpoint → Mentor review → next Session` 现场闭环前，不得宣称长期稳定无人值守运行已经验证。

本文件是 SAT2 项目的长期协作、GitHub 操作和证据治理协议。任何新 session 开始工作前，必须先读取本文件、`.sat2/project.yml`、当前 open PR 及其任务文件。

动态状态不在本文件重复维护：

```text
当前阶段、活动 PR、活动任务      .sat2/project.yml
任务范围、允许修改和验收标准     .sat2/tasks/<task-id>.yml
Relay 机器协作协议               docs/SAT2_CHAT_RELAY_PROTOCOL.md
workflow 名称与触发策略          doc/WORKFLOW_CATALOG.md
accepted run / artifact / hash   outputs/721_evaluation/paper_bundle/evidence_registry.json
当前执行和审查记录               活动 PR
```

## 1. 唯一事实源

```text
main                    当前稳定科研基线
当前唯一活动 PR          当前任务控制面
精确 commit SHA         代码、论文和实验身份
GitHub Actions run      执行记录
Artifact / Registry     正式证据
paper source            论文事实
```

聊天记忆、旧 session、截图、单次本地运行和未提交文件都不是项目事实源。Relay 的 SQLite 与扩展存储只用于恢复、去重和路由，不替代 GitHub。

## 2. 角色

| 角色 | 负责 | 不负责 |
|---|---|---|
| 用户 | 科研优先级、最终取舍、合并授权 | 手工追日志、逐文件核对 artifact、手工转发每条 Session 消息 |
| Mentor | 冻结任务；审查 PR、run、artifact；授权论文 claim | 把计划、pilot、单 seed 或成功 run 自动当正式结论 |
| Worker | 按任务单改代码或论文、提交、测试、运行命令、分析日志、生成产物 | 自行改变科学结论、放宽约束、修改历史 evidence、自动 merge |
| GitHub PR | 当前任务控制面、状态记录和交接入口 | 替代科研判断 |
| SAT2 Relay | Capsule 生成、角色路由、Decision 校验、确定性控制评论发布、去重与恢复 | 替代科研判断、改代码、运行实验、自动 merge |
| GitHub Actions | 在精确 SHA 上执行开发审计或正式实验 | 决定结果是否能写入论文 |
| Artifact / Registry | raw、aggregate、manifest、hash、source snapshot | 因“文件存在”自动证明 claim 有效 |

Mentor 和 Worker session 都可替换；仓库状态不可替换。

## 3. 主线与分支

1. `main` 始终代表当前稳定科研基线。
2. 每个阶段只保留一个活动控制 PR。
3. 所有新任务从最新 `main` 创建短生命周期分支。
4. 不再建立长期 stacked PR 链。
5. 历史 PR 只用于追溯，不承担新决策。
6. 同一任务、同一分支、同一文件集合只能有一个 Worker 写入。
7. AI session 不得 force-push `main`，不得删除 accepted evidence 分支或记录。

推荐分支：

```text
paper/<task-id>
experiment/<task-id>
fix/<task-id>
maintenance/<task-id>
```

## 4. 新 session 启动流程

无 Relay Capsule 时：

```text
1. 读取 sat2AI协作方式.md
2. 读取 .sat2/project.yml
3. 查询 open/draft PR
4. 确认唯一活动控制 PR
5. 读取 PR body、base/head SHA、任务文件和最近 run
6. 读取 evidence registry 与论文路径
7. 区分证据成熟度
8. 再决定本次唯一下一步
```

有 Relay Capsule 时：

```text
1. 核对 Capsule 中的 role、Task ID、PR、精确 SHA 和 delivery_token
2. 读取 Capsule 指定的任务 YAML、源评论、PR diff 和 required reading
3. 检查 allowed / forbidden paths 与 human gates
4. 完成当前唯一科研或工程动作
5. 输出人类可读报告
6. 追加一个当前 delivery_token 绑定的 SAT2_RELAY_DECISION JSON
7. 不手写 Relay YAML、SHA、parent、actor、target、event ID 或 timestamp
```

标准 Decision：

````markdown
<!-- SAT2_RELAY_DECISION -->
```json
{
  "delivery_token": "从当前 Capsule 原样复制",
  "decision": "WORKER_CHECKPOINT",
  "summary": "已完成当前受限任务，等待 Mentor 审查。"
}
```
````

自动检测失败时，使用扩展的“提交当前 Decision”按钮；该按钮必须走相同的 token、角色、状态、PR head、去重和 outbox 路径，不要求复制 YAML 或 SHA。

首份报告：

```text
SAT2 Mentor Status
Repository:
Default branch / SHA:
Active control PR:
Base SHA:
Head SHA:
Active task:
Evidence maturity:
Accepted evidence:
Current blockers:
Next authorized action:
Forbidden actions:
```

无法访问 GitHub 时，不得声称已读取仓库。

## 5. 任务文件

每项工作必须在 `.sat2/tasks/` 下有任务文件，并由活动 PR 引用。任务必须冻结：

- `task_id`；
- 科研目标；
- base SHA；
- allowed / forbidden paths；
- registry、methods、seeds；
- workflow；
- 验收标准；
- 预期 artifact；
- paper impact；
- forbidden claims。

Worker 越出 allowed paths 前，必须由 Mentor 或用户先更新任务单。

## 6. Base SHA 与 Candidate SHA

```text
Base SHA       Worker 开始修改的起点
Candidate SHA  Worker 提交后供验证和正式实验执行的精确版本
```

任何修改都会产生新 SHA。新代码不能解释旧 run；正式实验暴露代码问题后，必须新 commit、新 candidate SHA、新 run。

Relay 在发布 Worker checkpoint 或 Mentor review 前必须重新读取当前 PR head。若 Mentor 审查期间 head 发生变化，必须返回 `STALE_PR_HEAD` 并拒绝发布旧审查结论。

## 7. 证据状态机

```text
SPECIFIED
→ IMPLEMENTED
→ PREFLIGHT_PASSED
→ FORMAL_RUNNING
→ EVIDENCE_COMPLETE
→ MENTOR_ACCEPTED
→ PAPER_INTEGRATED
```

| 状态 | 含义 | 可写论文 |
|---|---|---|
| `SPECIFIED` | 任务和验收标准冻结 | 否 |
| `IMPLEMENTED` | 代码、论文或脚本已提交 | 否 |
| `PREFLIGHT_PASSED` | 开发检查通过 | 否 |
| `FORMAL_RUNNING` | 固定 SHA 正式运行中 | 否 |
| `EVIDENCE_COMPLETE` | raw、aggregate、manifest、hash、artifact 完整 | 否，等待 Mentor |
| `MENTOR_ACCEPTED` | Mentor 已记录 allowed / forbidden claims | 是 |
| `PAPER_INTEGRATED` | 论文、图表、宏和 registry 已同步 | 是 |

```text
代码完成 ≠ 实验完成
workflow 成功 ≠ 统计结论有效
artifact 存在 ≠ 可写论文
Relay COMPLETE ≠ evidence MENTOR_ACCEPTED
```

## 8. Relay 2.2 状态与事件

Protocol State、Control Event 和 Delivery/Publish State 必须分开：

```text
Protocol State:
DISPATCHED
MENTOR_REVIEW
COMPLETE
BLOCKED

Control Event:
SAT2_TASK_AUTHORIZED
SAT2_WORKER_ACK
SAT2_WORKER_CHECKPOINT
SAT2_MENTOR_CHANGES_REQUIRED
SAT2_MENTOR_ACCEPTED
SAT2_TASK_BLOCKED

Delivery / Publish State:
QUEUED
WAITING_FOR_ENDPOINT
LEASED
CONFIRMED
WAITING_FOR_HUMAN
PUBLISH_UNCERTAIN
PUBLISHED
RETRYABLE_ERROR
```

核心转换：

```text
DISPATCHED
  -- SAT2_WORKER_CHECKPOINT -->
MENTOR_REVIEW

MENTOR_REVIEW
  -- SAT2_MENTOR_CHANGES_REQUIRED -->
DISPATCHED

MENTOR_REVIEW
  -- SAT2_MENTOR_ACCEPTED -->
COMPLETE
```

`SAT2_WORKER_ACK` 是兼容性信息事件，不改变 Protocol State，也不阻塞 Worker 当前 task contract 内的工作。正常 2.2.2 路径以 transcript-confirmed Capsule 作为传输收到证明。

## 9. Relay 决策与发布规则

Relay 2.2.2 从当前 endpoint、冻结 task contract、本地账本和 GitHub 重新读取并自动生成：

```text
task / PR
current head
parent_event_id
correlation_id
actor / target
candidate/reviewed/control SHA
attempt / timestamp
event ID / YAML
```

Session 不能覆盖这些字段。

每次发布前至少验证：

```text
Decision JSON schema
delivery_token and active delivery
conversation and role
legal Protocol State transition
parent_event_id == last_event_id
PR open and task/base unchanged
fresh current PR head
candidate/reviewed/control SHA binding
fixed target routing
allowed/forbidden paths
frozen task-contract SHA-256
outbox deduplication
```

目标 endpoint 暂时不可用时保持等待。GitHub POST 状态不确定时进入 `PUBLISH_UNCERTAIN`，先按稳定 marker 搜索已有评论，再决定是否重试，不生成重复控制评论。

完整且可执行的 Mentor task 文档由 Relay 自动生成 v2 兼容的根控制事件并 dispatch，无需 Dashboard 再授权。执行过程中 task 文档 hash 发生变化时 fail closed，必须显式 rebaseline/新任务。通过同一冻结 task contract 的 `SAT2_MENTOR_ACCEPTED` 校验后直接进入 `COMPLETE`。

## 10. GitHub Actions 策略

长期 workflow 及其触发规则只以 `doc/WORKFLOW_CATALOG.md` 为准。当前策略是：

- 只保留开发审计和正式 deadline sweep 两个 workflow；
- 两者均为 `workflow_dispatch`；
- 必须输入完整 40 位 `source_sha`；
- 不允许 `push`、`pull_request`、`issue_comment`、`schedule`、`workflow_run` 或链式触发；
- 普通提交、PR 更新和评论不会启动 Actions；
- 不创建临时 workflow 执行仓库管理操作。

### 10.1 执行优先级

启动和查看 workflow 的优先级固定为：

```text
优先级 1：Work session 使用已认证的 GitHub CLI（gh）
优先级 2：用户在 GitHub Actions 页面手动 Run workflow
```

优先使用 `gh`，因为它可以完成启动、读取 run ID、查看状态、读取日志和下载 artifact，减少用户等待和网页操作。

当 Work session 没有额度、`gh` 不可用、认证失效或本地环境暂时不可用时，立即退回 GitHub UI 手动运行，不阻塞任务，不创建替代性自动触发 workflow。

### 10.2 标准 `gh` 操作

开发审计：

```bash
gh workflow run sat2_manual_development_audit.yml \
  --repo Upp-Ljl/sat2 \
  --ref main \
  -f source_sha=<40位candidate SHA>
```

正式 deadline sweep：

```bash
gh workflow run sat2_manual_formal_deadline_sweep.yml \
  --repo Upp-Ljl/sat2 \
  --ref main \
  -f source_sha=<40位candidate SHA>
```

启动后必须取得并记录 run ID。查看状态优先使用：

```bash
gh run view <run-id> --repo Upp-Ljl/sat2
```

需要机器可读信息时使用：

```bash
gh run view <run-id> --repo Upp-Ljl/sat2 \
  --json status,conclusion,url,headSha,event,jobs
```

下载 artifact：

```bash
gh run download <run-id> --repo Upp-Ljl/sat2
```

### 10.3 手动 UI 回退

当 `gh` 路径不可用时：

1. 打开 GitHub Actions；
2. 选择允许的 workflow；
3. 点击 `Run workflow`；
4. 填入精确 40 位 candidate SHA；
5. 启动后把 run ID / URL 写入活动 PR；
6. 后续由 Mentor 根据 run ID 读取状态。

UI 回退与 `gh` 路径具有相同科学约束，不得放宽 seed、registry、method、denominator 或 artifact 要求。

## 11. 运行监控

Worker 和用户都不需要持续打开 Actions 页面，也不允许 session 无限轮询。

启动后，活动 PR 必须记录：

```text
Task ID:
Candidate SHA:
Workflow:
Run ID / URL:
Expected artifact:
Evidence maturity: PREFLIGHT_RUNNING / FORMAL_RUNNING
```

监控优先级：

```text
1. Work session 使用 gh run view <run-id>
2. Mentor 使用 GitHub 工具读取 run / jobs / steps / logs / artifacts
3. 用户通过 Actions 页面查看
4. 长时间任务可建立一次明确的外部提醒或条件检查
```

若 run 未结束，session 应记录 run ID 后结束当前回复；后续按 ID 续查。不得在单次会话中持续等待几十分钟。

完成后必须记录：

```text
Task ID
Source SHA
Run ID / URL
Conclusion
First failing job / step（若失败）
Artifact state
Evidence maturity
Next gate
```

## 12. 并发与重复运行

- 开发审计和正式实验使用仓库约定的互斥策略；
- 启动前先检查是否已有相同任务、相同 SHA 的 queued / in_progress run；
- 已存在时不得重复启动；
- 正式 run 不因新提交自动取消；
- 不依赖聊天记忆判断是否已经启动，必须查 run ID。

## 13. 失败分类

| 类别 | 表现 | 处理 |
|---|---|---|
| `P` 平台 | 无 job、无 step、BlobNotFound、runner 未启动 | 同 SHA 最多重跑一次；再次失败保留证据并诊断 |
| `I` 基础设施 | checkout、setup、pip、artifact service | 科学代码未变时可重跑 |
| `C` 代码/契约 | test、schema、runner、cardinality、hash | 必须新 commit、新 SHA、新 run |
| `E` 证据/聚合 | 缺 shard、重复 seed、配对或 hash 错误 | 先审计 raw，再决定重跑 |
| `PPR` 论文 | LaTeX、引用、宏、PDF 失败 | 不自动否定实验，但论文不可交付 |

相同工具错误连续出现两次后停止，不无限重试。

Relay 错误必须使用稳定错误码，并直接指出故障层、原因和恢复动作；不得只返回 HTTP 500。最低包括：

```text
ENDPOINT_NOT_BOUND
ENDPOINT_STALE
ROLE_BINDING_CONFLICT
DELIVERY_TOKEN_MISMATCH
DECISION_SCHEMA_INVALID
ROLE_DECISION_MISMATCH
ILLEGAL_STATE_TRANSITION
PARENT_EVENT_MISMATCH
STALE_PR_HEAD
TASK_FILE_INVALID
GITHUB_PERMISSION_DENIED
GITHUB_PUBLISH_UNCERTAIN
```

## 14. GitHub 能力路由

### Mentor 优先使用 Chat 中的 GitHub 连接能力

- 读取 PR、diff、评论和 review thread；
- 查询 base/head SHA；
- 读取 workflow jobs、steps、logs 和 artifacts；
- 写 PR review、状态评论和 Mentor 接受记录；
- 在明确授权下更新 PR 状态；
- 不通过临时 workflow 补足连接器缺失能力。

### Worker 优先使用 Work session

- checkout / 修改 / 测试 / commit / push；
- 使用 `gh workflow run` 启动允许的 workflow；
- 使用 `gh run view` 查看状态；
- 使用 `gh run download` 下载 artifact；
- 使用 REST API 执行经用户明确授权的仓库维护任务。

当 GitHub 连接器没有所需写接口时，使用 Work + 已认证 `gh`；若 Work 不可用，则由用户在 GitHub UI 手动操作。禁止创建临时 Actions workflow 规避接口限制。

## 15. Worker 回报格式

```text
Task ID:
Base SHA:
Candidate SHA:
Modified files:
Development validation:
Workflow / run:
Failure class, if any:
Artifacts:
Deviation from task specification:
Evidence maturity:
Paper-ready:
Decision required from Mentor:
```

`Paper-ready` 只能是：

```text
NO — implementation only
NO — evidence complete, awaiting Mentor review
YES — Mentor accepted
```

禁止“基本完成”“看起来正常”“应该可以合并”等模糊表述。

若本消息来自 Relay Capsule，回报末尾必须追加当前 `delivery_token` 绑定的 Decision JSON，不得追加手写控制 YAML。

## 16. Mentor 接受证据

Mentor 必须核对：

- source SHA；
- workload / record cardinality；
- paired seeds；
- method pairing；
- denominator 与 metric；
- raw 到 aggregate 可重算性；
- manifest、hash、source snapshot；
- 统计方法；
- null / negative / cost results；
- claim 边界。

接受记录：

```text
Task ID:
Status: MENTOR_ACCEPTED
Source SHA:
Run ID:
Artifact ID / digest:
Registry hash:
Raw hash:
Aggregate hash:
Allowed claims:
Forbidden claims:
Paper sections authorized:
```

论文只读取或引用 Mentor 接受的 evidence registry。

## 17. 正式证据长期保存

GitHub Actions artifact 可能过期。进入 `MENTOR_ACCEPTED` 的正式证据必须在过期前固化：

- accepted artifact ZIP；
- digest / hash；
- evidence registry；
- source snapshot；
-生成图表和论文宏所需的最小文件；
- 对应 run ID、artifact ID 和 source SHA。

优先使用 GitHub Release 或仓库认可的长期存档位置。不得只保留即将过期的 Actions artifact ID。

## 18. PR 组织

默认分为：

```text
Experiment PR   runner、registry、tests、formal evidence
Paper PR        text、figures、tables、generated macros
Submission PR   references、formatting、anonymization、final PDF
Maintenance PR  Relay、协作协议和非科研基础设施
```

一个 PR 不应同时承载新科学设计、大规模实验、论文重写和仓库清理。

推荐使用：

- Draft PR：Worker 实施期；
- PR review：Mentor 的 `COMMENT`、`REQUEST_CHANGES`、`APPROVE`；
- 少量状态 label：`sat2:worker-active`、`sat2:mentor-review`、`sat2:blocked`、`sat2:evidence-ready`、`sat2:paper`；
- branch ruleset：保护 `main`、禁止 force push、默认通过 PR 合并。

## 19. 合并规则

AI session、Relay、daemon 和扩展不得自行 merge，除非用户在当前对话明确授权。

```text
Worker 完成
→ 本地验证
→ 精确 SHA 开发审计（需要时）
→ Mentor 审查
→ 正式 evidence（需要时）
→ Paper / PDF audit
→ 用户授权
→ merge
```

不得为了验证通过而修改科学口径、formal seeds、denominator、负结果、历史 hash 或证据边界。

## 20. 固定协议

```text
1. main is the stable scientific baseline.
2. One active task has one control PR and one Worker.
3. GitHub, task YAML and exact SHA are authoritative; chat and Relay storage are not.
4. Sessions make scientific decisions; Relay validates, publishes and routes them.
5. Sessions never handwrite Relay YAML or control fields when responding to a Capsule.
6. A complete Mentor task document is the normal authorization; ordinary task progression does not wait for duplicate human approval.
7. Workflow execution is explicit, exact-SHA, and manual-only unless the task contract explicitly changes that human gate.
8. Development audit is engineering validation, not paper evidence.
9. Formal evidence requires raw, aggregate, manifest, hashes, seeds, and source snapshot.
10. Mentor acceptance is required before paper integration; Relay Protocol COMPLETE is not itself evidence acceptance.
11. Sessions are replaceable; repository state is not.
12. Merge and all high-risk gates declared by governance/task contract require current explicit authorization.
13. Relay 2.2.2 remains field-acceptance pending until one real closed loop is observed.
```

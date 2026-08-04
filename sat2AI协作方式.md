# SAT2 AI 协作方式

> **Relay 2.2 运行规则。** 本文件中的科学治理和人工门保持有效。任何要求 Session 手写、校验或发布 Relay YAML 事件块的旧表述，均由 [`docs/RELAY_2.2_OPERATION.md`](docs/RELAY_2.2_OPERATION.md) 替代：收到 Capsule 的 Session 只提交带 `delivery_token` 的 Decision JSON；本地 Relay 在校验后确定性生成和发布控制事件。旧版 v2 技术文档仅作历史参考。

本文件是 SAT2 项目的长期协作、GitHub 操作、证据治理和 Chat Relay 总协议。任何新 Session 开始工作前，必须先读取本文件、`.sat2/project.yml`、当前任务文件和对应 PR。

动态状态不在本文件重复维护：

```text
当前阶段、稳定基线和 accepted evidence    .sat2/project.yml
任务范围、允许修改和验收标准               .sat2/tasks/<task-id>.yml
Relay 角色、监控 PR、模式和超时             .sat2/relay.yml
Relay 机器接口                              doc/SAT2_CHAT_RELAY_PROTOCOL.md
workflow 名称与触发策略                     doc/WORKFLOW_CATALOG.md
accepted run / artifact / hash              outputs/721_evaluation/paper_bundle/evidence_registry.json
当前执行和审查记录                          对应 Worker PR / 阶段控制记录
```

## 1. 唯一事实源

```text
main                         当前稳定科研基线
阶段集成分支                  当前阶段已接受但尚未进入 main 的集成状态
任务 YAML                     范围、依赖、验收和禁止事项
Worker PR                     当前任务执行与审查控制面
精确 commit SHA              代码、论文和实验身份
GitHub Actions run           执行记录
Artifact / Registry          正式证据
paper source                 论文事实
```

聊天记忆、旧 Session、浏览器插件存储、截图、单次本地运行和未提交文件都不是项目事实源。Relay 的 SQLite 是恢复和去重记录，不替代 GitHub。

## 2. 角色

| 角色 | 负责 | 不负责 |
|---|---|---|
| 用户 | 科研优先级、最终取舍、人工高风险门、合并授权 | 手工传递每条 Session 消息、持续追日志 |
| Mentor | 冻结任务；审查 PR、run、artifact；授权下一任务和论文 claim | 把计划、pilot、单 seed 或成功 run 自动当正式结论 |
| Worker | 按任务单修改、测试、提交、分析日志、生成产物和 checkpoint | 自行改变科学结论、放宽约束、修改历史 evidence、自动 merge |
| GitHub | 保存任务、SHA、PR、review、run、artifact 和 Relay 事件 | 替代科研判断 |
| SAT2 Control Center | 读取 GitHub、校验协议和状态、持久排队、重试、报警 | 作科研判断、改代码、运行正式实验、自动 merge |
| Browser Relay Extension | 绑定现有 ChatGPT Session、投递 Capsule、验证消息进入会话 | 保存权威项目状态、读取 GitHub token、决定状态转换 |
| GitHub Actions | 在精确 SHA 上执行开发审计或正式实验 | 决定结果是否能写入论文 |
| Artifact / Registry | raw、aggregate、manifest、hash、source snapshot | 因文件存在自动证明 claim 有效 |

Mentor 和 Worker Session 都可替换；仓库状态不可替换。Control Center 和扩展是确定性本地软件，不是新的 AI Agent。

## 3. 控制面、主线与分支

1. `main` 始终代表当前稳定科研基线。
2. 一个阶段可以有一个阶段集成分支和多个并行 Worker PR。
3. 每个活动任务必须只有一个 Task ID、一个 Worker、一个 Worker PR 和一组独占写路径。
4. 同一任务、同一分支或同一文件集合不得由多个 Worker 同时写入。
5. 并行 Worker 只有在依赖满足、allowed paths 不重叠且 Mentor 分别授权后才能启动。
6. 历史 PR 只用于追溯，不承担新决策。
7. AI Session 不得 force-push `main`，不得删除 accepted evidence 分支或记录。
8. 不建立无界的长期 stacked PR 链；阶段集成分支中的每次合入必须绑定明确任务和 Mentor 接受记录。

推荐分支：

```text
work/<phase>-<role>-<task-id>
integration/<phase>
paper/<task-id>
experiment/<task-id>
fix/<task-id>
maintenance/<task-id>
```

## 4. 新 Session 启动流程

无 Relay Capsule 时：

```text
1. 读取 sat2AI协作方式.md
2. 读取 .sat2/project.yml
3. 查询 open/draft PR
4. 定位当前角色对应的唯一 Task ID 和 Worker PR
5. 读取 PR body、base/head SHA、任务文件和最近 run
6. 读取必要的 evidence registry 与论文路径
7. 区分 Relay 状态和证据成熟度
8. 再决定本次唯一下一步
```

有 Relay Capsule 时，Session 只需：

```text
1. 核对 Capsule 中的角色、Task ID、PR 和精确 SHA
2. 读取 Capsule 指定的任务 YAML、源评论和 PR diff
3. 检查 allowed / forbidden paths 与人工门
4. 执行一个明确动作
5. 用固定人类报告和一个带当前 `delivery_token` 的 Relay 2.2 Decision JSON 结束；不得手写 Relay YAML、SHA、parent event、target 或 event ID
```

无法访问 GitHub 时，不得声称已读取仓库，也不得根据旧聊天继续写入。

Mentor 首份报告：

```text
SAT2 Mentor Status
Repository:
Default branch / SHA:
Integration branch / SHA:
Active tasks / PRs:
Task under review:
Base SHA:
Head / candidate SHA:
Evidence maturity:
Accepted evidence:
Current blockers:
Next authorized action:
Forbidden actions:
```

## 5. 任务文件

每项工作必须在 `.sat2/tasks/` 下有任务文件，并由对应 Worker PR 引用。任务必须冻结：

- `task_id`；
- 科研目标；
- base SHA；
- Worker 角色与 Worker PR；
- dependencies；
- allowed / forbidden paths；
- registry、methods、seeds；
- workflow；
- 验收标准；
- 预期 artifact；
- paper impact；
- forbidden claims；
- human gates。

Worker 越出 allowed paths、改变公共接口或改变任务含义前，必须由 Mentor 或用户先更新任务单并发布新的授权事件。

## 6. Base SHA、Candidate SHA 与 PR Head

```text
Base SHA        Worker 开始修改的精确起点
Candidate SHA   Worker 提交后供审查、验证或运行的精确版本
PR Head SHA     当前 PR 最新提交；可能包含 candidate 后的纯元数据提交
Reviewed SHA    Mentor 实际审查和接受/驳回的精确版本
```

Relay 和 Mentor 必须区分 Candidate SHA、metadata-only checkpoint SHA 与当前 PR head。任何修改都会产生新 SHA。新代码不能解释旧 run；正式实验暴露代码问题后，必须新 commit、新 candidate SHA、新 run。

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
Relay ACCEPTED ≠ evidence MENTOR_ACCEPTED
```

## 8. Relay 执行状态机

Relay 状态只描述任务交接，不描述证据成熟度：

```text
DORMANT
→ READY
→ DISPATCHED
→ WORKING
→ MENTOR_REVIEW
→ CHANGES_REQUIRED → WORKING
                or → ACCEPTED
                or → BLOCKED
                or → HUMAN_GATE
```

Relay 必须拒绝乱序转换、重复事件、错误 PR、错误 Task ID 和 SHA 不一致。单个任务异常只暂停该任务，其他不冲突任务可继续。

## 9. Relay 机器接口

完整接口以 `doc/SAT2_CHAT_RELAY_PROTOCOL.md` 和 `.sat2/schemas/relay-event-v1.schema.json` 为准。

控制评论必须包含固定机器事件：

```text
SAT2_TASK_AUTHORIZED
SAT2_WORKER_ACK
SAT2_WORKER_CHECKPOINT
SAT2_MENTOR_CHANGES_REQUIRED
SAT2_MENTOR_ACCEPTED
SAT2_TASK_BLOCKED
SAT2_HUMAN_GATE
SAT2_RELAY_ALERT
SAT2_TASK_CANCELLED
```

事件缺字段时不得猜测、补写或继续。普通自然语言评论仍可用于解释，但不自动触发 Relay。

Control Center 可以：

- 定期读取已配置 PR 的顶层评论；
- 验证 trusted actor、Schema、Task ID、PR、SHA 和状态转换；
- 生成短 Execution Capsule；
- 持久排队、去重、租约恢复和重试；
- 记录本地和 GitHub Alert。

在 Relay 2.2 中，Session 的结构化 Decision 经 delivery token、角色、当前状态、PR head 和去重校验后，Control Center 才可从 GitHub 与本地状态确定性生成完整控制事件并发布。它不得从普通自然语言推断决定，也不得替 Session 作科研判断。

Control Center 不可以：

- 自动 merge 或 mark ready；
- 自动启动 workflow 或正式实验；
- 修改代码、论文、registry、seed、accepted evidence 或历史 hash；
- 根据自然语言自行扩大任务范围；
- 绕过 ChatGPT 或 GitHub 的权限确认。

## 10. GitHub Actions 策略

长期 workflow 及其触发规则只以 `doc/WORKFLOW_CATALOG.md` 为准：

- 只保留允许的开发审计和正式 deadline sweep workflow；
- 均为 `workflow_dispatch`；
- 必须输入完整 40 位 `source_sha`；
- 不允许 `push`、`pull_request`、`issue_comment`、`schedule`、`workflow_run` 或 Relay 链式触发；
- 普通提交、PR 更新、机器事件和评论不会启动 Actions；
- 不创建临时 workflow 执行仓库管理操作。

执行优先级：

```text
1. Work Session 使用已认证 gh
2. 用户在 GitHub Actions 页面手动 Run workflow
```

Relay 对 workflow 只产生 `SAT2_HUMAN_GATE`，不得直接执行。

## 11. 标准 workflow 操作

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

启动后必须取得并记录 run ID。查看状态：

```bash
gh run view <run-id> --repo Upp-Ljl/sat2
gh run view <run-id> --repo Upp-Ljl/sat2 \
  --json status,conclusion,url,headSha,event,jobs
gh run download <run-id> --repo Upp-Ljl/sat2
```

当 `gh` 不可用时使用 GitHub UI 手动回退，不能创建替代性自动触发 workflow。

## 12. 运行监控

Worker 和用户都不需要持续打开 Actions 页面，也不允许 Session 无限轮询。

启动后，Worker PR 必须记录：

```text
Task ID:
Candidate SHA:
Workflow:
Run ID / URL:
Expected artifact:
Evidence maturity: PREFLIGHT_RUNNING / FORMAL_RUNNING
```

若 run 未结束，Session 记录 run ID 后结束当前回复；后续按 ID 续查。完成后记录：

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

## 13. 并发、去重与路径冲突

- 启动任务前检查 dependencies；
- Control Center 在授权时检查已配置 dependency state 和活动 Worker scope 前缀冲突；
- 在 checkpoint/review 时按 monitor 的 allowed / forbidden patterns 检查 PR 全部 changed files；
- 每个 Worker 同时最多处理一个活动任务；
- Mentor 的审查消息串行排队，一次只绑定一个 PR 和一个 SHA；
- 同一任务、同一 SHA 的 queued / in_progress run 不得重复启动；
- 正式 run 不因新提交自动取消；
- 不依赖聊天记忆判断是否已启动，必须查 GitHub 和 run ID；
- Relay 使用事件 ID、评论 ID、body hash 和 target role 去重；
- 已处理控制评论被编辑时立即停止并报警。

## 14. 失败分类

科学和执行失败：

| 类别 | 表现 | 处理 |
|---|---|---|
| `P` 平台 | 无 job、无 step、BlobNotFound、runner 未启动 | 同 SHA 最多重跑一次；再次失败保留证据并诊断 |
| `I` 基础设施 | checkout、setup、pip、artifact service | 科学代码未变时可重跑 |
| `C` 代码/契约 | test、schema、runner、cardinality、hash | 必须新 commit、新 SHA、新 run |
| `E` 证据/聚合 | 缺 shard、重复 seed、配对或 hash 错误 | 先审计 raw，再决定重跑 |
| `PPR` 论文 | LaTeX、引用、宏、PDF 失败 | 不自动否定实验，但论文不可交付 |

Relay 失败：

| 类别 | 表现 | 处理 |
|---|---|---|
| `R-NET` | GitHub/本机网络暂时失败 | 60/300/900 秒有界重试 |
| `R-BUSY` | 目标 Session 正在生成 | 保持队列并重试 |
| `R-LOGIN` | ChatGPT 登录失效 | 暂停该角色并提醒重新登录 |
| `R-BIND` | Session 未绑定、标签页丢失 | 暂停该角色并提醒重新绑定 |
| `R-APP` | 必需 GitHub App 无法确认附加 | 停止发送，不降级伪装成功 |
| `R-PROTOCOL` | Schema、trusted actor、PR/Task/SHA 或状态错误 | 停止该任务并报警 |
| `R-EDIT` | 已处理控制评论被编辑 | Stop-the-line，人工核验 |

相同工具错误连续达到配置上限后停止，不无限重试。

## 15. GitHub 能力路由

### Mentor

优先使用 Chat 中的 GitHub 连接能力：

- 读取 PR、diff、评论和 review thread；
- 查询 base/head SHA；
- 读取 workflow jobs、steps、logs 和 artifacts；
- 写 PR review、状态评论和 Mentor 接受记录；
- 在明确授权下更新 PR 状态；
- 不通过临时 workflow 补足连接器缺失能力。

### Worker

优先使用 Work Session：

- checkout / 修改 / 测试 / commit / push；
- 使用 `gh workflow run` 启动允许的 workflow；
- 使用 `gh run view` 查看状态；
- 使用 `gh run download` 下载 artifact；
- 使用 REST API 执行经用户明确授权的仓库维护任务。

当 GitHub Connector 缺少所需写接口时，使用 Work + 已认证 `gh`；若 Work 不可用，则由用户在 GitHub UI 手动操作。

## 16. Worker 回报格式

人类可读部分：

```text
Task ID:
Base SHA:
Candidate SHA:
PR head SHA:
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

若当前消息由 Relay 2.2 Capsule 投递，回报末尾必须追加一个带该 Capsule `delivery_token` 的 `SAT2_RELAY_DECISION` JSON，且不得追加手写 YAML 事件块。无 Capsule 的人工 GitHub 操作仍须使用仓库当时有效的控制协议。

## 17. Mentor 审查与接受

Mentor 必须核对：

- Task ID、base SHA、candidate/reviewed SHA 和 PR head；
- allowed / forbidden paths；
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
Reviewed SHA:
Run ID:
Artifact ID / digest:
Registry hash:
Raw hash:
Aggregate hash:
Allowed claims:
Forbidden claims:
Paper sections authorized:
Next task / actor authorization:
```

若当前消息由 Relay 2.2 Capsule 投递，Mentor 结果必须追加带该 Capsule `delivery_token` 的 `SAT2_RELAY_DECISION` JSON；Relay 负责生成 `SAT2_MENTOR_CHANGES_REQUIRED`、`SAT2_MENTOR_ACCEPTED` 或 `SAT2_TASK_BLOCKED` 控制事件。Mentor Accepted 仍须本地人工确认。接受当前候选不自动授权下一任务。

论文只读取或引用 Mentor 接受的 evidence registry。

## 18. 正式证据长期保存

进入 `MENTOR_ACCEPTED` 的正式证据必须在 artifact 过期前固化：

- accepted artifact ZIP；
- digest / hash；
- evidence registry；
- source snapshot；
- 生成图表和论文宏所需的最小文件；
- 对应 run ID、artifact ID 和 source SHA。

不得只保留即将过期的 Actions artifact ID。

## 19. PR 组织

默认类型：

```text
Worker PR       一个 Task ID、一个 Worker、一组独占路径
Experiment PR   runner、registry、tests、formal evidence
Paper PR        text、figures、tables、generated macros
Submission PR   references、formatting、anonymization、final PDF
Maintenance PR  协作协议、Relay、非科研基础设施
```

一个 PR 不应同时承载新科学设计、大规模实验、论文重写和仓库清理。

推荐：

- Draft PR：Worker 实施期；
- PR review：Mentor 的 `COMMENT`、`REQUEST_CHANGES`、`APPROVE` 或权威顶层评论；
- 少量状态 label：`sat2:worker-active`、`sat2:mentor-review`、`sat2:blocked`、`sat2:evidence-ready`、`sat2:paper`、`sat2:relay-alert`；
- branch ruleset：保护 `main`、禁止 force push、默认通过 PR 合并。

## 20. 合并和人工高风险门

AI Session、Relay、守护程序和扩展不得自行 merge，除非用户在当前对话明确授权。

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

以下动作始终为人工门：

- merge、mark ready 或改变 base branch；
- workflow dispatch 和正式实验；
- registry、seed、accepted evidence、历史 hash 或 artifact 删除；
- 论文数字和核心 claim；
- force push；
- 扩大任务范围；
- 跨 Worker 路径冲突。

不得为了验证通过而修改科学口径、formal seeds、denominator、负结果、历史 hash 或证据边界。

## 21. Relay 报警

Relay 至少提供：

1. 本地浏览器/桌面提醒；
2. SQLite 持久报警记录；
3. 可选 GitHub Alert Issue 评论；
4. 严重异常在对应 Worker PR 中留下阻塞记录。

为了可靠触发 GitHub 邮件，Alert 评论应由低权限 GitHub App 或独立 bot 身份发布并 `@Upp-Ljl`。个人账号对自己的评论不作为保证自我邮件提醒的唯一机制。

报警只包含 Task ID、PR、事件 ID、故障类型、最后成功状态、重试次数和所需人工动作，不包含 token。

## 22. Relay 部署和启用

1. 协议、守护程序和扩展通过独立 Maintenance PR 引入；Windows 部署采用按需启动，不注册登录计划任务。
2. 初始 `.sat2/relay.yml` 必须是 `enabled: false`、`mode: shadow`、`monitors: []`。
3. 安装本地守护程序和扩展，绑定 Mentor 与一个测试 Worker。
4. 在 Shadow Mode 验证事件解析，不发送消息。
5. 在 Dry-run 完成 Worker → Mentor → Worker → Mentor。
6. 单 Worker Active 通过后再启用多 Worker。
7. 对进行中的 PR，只能在当前事件、父事件和精确 SHA 已由 Relay 验证后恢复或续接；不得凭历史聊天自动接管。

## 23. 新 Session 提示词

Mentor：

```text
请接管 SAT2 Mentor 任务。
优先读取 Relay Capsule 指定的 Task YAML、源评论和 PR；如无 Capsule，再读取
sat2AI协作方式.md、.sat2/project.yml、.sat2/relay.yml 和当前 open PR。
确认 Task ID、base/head/reviewed SHA、evidence maturity、阻塞项和唯一下一步。
不要依赖旧聊天，不要立即修改仓库。
完成时给出固定 Mentor 记录；若有当前 Relay 2.2 Capsule，再附该 Capsule 指定格式的 Decision JSON，不手写 Relay YAML。
```

Worker：

```text
请作为 SAT2 Worker。
优先读取 Relay Capsule 指定的 Task YAML、授权评论和 Worker PR；核对 base SHA、
allowed paths、验收标准、依赖和禁止事项后再修改。
GitHub Actions 优先使用已认证 gh；gh 不可用时回退用户手动 UI。
完成时按固定 Worker 格式报告；若有当前 Relay 2.2 Capsule，再附该 Capsule 指定格式的 Decision JSON，不手写 Relay YAML。
```

## 24. 固定协议

```text
1. main is the stable scientific baseline.
2. One active task has one Worker PR, one Worker, one exact base, and non-overlapping write paths.
3. Parallel Workers require accepted dependencies and explicit Mentor authorization.
4. GitHub and exact SHA are authoritative; chat memory and Relay storage are not.
5. Relay validates and transports; it does not make scientific decisions.
6. Invalid, edited, untrusted, duplicate, out-of-order, or SHA-mismatched events fail closed.
7. Workflow execution is explicit, exact-SHA, manual-only, and never Relay-triggered.
8. Development audit is engineering validation, not paper evidence.
9. Formal evidence requires raw, aggregate, manifest, hashes, seeds, and source snapshot.
10. Mentor acceptance is required before paper integration.
11. Sessions and browser tabs are replaceable; repository state is not.
12. Merge and all high-risk gates require current explicit user authorization.
```

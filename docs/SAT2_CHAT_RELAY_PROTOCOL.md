# SAT2 Chat Relay Protocol 2.2.2

## 0. 核心原则

Relay 2.2.2 的正常协作是 **Mentor 文档驱动**，不是重复审批驱动。

```text
Mentor 在任务开始前写完整 task 文档
→ task 文档给出目标、范围、角色、依赖、验收标准和 human gates
→ Relay 校验文档是否可执行
→ Relay 自动投递 Worker
→ Worker 按文档工作并回传 checkpoint
→ Relay 自动投递 Mentor
→ Mentor 按同一份冻结任务合同验收
→ CHANGES_REQUIRED 回 Worker，或 ACCEPTED 结束任务
→ 依赖满足后自动发现下一份可执行任务文档
```

有效且完整的 Mentor task 文档本身就是正常任务授权。用户不需要再点击“授权任务”，`MENTOR_ACCEPTED` 也不需要机械二次确认。只有 task 文档明确列出的 `human_gates`、无法机器判定的冲突/缺失、或 `TASK_BLOCKED` 才停下来找用户。

Relay 不替代科研判断；它负责消息传递、角色与会话绑定、状态、SHA、parent、路由、去重、GitHub 控制评论和恢复。

当前实现状态：

```text
Mentor document contract validation: IMPLEMENTED IN SOURCE
automatic document dispatch: IMPLEMENTED IN SOURCE
automatic Mentor Accepted publication: IMPLEMENTED IN SOURCE
bidirectional Worker ↔ Mentor relay: IMPLEMENTED IN SOURCE
bound Session reply notification: IMPLEMENTED IN SOURCE
real Windows/ChatGPT closed-loop acceptance: PENDING FIELD ACCEPTANCE
long-term unattended stability: NOT YET CLAIMED
```

## 1. 权威事实

优先级固定为：

```text
1. 当前 GitHub PR / exact base+head SHA / comments
2. 当前 Mentor task YAML 及其 task_ref
3. 当前 Relay Capsule / parent event
4. 当前 checkpoint / handoff
5. 长期协作和科学方向文档
6. 历史聊天和历史状态描述
```

SQLite 和扩展存储只用于传输、恢复、去重和路由，不替代 GitHub。

## 2. Mentor task 文档合同

任务在自动投递前至少必须满足：

```text
task_id
non-empty title
explicit executable status
repository / PR / worker_role binding
purpose 或 objective
non-empty acceptance / acceptance_criteria
explicit allowed_paths
explicit forbidden_paths（可为空列表，但字段必须存在）
explicit human_gates（可为空列表，但字段必须存在）
base/branch 约束（文档提供时必须与当前 PR 一致）
```

Mentor 应在任务开始前把 `required_reading`、依赖关系、forbidden claims、阶段边界和验收判据写清楚。验收标准不得使用“基本完成”“差不多”“看起来可用”等不可判定措辞。

Relay 第一次自动 dispatch 时计算并冻结：

```text
task_contract_sha256 = SHA256(exact task document text)
```

后续 Worker checkpoint、Mentor changes-required、Mentor accepted 都必须仍对应同一 task-contract hash。执行过程中静默修改目标或验收标准时：

```text
TASK_SPEC_CHANGED_DURING_EXECUTION
→ fail closed
```

应创建新的任务/明确 rebaseline，而不是让旧 Session 按漂移标准继续。

## 3. 正常 Protocol State

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

`SAT2_WORKER_ACK` 只保留兼容性，不是正常路径的阻塞步骤。Capsule 已在会话 transcript 中确认，即代表传输层收到。

`WAITING_FOR_ENDPOINT` 是 delivery 状态，`PUBLISH_UNCERTAIN` 是 publish 状态，都不能冒充任务 Protocol State。

## 4. 文档自动 dispatch

Relay 每轮 poll 检查 enabled monitor。某任务尚无活动 Protocol State 时，只有同时满足以下条件才允许自动启动：

```text
Mentor task 文档存在且 YAML 可解析
Task/PR/role/paths 与 monitor 一致
status 可执行
purpose/objective 非空
acceptance criteria 非空
PR open
base/branch 约束一致
dependencies 满足
无 active write-scope conflict
github.allow_writes = true
```

Relay 为 v2 兼容仍在 GitHub 线上生成一次 `SAT2_TASK_AUTHORIZED` 根事件，但它的语义是 **deterministic document-dispatch root**，不是额外的人类授权门。Session 和用户不手写此事件。

## 5. Guidance Capsule

需要 Session 处理的 Capsule 至少携带：

```text
delivery token
role / task / PR
exact bound SHA / current control head
parent event / source
exact task file + ref
task-contract SHA-256
purpose/objective snapshot
acceptance criteria snapshot
required reading snapshot
allowed / forbidden paths
human gates
当前唯一动作
合法 Decision 集合
```

Capsule 中的验收标准快照用于防错，不能代替 Session 读取完整 task 文档。

## 6. Session 输出合同

正常 Worker 只需要：

```text
SAT2_RELAY_DECISION
{"delivery_token":"...","decision":"WORKER_CHECKPOINT","summary":"..."}
```

Mentor 只需要：

```text
SAT2_RELAY_DECISION
{"delivery_token":"...","decision":"MENTOR_CHANGES_REQUIRED","summary":"..."}
```

或：

```text
SAT2_RELAY_DECISION
{"delivery_token":"...","decision":"MENTOR_ACCEPTED","summary":"..."}
```

Session 不得填写或覆盖 task、PR、SHA、parent、actor、target、correlation、event ID、timestamp 或 Relay YAML。

Worker checkpoint 的语义是：Worker 声明当前 candidate 已满足 task 文档中本阶段适用的全部 acceptance criteria。Mentor 必须独立重新读取 task 文档、当前 PR diff 和 exact head 后逐项验收；不能只相信 Worker summary。

不满足、证据不足或文档/PR 不一致时应 `TASK_BLOCKED`，不得猜测或降低标准。

## 7. Mentor → Worker 传递保障

固定链路：

```text
Mentor task/changes decision
→ Relay 生成唯一 GitHub control event
→ GitHub poller 接受并更新本地 ledger
→ resolve_target(event) 固定计算 task.worker_role
→ 生成带随机 delivery_token 的 Worker Capsule
→ delivery 只能由绑定该 worker_role 的 fresh endpoint lease
→ extension 注入具体 /c/<conversation-id>
→ content script 必须在 transcript 中看到精确 Relay delivery marker
→ 才向 daemon 回报 DELIVERED
```

没有 Worker endpoint 时消息等待，不改科学状态、不跨角色投递。

## 8. Worker → Mentor 回传保障

固定链路：

```text
Worker 完成回复
→ Decision JSON 回传当前 delivery_token
→ extension 只在绑定 Worker conversation 中提取
→ 提交 installation/role/conversation/delivery/message hash
→ daemon 校验 endpoint、token、delivery、role、Protocol State
→ 发布前重新读取 PR current head
→ candidate_sha/control_head_sha 自动绑定 current head
→ 生成稳定 event marker + outbox
→ GitHub 发布
→ poller 接受 checkpoint
→ target 固定解析为 mentor
→ Mentor Capsule 只能由绑定 mentor endpoint lease
→ 注入 Mentor conversation 并确认 transcript marker
```

因此 Worker 不能自行把消息“发给另一个角色”，Mentor 也不能靠手写 target 改路由。

## 9. 因果绑定、去重与恢复

Decision 必须同时绑定：

```text
delivery_id
delivery_token
role
conversation_key
assistant_message_id
assistant_message_hash
current Protocol State
current PR head
```

同一：

```text
task_id + delivery_id + assistant_message_hash + decision + current_head
```

只允许生成一个 outbox/event marker。

GitHub POST 不确定时：

```text
PUBLISH_UNCERTAIN
→ 先按 stable marker 搜索 PR comments
→ 已存在则恢复 PUBLISHED
→ 不存在才重试原事件
```

Daemon 重启恢复 pending outbox/delivery；endpoint 临时离线保持等待。

## 10. STALE_PR_HEAD

Worker checkpoint 发布前：

```text
candidate_sha == control_head_sha == freshly-read PR head
```

Mentor review 发布前：

```text
reviewed_sha == checkpoint candidate_sha == freshly-read PR head
```

Mentor 审查期间 head 改变时返回 `STALE_PR_HEAD`，旧审查结论不得绑定新 head。

## 11. Human gates

任务文档中的 `human_gates` 才是暂停依据。Relay 永远不能因为“自动推进”而绕过：

```text
merge / mark ready
workflow dispatch
qualification / formal experiment
registry / seed / accepted evidence change
paper claim / paper number change
force push / base change / scope expansion
```

如果 Mentor task 文档没有授权这些动作，Session 不能执行。

## 12. 绑定 Session 回复完成提醒

扩展提供独立开关：

```text
绑定 Session 回复完成提醒
```

它与 `自动推进` 解耦。即使 Relay daemon 停止或自动推进关闭，只要具体 ChatGPT conversation 已绑定且扩展运行：

```text
assistant 开始/持续生成
→ watcher 等待生成停止且文本稳定
→ 独立 extension Port 上报完成
→ background 校验该 conversation 确实绑定某个 role
→ 去重
→ Chrome/Edge 弹出“SAT2 <role> 回复完成”通知
```

点击通知返回对应 Session。未绑定会话不提醒；历史页面首次加载不会对既有旧回答补发通知；同一回复只提醒一次。该功能不产生 Relay Control Event，也不改变 Protocol State。

## 13. 现场验收标准

正式宣称闭环可靠前至少现场跑通：

```text
Relay 完全停止
→ 插件一键启动协作
→ 自动发现完整 Mentor task 文档
→ Worker 收到 Capsule 并 transcript-confirmed
→ Worker checkpoint 自动发布
→ Mentor 收到 review Capsule 并 transcript-confirmed
→ Mentor changes-required 自动回 Worker
→ 第二轮 checkpoint 自动回 Mentor
→ Mentor accepted 自动 COMPLETE
→ 若有 dependency-satisfied 下一任务，则自动 dispatch
```

同时验证：

```text
0 手写 YAML / SHA / parent / target
0 跨角色 delivery
0 重复 GitHub control comment
错误 token/错误 conversation 被拒绝
endpoint 离线可恢复
daemon 重启可恢复
STALE_PR_HEAD fail closed
task contract 中途变化 fail closed
自动推进关闭时绑定 Session 回复提醒仍工作
```

在这条真实 Windows + ChatGPT 闭环完成前，状态只能写：`IMPLEMENTED IN SOURCE / PENDING FIELD ACCEPTANCE`。

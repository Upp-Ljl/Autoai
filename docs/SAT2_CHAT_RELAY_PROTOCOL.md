# SAT2 Chat Relay Protocol 2.2

## 0. 文档地位

本文件是 SAT2 Relay 2.2 的当前机器协作协议。它取代任何要求 ChatGPT Session 手写、校验或发布 Relay YAML 控制事件的旧规则。

科学治理、任务范围、精确 SHA、证据状态和人工高风险门仍由 SAT2 仓库中的协作协议、任务 YAML、PR 和 GitHub 事实决定。Relay 只传递、校验和确定性发布协作决定，不替代 Mentor 或 Worker 的科研判断。

当前实现状态：

```text
Relay 2.2 code and local installation: IMPLEMENTED
Decision JSON / deterministic publishing path: IMPLEMENTED
real Session closed-loop acceptance: PENDING FIELD ACCEPTANCE
long-term unattended stability: NOT YET CLAIMED
```

在完成一次真实的 `Decision JSON → GitHub control comment → next Session Capsule` 闭环前，只能称为“已实现、待现场验收”，不得称为长期稳定验证完成。

## 1. 权威事实顺序

对活动任务，事实优先级固定为：

```text
1. 当前 GitHub PR、base/head SHA、评论与 review
2. 当前任务 YAML 及其 task_ref
3. 当前 Relay Capsule 与源事件
4. 当前 checkpoint / handoff
5. SAT2 总协作协议和科学方向文档
6. 历史设计文档中的状态描述
```

SQLite、扩展存储和聊天记忆只用于恢复、去重和路由，不替代 GitHub 事实。

## 2. 组件职责

```text
Session:
  读取 Capsule 指定文档和 PR
  完成科研分析、源码工作或审查
  输出简短 Decision JSON

Browser Extension:
  绑定 role ↔ concrete /c/<conversation-id>
  heartbeat
  Capsule 注入与确认
  检测或手动提交当前 Decision

Local Relay:
  GitHub 轮询
  Guidance Capsule 生成
  状态、角色、SHA、parent 和路径校验
  控制事件确定性生成
  GitHub 评论发布
  outbox 恢复、去重、双向路由和诊断

GitHub:
  保存任务、PR、精确 SHA 和正式控制事件
```

扩展不得保存 GitHub PAT；Session 不得计算控制字段；Relay 不得作科研判断。

## 3. 按需运行

Windows 登录时：

```text
Relay daemon: NOT STARTED
supervisor: NOT STARTED
port 8765: CLOSED
login scheduled task: NONE
```

使用前由用户或本地 Agent运行：

```text
SAT2 Relay - Start or Repair
```

该入口启动本地 supervisor/daemon、检查健康并执行一次 poll。浏览器扩展在 daemon 已运行时自动 heartbeat 和投递，但当前版本不能在 daemon 完全停止时直接启动 Windows 进程。

停止入口应使 Relay 后台进程和 8765 监听归零。

## 4. Guidance Capsule

每条需要 Session 处理的 Capsule 至少包含：

```text
delivery_id
delivery_token
target_role
task_id
repository
PR number
exact bound SHA
parent/source event
source comment
required reading
task file path/ref
allowed paths
forbidden paths
human gates
one current action
expected Decision type
```

`delivery_token` 是本次 Session 回答与当前 Capsule 的因果绑定。它不是 GitHub token，也不是本地 API token。

目标 Session 未绑定、标签页关闭、浏览器休眠或 heartbeat 过期时，delivery 进入：

```text
WAITING_FOR_ENDPOINT
```

这不是 Protocol State，也不视为永久失败，不应消耗正常投递尝试次数。Endpoint 恢复后继续投递。

## 5. Session 输出合同

收到 Capsule 的 Session 可以先输出正常的人类可读科研报告，但末尾必须包含且只包含一个当前 Decision：

````markdown
<!-- SAT2_RELAY_DECISION -->
```json
{
  "delivery_token": "从当前 Capsule 原样复制",
  "decision": "WORKER_CHECKPOINT",
  "summary": "已完成当前范围内的源码修复，等待 Mentor 审查。"
}
```
````

Session 允许填写：

```text
delivery_token
decision
summary
```

当前支持的 `decision`：

```text
WORKER_ACK
WORKER_CHECKPOINT
MENTOR_CHANGES_REQUIRED
MENTOR_ACCEPTED
TASK_BLOCKED
```

Session 禁止填写或覆盖：

```text
protocol
event_id
event_type
repository
task_id
pr_number
actor_role
target_role
parent_event_id
correlation_id
base_sha
candidate_sha
reviewed_sha
control_head_sha
attempt
timestamp
YAML event block
```

普通自然语言、旧 Capsule 的 token、其他角色或其他会话中的 JSON 都不能推进任务。

## 6. 自动检测与手动后备

扩展提供两条等价路径：

```text
自动路径：
最新 assistant message 完成生成
→ 检测 SAT2_RELAY_DECISION
→ 提交 localhost Relay

手动后备：
用户点击“提交当前 Decision”
→ 读取当前 assistant message
→ 走同一校验、去重和发布路径
```

DOM 变化、流式输出或后台标签页导致自动检测失败时，手动按钮不得要求用户复制 JSON、YAML、SHA 或 parent event。

## 7. 三类状态严格分离

### 7.1 Protocol State

```text
DISPATCHED
MENTOR_REVIEW
COMPLETE
BLOCKED
```

### 7.2 Control Event

```text
SAT2_TASK_AUTHORIZED
SAT2_WORKER_ACK
SAT2_WORKER_CHECKPOINT
SAT2_MENTOR_CHANGES_REQUIRED
SAT2_MENTOR_ACCEPTED
SAT2_TASK_BLOCKED
```

### 7.3 Delivery / Publish State

```text
QUEUED
WAITING_FOR_ENDPOINT
LEASED
CONFIRMED
WAITING_FOR_HUMAN
PUBLISH_UNCERTAIN
PUBLISHED
RETRYABLE_ERROR
```

Delivery 或 Publish 状态不得被写成任务 Protocol State。

## 8. 最小状态转换

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

`SAT2_WORKER_ACK` 是信息事件，不改变 Protocol State，也不阻塞 Worker 开始当前授权范围内的工作。

`SAT2_TASK_BLOCKED` 将任务置于 `BLOCKED`，等待明确的人类恢复或新授权；Relay 不自行扩展任务范围。

非法转换必须在本地发布前拒绝，不能先污染 GitHub 再事后报警。

## 9. 固定路由

路由由事件类型和任务配置计算，不能由 Session 自报：

```text
SAT2_TASK_AUTHORIZED          → task.worker_role
SAT2_WORKER_ACK               → mentor
SAT2_WORKER_CHECKPOINT        → mentor
SAT2_MENTOR_CHANGES_REQUIRED  → task.worker_role
SAT2_MENTOR_ACCEPTED          → task.worker_role
SAT2_TASK_BLOCKED             → mentor
```

一个 concrete conversation 默认只能绑定一个角色；一个角色默认只能存在一个 active endpoint。冲突必须由 Deep Doctor 明确报告。

## 10. Relay 确定性生成控制事件

Relay 接收合法 Decision 后，从本地账本和 GitHub 重新读取：

```text
current endpoint role
current task and PR
current PR head
last accepted event / parent_event_id
correlation_id
worker role
current state
attempt
timestamp
```

然后生成 schema-valid YAML 评论。Session 不参与控制字段生成。

首次 `SAT2_TASK_AUTHORIZED` 也应由 Dashboard 的“授权任务”操作生成。用户只确认任务，Relay 自动读取 task、PR、base/head、worker role、allowed/forbidden paths 和 human gates；用户不手写授权 YAML。

## 11. 发布前闸门

每次发布前至少校验：

```text
1. Decision JSON 可解析且只有允许字段
2. delivery_token 对应当前未完成 delivery
3. conversation 与 endpoint role 匹配
4. decision 与 actor role 匹配
5. 当前 Protocol State 允许该 decision
6. parent_event_id 等于本地账本 last_event_id
7. PR 仍 open，task/PR/base 未漂移
8. 发布前重新读取当前 PR head
9. Worker checkpoint candidate/control SHA 等于当前 head
10. Mentor review 的 reviewed SHA 等于被投递 checkpoint SHA
11. target role 等于固定路由
12. 同一 Decision 不存在 pending 或已发布 outbox
13. allowed/forbidden paths 与 task spec 一致
```

Mentor 审查期间 PR head 改变时，必须返回：

```text
STALE_PR_HEAD
```

并拒绝自动发布，要求 Mentor 重新读取最新 diff；不得把旧 head 的审查结论绑定到新 head。

## 12. 幂等、Outbox 与发布恢复

最小去重键必须绑定：

```text
task_id
+ delivery_id
+ assistant_message_hash
+ decision
+ current_head
```

稳定 event marker 至少由以下字段确定：

```text
delivery_id
+ assistant_message_hash
+ decision
```

同一 assistant message 被重复扫描、重复点击或 daemon 重启后，只能发布一条 GitHub 控制评论。

GitHub POST 超时或返回不确定时：

```text
PUBLISH_UNCERTAIN
→ 先按稳定 event marker 搜索 PR 全部评论
→ 已存在：绑定已有 comment_id，标记 PUBLISHED
→ 不存在：重试同一事件，不生成新 marker
```

Daemon 重启后必须恢复 pending outbox、pending delivery 和 publish-uncertain 状态，不要求 Session 重发 Decision。

## 13. 自动发布与人工门

Relay 2.2 可以在本地 `github.allow_writes=true` 且发布闸门通过后自动发布低风险控制评论：

```text
SAT2_WORKER_ACK
SAT2_WORKER_CHECKPOINT
SAT2_MENTOR_CHANGES_REQUIRED
SAT2_TASK_BLOCKED
```

以下仍需用户确认：

```text
首次 SAT2_TASK_AUTHORIZED
SAT2_MENTOR_ACCEPTED（默认一次本地确认）
任务范围或基线变化
```

Relay 永远不得自动执行或授权：

```text
merge / mark ready
base branch change / force push
workflow dispatch
qualification
formal experiment
registry or seed change
accepted-evidence change
paper claim or number change
```

控制评论自动发布不等于自动执行上述高风险动作。

## 14. 诊断要求

Dashboard/Deep Doctor 至少显示：

```text
Relay health and version
last poll and last poll error
task Protocol State
current PR head
last_event_id
expected next actor / decision
role endpoints and heartbeat
pending deliveries
pending outbox
last stable error code
recommended recovery action
```

稳定错误码至少包括：

```text
ENDPOINT_NOT_BOUND
ENDPOINT_STALE
ROLE_BINDING_CONFLICT
NO_ACTIVE_DELIVERY
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

错误信息必须指出故障层、原因和恢复动作，不能只显示 HTTP 500。

## 15. 现场验收

启用真实自动推进前，必须至少完成一次：

```text
Dashboard 人工授权
→ Worker Session 收到 Capsule
→ Worker 输出带 delivery_token 的 Decision JSON
→ Relay 自动发布唯一 checkpoint 评论
→ Mentor Session 自动收到 review Capsule
→ Mentor 输出 changes-required 或 accepted Decision
→ Relay 自动发布并路由下一角色
```

验收目标：

```text
0 手写 Relay YAML
0 手工复制 SHA
0 手工填写 parent_event_id
0 手工转发 Session 内容
0 重复 GitHub 控制评论
0 跨角色投递
Session 暂时离线后可恢复
daemon 重启后可恢复
自动检测失败时可按钮提交
```

完成现场闭环后可记录“真实闭环验收通过”；在此之前不得宣称长期稳定无人值守运行已经验证。

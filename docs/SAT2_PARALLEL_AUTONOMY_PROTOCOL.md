# SAT2 并行自主协作协议（Route-local Progress + Session Attestation）

状态: **IMPLEMENTATION-READY — FIELD ACCEPTANCE PENDING**  
日期: 2026-08-12  
适用实现分支: `agent/parallel-route-autonomy`  
范围: SAT2 双科研路线自主闭环（Route A 单星视觉 / Route B 单星 Agent）

---

## 1. 目标与非目标

### 1.1 目标形态

```text
Route A
S1 Mentor → task document → Relay → S2 Worker
    ↑                              ↓
    └──── review / next task ──────┘

Route B
S3 Mentor → task document → Relay → S4 Worker
    ↑                              ↓
    └──── review / next task ──────┘
```

两条路线满足：

1. 各自拥有独立 Mentor、Worker、科学 PR、控制 ref、progress document、task root 和运行状态。
2. A 不等待 B；B 不等待 A。
3. A 的 endpoint 离线、progress 错误、sequence gap、task blocked 不得暂停 B。
4. Mentor 自主决定下一科研任务；Relay 不选择科研方向、不生成科研任务、不修改论文证据。
5. Relay 只负责 observe / validate / bind / route / persist / deduplicate / recover。
6. 探索阶段独立，最终集成显式进行；两条自主路线不得同时竞争同一个科学 PR。

### 1.2 “互不知晓”的精确定义

同一 GitHub repository 下无法实现目录级信息不可见，因此本协议要求的是 **protocol independence**：

- Capsule 不注入另一条路线的信息；
- 路由、状态、序号、任务依赖和故障处理不依赖另一条路线；
- Relay 不做跨路线科研协调；
- 路线无需读取另一条路线即可完整推进。

本协议不声称 cryptographic / information-theoretic isolation。

---

## 2. 控制平面模型

### 2.1 Route 配置

`.sat2/relay.yml` 可新增 `routes`。旧 `monitors` 不配置 route 时保持 Relay 2.2.2 comment-driven 行为。

推荐双路线配置：

```yaml
routes:
  - route_id: vision
    mentor_role: S1
    worker_role: S2
    pr_number: <VISION_PR>
    progress_file: collaboration/routes/vision/progress.yaml
    progress_ref: relay/vision
    task_root: .sat2/routes/vision/tasks
    bootstrap_task_file: .sat2/routes/vision/tasks/VISION-01.yml
    signal_mode: progress_shadow   # 验证后切换为 progress

  - route_id: agent
    mentor_role: S3
    worker_role: S4
    pr_number: <AGENT_PR>
    progress_file: collaboration/routes/agent/progress.yaml
    progress_ref: relay/agent
    task_root: .sat2/routes/agent/tasks
    bootstrap_task_file: .sat2/routes/agent/tasks/AGENT-01.yml
    signal_mode: progress_shadow
```

### 2.2 强制隔离不变量

对 `signal_mode: progress` 的 enabled routes，Relay 配置校验必须拒绝：

- 两条路线复用同一个 Session role；
- 两条路线复用同一个 `progress_ref`；
- 两条路线复用同一个 scientific PR；
- 两条路线的 `task_root` 重叠；
- `mentor_role == worker_role`；
- active progress 使用 `@default` / `@pr-head` / `@pr-base` 作为控制 ref。

因此推荐：

```text
scientific PR A: research/vision → PR A
control ref A:   relay/vision

scientific PR B: research/agent  → PR B
control ref B:   relay/agent
```

**科学 SHA 与控制文档 SHA 必须分离。** Mentor 写 progress / next_task 不得改变正在审查的 scientific PR head。

---

## 3. Progress Document v2

### 3.1 语义

`handoff_sequence` 是 **Relay-relevant handoff 序号**，不是 Git commit 计数。

Worker 可以为一个任务产生多个普通 commit，但只有准备向 Mentor 交付 checkpoint 时才推进一次 handoff sequence。

### 3.2 Schema

初始化：

```yaml
schema: 2
route: vision
handoff_sequence: 0
parent_sequence: null
event_type: ROUTE_INIT
route_status: ACTIVE
stage: 0
updated_by: S1
updated_at: "2026-08-12T12:00:00Z"
current_task: null
task_id: null
next_task: null
pr_number: null
candidate_sha: null
reviewed_sha: null
control_head_sha: null
task_contract_sha256: null
last_summary: "route initialized"
```

Worker checkpoint：

```yaml
schema: 2
route: vision
handoff_sequence: 1
parent_sequence: 0
event_type: WORKER_CHECKPOINT
route_status: ACTIVE
stage: 0
updated_by: S2
updated_at: "2026-08-12T12:05:00Z"
current_task: .sat2/routes/vision/tasks/VISION-01.yml
task_id: VISION-01
next_task: null
pr_number: 101
candidate_sha: <CURRENT_SCIENTIFIC_PR_HEAD>
reviewed_sha: null
control_head_sha: <CURRENT_SCIENTIFIC_PR_HEAD>
task_contract_sha256: <FROZEN_TASK_SHA256>
last_summary: "candidate ready for mentor review"
```

Mentor accepted + next task：

```yaml
schema: 2
route: vision
handoff_sequence: 2
parent_sequence: 1
event_type: MENTOR_ACCEPTED
route_status: ACTIVE
stage: 1
updated_by: S1
updated_at: "2026-08-12T12:10:00Z"
current_task: .sat2/routes/vision/tasks/VISION-01.yml
task_id: VISION-01
next_task: .sat2/routes/vision/tasks/VISION-02.yml
pr_number: 101
candidate_sha: null
reviewed_sha: <CHECKPOINT_SHA>
control_head_sha: <CURRENT_SCIENTIFIC_PR_HEAD>
task_contract_sha256: <FROZEN_TASK_SHA256>
last_summary: "accepted; start VISION-02"
```

路线终态：

```yaml
event_type: MENTOR_ACCEPTED
route_status: COMPLETE
next_task: null
```

### 3.3 合法事件

| `event_type` | 合法角色 | Relay 行为 |
|---|---|---|
| `ROUTE_INIT` | route Mentor/Worker | 仅 seq=0；建立基线 |
| `WORKER_CHECKPOINT` | Worker | 校验 candidate SHA → 投递本路线 Mentor |
| `MENTOR_CHANGES_REQUIRED` | Mentor | 校验 reviewed SHA → 回本路线 Worker |
| `MENTOR_ACCEPTED` | Mentor | 当前 task COMPLETE；next_task 自动启动或 route COMPLETE |
| `TASK_BLOCKED` | Mentor/Worker | 当前路线 fail-closed；不影响其他路线 |

禁止从 `last_summary` 自然语言推断控制动作。

---

## 4. 身份、因果与授权

### 4.1 Progress 不是身份凭证

`updated_by: S1` 是状态声明，不足以证明真正写入者是 S1。

因此 active progress 模式使用 **双因子式控制事实**：

```text
GitHub progress document
  = route state + exact task/SHA/sequence facts

Session-bound SAT2_RELAY_DECISION
  = actor identity attestation
```

现有 extension / daemon 必须继续校验：

- installation_id；
- role；
- conversation_key；
- delivery_id；
- delivery_token；
- assistant_message_id/hash；
- delivery target；
- current Protocol State。

只有 progress 与该 Session Decision 完全一致时，Relay 才接受 handoff。

### 4.2 科研因果绑定

每个非初始化 handoff 必须绑定：

```text
route_id
handoff_sequence / parent_sequence
event_type
task_id / current_task
pr_number
control_head_sha
task_contract_sha256
candidate_sha 或 reviewed_sha（按事件类型）
```

Worker checkpoint：

```text
candidate_sha == control_head_sha == freshly-read scientific PR head
```

Mentor review：

```text
reviewed_sha == checkpoint candidate_sha == freshly-read scientific PR head
```

不一致：`STALE_PR_HEAD`，fail closed。

### 4.3 Mentor next_task 即科研授权

合法 `MENTOR_ACCEPTED` 中的 `next_task` 本身就是 Mentor 的下一阶段授权，**不增加机械二次人工确认**，前提是同时满足：

1. Decision 来自绑定的 route Mentor Session；
2. reviewed SHA 未漂移；
3. next_task 位于该 route 的 `task_root`；
4. task YAML 可解析并通过 `validate_task_spec_ready`；
5. route / worker / mentor / PR 绑定一致；
6. task contract 的 `human_gates` 未被绕过。

Relay 不得自行创建 next_task 内容。

---

## 5. Sequence、幂等与故障恢复

### 5.1 严格单调规则

```text
new_sequence == processed_sequence + 1
parent_sequence == processed_sequence
```

处理规则：

| 情况 | 行为 |
|---|---|
| `seq == processed` | 已处理/重复；不得重复投递 |
| `seq == processed + 1` | 等待匹配 Session attestation，然后原子接受 |
| `seq > processed + 1` | `PROGRESS_SEQUENCE_GAP`，本路线 fail closed |
| `seq < processed` | `PROGRESS_SEQUENCE_ROLLBACK`，本路线 fail closed |

不再采用“跳号接受 + 告警”。

### 5.2 Crash-safe 接受

Progress 模式复用现有 SQLite `events + task_state + deliveries` 原子事务：

```text
validate progress + Session Decision
→ accept_event()
   BEGIN IMMEDIATE
   insert event
   update task_state
   create next delivery
   COMMIT
→ mark progress sequence processed
```

若 daemon 在事务提交后、`processed_sequence` 更新前崩溃：

- 重启时通过 deterministic progress event id 发现 event 已存在；
- 不重复创建 delivery；
- 补齐 sequence checkpoint；
- 若是 `MENTOR_ACCEPTED`，确保 next_task root 已 exactly-once 创建后再推进。

### 5.3 Runtime Monitor

`next_task` 不修改全局 `relay.yml`。

Relay 从 route manifest 动态构造 `RepoMonitor`，并把运行时 monitor 持久化到 SQLite meta：

```text
runtime_monitor:<task_id>
```

运行时 monitor 继承：

```text
route_id
mentor_role
worker_role
scientific PR
progress file/ref
required apps
task file/ref
exact allowed/forbidden paths
dependencies
```

因此 `refresh_config()` 不会抹掉正在执行的 next task。

---

## 6. Signal Mode 与迁移

支持三种模式：

### `comment`

Relay 2.2.2 旧模式。GitHub control comments 是 dispatch authority。

### `progress_shadow`

Progress 只读取、校验和记录，不产生 route delivery。旧 comment 路径仍是唯一 ACTIVE authority。

用于迁移验证；**禁止 comment 与 progress 同时真实 dispatch**。

### `progress`

Progress + Session Decision 成为该路线唯一 handoff authority；不再需要每个 handoff 生成 GitHub control comment。SQLite 保留本地审计/幂等记录。

推荐迁移：

```text
Phase 1: comment ACTIVE + progress_shadow
→ 完整跑两轮 Mentor↔Worker 闭环
→ 对照 sequence / task / SHA / target

Phase 2: route signal_mode = progress
→ comment 降为历史审计，不再 dispatch

Phase 3: 双路线 progress ACTIVE
→ 做故障注入 + 24h field acceptance
```

切换是 per-route 的，不要求 A/B 同时迁移。

---

## 7. 路线隔离不变量

正式验收必须证明：

1. Route A Worker event 永远不能生成 Route B target。
2. Route B Worker event 永远不能生成 Route A target。
3. Route A endpoint offline 不影响 Route B poll/delivery。
4. Route A malformed progress 不把全局 Relay 置为 PAUSED。
5. Route A sequence gap 不影响 Route B bootstrap/advance。
6. Route A `TASK_BLOCKED` 不修改 Route B task state。
7. 两路线不能共享 active scientific PR。
8. 两路线不能共享 active progress ref。
9. 两路线不能共享 Session role。
10. duplicate handoff 产生 0 duplicate deliveries。
11. old/delayed Session reply 不能作用到新 task delivery token。
12. task contract 在执行中改变必须继续 `TASK_SPEC_CHANGED_DURING_EXECUTION` fail closed。
13. Mentor review SHA 发生漂移必须继续 `STALE_PR_HEAD` fail closed。
14. `next_task` 只能消费一次。
15. daemon restart 后各路线独立恢复自己的 sequence/runtime monitor。

---

## 8. Capsule 合同

Route monitor 的 Guided Execution Capsule 必须显式包含：

```text
route_id
mentor_role / worker_role
progress_file / progress_ref
signal_mode
task file/ref + frozen task SHA-256
scientific PR + bound SHA
allowed / forbidden paths
human gates
合法 Decision 集合
```

并要求 Session 在最终 `SAT2_RELAY_DECISION` 前完成对应 progress handoff。

Session 不得自行填写 Relay event id、parent event id、target role 或 transport SHA；这些仍由 daemon 生成/校验。

---

## 9. 已定案问题（原开放问题）

1. **序号粒度**：每次 Relay-relevant handoff +1，不是每次 Git commit +1。
2. **next_task 自动推进**：允许；合法 Mentor next_task 即授权，不增加二次人工闸门。
3. **并存期**：使用 `progress_shadow`，始终只有一个 ACTIVE dispatch authority；建议至少两轮完整闭环后切换。
4. **序号跳变**：拒绝；只有未来实现可验证的历史 replay 才能恢复，不能直接吞 gap。
5. **Session 不遵守 Progress Contract**：当前路线 fail closed / 告警；其他路线继续。
6. **科研授权原则**：不冲突；科研决策仍来自 Mentor，Relay 只验证并执行确定性路由。

---

## 10. 实施状态与现场验收

### 10.1 本分支实现范围

`agent/parallel-route-autonomy` 实现：

- route-aware Mentor/Worker model；
- `RepoRoute` / route isolation config validation；
- Progress Document v2 parser/validator；
- route-local target resolution；
- Session Decision ↔ progress attestation；
- strict handoff sequence；
- dynamic next-task runtime monitor；
- deterministic root / exactly-once recovery；
- per-route error isolation；
- legacy comment-mode backward compatibility；
- daemon integration tests。

### 10.2 尚不得宣称

在本地真实四 Session 验收完成前，不得宣称：

- unattended production reliability；
- 24h 稳定性；
- Chrome/Edge + real ChatGPT 四 Session 完整 field acceptance；
- Windows supervisor/network interruption 下已证明无重复/丢 handoff。

### 10.3 本地验收门槛

至少完成：

```text
S1 Route-A Mentor
S2 Route-A Worker
S3 Route-B Mentor
S4 Route-B Worker
```

并通过：

- 单路线连续 3 task 自动推进；
- 双路线同时闭环；
- endpoint offline 故障注入；
- malformed progress；
- sequence gap / rollback；
- stale scientific PR head；
- daemon restart at handoff；
- next_task exactly-once；
- delayed old Session reply；
- 一路线 BLOCKED、另一条继续；
- 24h 无异常 delivery/outbox/session 堆积。

全部通过后，状态才能从 `FIELD ACCEPTANCE PENDING` 升级为正式可用。

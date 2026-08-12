# SAT2 Parallel Autonomy — Local Field Test

状态: FIELD ACCEPTANCE CHECKLIST  
目标分支: `agent/parallel-route-autonomy`

本清单用于 Windows + Chrome/Edge + 4 个真实 ChatGPT Session 的现场验收。不要在通过本清单前把 parallel autonomy 标记为长期稳定。

## 1. 代码回归

在 Autoai 本地 checkout：

```bash
git fetch origin
git checkout agent/parallel-route-autonomy
cd daemon
python -m pip install -e ".[test]"
pytest -q
```

要求：旧 Relay 2.2.2 tests 与 `test_parallel_autonomy.py` 全部通过。

## 2. SAT2 目标仓库准备

为两条路线准备独立 scientific PR 与独立 control ref：

```text
Route vision:
  Mentor = S1
  Worker = S2
  scientific PR = <VISION_PR>
  control ref = relay/vision

Route agent:
  Mentor = S3
  Worker = S4
  scientific PR = <AGENT_PR>
  control ref = relay/agent
```

禁止两路线共用 scientific PR、control ref、Session role 或 task root。

在 `.sat2/relay.yml` 中先配置 `signal_mode: progress_shadow`：

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
    signal_mode: progress_shadow

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

两份 progress 初始化为 schema 2 / sequence 0 / `ROUTE_INIT`。

## 3. Session 绑定

在扩展中绑定四个不同 ChatGPT conversation：

```text
S1 → Route vision Mentor
S2 → Route vision Worker
S3 → Route agent Mentor
S4 → Route agent Worker
```

检查：

- 四个 conversation_key 不重复；
- heartbeat 中四个 role 都 fresh；
- 不使用 legacy `mentor` endpoint 承担两条路线；
- extension 不允许同一 conversation 重复绑定多个 role。

## 4. Shadow 验证

保持旧 comment path 为 ACTIVE、route progress 为 `progress_shadow`，至少完成两轮：

```text
Worker checkpoint
→ Mentor review
→ changes-required 或 accepted
→ 下一轮 Worker
```

逐项核对 progress 与旧 control chain：

- event type 一致；
- task_id 一致；
- scientific PR head 一致；
- candidate/reviewed SHA 一致；
- task contract SHA-256 一致；
- route target 一致；
- handoff sequence 无 gap/rollback。

Shadow 阶段不得产生第二套真实 progress dispatch。

## 5. 切换 active progress

逐路线切换，不要求同时切换：

```yaml
signal_mode: progress
```

切换后正常 handoff 不应再依赖 GitHub control comment；Session Decision + progress document 为 route authority。

预期主链：

```text
S2 WORKER_CHECKPOINT
→ progress seq +1
→ S1 review Capsule

S1 MENTOR_CHANGES_REQUIRED
→ progress seq +1
→ S2 repair Capsule

或

S1 MENTOR_ACCEPTED + next_task
→ current task COMPLETE
→ next task exactly-once dispatch S2
```

Agent 路线同理：S4 ↔ S3。

## 6. 强制故障注入

每项都要记录 daemon 状态、alert、delivery/outbox 数量与受影响 route。

### A. Endpoint isolation

关闭 S2 tab 或解绑 S2。

期望：vision 等待；agent S3/S4 继续。

### B. Malformed progress

让 vision progress YAML 暂时不可解析。

期望：vision `PROGRESS_INVALID`；agent 继续；Relay 全局不 PAUSED。

### C. Sequence gap

把 vision 从 seq N 直接写成 N+2。

期望：`PROGRESS_SEQUENCE_GAP`；不得接受或跳过；agent 继续。

### D. Sequence rollback

把 seq 写回 N-1。

期望：`PROGRESS_SEQUENCE_ROLLBACK`；不得重新派发旧任务。

### E. Wrong identity

让非本路线 role 的 Session 尝试提交当前 delivery token/Decision。

期望：role/delivery mismatch fail closed。

### F. Stale scientific head

Worker checkpoint 后，在 Mentor 完成审查前改变 scientific PR head。

期望：`STALE_PR_HEAD`；旧 Mentor 结论不得作用到新 head。

### G. Task contract drift

任务执行中静默修改 task YAML 的 acceptance。

期望：`TASK_SPEC_CHANGED_DURING_EXECUTION` / contract mismatch；必须 rebaseline/new task。

### H. Daemon restart

分别在以下窗口重启：

1. progress 已更新、Decision 尚未提交；
2. event/delivery 已写 SQLite、processed sequence 尚未确认；
3. Mentor accepted 后、next-task root 创建前后。

期望：不丢 handoff、不重复 delivery、next_task exactly once。

### I. Delayed old reply

完成 task N 后，让旧 Session 延迟产生针对旧 delivery token 的回复。

期望：不能污染 task N+1。

### J. Route blocked isolation

vision 提交 `TASK_BLOCKED` / `route_status: BLOCKED`。

期望：vision fail closed；agent 正常推进。

## 7. 连续运行验收

通过上述故障注入后，再进行 24h unattended run。

要求：

- 两路线各完成至少 3 个连续 task handoff；
- 无 duplicate delivery；
- 无 sequence gap/rollback；
- 无 cross-route target；
- 无 stale endpoint 导致另一条路线停摆；
- 无无限 retry / outbox 堆积；
- daemon restart 后 runtime monitor 与 processed sequence 恢复正确；
- extension 无明显 Session 泄漏或错误绑定；
- human gates 未被自动推进绕过。

## 8. 最终判定

只有以下三层均通过才能升级状态：

```text
SOURCE TESTS PASSED
→ REAL 4-SESSION CLOSED LOOP PASSED
→ 24H UNATTENDED + FAILURE INJECTION PASSED
```

任何一层失败，保留 `FIELD ACCEPTANCE PENDING`，不要合并为正式稳定版本。

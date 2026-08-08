# Relay 2.2.2 operating protocol

本文件是 [`SAT2_CHAT_RELAY_PROTOCOL.md`](SAT2_CHAT_RELAY_PROTOCOL.md) 的本地运行补充。SAT2 当前 Mentor task YAML、PR 和 exact SHA 是任务事实源。

## 1. 当前模型

正常路径已经改为：

```text
Mentor 写完整 task 文档
→ Relay 自动校验并 dispatch
→ Worker 按文档完成
→ Worker checkpoint 自动回 Mentor
→ Mentor 按同一 task contract 验收
→ changes-required 自动回 Worker，accepted 自动完成
→ 下一份依赖满足的 task 文档自动启动
```

不再需要 Dashboard“首次任务授权”，也不再要求 `MENTOR_ACCEPTED` 的机械二次确认。高风险动作仍只受 task 文档 `human_gates` 与 SAT2 科研治理约束。

当前状态：

```text
on-demand Windows installation: IMPLEMENTED
unique role/session binding: IMPLEMENTED
Mentor task-contract validation: IMPLEMENTED IN SOURCE
automatic document dispatch: IMPLEMENTED IN SOURCE
Worker → Mentor checkpoint relay: IMPLEMENTED IN SOURCE
Mentor → Worker changes relay: IMPLEMENTED IN SOURCE
Mentor accepted → COMPLETE: IMPLEMENTED IN SOURCE
bound Session reply notification: IMPLEMENTED IN SOURCE
real Windows/ChatGPT closed-loop acceptance: PENDING FIELD ACCEPTANCE
long-term unattended stability: NOT YET CLAIMED
```

## 2. 日常开始协作

Windows 登录时 Relay 不常驻。第一次安装/扩展 ID 改变后注册一次 Native Messaging host；以后日常：

```text
打开已绑定的 Mentor / Worker ChatGPT Session
→ 打开 SAT2 Relay 插件
→ 点击“一键启动协作”
```

按钮执行：

```text
检查 127.0.0.1:8765
→ 必要时 Native host ensure_running
→ 等待 daemon healthy
→ 自动推进 ON
→ heartbeat
→ poll
→ 自动恢复 pending outbox / delivery
→ 自动扫描 enabled task documents
```

文档完整且可执行时直接 dispatch，不再等待用户“授权任务”。

## 3. Mentor task 文档开工条件

Relay 开工前至少机器校验：

```text
task_id/title/status
repository/pr_number/worker_role
purpose 或 objective
acceptance criteria 非空
allowed_paths
forbidden_paths 字段
human_gates 字段
PR open
base/branch 一致
依赖满足
无 active write-scope conflict
```

第一次 dispatch 冻结 task document SHA-256。后续 checkpoint/review 必须仍对应同一合同；中途修改验收标准会 fail closed。

## 4. Worker / Mentor Session 输出

Worker 正常完成时：

```json
{"delivery_token":"当前 Capsule token","decision":"WORKER_CHECKPOINT","summary":"按任务合同完成当前候选，等待 Mentor 验收。"}
```

Mentor 未通过：

```json
{"delivery_token":"当前 Capsule token","decision":"MENTOR_CHANGES_REQUIRED","summary":"列出仍未满足的验收项。"}
```

Mentor 通过：

```json
{"delivery_token":"当前 Capsule token","decision":"MENTOR_ACCEPTED","summary":"当前 exact head 满足任务合同的全部适用验收标准。"}
```

Session 不写 Relay YAML、SHA、parent、actor、target 或 event ID。

## 5. 双向传递保障

Mentor → Worker：GitHub event 固定路由到 `task.worker_role`；delivery 只能由对应 fresh role endpoint lease；注入后必须在具体 conversation transcript 看到精确 delivery marker 才算 DELIVERED。

Worker → Mentor：Decision 必须同时匹配 delivery token、绑定 conversation、endpoint role 和当前 delivery；Relay 发布前重新读取 current PR head，自动生成 checkpoint SHA/parent/target；checkpoint GitHub event 固定路由到 Mentor。

没有目标 endpoint 时等待，不跨角色投递，也不把 endpoint 离线当科学失败。

## 6. 去重与恢复

同一：

```text
task_id + delivery_id + assistant_message_hash + decision + current_head
```

只允许一条 outbox/control comment。GitHub POST 不确定时先按 stable marker 搜索已有评论，再决定是否重试。Daemon 重启恢复 pending outbox/delivery。

## 7. STALE_PR_HEAD 与合同漂移

Worker checkpoint：candidate/control SHA 必须等于发布前 freshly-read PR head。

Mentor review：reviewed SHA、checkpoint candidate SHA、fresh PR head 必须相同。

不一致返回 `STALE_PR_HEAD`。

执行过程中 task 文档 hash 改变返回 `TASK_SPEC_CHANGED_DURING_EXECUTION`；不得静默换验收口径。

## 8. 绑定 Session 回复完成提醒

插件新增独立开关：

```text
绑定 Session 回复完成提醒
```

该功能不依赖“自动推进”。关闭自动推进后仍可使用。

行为：

```text
绑定的 ChatGPT Session 完成 assistant 回复
→ watcher 等待生成停止且文本稳定
→ 独立 extension Port 通知 background
→ background 核对 conversation 确实绑定某个 role
→ 同一回复去重
→ Chrome / Edge 弹出“SAT2 <role> 回复完成”
→ 点击通知回到对应标签页
```

未绑定 Session 不通知；页面首次加载不会为旧回复补发；此提醒不会生成 GitHub 事件，也不会推进 Protocol State。

## 9. Human gates

Relay 自动推进不等于自动执行：

```text
merge / mark ready
workflow dispatch
qualification / formal experiment
registry / seed / accepted-evidence change
paper claim / number change
force push / base retarget / scope expansion
```

这些仍以 task 文档和 SAT2 科研治理为准。

## 10. 现场验收

至少现场跑通一次：

```text
Relay 完全停止
→ 插件一键启动
→ 自动发现完整 Mentor task 文档
→ Worker 收到 Capsule
→ Worker checkpoint 自动到 GitHub
→ Mentor 收到 Capsule
→ Mentor changes-required 自动回 Worker
→ Worker 第二 checkpoint 自动回 Mentor
→ Mentor accepted 自动 COMPLETE
```

并验证：

```text
0 手写 YAML/SHA/parent/target
0 跨角色投递
0 重复评论
错误 token 被拒绝
endpoint 离线可恢复
daemon 重启可恢复
STALE_PR_HEAD fail closed
task contract 漂移 fail closed
自动推进 OFF 时回复完成提醒仍弹出
```

在这条真实 Windows + ChatGPT 闭环完成前，不宣称长期稳定无人值守已验证。

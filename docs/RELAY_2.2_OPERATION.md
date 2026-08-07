# Relay 2.2 operating protocol

本文件是 [`SAT2_CHAT_RELAY_PROTOCOL.md`](SAT2_CHAT_RELAY_PROTOCOL.md) 的本地运行补充。发生冲突时，以该协议、SAT2 当前任务 YAML、PR 和精确 SHA 为准。旧版 Relay 2.0 文档只作历史记录。

## 1. 当前能力与验收状态

```text
on-demand Windows installation: IMPLEMENTED
unique role/session binding: IMPLEMENTED
GitHub inbound event → Session Capsule: IMPLEMENTED
Session Decision JSON → deterministic GitHub comment: IMPLEMENTED
bidirectional role routing: IMPLEMENTED
extension one-click local bootstrap: IMPLEMENTED IN SOURCE / FIELD ACCEPTANCE PENDING
real Session closed-loop acceptance: PENDING FIELD ACCEPTANCE
long-term unattended stability: NOT YET CLAIMED
```

当前可以写“已实现、待真实闭环验收”，不能写“长期稳定无人值守已验证”。

## 2. 日常启动与停止

Windows 登录时不启动 Relay，不注册登录计划任务，不监听 8765。

### 2.1 首选：扩展一键启动协作

一次性安装/更新后，先把当前 Chromium extension ID 注册给本机 Native Messaging host：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$env:LOCALAPPDATA\SAT2Relay\on-demand\REGISTER_NATIVE_HOST.ps1" `
  -ExtensionId <popup 显示的 32 位 Extension ID>
```

注册完成后，日常只需打开扩展并点击：

```text
一键启动协作
```

该按钮按固定顺序执行：

```text
1. 检查 127.0.0.1:8765 Relay 是否在线
2. 若离线，调用受限 Native Messaging host 的 ensure_running
3. Native host 只调用固定 START_OR_REPAIR.ps1 -SkipPoll
4. 等待 daemon health 可用
5. 打开扩展“自动推进”
6. 执行一次 heartbeat / poll / delivery cycle
7. 返回 Doctor 与启动诊断结果
```

Native host 只允许 `status` 和 `ensure_running`，不能执行任意命令，也不读取 GitHub PAT。

### 2.2 桌面故障后备

仍保留：

```text
SAT2 Relay - Start or Repair
SAT2 Relay - Stop
```

停止后应满足：

```text
Relay processes: NONE
127.0.0.1:8765 listener: NONE
login scheduled task: NONE
```

Native Messaging helper 本身不是常驻服务；浏览器发起一次请求时启动，完成请求后退出。

## 3. Session 绑定

- 只绑定具体 `https://chatgpt.com/c/<conversation-id>` 页面；
- 不绑定项目主页、新建聊天页或不确定 URL；
- 一个会话默认只绑定一个角色；
- 一个角色默认只保留一个 active endpoint；
- Mentor、Worker 等角色必须分别完成 GitHub 能力可读性验证；
- Deep Doctor 必须显示无重复绑定、无角色歧义和新鲜 heartbeat。

目标 endpoint 未绑定、标签页关闭或 heartbeat 过期时，delivery 保持：

```text
WAITING_FOR_ENDPOINT
```

它不会仅因 endpoint 暂时不可用而成为永久失败。

## 4. Session Decision

收到 Relay Capsule 后，Session 完成人类可读报告，并在末尾输出：

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

Session 不得手写 Relay YAML，也不得填写 task、PR、SHA、parent、actor、target、event ID 或 timestamp。

扩展自动检测最新完整 assistant message。自动检测失败时，使用“提交当前 Decision”按钮；该按钮走同一 token、角色、状态、SHA、去重和 outbox 路径，不需要复制内容。

## 5. GitHub 写权限

新安装默认：

```text
github.allow_writes: false
```

只有在以下条件通过后才启用：

```text
Deep Doctor passed
Mentor endpoint present
Worker endpoint present
no duplicate role/session binding
private repository read passed
current task/config read passed
```

启用写权限只允许 Relay 发布通过本地闸门的低风险控制评论。它不授权 merge、workflow dispatch、qualification、formal experiment、registry/seed/evidence 或论文变更。

首次任务授权通过 Dashboard 的“授权任务”按钮完成；Relay 自动读取 task、PR 和当前 head。`MENTOR_ACCEPTED` 默认需要一次本地人工确认。

## 6. 发布恢复与去重

同一 Decision 的去重至少绑定：

```text
task_id + delivery_id + assistant_message_hash + decision + current_head
```

GitHub POST 状态不确定时进入 `PUBLISH_UNCERTAIN`，Relay 先按稳定 event marker 搜索现有评论，确认不存在后才重试。Daemon 重启后恢复 pending outbox 和 delivery，不要求 Session 重发 Decision。

## 7. STALE_PR_HEAD

每次发布前 Relay 必须重新读取 PR 当前 head：

- Worker checkpoint 的 candidate/control SHA 必须等于当前 head；
- Mentor review 的 reviewed SHA 必须等于被投递 checkpoint SHA；
- Mentor 审查期间 head 变化时返回 `STALE_PR_HEAD` 并拒绝发布。

恢复动作是 Mentor 重新读取最新 diff 和新的 review Capsule，不是修改旧 Decision 或手写 YAML。

## 8. 现场闭环验收

正式依赖自动推进前，至少完成一次：

```text
扩展点击“一键启动协作”
→ daemon 从完全停止状态成功启动
→ Dashboard/Doctor healthy
→ Dashboard 授权
→ Worker 收到 Capsule
→ Worker Decision 自动发布 checkpoint
→ Mentor 自动收到 review Capsule
→ Mentor Decision 自动发布并回到 Worker
```

验收必须确认：

```text
no handwritten YAML
no manual SHA / parent copy
no duplicate GitHub comment
no cross-role delivery
endpoint offline recovery works
daemon restart recovery works
manual Decision button works
one-click bootstrap works after full daemon stop
```

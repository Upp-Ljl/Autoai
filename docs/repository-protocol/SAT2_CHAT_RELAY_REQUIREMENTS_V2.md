# SAT2 Relay 2.0 最终需求与验收规范

## 1. 目标状态

SAT2 Relay 的主控制面是浏览器扩展。扩展负责：

- 绑定 Mentor、S1、S2、S3、S4 的既有 ChatGPT 网页 Session；
- 自动心跳、Session 健康检查、任务投递、去重和投递确认；
- 在 Worker 忙碌、页面重载、浏览器重启、Session 暂时不可用时自动恢复；
- 通过清晰 UI 显示当前任务、队列、故障阶段和恢复动作。

本地 Control Center 是轻量辅助进程。它只负责：

- 使用本地凭据读取 GitHub 控制事件和任务规范；
- 校验协议、角色、PR、SHA、依赖、路径范围和状态机；
- 用 SQLite 保存事件、任务、投递、重试、心跳和警报；
- 向扩展提供本机回环 API；
- 通过 supervisor 自动重启；
- 提供可选的只限 Relay 运维能力的 MCP stdio server。

本地进程不得代替 Mentor/Worker 作科研判断，不得自行 merge、dispatch workflow、执行正式实验或修改论文证据。

## 2. 不可违反的控制原则

1. GitHub 控制评论、任务 YAML 和精确 SHA 是权威状态。
2. 每个任务只能由配置指定的 Worker Session 执行。
3. v2 事件通过 `parent_event_id` 形成可验证因果链。
4. 任务规范默认从 Relay 配置所在 ref 读取，不再错误地假设任务 YAML 必须存在于 Worker PR head。
5. 任何任务文件读取失败必须保留为可重试状态；修复路径/ref/凭据后，同一授权评论自动重放，不要求发布新评论。
6. 浏览器投递只有在 transcript 中检测到完整 `Relay delivery:` marker 后才算成功。
7. 所有重试均为短周期、有上限的固定阶梯，不使用无限指数退避。
8. 任何 token 都不得进入扩展、ChatGPT 消息、GitHub 评论或普通日志。

## 3. 任务规范定位

每个 monitor 必须声明：

```yaml
task_file: .sat2/tasks/P0-B-WP-B.yml
task_ref: "@config"
```

支持的 `task_ref`：

- `@config`：Relay 配置文件所在 ref，默认且推荐；
- `@default`：仓库默认分支；
- `@pr-head`：Worker PR 当前 head；
- `@pr-base`：Worker PR 当前 base；
- 任意显式 branch/tag/SHA。

`doctor --json` 必须显示每个 task 的最终 path、ref 和 SHA-256。

## 4. 凭据要求

- Windows：使用当前用户 DPAPI 加密凭据仓库；
- Linux：使用 mode 0600 本地文件；
- 本地凭据仓库优先于环境变量，避免计划任务继承旧环境变量；
- 主 GitHub token 至少需要 Metadata/Contents 读取权限；
- 需要本地辅助写 GitHub 评论时，额外提供 Issues/Pull requests 写权限；
- 扩展只保存本地 API token，不保存 GitHub PAT。

## 5. 可用性要求

- Windows 一键安装；
- 安装后计划任务运行 `supervise`，子进程崩溃后 3 秒固定重启；
- daemon 只监听 `127.0.0.1`；
- 自动轮询默认 15 秒；浏览器扩展默认 30 秒 heartbeat，并可立即手动运行；
- 浏览器重启后自动恢复绑定；
- 绑定同一 Session 到多个角色时明确警告；
- 不要求每条消息重新手动附加 `@GitHub`，默认采用 Session 已验证模式；
- 可切换到尽力附加或严格附加模式。

## 6. 可观测性要求

每个错误至少包含：

- stage；
- repository / PR / task；
- path / ref；
- HTTP status 和 GitHub request ID；
- token 来源和不可逆 fingerprint；
- comment/event/delivery ID；
- 是否可重试；
- 推荐恢复动作。

Control Center 必须展示：

- 当前 mode、最近 poll、最近 poll error；
- 任务状态；
- delivery 状态和尝试次数；
- Session heartbeat 和角色绑定；
- 未解决警报；
- 最近评论处理 outcome、retry count 和 last error；
- 深度 doctor 结果。

## 7. 状态机

```text
SAT2_TASK_AUTHORIZED
  → DISPATCHED
SAT2_WORKER_ACK
  → WORKING
SAT2_WORKER_CHECKPOINT
  → MENTOR_REVIEW
SAT2_MENTOR_CHANGES_REQUIRED
  → DISPATCHED
SAT2_MENTOR_ACCEPTED
  → ACCEPTED
```

v2 的 ACK、checkpoint 和 Mentor review 必须通过 `parent_event_id` 绑定前一事件。

## 8. 本地 Agent / MCP 边界

可选 MCP server 只暴露：

- relay_status；
- relay_doctor；
- relay_poll_once；
- relay_reload_credentials；
- relay_replay_comment；
- relay_resolve_alert。

不暴露任意 shell、任意文件读写、token 读取、merge、workflow dispatch 或实验执行工具。

## 9. 验收标准

最终版本必须通过：

1. Python unit/integration tests；
2. v1 配置和 SQLite 自动迁移测试；
3. task ref 回归测试；
4. 同一评论在 task file 404 修复后自动重放测试；
5. v2 parent-event 因果校验；
6. extension JavaScript syntax；
7. Chromium extension packaging；
8. 模拟 ChatGPT composer 真实 marker 确认；
9. MCP initialize/tools/list/tools/call 测试；
10. wheel build/import/install smoke test。

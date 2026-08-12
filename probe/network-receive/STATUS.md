# 3A Network-First Receive — Status

**Status: NETWORK-FIRST RECEIVE MECHANISM VALIDATED → PRODUCTION INTEGRATION PENDING**

Decision (2026-08-12): 暂停 3A 主动测试，回到 parallel autonomy 主线。
本分支与 Probe 保留，不删除、不合并进 PR #1。

## 已验证（真实 ChatGPT 流量）

| 机制 | 结果 |
|---|---|
| `Network.getResponseBody` 读取已完成 SSE 响应 | ✅ 可行（11+ turns，0 body errors） |
| SSE 语义终止信号 `message_stream_complete` | ✅ 可作为 completion 判据（不能单独用 loadingFinished） |
| conversation / message attribution | ✅ POST body conversation_id + `message_marker` message_id + 页面 URL `/c/<id>` 三重一致 |
| error path（`v = {error, error_code}` 事件） | ✅ network-first 可检出；出错后仍会发 stream_complete → 必须显式检测 error |
| 短回复内容载体 | ✅ 内容增量在无 type 事件的 `v` 字段（`c` 为数字索引） |
| WS 拓扑 | ✅ per-tab socket；内容走 HTTP SSE，天然按 tab/requestId 隔离 |

## 待集成验证（DEFERRED → 借真实 Capsule 自然完成）

- transport vs DOM 的 `text_hash` 一致性
- `SAT2_RELAY_DECISION` parser 在真实 Capsule 上的一致性
- 完整 transport-vs-DOM 双轨 shadow（约定：真实 Capsule 连续多次完全一致后，Transport 从 SHADOW 升 ACTIVE）

## 生产集成方式（预定）

```text
真实 Capsule
→ 现有路径正常工作（DOM ACTIVE）
→ Transport receiver 同时旁路观察 SSE（shadow）
→ 比较 message_id / terminal / text_hash / Decision
→ 连续多次完全一致 → Transport ACTIVE / DOM SHADOW
```

## 使用说明

- 扩展：`extension/`（附着选中标签页、清空记录、导出）
- 分析：`tools/compare3a.py <sat3a-shadow-*.jsonl>`（输出 phase3a-summary.json + 报告）
- 只读 CDP（Network.enable + getResponseBody 于已完成请求）；不拦截/修改/重放；不持久化正文/凭证

## 边界

- 内存问题（4 长会话页面渲染）**未解决**：3A 只证明"接收可去 DOM"；发送侧 runtime minimization 为 Phase 3B，暂停，不阻塞科研主线。

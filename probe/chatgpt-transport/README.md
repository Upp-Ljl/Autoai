# SAT2 ChatGPT Web Transport Probe (read-only)

## 目的

在**专门的 ChatGPT 测试会话**上，以只读方式采集 ChatGPT 网页版消息传输的**元数据**，
用于调研"会话间自动推进"所需的传输机制（请求结构、WebSocket 流、payload schema）。

**绝不**：拦截/修改/重放请求；读取或存储 Cookie/Authorization/session token 的值；
存储任何 payload 内容；不实现私有 API client。

## 结构

```text
probe/chatgpt-transport/
├── extension/            MV3 Probe 扩展（chrome.debugger + CDP Network）
│   ├── manifest.json
│   ├── background.js     CDP 采集与脱敏逻辑
│   ├── popup.html       控制界面
│   └── popup.js
└── tools/
    └── summary.py        JSONL 汇总脚本
```

## 使用步骤

1. Chrome → `chrome://extensions` → 开发者模式 → "加载已解压的扩展程序" →
   选择 `probe/chatgpt-transport/extension`。
2. 打开**专门的测试 ChatGPT 会话**（不要用 S1–S4 科研会话）。
3. 点扩展图标 → 「附着到当前标签页」（会弹出调试器提示，确认即可）。
4. 点「标记: 普通文本消息」→ 在会话里发送一条普通文本消息 → 等回复结束。
5. 点「标记: @GitHub 消息」→ 在会话里发送一条 `@GitHub` 消息 → 等回复结束。
6. 点「停止并导出 JSONL」→ 保存生成的文件。
7. 本地运行汇总：

```powershell
python probe/chatgpt-transport/tools/summary.py <下载的.jsonl>
```

## 采集内容（全部脱敏）

| 类别 | 采集 | 不采集 |
|---|---|---|
| 请求 | method / URL path / query 参数名 / resource type / header **名** / status / mime / 结束信息 | header 值、URL query 值 |
| payload | JSON key 骨架 / 大小 / SHA-256 | 内容本身 |
| WebSocket | 帧 opcode / 大小 / SHA-256 / schema / 打开关闭 | 帧内容 |

## 安全边界

- 只使用 `Network.enable` 及相关只读事件；不使用 `Network.getResponseBody`、
  `Network.setRequestInterception`、`Network.setBlockedURLs` 等任何改写能力。
- 扩展代码与导出数据均不含凭证；不要向仓库提交任何 JSONL 产物。

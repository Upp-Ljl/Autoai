# SAT2 Relay 2.0 MCP 本地辅助接入

MCP 不是任务推进主链。主链仍是：GitHub 控制事件 → 本地 Control Center → 浏览器扩展 → 已绑定 ChatGPT Session。MCP 只用于本机诊断和有限恢复。

## stdio server

安装后可执行文件：

```text
%LOCALAPPDATA%\SAT2Relay\venv\Scripts\sat2-relay-mcp.exe
```

通用 MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "sat2-relay": {
      "command": "%LOCALAPPDATA%\\SAT2Relay\\venv\\Scripts\\sat2-relay-mcp.exe",
      "args": [
        "--config",
        "%LOCALAPPDATA%\\SAT2Relay\\config.yml"
      ]
    }
  }
}
```

客户端不展开 Windows 环境变量时，改用绝对路径。

## 暴露工具

- `relay_status`
- `relay_doctor`
- `relay_poll_once`
- `relay_reload_credentials`
- `relay_replay_comment`
- `relay_resolve_alert`

这些工具不会返回 PAT，不提供任意 shell，不允许 merge、workflow dispatch、qualification、formal experiment 或论文证据修改。

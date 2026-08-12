# SAT2 并行自主协作协议（Progress-Document + Monotonic Sequence）— 详细方案

状态: DRAFT — 供 Mentor/外部 GPT 审查
日期: 2026-08-12
范围: 科研双路线并行协作（Route A 单星视觉 / Route B 单星 Agent）的会话间自动传递机制

---

## 1. 背景与问题

### 1.1 目标形态

```text
两个 Mentor 会话完全独立地自我驱动：
  各自写任务文档 → relay 自动派给各自 Worker → Worker 执行提交
  → relay 自动投递审查给 Mentor → 审查通过 → Mentor 写下一阶段任务文档 → 循环
两个路线互不知晓对方存在，不需要任何外部同步。
```

### 1.2 现状架构的缺陷（已在实际运行中暴露）

| 缺陷 | 表现 |
|---|---|
| 协调器（LLM）充当推进引擎 | 会越界改任务文件/DB/推状态文档，造成契约 SHA 失配、跨路线文件污染、STALE_PR_HEAD 卡死 |
| 新文件扫描式触发 | 两个会话在同一轮询间隔内写完文档时可能被合并误判；扫描逻辑繁琐 |
| 自激振荡 | 协调器自身的产物改变状态 → 再次触发自身 |
| 手工 DB 操作 | 绕过 daemon 校验（枚举大小写错误等），无审计 |
| 触发链路复杂 | watcher → 协调器 → 写控制面，链路越长故障面越大 |

### 1.3 本方案的核心思路

**用"进度文档 + 单调递增序号"作为唯一的会话间同步信号，取代所有外部扫描与推进引擎。**

- 每次会话产出提交前，必须先更新进度文档中的序号（单调 +1）
- relay 只读一个文件、比一个序号，即可判断"是否有新内容需要投递"
- 两个路线各自独立进度文件，天然消除并发写冲突与误判

---

## 2. 进度文档协议

### 2.1 文件约定

| 路线 | 进度文件路径 | 可写角色（allowed_paths 强制） |
|---|---|---|
| Route A（视觉） | `collaboration/routes/vision/progress.yaml` | S1（Mentor）、S2（Worker） |
| Route B（Agent） | `collaboration/routes/agent/progress.yaml` | S3（Mentor）、S4（Worker） |

进度文件必须位于各自路线的 allowed_paths 内（已满足），保证：
- 两个路线永远不写同一文件 → 无跨路线写冲突
- relay 的 checkpoint 路径校验（PR 文件须在 allowed_paths 内）天然接受该文件

### 2.2 Schema

```yaml
schema: 1
route: route-a-vision        # 固定标识
sequence: 17                 # 单调递增序号（核心信号）
stage: 3                     # 当前阶段号（0..6，903 协议）
updated_by: S1               # 最后更新者（S1/S2/S3/S4）
updated_at: "2026-08-12T12:00:00Z"
next_task: .sat2/tasks/900-ROUTE-A-VISION-04.yml   # Mentor 审查通过时声明
last_summary: "Stage 3 observation freeze committed"  # 本次产出摘要（≤200 字）
```

### 2.3 更新规则（硬约束，写入 Capsule 指令 + relay 校验）

1. **先更新、后提交**：任何产出提交（代码/文档/证据）之前，更新者必须先
   - `sequence` 加 1
   - `updated_by` 填自己的角色
   - `updated_at` 填当前时间
   - `last_summary` 填本次产出的一句话摘要
2. **Mentor 审查通过时**（MENTOR_ACCEPTED 决策提交前）额外：
   - `stage` 递增（或按协议推进）
   - `next_task` 填下一阶段任务文档路径（该文档需同时提交）
3. **禁止**：序号回退、跳过序号、他人代写（updated_by 必须是自己角色）、改动他路线进度文件

### 2.4 校验规则（relay 执行）

| 检查 | 规则 | 违规处理 |
|---|---|---|
| 序号递增 | `new_seq > last_seq` | 不满足 → 拒绝投递 + 告警 |
| 序号回退 | `new_seq < last_seq` | 拒绝投递 + 高优先级告警（疑似分支混乱/回滚） |
| 序号跳变 | `new_seq > last_seq + 1` | 接受投递，但记录跳变告警（会话跳过中间产物） |
| 更新者合法 | `updated_by ∈ {该路线 mentor_role, worker_role}` | 否则拒绝 + 告警 |
| 阶段合法性 | `stage ∈ [0..6]` 且 `next_task` 存在（若 stage 变化） | 否则拒绝 + 告警 |

---

## 3. Relay 信号源集成设计

### 3.1 数据流

```text
relay poll（每 15 秒，已有）
  └─ GET 进度文件（Contents API + ETag/If-None-Match）
       ├─ 304（未变化）→ 跳过该路线，零开销
       └─ 200（变化）→ 解析 YAML → 校验（§2.4）
            └─ 通过 → 更新本地 last_seq（DB meta）
                 └─ 按动作决策表生成投递
```

### 3.2 动作决策表

| 最后更新者 | 变化内容 | relay 动作 |
|---|---|---|
| Worker（S2/S4） | sequence+1，stage 不变 | 投递审查 Capsule → 该路线 mentor |
| Mentor（S1/S3） | sequence+1，stage 不变，无 next_task | 不投递（仅记录）；等待下一次 |
| Mentor | sequence+1，stage+1，next_task 已声明 | 注册新任务（monitor 级联）→ 投递执行 Capsule → Worker |
| 任何角色 | TASK_BLOCKED 语义（进度文件或评论声明） | 按现有 BLOCKED 路径处理 |

### 3.3 与现有评论驱动机制的关系

- **兼容策略**：进度文件作为**新增信号源**，与现有控制评论驱动并存一个过渡期；两者都能产生投递，但以进度文件为准（评论驱动的投递按现有协议校验）
- **迁移完成**（验证期通过）后：控制评论降级为审计记录，不再驱动投递

### 3.4 阶段推进的自动化

- Mentor 审查通过时声明 `next_task` + 提交该任务文档
- relay 读到后：校验任务文档（沿用现有 `validate_task_spec_ready`）→ 更新该路线 monitor 的 task_id/task_file → 自动派发
- **无需注册脚本、无需协调器写控制面**——推进信号全部来自进度文档

---

## 4. Capsule 指令模板约束（变更）

在现有 SAT2 Guided Execution Capsule 末尾追加：

```text
PROGRESS CONTRACT:
1. 在你提交任何产出之前，必须先更新 <route> 的进度文档
   collaboration/routes/<route>/progress.yaml：
   - sequence 加 1
   - updated_by 填你的角色
   - last_summary 填本次产出的一句话摘要
2. 如果你是 Mentor 且本次决策为接受（MENTOR_ACCEPTED）：
   - 额外更新 stage 与 next_task（下一阶段任务文档路径）
   - 同时提交该 next_task 任务文档
3. 进度文档与你的产出必须在同一次提交中一起到达。
4. 严禁回退序号、跳过序号、或修改其他路线的进度文档。
```

### 4.1 校验点

- relay 在处理任何决策/投递前，先校验进度文档序号是否合法（§2.4）
- 若会话产出已提交但进度序号未更新 → 投递延迟 + 告警（不阻塞其他路线）

---

## 5. 可靠性设计

| 关注点 | 设计 |
|---|---|
| 幂等 | relay 以 `last_seq`（DB meta）为唯一记忆点；重启后从进度文档当前序号继续，无状态丢失 |
| 并发（两 Mentor 同时写） | 各自独立文件 + 各自 allowed_paths，物理隔离，无写冲突 |
| 并发（同路线 Mentor/Worker 同时写） | 同文件同字段更新有 git 冲突风险 → 约定"同路线同角色串行产出"（relay 的投递本身已强制串行：一次一个任务） |
| relay 重启 | last_seq 持久化在 DB；进度文档为事实源，重启后对比即可 |
| 网络故障 | ETag 304 机制天然容忍；GitHub API 失败按现有重试 |
| 会话失联/产出丢失 | 序号跳变告警 → 协调器（异常角色）介入调查 |
| 安全边界 | 进度文件在 allowed_paths 内；序号校验防会话绕过约束；协调器不再有控制面写权限 |

---

## 6. 测试与验证计划

### 6.1 单元/集成测试（daemon）

1. 进度文档解析 + 序号校验（递增/回退/跳变/非法角色）
2. 动作决策表（Worker 提交 → 审查投递；Mentor+next_task → 新任务派发）
3. ETag 缓存逻辑（304 跳过、200 处理）
4. relay 重启后 last_seq 恢复
5. 两路线进度文件独立（并发场景模拟）

### 6.2 端到端演练（人工）

1. 单路线闭环：Mentor 写任务 → Worker 执行 → 审查 → Mentor 声明 next_task → 下一阶段自动开始
2. 双路线并行：A/B 各自完整跑一轮，验证互不干扰
3. 故障注入：
   - 序号回退 → 拒绝 + 告警
   - 会话提交产出但未更新进度 → 投递延迟 + 告警
   - relay 中途重启 → last_seq 恢复无误投
4. 4 会话内存方案落地后验证长时间稳定运行（24h）

### 6.3 验收标准（全部通过才视为可靠）

- [ ] 单路线 3 个阶段在无协调器介入下自动完成
- [ ] 双路线同时推进，无跨路线干扰
- [ ] 所有故障注入场景按预期处理（拒绝/告警/恢复）
- [ ] 24h 无异常堆积、无自触发、无 session 泄漏

---

## 7. 开放问题（请审查确认）

1. **序号粒度**：每次产出提交 +1 即可，还是需要"阶段内多产物"的细粒度？建议 +1/次提交（简单），审查确认。
2. **next_task 的 monitor 级联**：relay 读到 next_task 后自动更新 monitor 配置——是否需要人工确认闸门（首轮建议自动 + 审计日志，成熟后再说）？
3. **进度文件与评论驱动的并存期**：多长？建议 2 轮完整闭环验证后切换。
4. **序号校验的严格度**：跳变是"接受+告警"还是"拒绝"？建议接受+告警（避免会话正常跳过中间态时卡死）。
5. **Capsule 模板约束的强制性**：会话不遵守时 relay 的行为（延迟投递 vs 拒绝）？建议延迟+告警。
6. **阶段推进的授权**：Mentor 声明 next_task 即可自动推进——与"科研决策需 Mentor 授权"的原则是否冲突？建议：next_task 由 Mentor 产出本身即授权（它写的就是任务），审查确认。

---

## 8. 实施步骤（审查通过后）

1. daemon：进度文件信号源（解析/校验/决策表/ETag）+ 测试
2. daemon：next_task monitor 级联 + 测试
3. Capsule 模板追加进度契约（扩展同步）
4. 双路线进度文件初始化 + 现有任务迁移
5. 端到端演练 + 故障注入
6. 验收 → 切换进度文件为唯一信号源 → 协调器降级为异常介入

# wxjerry / Rust 增量吸收审计（2026-08-24）

## 当前引用

- wxjerry `origin/main=af38e9e`，`origin/xuruodeaiban=cd2a2e6`。
- Rust `origin/main=b74c2ed1`，MiniApp/World Boss 分支沿用前序审计引用。
- 本轮只读拉取和对照，没有修改上游工作树，没有发送游戏命令，也没有调用新的生产 MiniApp 写接口。

## 结论

本轮没有可直接 cherry-pick 到生产的窄补丁。wxjerry 最新提交把青元子 `/begin` 按配置身份列表顺序串行错峰，并清理旧的非完美命中时序漂移补偿；Rust 最新主线主要重构文件日志布局。两条线都改变了较大的运行边界，不能按文件覆盖吸收。

## 可取部分

### World Boss 入场顺序

wxjerry `af38e9e` 新增按配置身份列表的 `_WorldBossBeginCoordinator`，明确区分“入场顺序”和“战斗请求预算”，并在身份失效时释放队列位置。这个边界可作为本地离线回放的对照项。

本地主线已有四登录账号上限、单账号身份去重、全局 MiniApp 预算、单身份内部串行和事件终态停止标记；因此不直接复制 coordinator。下一步只补回放测试，验证频道身份 `playerId`、身份失效、提前结束时不会留下等待槽。

### 副本冷却回写

wxjerry `3a1c4b7` 兼容“冷却中 / 当前无法加入队伍 / 剩余时间”新文案，并把解析出的等待时间写回副本冷却。主线已有同类 `parse_wait_time` 和副本 Gate C 边界，适配价值仅限新增真实文案 fixture；没有真实新回包前不改 reducer。

### MiniApp 运维刹车

Rust `814c9b0c` 将账号/身份 disabled 约束前置到签名铸造，避免被禁用账号继续发 Telegram RPC。主线公共入口已经在 WebView 前做身份资格、频道健康和目标群成员门禁；本轮不改共享签名层，避免重复实现两套资格判定。

### 日志布局

Rust `797ba201` 使用 `latest` 加启动/跨日归档，且永不删除。该设计不适合直接搬到主线：主线已有按日期消息日志、健康观察和脱敏 MiniApp capture 的保留策略，日志路径和轮转方式属于部署运维边界，不应与游戏状态机绑定。可另开部署级日志审计，不作为本轮生产代码吸收项。

## 明确不吸收

- 不整包迁移 Rust MiniApp / World Boss 框架。
- 不把上游的账号错峰策略直接覆盖本地主线世界 Boss 调度。
- 不因为上游新文案主动制造副本或 Boss 流量。
- 不接管 CommandAttempt 的恢复、补发、CD、reducer 或 Gate 4 控制。

## 下一步

1. 将 World Boss 入场顺序和无 session 终态加入 Lab 回放矩阵。
2. 取得真实副本新冷却文案后，仅增加 parser fixture。
3. 保持生产直连、90 次/分钟预算和当前公共入口资格门禁不变。

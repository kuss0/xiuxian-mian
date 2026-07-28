# 天机阁灵兽查询观察 Gate（2026-07-29）

## 结论

`.我的灵兽` 的真实群内回包已确认是完整灵兽面板，包含灵兽名称、状态、等级、战力、体力、羁绊和放养行迹等字段。
当前生产代码没有与该面板对应的灵兽/放养 reducer；旧 `ranch.py` 只处理 `.放养` 发送与归来广播，不能安全接收整段面板后直接改写其阶段。

因此本 Lab 增加了保守边界：天机阁请求仍可读取该面板，但结果只作为观察事实返回，记录首行、长度和摘要，不写放养状态，也不把结果标记为“已同步”。

## 证据

- 真实样本来源：`data/messages/2026-07-19.log` 中 `.我的灵兽` 的 bot 回包。
- 面板同时出现 `休息中` 与 `巡边中`，说明不能用单一“有/无灵兽”字段替代现有放养阶段。
- wxjerry 最新线已将该领域迁移为独立“万兽谷·驭灵行迹” MiniApp，接口为：
  - `/api/miniapp/xianxia-spirit-beast/start`
  - `/api/miniapp/xianxia-spirit-beast/expedition/start`
  - `/api/miniapp/xianxia-spirit-beast/expedition/choose`

## Gate 边界

- 当前改动只影响 Lab 的 `.我的灵兽` 只读结果边界。
- 不自动放养、不改变 `ranch_*` 计时器、不重放未知 HTTP POST。
- 不把 wxjerry 旧架构整包 cherry-pick 到主线。
- 专属 MiniApp 仍需真实 `externalApps key=spirit_beast` capture、脱敏回包 fixture、单身份 Lab 和独立生产 canary。

## 验证

- `tests/test_cave_treasure_runtime.py -k tianjige`: 12 passed。
- 新增真实面板形状测试，确认返回中不含原始 token/initData，也不写入放养状态。

## 主线接手条件

只有在专属 MiniApp 的 `start` 回包和行迹选择协议完成离线回放，并接入当前公共洞府 session、全局 90 次/分钟预算、capture 脱敏和单身份互斥后，才评审替换旧 `ranch.py` 主动发送链。

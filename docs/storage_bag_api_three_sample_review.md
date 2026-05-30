# 储物袋 API 三样本对照与落地清单

## 结论

储物袋外挂按“最小 API 调用 + 本地状态机仍为主”的方案落地：

1. UI 手动点 `API读取` 才读取库存。
2. `验证` 和低频保活只维护 `session`、`X-API-Token`、状态和物品名映射，不改库存，不发游戏命令。
3. Tianjige 失败只影响 Web 展示，不影响账号启动、命令队列、魂切换和本地文案状态机。
4. API 结果只作为储物袋快照输入，不替代真实 bot 文案和本地状态。
5. 一个已验证的 cookie 是账号级查询凭据；手动刷新时先读 `/api/me`，再对本地未覆盖身份按 username 补 `/api/cultivator/{username}`。

## 三份样本的取舍

### 1. `/root/2026/docs.zip`

读过的关键文件：

- `docs/tianjige/api.md`
- `docs/architecture/07-external-services.md`
- `docs/architecture/11-account-onboarding-and-presets.md`

可采纳点：

- 真实域名是 `https://asc.aiopenai.app`。
- 认证是 `session` Cookie + `X-API-Token`。
- 稳定边界是 `/api/bootstrap`、`/api/me`、`/api/cultivator/{username}`。
- `/api/bootstrap` 适合拿 `game_items`，用于把 `mat_001` 这类 id 映射成物品名。
- `/api/me` 只适合 Web 展示增强和人工核对，不是运行时权威源；其他角色详情应走 `/api/cultivator/{username}`。
- `401/403/429` 必须降级，不触发账号重启，不高频重试。

本地落地：

- 默认 Base URL 已改为 `https://asc.aiopenai.app`。
- `验证` 使用 `/api/bootstrap`。
- `API读取` 先使用 `/api/me`，再用同一个 cookie 对本地未覆盖身份补 `/api/cultivator/{username}`。
- 失败时只记录 UI 状态和保活状态，不影响运行时。

### 2. Rust 线 `takaranoao/main`

读过的关键文件：

- `src/xiuxian/fetcher.rs`
- `src/xiuxian/inventory.rs`
- `src/server/credentials.rs`
- `src/xiuxian/mod.rs`

可采纳点：

- 请求层固定走 `GET /api/bootstrap` 与 `GET /api/cultivator/{username}`。
- 请求头带 `X-API-Token`。
- 响应里的 `Set-Cookie` 要回收，哪怕失败也尽量保留新 cookie。
- cookie/token 写入要避免旧值覆盖新值。
- inventory 的真实结构要兼容 `inventory.items[]` 和 `inventory.materials` 两类。

本地落地：

- 客户端会提取并保存旋转后的 `session`。
- 已有 token 时直接请求 API，不额外探首页。
- 没有 token 时才访问首页提取 `window.DASHBOARD_API_TOKEN`。
- 库存解析兼容 `items` 列表和 `materials` 字典。
- 手动刷新时同一个 cookie/token 会连续复用，响应里的旋转 cookie 会传给下一次角色查询。

Rust 线没有给出更多宗门模块，它更适合作为“HTTP 请求与库存结构”的稳定样本。

### 3. TingHua `zidongxiuxian`

读过的关键文件：

- `tg_game/clients/asc_client.py`
- `tg_game/services/external_sync.py`
- `tg_game/storage.py`
- `tg_game/modules/*.py`
- `tg_game/sect_features.py`

可采纳点：

- 外部 API 是账号级资源，不是魂级资源。
- Cookie 可以从前端录入后复用，并且需要定期维护状态。
- 全量 Cookie 应尽量规整到 `session=...`，避免保存和透传无关字段。
- 功能模块和宗门可以用 manifest/registry 方式组织，方便 UI 展示和后续接入。

不能直接照搬的点：

- 该项目大量模块是 `FeatureModule` 元数据和 Web 入口，不等同于本地已经需要的文案状态机。
- 本地原则是被动读真实 bot 文案优先，API 只做外部辅助；不能改成 API 作为运行核心。

本地落地：

- 前端增加 Cookie、API Token、Base URL 配置。
- 粘贴浏览器 F12 的整段 Cookie 时，会优先规整为 `session=...`。
- API 凭据不会出现在 UI snapshot 中。

## 本地已实现清单

代码入口：

- `model/storage_bag_api_client.py`：Tianjige 只读客户端。
- `model/state.py`：`storage_bag_api_config` 状态。
- `model/persistence.py`：配置持久化。
- `model/ui.py`：配置、验证、手动刷新、低频保活。
- `model/app.py`：接入全局调度器，但只跑保活。
- `model/web/static/js/storage_bag_ui.js`：Web 操作面板。
- `tests/test_storage_bag_api.py`：新测试。

关键保护：

- 手动刷新才更新 `storage_bag_records`。
- 保活只访问 `/api/bootstrap`，只更新 cookie/token/status/item map。
- 一个 cookie 可用于 `/api/cultivator/{username}` 补查其他本地角色，不再把刷新限制在 `/api/me` 返回的角色里。
- 测试覆盖“不发送游戏命令”。
- 测试覆盖“未匹配身份不改旧库存”。
- 测试覆盖“已有 token 时不额外访问首页”。
- 测试覆盖“整段 Cookie 规整为 session Cookie”。
- 测试覆盖“同一 cookie 读取 `/api/me` 后继续查询另一个本地角色”。

## 本地缺的功能模块

本地现有模块入口来自 `model/config.py` 的 `MODULE_NAMES`，主要是：

- 灵树、法宝、温养器灵、器灵试炼、放养、野外历练、观星台、观星监控、观星、登天阶、玄骨考校、极阴祖师、侍妾、天机代卜、共历心劫、南陇侯、元婴、深度闭关、小世界、点卯、闯塔、第二元神、太一、自动副本。

TingHua 有独立 manifest 的功能模块：

- 法宝、战斗、突破、侍妾、修炼、外交、副本、洞府、物品管理、市集、其他玩法、宗门、商城、股市。

按“本地是否有独立入口 + 状态 + 文案链”判断，明显缺或未成一级模块的是：

- 战斗：本地只解析战力卡和少量斗法相关信息，没有独立战斗自动化模块。
- 突破：本地能被动识别突破广播和境界变化，没有独立突破计划/执行模块。
- 外交：本地没有对应独立模块。
- 市集：本地储物袋转移会用挂单思路，但没有市场行情/挂单管理一级模块。
- 商城：本地没有商城购买/兑换模块。
- 股市：本地没有股市持仓、买卖、风控模块。
- 宗门总控：本地有点卯和若干宗门专项，但没有统一宗门 registry/panel。
- 洞府总控：本地有灵树、小世界、放养等专项，但没有统一洞府模块。
- 物品管理总控：本地有储物袋和转移，但没有像 inventory manifest 那样覆盖耐久、分类、策略的一体化物品模块。

本地已有但组织更分散的能力：

- 法宝：本地有 `法宝/温养器灵/器灵试炼`，比 TingHua 的 manifest 更偏可执行。
- 副本：本地有 `自动副本`、主线/本地群兼容和副本静默逻辑，但仍缺统一副本 manifest。
- 修炼：本地有 `深度闭关`、`太一`、`元婴`、`小世界` 等专项，不是一个总控修炼模块。
- 侍妾：本地有较深的文案状态机，不能按 TingHua 的简单 manifest 判断缺失。

## 本地缺的宗门组织

TingHua 的 `sect_features.py` 明确列了：

- 黄枫谷
- 太一门
- 星宫
- 凌霄宫
- 合欢宗
- 万灵宗
- 落云宗
- 阴罗宗
- 元婴宗

本地现状：

- 太一门：有 `太一` 模块。
- 星宫：有观星台、观星、侍妾联动。
- 凌霄宫：有 `登天阶`。
- 万灵宗：有 `放养`。
- 落云宗：有 `灵树`。
- 元婴宗：有 `元婴`。
- 合欢宗：没有作为宗门一级入口，侍妾/心劫能力只是相邻玩法。
- 黄枫谷：没有药园、播种、采药、除草、除虫、浇水的一体化模块。
- 阴罗宗：没有对应宗门入口。

下一步如果要吸收，优先不是复制宗门命令，而是先做统一宗门 manifest，再按真实文案 replay 补状态机。

## 验证记录

已跑：

- `tests/test_storage_bag_api.py`
- `tests/test_storage_bag_transfer.py`
- `tests/test_persistence_runtime_flags.py`

当前测试结果：

- `36 passed`

未做：

- 没用真实 Cookie 打远端 live API。没有用户明确配置 Cookie 前，不应伪造 live 验证结果。
- 没新增缺失模块和宗门功能。本报告只是列清单，按要求先不做。

# xiuxian-mian

`xiuxian-mian` 是当前修仙 Telegram 自动化脚本的独立主线仓库。它从旧仓库分出，用来承载现在实际运行的 mian 线：多账号、多身份、日志群控制、Web 运维面板、玩法模块状态机、消息盒子和安全 watchdog。

这个项目不是通用机器人框架，也不是陌生环境可以直接开箱即用的产品。它服务于当前固定的游戏群、日志群、账号身份和人工接管流程；代码目标是让自动化动作可观察、可暂停、可恢复，并尽量避免无依据的主动查询和重复发送。

## 核心原则

- 被动优先：优先吃真实游戏回执、日志群文案、历史消息和本地缓存。
- 少发送：能从本地状态判断就不发命令，能等回复就不抢查。
- 本地文案优先：库存、冷却、玩法阶段等判断尽量来自已采集文本和本地快照。
- API 只是备用：除明确要求的储物袋、文案加 API 策略、人工 UI 点击外，不把 API 当默认数据源。
- 失败保守：遇到未知回复、身份不明、状态冲突或资源不足，优先记录并停链路，不猜着继续。
- 可审计：关键发送、回复、按钮、状态推进和异常都应能在日志群或本地记录里复盘。

## 当前能力

运行层：

- Telethon 多账号 client 管理。
- 多 identity 状态隔离和身份调度。
- 游戏主群、日志群、topic、按钮回调处理。
- 发送队列、全局锁、回复跟踪、短冷却和有限补发。
- 本地 SQLite 持久化、启动恢复、运行时标记和安全 watchdog。
- Web UI 登录、身份配置、模块开关、运行态查看和部分人工操作入口。
- 游戏消息落盘、被动消息盒子、离线分析和健康检查工具。

玩法层：

- 灵树归档保留、法宝、器灵温养、器灵试炼。
- 放养、野外历练、观星台、观星监控。
- 登天阶、玄骨考校、天机问答、天道审判、天道 Mini App。
- 侍妾、共历心劫、南陇侯、合欢、极阴、阴罗。
- 元婴、元婴宗问道、深度闭关、第二元神、太一节点。
- 小世界、点卯/授业、闯塔、世界 boss。
- 副本报名、队伍推荐、日志群按钮、静场令、坠魔谷等副本流程。
- 探寻裂缝、寻节点、天星、阵法。
- 储物袋快照、材料汇总、转移流程和高置信库存同步。
- 天机代卜、昆吾资源换取和本地库存优先的资源调度。

每个玩法模块都应尽量保持“输入解析 + 状态机 + 输出适配”边界清楚。框架层负责收集消息、路由证据、记录发送和做安全约束；模块层只处理自己的状态推进和必要动作。

## 目录结构

- `xiuxian.py`：systemd 入口，启动 supervisor/worker，并在代码稳定变更后做语法检查和 worker 重启。
- `model/app.py`：Telegram 事件入口、消息路由和 scheduler 主循环。
- `model/runtime.py`：命令发送、日志推送、回复跟踪、UI 登录 token 等运行工具。
- `model/state.py`：内存状态模板、profile 和全局配置访问器。
- `model/persistence.py`：SQLite schema、迁移、加载和保存。
- `model/control.py`：日志群指令、身份注册、模块开关和运行态初始化。
- `model/app_runtime.py`：运行时去重、事件 claim 和公共状态。
- `model/app_message_log.py`：游戏群、副本群、日志群消息落盘。
- `model/app_replica.py`：副本、虚拟大厅、聚合查询和按钮流程。
- `model/features/`：各玩法模块。
- `model/features/passive_inbox.py`：被动消息盒子。
- `model/features/passive_event_ledger.py`：被动事件账本。
- `model/web/`：Web UI 页面、样式和前端脚本。
- `tools/`：离线分析、储物袋汇总、安全 watchdog、健康观察等工具。
- `tests/`：pytest 回归测试，默认使用隔离测试目录，不应污染 live DB。
- `deploy/`：systemd 相关服务模板。

## 运行依赖

建议 Python 3.11 以上。当前线上目录使用仓库内 `.venv`。

最小依赖：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install telethon segno "requests[socks]"
```

仓库暂时没有锁定依赖文件。升级依赖前先跑测试，不要在生产目录里临时试包。

## 配置

复制示例配置：

```bash
cp .env.example .env
```

必须配置：

- `API_ID` / `API_HASH`：Telegram API 凭据。
- `ADMIN_ID`：允许在日志群执行控制指令的 Telegram 用户 ID，多个用逗号分隔。
- `LOG_GROUP_ID`：审计日志和远程控制所在群。
- `CHAOGU_UI_HOST` / `CHAOGU_UI_PORT`：Web UI 监听地址和端口。

常用可选配置：

- `TG_PROXY_TYPE` / `TG_PROXY_HOST` / `TG_PROXY_USERNAME` / `TG_PROXY_PASSWORD`：Telegram 和 Bot API 代理。
- `LOG_SEND_MODE`：`account` 或 `bot`。
- `LOG_BOT_TOKEN`：`LOG_SEND_MODE=bot` 时使用。
- `CHAOGU_UI_PUBLIC_BASE_URL`：公网访问 UI 时建议显式填写。
- `TIANDAO_MINIAPP_VERIFY_URL` / `TIANDAO_MINIAPP_BOT_USERNAME`：天道 Mini App 验证配置。

不要提交真实 `.env`、Telegram session、live SQLite、消息日志、cookie、token 或任何运行态敏感文件。

## 启动

开发或手动启动：

```bash
. .venv/bin/activate
python xiuxian.py
```

线上 systemd 入口示例：

```ini
WorkingDirectory=/opt/xiuxian-main
ExecStart=/opt/xiuxian-main/.venv/bin/python /opt/xiuxian-main/xiuxian.py
```

常用服务命令：

```bash
systemctl status xiuxian.service
systemctl restart xiuxian.service
journalctl -u xiuxian.service --since "10 min ago" --no-pager
```

README 修改不需要重启服务；代码或配置变更上线后再按实际情况重启。

## 首次接入

1. 准备 `.env`，确认 `LOG_GROUP_ID` 和 `ADMIN_ID` 正确。
2. 启动服务，完成 Telegram session 登录。
3. 在日志群发送 `.登录`，使用返回链接进入 Web UI。
4. 在 UI 中补充游戏群、bot、topic、账号和 identity。
5. 先观察消息盒子和日志群，再逐个开启模块，不要一次性全开新身份。

UI 登录链接有有效期，浏览器会话也会超时。它是运维入口，不应该裸露在未受控公网环境里。

## 常用日志群指令

- `.登录`：获取 Web UI 登录链接。
- `.状态`：查看全局和身份状态。
- `.全局暂停` / `.全局恢复`：暂停或恢复自动调度。
- `.开启<模块>` / `.关闭<模块>`：按模块开关当前身份或指定身份。
- `.储物袋汇总`：基于历史快照汇总材料，不主动发送游戏命令。
- `.日志推送状态` / `.审计推送状态`、`.上线预检` / `.预发布检查`、`.发送健康码` / `.分析健康`：维护和排障入口。

具体支持项以 `model/control.py` 中的正则和处理器为准。

## 测试和发布前检查

默认 pytest 通过 `tests/conftest.py` 使用临时数据目录。不要把测试指向 live DB，除非明确知道自己在做什么。

推荐检查：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile xiuxian.py model/*.py model/features/*.py
.venv/bin/python tools/ui_http_smoke.py
.venv/bin/python tools/safety_watchdog.py --once --dry-run
```

上线后至少确认：

```bash
systemctl is-active xiuxian.service
sqlite3 data/state/chaogu_state.db 'select count(*) from pending_tasks;'
journalctl -u xiuxian.service --since "10 min ago" -g 'Traceback|ERROR|Exception|NameError|TypeError|KeyError|ValueError|UnboundLocalError' --no-pager
```

## 数据边界

- `data/session/`：Telegram session，敏感。
- `data/state/`：SQLite、状态文件、安全标记，通常敏感。
- `data/messages/`：消息日志，可能含群消息和身份信息。
- `data/analysis/`：离线分析输出，提交前也要检查内容。

这些目录属于运行数据，不是代码交付物。排障时可以读取，提交前必须确认没有把敏感数据带进 git。

## 维护方式

- 先读当前代码、当前日志、当前状态，再判断问题。
- 新模块先补输入输出边界、被动证据和回归样例，再接调度。
- 发送策略要有冷却、补发上限、白名单和 watchdog 依据。
- 涉及库存、资源、洞府、心劫、冷却、身份切换时，优先补测试。
- 大改动先在 lab 或测试环境走通，再上线生产。
- 当前活跃目录是 `/opt/xiuxian-main`；旧目录 `/opt/xiuxian` 和 `xiuxian-c` 默认不碰。

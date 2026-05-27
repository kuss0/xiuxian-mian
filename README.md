# Xiuxian Main

这个仓库是当前修仙群自动化的本地主控台。它不是通用机器人框架，也不是给陌生环境开箱即用的产品；代码优先服务于现有 Telegram 群、多个账号身份、日志群审计和可随时人工接管的运行方式。

核心目标很简单：把容易跑飞的自动化动作放进一个可观察、可暂停、可恢复的本地进程里。程序通过 Telethon 驱动 Telegram 账号，状态落在本机 SQLite 和本地文件中，Web UI 只作为运维控制面使用。

## 当前能力

运行层：

- 多 Telegram 账号 client 管理
- 多 identity 运行态隔离
- 游戏主群、日志群、topic 感知
- 命令发送队列、回复链路跟踪、短冷却重试
- 本地 Web UI 登录和运维控制
- SQLite 持久化、启动恢复、异常 watchdog
- 游戏群消息落盘和离线分析工具

已接入的主要玩法模块：

- 灵树、法宝、器灵温养、器灵试炼
- 放养、野外历练、观星台、观星监控
- 登天阶、玄骨考校、天机问答、天道审判
- 侍妾、天机代卜、共历心劫、南陇侯
- 元婴、深度闭关、第二元神、太一节点
- 小世界、点卯/授业、闯塔
- 副本报名、静默处理、被动收件箱
- 储物袋快照、材料汇总、转移流程和高置信库存同步

这些模块都按“状态机 + 真实回执解析 + 保守调度”的方式写。看到不认识的游戏文本时，默认应该停下来记录，而不是猜着继续发命令。

## 目录结构

- `xiuxian.py`：systemd 入口。默认先启动 supervisor，再拉起 worker；检测到 Python 代码稳定变更后会做语法检查并重启 worker。
- `model/app.py`：Telegram 事件入口、消息路由和 scheduler 主循环。
- `model/app_runtime.py`：运行时去重、事件 claim 等公共状态。
- `model/app_message_log.py`：游戏群、复制/副本群消息落盘。
- `model/app_replica.py`：副本、虚拟大厅、聚合查询相关逻辑。
- `model/control.py`：日志群指令、身份注册、模块开关和运行态初始化。
- `model/runtime.py`：命令发送、审计日志、回复跟踪、UI 登录 token 等运行工具。
- `model/state.py`：内存状态模板、profile 和全局配置访问器。
- `model/persistence.py`：SQLite schema、迁移、加载和保存。
- `model/features/`：各玩法模块。
- `model/web/`：Web UI 页面、样式和前端脚本。
- `tools/`：离线分析、储物袋汇总、安全 watchdog。
- `tests/`：pytest 回归测试，默认用隔离测试目录，不应污染 live DB。

## 运行依赖

建议 Python 3.11 以上。当前线上环境使用仓库内 `.venv`。

最小依赖：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install telethon segno "requests[socks]"
```

仓库暂时没有锁定依赖文件；升级依赖前先跑测试，不要在 live 目录里临时试包。

## 配置

复制示例配置：

```bash
cp .env.example .env
```

必须配置的字段：

- `API_ID` / `API_HASH`：Telegram API 凭据。
- `ADMIN_ID`：允许在日志群执行控制指令的 Telegram 用户 ID，多个用逗号分隔。
- `LOG_GROUP_ID`：审计日志和远程控制所在群。
- `CHAOGU_UI_HOST` / `CHAOGU_UI_PORT`：Web UI 监听地址和端口。

常用可选字段：

- `TG_PROXY_TYPE` / `TG_PROXY_HOST` / `TG_PROXY_USERNAME` / `TG_PROXY_PASSWORD`：Telegram 和 Bot API 代理。
- `LOG_SEND_MODE`：`account` 或 `bot`。
- `LOG_BOT_TOKEN`：`LOG_SEND_MODE=bot` 时使用。
- `CHAOGU_UI_PUBLIC_BASE_URL`：公网访问 UI 时建议显式填写。
- `TIANDAO_MINIAPP_VERIFY_URL` / `TIANDAO_MINIAPP_BOT_USERNAME`：天道 Mini App 验证配置。

不要提交真实 `.env`、session 文件、live SQLite 或消息日志。

## 启动

开发/手动启动：

```bash
. .venv/bin/activate
python xiuxian.py
```

线上 systemd 入口：

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

## 首次接入

1. 准备 `.env`，确认 `LOG_GROUP_ID` 和 `ADMIN_ID` 正确。
2. 启动服务。
3. 在日志群发送 `.登录`，使用返回链接进入 Web UI。
4. 在 UI 中补充游戏群、bot、topic、账号和 identity。
5. 逐个打开模块，不要一次性全开新身份。

UI 登录链接有有效期；浏览器会话也会超时。它是运维入口，不应该裸露在公网未受控环境里。

## 常用日志群指令

- `.登录`：获取 Web UI 登录链接。
- `.状态`：查看全局和身份状态。
- `.全局暂停` / `.全局恢复`：暂停或恢复自动调度。
- `.开启<模块>` / `.关闭<模块>`：按模块开关当前身份或指定身份。
- `.储物袋汇总`：使用历史快照汇总材料，不主动发送游戏命令。
- `.审计推送状态`、`.预发布检查`、`.分析健康`：维护和排障入口。

具体支持项以 `model/control.py` 中的正则和处理器为准。

## 测试和发布前检查

默认 pytest 会通过 `tests/conftest.py` 使用临时数据目录。不要把测试指向 live DB，除非明确知道自己在做什么。

推荐检查：

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m py_compile xiuxian.py model/*.py model/features/*.py
.venv/bin/python tools/safety_watchdog.py --once --dry-run
```

上线后至少确认：

```bash
systemctl is-active xiuxian.service
sqlite3 data/state/chaogu_state.db 'select count(*) from pending_tasks;'
journalctl -u xiuxian.service --since "10 min ago" -g 'Traceback|ERROR|Exception|NameError|TypeError|KeyError|ValueError|UnboundLocalError' --no-pager
```

## 数据目录

- `data/session/`：Telegram session。
- `data/state/`：SQLite、状态文件和安全标记。
- `data/messages/`：消息日志。
- `data/analysis/`：离线分析输出。

这些目录属于运行数据，不是代码交付物。做问题复盘时可以读，提交前要确认没有把敏感数据带进 git。

## 维护原则

- 以真实游戏回执为准，不靠想象补解析。
- 不认识的回复宁可停链路、写审计日志，也不要继续发下一条命令。
- 涉及库存、资源、冷却、身份切换时，优先补测试。
- 线上目录 `/opt/xiuxian-main` 是当前活跃目标；不要误改旧目录或其他 Docker 目标。
- 大改动先保证本地测试和 watchdog，再考虑推分支。

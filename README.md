# Xiuxian

一个基于 Telethon 的 Telegram 修仙游戏自动化控制台，提供本地 Web UI、多账号/多身份运行、SQLite 持久化恢复，以及日志群远程控制能力。

## 项目简介

这个项目的目标是把 Telegram 修仙游戏的常用自动化动作收口到一个可恢复、可观测、可手动干预的控制台里。

当前实现重点：

- Telethon 长连接与会话管理
- 多账号 / 多身份并发运行
- 本地 Web UI 控制台
- SQLite 持久化恢复
- 日志群远程指令控制
- 模块化 scheduler 与回复链路处理

当前已接入模块：

- 灵树
- 法宝
- 观星台
- 玄骨考校
- 极阴祖师
- 元婴
- 深度闭关
- 点卯
- 闯塔

## 运行要求

- Python 3.11+
- 能正常访问 Telegram
- 已在 https://my.telegram.org/apps 创建应用并拿到 `API_ID` / `API_HASH`

当前仓库没有 `requirements.txt` / `pyproject.toml`，部署时直接手动安装依赖即可。

## 快速部署

### 1. 创建虚拟环境并安装依赖

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install telethon
```

如果你的环境里 `python3.11` 不存在，至少要保证 Python 版本不低于 3.11。

### 2. 配置 `.env`

先从示例文件复制：

```bash
cp .env.example .env
```

示例见 [.env.example](.env.example)。当前启动配置统一从根目录 `.env` 读取。

最小可用配置示例：

```dotenv
API_ID=123456
API_HASH=your_api_hash
LOG_GROUP_ID=-1001234567890
LOG_SEND_MODE=account
LOG_BOT_TOKEN=
ADMIN_ID=123456789
CHAOGU_UI_HOST=0.0.0.0
CHAOGU_UI_PORT=3030
CHAOGU_UI_PUBLIC_BASE_URL=
CHAOGU_UI_AUTH_IDLE_TIMEOUT_SEC=3600
CHAOGU_UI_AUTH_SESSION_TIMEOUT_SEC=86400
```

配置项说明：

- `API_ID` / `API_HASH`：Telegram API 凭据
- `LOG_GROUP_ID`：日志群 ID。审计日志、远程控制指令、UI 登录链路都依赖它
- `LOG_SEND_MODE`：日志群发送模式，可选 `account` / `bot`
- `LOG_BOT_TOKEN`：当 `LOG_SEND_MODE=bot` 时使用的 bot token
- `ADMIN_ID`：管理员 Telegram 用户 ID，只有该用户在日志群发送的控制指令会被响应
- `CHAOGU_UI_HOST`：UI 监听地址，默认 `0.0.0.0`
- `CHAOGU_UI_PORT`：UI 监听端口，默认 `3030`
- `CHAOGU_UI_PUBLIC_BASE_URL`：浏览器实际访问地址；如果不填，程序会尝试自动探测公网 IP，失败后回退到 `http://127.0.0.1:<port>`
- `CHAOGU_UI_AUTH_IDLE_TIMEOUT_SEC`：登录链接有效期，默认 `3600` 秒
- `CHAOGU_UI_AUTH_SESSION_TIMEOUT_SEC`：UI 会话无请求超时，默认 `86400` 秒

以下 UI 项继续使用代码默认值，一般不用写进 `.env`：

- `CHAOGU_UI_AUTO_REFRESH_SEC`
- `CHAOGU_UI_AUTH_COOKIE_NAME`

如果你准备通过公网 HTTPS 暴露 UI，建议显式设置 `CHAOGU_UI_PUBLIC_BASE_URL=https://...`，这样 UI 会自动使用 Secure Cookie。

### 3. 启动程序

```bash
PYTHONDONTWRITEBYTECODE=1 python xiuxian.py
```

入口文件是 [xiuxian.py](xiuxian.py)，实际会进入 [model/app.py](model/app.py) 中的 `main()`，启动流程大致是：

1. 连接主 Telethon client
2. 读取 SQLite / 本地状态
3. 拉起已保存的额外账号 client
4. 启动 Web UI 服务
5. 恢复各身份运行态
6. 进入主循环，每 5 秒调度一次各模块 scheduler

## 首次使用

### 1. 打开 Web UI

默认访问地址通常是：

- `http://127.0.0.1:3030`

如果你在 `.env` 中设置了 `CHAOGU_UI_PUBLIC_BASE_URL`，则以该地址为准。

### 2. 在日志群发送 `.登录`

UI 不是匿名访问的。先在日志群发送：

```text
.登录
```

程序会回复一个登录链接。当前实现下：

- 登录链接默认 60 分钟有效
- 登录后的浏览器会话默认 24 小时无请求自动失效
- 支持多个登录链接和多个浏览器会话并存

浏览器打开该链接后，即可交换为 UI 会话。

### 3. 配置基础运行参数

进入 UI 后，按需配置：

- 游戏主群 ID
- 游戏 bot / 频道 ID
- topic 配置
- 自动删除已发送消息等基础选项
- 各 identity 的模块开关和运行参数

这部分属于运行态配置，保存在本地状态 / SQLite 中，不放进 `.env`。

### 4. 在 UI 内登录 Telegram 账号

进入控制台后，可以按页面提示完成 Telegram 账号登录。当前 UI 支持：

- 手机号验证码登录
- 二维码登录
- 多账号管理

## 持久化与目录说明

程序启动时会自动准备以下目录：

- [data/session/](data/session/)：Telegram session 文件
- [data/state/](data/state/)：本地状态与数据库
- [data/messages/](data/messages/)：消息日志缓存

默认 SQLite 数据库文件：

- [data/state/chaogu_state.db](data/state/chaogu_state.db)

## 常用控制方式

### 日志群指令

日志群可作为远程控制入口，当前常见指令包括：

- `.登录`
- `.状态`
- `.全局暂停`
- `.全局恢复`
- `.开启观星台`
- `.关闭观星台`

### Web UI

Web UI 适合做这些事：

- 查看全局状态和各 identity 快照
- 开关模块
- 调整模块参数
- 新增 / 删除 identity
- 登录额外 Telegram 账号
- 处理首次部署后的基础配置

## 常见问题

### 1. 启动时报 `missing .env`

说明根目录缺少 `.env`，先执行：

```bash
cp .env.example .env
```

然后补全必填字段再启动。

### 2. 浏览器访问地址不对

优先在 `.env` 中显式设置：

```dotenv
CHAOGU_UI_PUBLIC_BASE_URL=http://你的访问地址:3030
```

否则程序会尝试自动探测公网 IP，探测不到时回退到本地地址。

### 3. 为什么打开 UI 后提示先发送 `.登录`

这是当前 UI 的认证流程。必须先在日志群发送 `.登录`，再打开返回的登录链接完成会话交换。

### 4. 首次启动没有主账号 session 怎么办

当前启动流程允许主 client 先 connect；如果本地还没有可用 session，可以先拉起 UI，再在 UI 内补做 Telegram 账号登录。

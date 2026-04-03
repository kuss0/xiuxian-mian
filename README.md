# Xiuxian

一个基于 Telethon 的 Telegram 修仙游戏自动化控制台，带本地 Web UI、SQLite 持久化和多身份运行能力。

## 功能概览

- Telegram 连接与会话管理
- 多身份 / 多账号运行
- 本地 Web UI 控制台
- SQLite 持久化状态恢复
- 已实现模块：灵树、法宝、观星台、玄骨考校、极阴祖师、元婴、深度闭关、点卯、闯塔
- 日志群远程指令控制（如 `.登录`、`.状态`、`.全局暂停`）

## 运行要求

- Python 3.11+
- 可访问 Telegram
- 已准备好 Telegram API 凭据（`api_id` / `api_hash`）

## 安装与部署

### 1. 准备虚拟环境

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install telethon
```

如果你使用的不是 `python3.11`，至少需要保证 Python 版本为 3.11 及以上。

### 2. 配置 `.env`

根目录提供了示例文件 [.env.example](.env.example)。

先复制一份：

```bash
cp .env.example .env
```

最小配置示例：

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

- `API_ID` / `API_HASH`：Telegram API 凭据，在 https://my.telegram.org/apps 创建应用后获取
- `LOG_GROUP_ID`：日志复盘群 ID；审计日志、远程指令和 UI 登录链路都依赖它
- `LOG_SEND_MODE`：日志群发送模式，可选 `account` / `bot`
- `LOG_BOT_TOKEN`：当 `LOG_SEND_MODE=bot` 时使用的 bot token
- `ADMIN_ID`：管理员 Telegram 用户 ID，只有该用户在日志群发送的控制指令会被响应
- `CHAOGU_UI_HOST`：UI 监听地址，默认 `0.0.0.0`
- `CHAOGU_UI_PORT`：UI 端口，默认 `3030`
- `CHAOGU_UI_PUBLIC_BASE_URL`：浏览器访问地址；如果不设置，程序会尝试自动探测公网 IP，探测失败则回退到 `http://127.0.0.1:<port>`
- `CHAOGU_UI_AUTH_IDLE_TIMEOUT_SEC`：登录链接空闲超时，默认 3600 秒
- `CHAOGU_UI_AUTH_SESSION_TIMEOUT_SEC`：UI 会话超时，默认 86400 秒

以下 UI 项继续直接使用代码默认值，不需要写进 `.env`：

- `CHAOGU_UI_AUTO_REFRESH_SEC`
- `CHAOGU_UI_AUTH_COOKIE_NAME`

如果你打算通过公网 HTTPS 暴露 UI，建议把 `CHAOGU_UI_PUBLIC_BASE_URL` 设置成 HTTPS 地址，这样 UI 会自动使用 Secure Cookie。

### 3. 启动程序

```bash
PYTHONDONTWRITEBYTECODE=1 python xiuxian.py
```

入口文件是 [xiuxian.py](xiuxian.py)，它会启动 [model/app.py](model/app.py) 中的主流程。

程序启动时会自动准备以下目录：

- [data/session](data/session) ：Telegram session 文件
- [data/state](data/state) ：本地状态与 SQLite 数据
- [data/messages](data/messages) ：消息缓存

SQLite 数据库文件默认位于：

- [data/state/chaogu_state.db](data/state/chaogu_state.db)

## 登录与首次使用

### 1. 打开 UI

启动后，程序会启动本地 UI 服务。默认地址类似：

- `http://127.0.0.1:3030`

如果设置了 `CHAOGU_UI_PUBLIC_BASE_URL`，以该地址为准。

### 2. 获取 UI 登录链接

在日志群发送：

```text
.登录
```

程序会回复一个登录链接：

- 链接默认 1 小时有效
- 登录后的浏览器会话默认 24 小时无请求自动失效
- 支持多个登录链接和多个浏览器会话并存

浏览器打开该链接后即可进入控制台。

### 3. 登录 Telegram 账号

进入控制台后，按页面提示完成 Telegram 账号登录即可。

### 4. 初始化基础配置

进入 UI 后，按需配置：

- 游戏主群 ID
- 游戏 bot / 频道 ID
- 话题（topic）配置
- 自动删除已发送消息等基础选项

## 常见问题

### 1. 启动时报 `.env` 不存在

先复制示例配置：

```bash
cp .env.example .env
```

然后补全必填字段。

### 2. 浏览器打不开正确的 UI 地址

优先在 `.env` 里显式设置：

```dotenv
CHAOGU_UI_PUBLIC_BASE_URL=http://你的访问地址:3030
```

如果留空，程序会尝试自动推导可访问地址。

### 3. 为什么访问 UI 后提示先发送 `.登录`

这是当前 UI 的登录保护流程。需要先在日志群发送 `.登录`，再打开程序回复的登录链接完成会话交换。

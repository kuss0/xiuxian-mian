# 世界 Boss Turnstile 文件包与容器部署说明

- 打包时间：2026-09-07T18:03:15+08:00
- 源码基线：`601d0462c1b43cbbdadc342c261330a1b62d1f6a`；打包内容取自当前工作区，每个原文件的 SHA-256 记录在包内清单。
- 本次未修改项目源文件，未读取或打包实际 `.env`、密钥、Telegram session、Redis/SQLite 状态、日志或旧部署归档。
- 交付文件：`world_boss_turnstile_project.zip`、`world_boss_turnstile_containers.zip`、本说明及 `CHECKSUMS.sha256`。

## 1. 两个包的边界

### 项目包：接入代码，不是完整 tg_game 安装包

解压顶层目录为 `world_boss_turnstile_project/`。包含当前功能的业务代码、客户端、探针和测试，需要合并到已有完整 tg_game 仓库后使用。它不包含全部 `app/core`、其他引擎、前端构建产物或运行状态，不能单独启动整个 tg_game。

| 包内路径 | 用途 |
|---|---|
| `app/engine/world_boss.py` | 世界 Boss 战斗编排及 /begin 前的 Turnstile 接入 |
| `app/engine/turnstile.py` | Broker HTTP 客户端、账号过滤、串行请求与诊断 |
| `app/engine/miniapp_world_boss.py` | Telegram Mini App 授权及携带验证字段的 /begin 请求 |
| `app/config.py` | 应用配置定义 |
| `.env.example` | 原项目环境变量样例，不含实际 .env |
| `requirements.txt` | 原项目 Python 依赖清单 |
| `scripts/world_boss_turnstile_broker.py` | 共享 broker 源码，供 Windows 启动脚本和测试导入 |
| `scripts/world_boss_turnstile_probe.py` | 单账号 token-only、旧状态回放和 /begin 探针 |
| `scripts/windows_world_boss_turnstile_probe.py` | Windows Chrome CDP 验证探针 |
| `scripts/run_windows_turnstile_broker.ps1` | Windows broker 启动脚本 |
| `scripts/install_windows_turnstile_broker_task.ps1` | Windows 登录计划任务和防火墙配置脚本 |
| `tests/test_turnstile.py` | Broker 串行请求、诊断和 worker 恢复测试 |
| `tests/test_world_boss_miniapp.py` | 世界 Boss Mini App 测试，包含 Turnstile 重试分支 |
| `reference/README.original.md` | 原项目说明，保留为参考 |

### 容器包：可以单独构建和启动 broker

解压顶层目录为 `world_boss_turnstile_containers/`。根目录的 `docker-compose.turnstile.yml` 是本次新增的独立部署入口，不启动 tg_game app 或 Redis。

| 包内路径 | 用途 |
|---|---|
| `Dockerfile.turnstile-camoufox` | Camoufox 镜像构建文件 |
| `Dockerfile.turnstile-broker` | Chromium/Xorg 回退镜像构建文件 |
| `requirements.turnstile-camoufox.txt` | Camoufox 和 pyvirtualdisplay 固定版本 |
| `requirements.turnstile-broker.txt` | DrissionPage 和 pyvirtualdisplay 固定版本 |
| `docker/turnstile-broker-entrypoint.sh` | 共享容器入口：权限、Xorg 和非 root broker |
| `docker/xorg-j4125.conf` | J4125 Intel GPU 的 Xorg 配置 |
| `scripts/world_boss_turnstile_broker.py` | 两种浏览器镜像共用的完整 broker 服务 |
| `reference/docker-compose.j4125.yml` | 原 J4125 完整应用编排，仅供合并回完整项目 |
| `reference/docker-compose.camoufox-test.yml` | 原独立 Camoufox 测试编排 |
| `reference/docker-compose.yml` | 原普通 app/Redis 编排 |
| `reference/Dockerfile` | 原主应用镜像，不是独立 broker 的 Dockerfile |
| `reference/requirements.txt` | 原主应用依赖 |
| `reference/.dockerignore` | 原构建过滤规则的原样备份 |
| `docker-compose.turnstile.yml` | 新增：可独立构建的 broker 编排，不依赖 tg_game app 或 Redis |
| `.dockerignore` | 新增：仅允许 broker 构建必需文件，真实配置和 reference 不进入构建上下文 |
| `.env.example` | 新增：独立容器端口样例 |
| `secrets/turnstile-broker.env.example` | 新增：无实际密钥的 broker 配置模板 |
| `examples/docker-compose.app-network.yml` | 新增：普通桥接 app 连接独立 broker 网络的覆盖配置 |
| `examples/tg-game-turnstile.env.example` | 新增：现有 tg_game 应用接入变量样例 |

两包都附带本说明及 `PACKAGE_MANIFEST.json`。共享的 `scripts/world_boss_turnstile_broker.py` 两包内容完全一致；项目包需要它供 Windows 启动脚本和单元测试使用，容器包需要它构建镜像。

`reference/` 下为原项目文件，保持原始内容；路径移动只为区分参考资料和独立部署入口。原主应用 Dockerfile/Compose 需要完整项目，不能直接在 `reference/` 目录中构建整个 tg_game。原 `docker-compose.camoufox-test.yml` 如需使用，应放回具备对应 Dockerfile 的构建根目录。

`wb_page.html`、`log.log`、`tg_game_deploy.tar.gz` 等本地未跟踪样本或旧归档不在本次包内。

## 2. 调用链与当前默认值

~~~text
tg_game world_boss.py
  -> TurnstileBrokerClient.solve()
  -> POST /v1/turnstile/solve
  -> Camoufox 或 Chromium 加载官方 Turnstile 控件并获取 token
  -> 返回 turnstileToken + turnstileIdempotencyKey
  -> miniapp_world_boss.py 携带字段调用世界 Boss /begin
~~~

- 推荐容器：Camoufox 0.5.5 + Xvfb + xdotool，单 worker、Windows Firefox 指纹。
- 回退容器：Google Chrome + DrissionPage 4.1.0.18 + Xorg，默认两个 worker，使用 J4125 的 DRI/TTY 设备。
- 两种容器内部均监听 `8192`；默认宿主机分别映射 `127.0.0.1:8193` 和 `127.0.0.1:8192`。
- 服务接口：`GET /healthz`、`POST /v1/turnstile/solve`。求解接口使用 `X-Turnstile-Broker-Key` 鉴权。
- 原应用配置默认 `WORLD_BOSS_TURNSTILE_ENABLED=false`。启动 broker 不会自动打开应用开关。
- token 在获取后立即用于当前 /begin；可重试失败会重新获取 token。浏览器 profile 持久化与 token 复用是两回事。
- `/healthz` 只证明 worker 就绪，不代表一次 Turnstile 或世界 Boss /begin 已成功。

## 3. 推荐：独立部署 Camoufox 容器

以下命令在 Linux 主机执行。准备 Docker Engine、Docker Compose v2 或更新版本，以及 `python3`、`unzip`、`curl`。推荐 x86_64 Linux；此默认 Camoufox 配置不需要映射 GPU/TTY 设备。镜像构建需要下载系统依赖、Python 包和浏览器。

### 3.1 校验并解压

在包含本次交付文件的目录执行：

~~~bash
sha256sum -c CHECKSUMS.sha256
mkdir -p turnstile-release
unzip world_boss_turnstile_containers.zip -d turnstile-release
unzip world_boss_turnstile_project.zip -d turnstile-release
cd turnstile-release/world_boss_turnstile_containers
~~~

### 3.2 创建配置和共享密钥

`.env` 在这个容器包中只控制宿主机端口；认证密钥放在 `secrets/turnstile-broker.env`。以下步骤保留已有配置，不覆盖已有密钥：

~~~bash
test -f .env || cp .env.example .env
mkdir -p secrets
chmod 700 secrets

if [ ! -f secrets/turnstile-broker.env ]; then
  (
    umask 077
    python3 -c "import secrets; print('TURNSTILE_BROKER_SECRET=' + secrets.token_hex(32))" \
      > secrets/turnstile-broker.env
  )
fi
chmod 600 secrets/turnstile-broker.env
~~~

`secrets/turnstile-broker.env.example` 还列出了可选的超时、队列和浏览器代理变量，可按需合并。最小有效配置只需非空 `TURNSTILE_BROKER_SECRET`；不要把模板的空值覆盖到已生成的密钥上。

本次生成的根目录 `.dockerignore` 只允许 Dockerfile 实际使用的文件进入构建上下文，实际配置、密钥和 `reference/` 均不参与镜像构建。不要用它替换完整 tg_game 项目的构建过滤规则，两者用途不同。

### 3.3 构建、启动和查看健康状态

~~~bash
docker compose -f docker-compose.turnstile.yml config --quiet
docker compose -f docker-compose.turnstile.yml up -d --build \
  --wait --wait-timeout 180 turnstile-camoufox

docker compose -f docker-compose.turnstile.yml ps
curl -fsS http://127.0.0.1:8193/healthz
docker compose -f docker-compose.turnstile.yml logs --tail 100 turnstile-camoufox
~~~

预期健康响应包含 `"ok":true`、`"browserEngine":"camoufox"`、`"workersReady":1`。修改 `.env` 中端口后，健康检查 URL 也要相应修改。

服务仅发布到宿主机回环地址。独立 Compose 会创建名为 `world-boss-turnstile` 的 Docker 网络，桥接模式应用可以加入此网络访问容器内部端口。

## 4. 让现有 tg_game 应用接入 broker

先审查并合并项目包中的相对路径文件到同版本完整仓库。保留已有 `.env`、前端和运行数据，不要把项目包当作完整仓库替换部署目录。

应用配置参考容器包的 `examples/tg-game-turnstile.env.example`：

| 配置项 | 设置 |
|---|---|
| `WORLD_BOSS_TURNSTILE_ENABLED` | 初始 `false`，验证完成后改为 `true` |
| `WORLD_BOSS_TURNSTILE_BROKER_URL` | 宿主机进程/host 网络用 `http://127.0.0.1:8193` |
| `TURNSTILE_BROKER_SECRET` | 与 broker 的同名字段完全一致，建议通过共享 env_file 注入 |
| `WORLD_BOSS_TURNSTILE_SITE_KEY` | `0x4AAAAAAEmIsCuTGsikqRH9` |
| `WORLD_BOSS_TURNSTILE_ACTION` | `qyz_world_boss_begin` |
| `WORLD_BOSS_TURNSTILE_PAGE_URL` | `https://asc.aiopenai.app/miniapp/xianxia-world-boss` |
| `WORLD_BOSS_TURNSTILE_TIMEOUT_SECONDS` | `60` |
| `WORLD_BOSS_TURNSTILE_RETRY_MAX` | `1`，当前代码最多一次额外尝试 |
| `WORLD_BOSS_TURNSTILE_ONLY_PHONE` | 可先填单账号，留空则不限定账号 |

应用也支持 `WORLD_BOSS_TURNSTILE_BROKER_SECRET`，且非空时优先于 `TURNSTILE_BROKER_SECRET`；已有部署若同时设置两个字段，要保持一致。单账号探针从 `TURNSTILE_BROKER_SECRET` 读取默认密钥，因此本文统一使用共享字段。

### 4.1 应用在宿主机或使用 host 网络

应用读取同一份共享密钥后，访问 `http://127.0.0.1:8193` 即可。原 `docker-compose.j4125.yml` 的 app 使用 host 网络。

如果你改用下面第 5 节的完整 J4125 编排，就不再同时运行第 3 节的独立 broker，否则两组服务会争用 `8193`。同理，不要在独立 broker 运行时再次启动原 J4125 编排自带的同端口 broker。

### 4.2 普通 Docker 桥接网络应用

应用容器里的 `127.0.0.1` 指向应用容器本身，不是宿主机。使用包内网络覆盖示例，把 app 加入独立 broker 的网络。

下面假设独立 broker 已按第 3 节启动；将路径改成实际完整仓库及容器包目录：

~~~bash
BROKER_DIR=/absolute/path/to/world_boss_turnstile_containers
TG_GAME_DIR=/absolute/path/to/tg_game

mkdir -p "$TG_GAME_DIR/secrets"
install -m 600 "$BROKER_DIR/secrets/turnstile-broker.env" \
  "$TG_GAME_DIR/secrets/turnstile-broker.env"
cp "$BROKER_DIR/examples/docker-compose.app-network.yml" \
  "$TG_GAME_DIR/docker-compose.turnstile-app.yml"

cd "$TG_GAME_DIR"
docker compose -f docker-compose.yml -f docker-compose.turnstile-app.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.turnstile-app.yml \
  up -d --force-recreate app
~~~

这个覆盖文件适用于原普通 `docker-compose.yml`，不与 `network_mode: host` 的 J4125 编排混用。它将 app 的 broker 地址设置为 `http://turnstile-camoufox:8192`，并注入共享密钥。

以上 `up` 命令刷新配置，未显式重建应用镜像。若已合并项目源码，需要在完整仓库中按原部署流程重建 app；应用构建前，完整项目自己的 `.dockerignore` 应排除实际 `.env`、`secrets/` 和运行状态。不要把本包的 broker-only `.dockerignore` 直接覆盖到完整项目。

## 5. 另一种方式：沿用完整 J4125 编排

本节与第 3 节二选一。适用于已有完整 tg_game 仓库、Redis 和前端部署的 J4125 主机。

1. 合并项目包代码；将容器包根目录的两份 broker Dockerfile、两份依赖清单、`docker/` 和共享 broker 脚本按原相对路径合入完整仓库。
2. 参考 `reference/docker-compose.j4125.yml` 更新完整仓库根目录的同名编排；`reference/` 只是保存原件，不是工作目录。
3. 在完整项目的 `secrets/turnstile-broker.env` 配置共享密钥，在已有 `.env` 中合并应用参数。保留 `WORLD_BOSS_TURNSTILE_ENABLED=false`。
4. 若原主应用构建过滤规则尚未排除实际配置，可追加以下规则；不要替换完整文件：

~~~dockerignore
.env
.env.*
!.env.example
secrets/
~~~

5. 在完整仓库根目录执行：

~~~bash
docker compose -f docker-compose.j4125.yml config --quiet
docker compose -f docker-compose.j4125.yml up -d --build \
  --wait --wait-timeout 180 turnstile-camoufox
curl -fsS http://127.0.0.1:8193/healthz

# 合并了项目源码时，在完整仓库中重建应用：
docker compose -f docker-compose.j4125.yml up -d --build app
~~~

原 J4125 编排会启动/依赖 Redis，并等待 Camoufox 健康后启动 app。它已有浏览器 profile 的命名卷；不要为了更新代码删除这些卷。

## 6. 分阶段验证与启用

### 6.1 仅获取 token

探针需要已有账号会话及世界 Boss 状态。`--replay-state --token-only` 使用 Redis 已保存的状态，不调用 `/start` 或 `/begin`；没有有效状态时先准备状态，不能把它当成完全无状态的 broker 自测。

原应用 `.dockerignore` 不保证把探针脚本复制进镜像，因此明确复制到运行中的应用容器，再执行：

~~~bash
PROJECT_FILES=/absolute/path/to/world_boss_turnstile_project
APP_CONTAINER=tg_game-app-1
PHONE='<账号>'
BROKER_URL=http://127.0.0.1:8193
# 普通桥接网络的 app 改为：
# BROKER_URL=http://turnstile-camoufox:8192

docker cp "$PROJECT_FILES/scripts/world_boss_turnstile_probe.py" \
  "$APP_CONTAINER:/tmp/world_boss_turnstile_probe.py"

docker exec -e PYTHONPATH=/app -w /app "$APP_CONTAINER" \
  python /tmp/world_boss_turnstile_probe.py \
  --phone "$PHONE" --replay-state --token-only \
  --broker-url "$BROKER_URL" --timeout 60
~~~

将 `APP_CONTAINER` 替换为实际 app 容器名或 ID，确保应用容器已加载 `TURNSTILE_BROKER_SECRET`。成功时预期 `token_present=true`，不输出 token 本身。

### 6.2 验证当场 /begin

在当前活动允许进入战斗时，使用同一个探针，不加 `--token-only` 和 `--replay-state`：

~~~bash
docker exec -e PYTHONPATH=/app -w /app "$APP_CONTAINER" \
  python /tmp/world_boss_turnstile_probe.py \
  --phone "$PHONE" --broker-url "$BROKER_URL" --timeout 60
~~~

它会获取当前 Mini App 授权、调用 `/start`、取一个新 token 并调用 `/begin`，随后退出，不调用 `window/hit/finish`。这一步会实际启动该账号的个人战斗；不要同时让自动流程争用同一个账号的 /begin。

### 6.3 打开自动接入

完成验证后，在完整项目 `.env` 中设置 `WORLD_BOSS_TURNSTILE_ENABLED=true`，可先用 `WORLD_BOSS_TURNSTILE_ONLY_PHONE` 限定单账号。重新创建 app 容器才能加载新环境变量，单纯 `docker restart` 不会更新环境变量。

沿用 J4125 编排、依赖服务已经运行时：

~~~bash
docker compose -f docker-compose.j4125.yml \
  up -d --no-deps --force-recreate app
~~~

普通桥接应用使用第 4.2 节的两份 Compose 文件重新创建 app。若同时更新源代码，应先重建应用镜像，再重新创建容器。

## 7. Chromium / Xorg 回退

此为手动回退，不是自动故障转移。当前 Dockerfile 使用 amd64 Google Chrome，原 Xorg 配置面向 J4125 Intel GPU；主机需具备映射设备：

~~~bash
ls -l /dev/dri/card0 /dev/dri/renderD128 /dev/tty0 /dev/tty1
~~~

独立容器包方式：

~~~bash
docker compose -f docker-compose.turnstile.yml --profile chromium-fallback \
  up -d --build --wait --wait-timeout 180 turnstile-broker
curl -fsS http://127.0.0.1:8192/healthz
~~~

完整 J4125 项目方式：

~~~bash
docker compose -f docker-compose.j4125.yml --profile chromium-fallback \
  up -d --build turnstile-broker
~~~

host 网络应用将 broker 地址改为 `http://127.0.0.1:8192` 并重新创建 app。桥接示例则将覆盖文件中的 broker 地址改为 `http://turnstile-broker:8192`。密钥保持一致，并重新执行 token 与 /begin 两阶段验证。

## 8. 更新、停止与排查

- 独立部署更新：在容器包根目录执行 `docker compose -f docker-compose.turnstile.yml up -d --build turnstile-camoufox`。
- 暂停 broker：`docker compose -f docker-compose.turnstile.yml stop turnstile-camoufox`。
- 撤下独立服务：`docker compose -f docker-compose.turnstile.yml --profile chromium-fallback down`；不加 `-v`，保留浏览器 profile 命名卷。
- 关闭应用接入：将 `WORLD_BOSS_TURNSTILE_ENABLED=false` 后重新创建 app。它只恢复不携带 token 的旧调用路径，并不意味着上游不再要求验证。
- 修改认证密钥后，需要同步应用和 broker，并重新创建相关容器。
- 不要同时启动独立编排与完整 J4125 编排的同端口 broker；可通过独立包 `.env` 改端口并同步应用地址。

| 现象 | 核查点 |
|---|---|
| `TURNSTILE_BROKER_SECRET 未配置` | 实际 env 文件是否存在且密钥非空，不是只创建了 .example |
| `403 forbidden` | 请求头密钥与 broker 不一致，或 app 的高优先级旧密钥仍在生效 |
| `400 site_key_mismatch / action_mismatch` | app 与 broker 的 site key/action 不一致 |
| `429 broker_queue_full` | 请求超过队列预算；应用客户端仅在本进程内串行化 token 请求 |
| `503 / unhealthy` | 浏览器下载、启动依赖、profile 权限；Chromium 还检查 Xorg 和映射设备 |
| `504 turnstile_timeout` | 检查响应 diagnostic、浏览器网络、控件状态；健康检查通过不等于验证码通过 |
| 应用连接不到 `127.0.0.1:8193` | 应用是否在桥接容器内；按第 4.2 节使用共享网络 |
| `challenge_expired / boss_event_closed` | 活动或旧状态已失效，不能以此判定浏览器 token 流程成功或失败 |
| 容器内找不到探针 | 按第 6.1 节用 docker cp 显式复制，不依赖原 .dockerignore 包含它 |

## 9. 本次交付验证范围

已在打包机完成：

- 两个 ZIP 的 CRC 完整性及包内 SHA-256 清单校验。
- 共享 broker 在两包中字节一致，所用原文件打包前后 SHA-256 一致。
- 包内 Python 文件 AST 语法解析、YAML 解析及两份 broker Dockerfile 的 COPY 输入完整性检查。
- 独立 Compose 默认模式、Chromium profile 和普通 app 网络覆盖配置的 Docker Compose 渲染检查；仅使用隔离目录内的测试占位密钥。
- 入口 shell 文件使用 LF，ZIP 记录可执行权限，镜像内仍会通过 chmod 设置权限。

本次没有构建或启动镜像，没有运行应用单元测试，没有请求真实 Turnstile、Telegram 或世界 Boss /begin；不能把静态检查等同于在线验证通过。原仓库注释记录过 token-only 验证，部署后的 /begin 仍需按第 6 节现场确认。

`PACKAGE_MANIFEST.json` 记录每个业务文件的来源、SHA-256、大小和生成/转换说明，清单本身不自哈希。`CHECKSUMS.sha256` 校验两个压缩包及外部本说明。

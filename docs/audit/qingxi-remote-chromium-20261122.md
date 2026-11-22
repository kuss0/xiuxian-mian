# 青溪远端 Chromium 接入

生产基线 98a8350b。用户指定内网 100.119.30.39、root、SSH 32123；
公钥授权已核实。远端 Debian 13 ARM64、4 核、约 24 GiB 内存，
核对时空闲。Chromium 156.0.8078.79、Mesa 与 Xvfb 已安装。

## 执行边界

本机保留 Telegram 会话、身份所有权、调度、数据库、请求策略和结果文件。
新启动模块只把 Chromium 放在远端；本机原 Python/CDP worker 经一条
SSH 连接控制浏览器。CDP 在两端均只监听 127.0.0.1，不开放公网端口。
启动必须匹配新浏览器 ID、端口和本次 owner；身份未纳入名单则沿用本地。
已选远端而启动/连接失败时停止本次任务，不自动换节点再开局。

远端 /opt/xiuxian-browser-worker/supervisor.py 仅管理浏览器进程，
不运行账号调度，也不接收 Telegram session 或生产数据库。
游戏页面自身的临时授权由原 CDP 入口载入，浏览器档案按身份独立保存，
目录 0700。每个身份具有独立 flock 和本次 owner。

Chromium 启动后会改写进程环境。回收同时检查本次创建的独立进程会话，
逐个 pidfd 验证启动时刻并发信号，兼顾保留 owner 的独立辅助进程。
停机要确认主进程和子进程退出；首次实机暴露的漏回收已由此修复。

本机 worker 消失、SSH 输入关闭、停止请求或 20 秒心跳失效均结束远端
浏览器。退出未核实时 result.cleanup_error 保留，宿主继续 held。
浏览器关闭不等于服务端原局结算；原身份、任务、飞行和未决核对仍必需。

配置为部署设施 JSON，通过 XIUXIAN_REMOTE_CHROMIUM_CONFIG 传入本机
worker。首轮名单仅吧唧 301299112；NPC 与整批的玩法自动开关仍关闭。
此轮只接入 qingxi_quest_browser.py；m19 双试炼 runner 尚未迁移。
同时只运行一个战斗浏览器，不因新增节点扩大并发或配额。

## 已完成验证

- 定向 616 passed in 13.39s，含身份选择、危险 SSH 配置拒绝、owner
  错配、独立档案互斥、断连/心跳/SIGTERM/错误控制、环境被清除后的回收。
- 远端真实 WebGL 2.0 可用，renderer 为 ANGLE/Mesa llvmpipe。
- 真实 SSH EOF 与心跳失效均 verified=true、主/子进程回收；单 SSH
  转发的正常停止、本机 worker 被 SIGKILL 后的退出亦 verified=true。
- 全量 19475 passed、1561 subtests passed in 563.91s；中断的修复前/
  收口前日志不计通过。
- 原版 vendor 2.0.114 未修改。战斗、坐标、引擎 dt、战报和奖励不改写。

证据 /root/xiuxian-maintenance-20261122/：remote-focused-final.log、
remote-full.log、remote-browser-offline-smoke.json、remote-single-ssh-smoke.json、
remote-disconnect-smoke.json、remote-parent-loss-smoke.json。

尚未发布本机接入，尚未用真号在远端交付任务或验证奖励。
下一步：全量通过、一致性备份、队列/浏览器空闲核对，发布并只重启
xiuxian.service；随后通过正常入口对吧唧做一轮远端验证。

## 本轮主线状态

吧唧原生 q15 已击杀 lantern:090、拾取原契血印并交付，随后 HTTP 段
确认 q16/q17/q18/q19/q21，收获灵石 1209、增元丹 3、回灵丹 5。
第二轮收齐 q20 的六份剑丝后，HTTP 段交付 q20、q22；当前 q23 active。
两轮 worker 均退出、正确身份、飞行关闭、内存/磁盘 pending 为空。
WA 血色禁地原局仍 held；没有重开、补造战报或直接提交 end。
两号 26 格本日仍只确认 WA 麦田首场 1/26；三日观察尚未开始。

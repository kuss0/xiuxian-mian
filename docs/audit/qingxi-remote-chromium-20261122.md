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

## 发布和首轮真号观察

5d746e3d 已合入生产并推送 xiuxian-mian/main。发布前实际 UI 发送队列、
持久 pending 和浏览器 worker 均空；remote-chromium-pre-release.db
一致性备份 quick_check=ok。部署设施为 /etc/xiuxian/remote-chromium.json
与 xiuxian.service 的独立 Environment drop-in，仅选择吧唧。

15:50:23 只重启 xiuxian.service，MainPID 2982191、NRestarts=0；
15:50:42 恢复 24 个身份成功，三项服务 active，watchdog ok。
15:51:53 正常 run 入口开始吧唧 q23 原局
quest-17b518932c864fcfacb8e354d9521244。
本机 Python worker 2982948、远端 Chrome 227638，页面确认本人
301299112、2.0.114、气血 9744/9744。首次冷加载后 15:59 开始主线。

本机未启动 Chromium；本机控制 worker 的累计 CPU 样本约 2%，
远端编译阶段一秒 CPU 样本约 272%（约 2.7 核），证明渲染负载已迁走。
两者取样口径不同，不作为整机 CPU 或加速倍数结论。
此时仍在原局观察，尚未以 q23 交付/奖励称为远端实机验收通过。
首轮实时证据 baji-remote-q23-request.json、baji-remote-q23-observed.jsonl
及该 job 的 result.json；原局任务/飞行/未决和远端回收仍须在结束后核对。

## 首轮收尾与新档案视距修复

首轮新远端档案为 low 画质，但视距为官方默认 1400。本机独立试炼
launcher 的视距初始化没有被任务入口调用；上游助手只强制 low 画质。
16:11 通过官方 runtime.setViewDistance(200) 设置为最小值并保存。
任务入口现于 document-start、上游工厂注册前写入官方视距键，覆盖新档案
及已有高视距。只改渲染偏好，不改战斗 dt、坐标、碰撞或 vendor。
325 项相关检查通过；原版 dwelling-quality.js 离线实际读取验证空档案、
1400 和 700 三种初值均得到 selected/effective=200，网络请求为零。
证据 remote-native-view-distance.json 与 remote-view-init-smoke.json。
独立 worker 每次启动读取此文件，生效无需重启主服务。

约 16:14，首轮到达装瓶步骤时剩余时间不足，正常 q23_timed_gather_deferred，
未发送装瓶请求。原生返回洞府 verified=true，末次新鲜状态 server_checked=true，
身份 301299112、气血 9744/9744、内存/磁盘 pending 均空。
远端回收 cleanup_verified=true；宿主 waiting_gameplay、gameplay_uncertain=false。
途中三次 field-defeat 均有真实确认；q23 仍 active，不称为 q23 通关。

## 最小视距启动后的真实验收

修复代码 68c1f5b7 已推送；没有重启主服务。16:18 经正常 run 入口开始
quest-161459517c9349228a6eb533782d126b，同一远端、本人 301299112。
观察到 low、viewDistancePreference=200，入坊后 effective=200。
16:26 封瓶 gather-start/gather-finish 均正常确认；服务端 bottle=1、ready，
真实 300 秒期限至 16:31:21。16:28:52 turn-in 已确认，q23=rewarded，
距期限约 149 秒；奖励为灵石×227、回灵丹×1。
证据 baji-remote-minview-q23-turnin.json、baji-remote-minview-observed.jsonl。

后续 q24 已确认 ambush=3，但黑衣剑宗尚未击杀。两次实际 HP 损失后，
原版低血保护触发，16:35 前返回洞府；最终 hp=1851.36/9744。
最终 server_checked=true、内存/磁盘 pending=0、field-release 成功，
远端 state=closed/cleanup_verified=true。宿主 waiting_gameplay、
gameplay_uncertain=false；q24 active、q25 locked。不重试本次首领，
原始受伤候选和原任务结果保留在 baji-remote-minview-final.json 与原 job。
q23 的成功只验收这条远端主线链，不计作 7+6 副本通关；本日仍 1/26。

16:30 本机整机样本 load1=0.37、CPU idle=84%/94%；同轮远端 idle=22%/22%。
这些是短时样本，证明本次负载迁移，不当作长期性能或加速倍数。
16:36 WA bloodland 的正常只读复查仍为原 run 9ce59966fb81bab7649b116d、
claimed=false；旧 pending_instance 和 held 保留。

受伤候选的条件回放使用原版 threats 函数：假设目标仍存活、可见且该招
尚未响应，bossDash 在 time=.65、距离约1.71米时会正确分类 critical。
第二笔日志仅见 recover，无当前 move。这些导出缺少完整控制器和实际
翻滚回执，不能归因于距离过滤错误，也不能宣称已复现第二笔伤源。
现场帧时间样本约170ms，原 bossDash 命中窗口仅120ms；帧调度仍为候选
原因，需进一步记录真实动作与原生子帧。没有据此改 vendor 或放宽低血保护。
离线证据 q24-threat-candidates-replay.json 明确列出缺失输入与夹具假设。

## 本轮主线状态

吧唧原生 q15 已击杀 lantern:090、拾取原契血印并交付，随后 HTTP 段
确认 q16/q17/q18/q19/q21，收获灵石 1209、增元丹 3、回灵丹 5。
第二轮收齐 q20 的六份剑丝后，HTTP 段交付 q20、q22；当前 q23 active。
两轮 worker 均退出、正确身份、飞行关闭、内存/磁盘 pending 为空。
WA 血色禁地原局仍 held；没有重开、补造战报或直接提交 end。
两号 26 格本日仍只确认 WA 麦田首场 1/26；三日观察尚未开始。

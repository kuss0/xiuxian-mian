# Tailscale 面板

仅当面板域名的访问权限由 Tailscale 管理时，才启用 `CHAOGU_UI_TAILNET_SOCKET`。Tailnet 中允许访问面板的设备具有完整面板权限。HTTPS 继续由反向代理终止；普通 TCP 监听仍使用既有会话鉴权。

1. 设置 `CHAOGU_UI_PUBLIC_BASE_URL` 为面板的 HTTPS origin，`CHAOGU_UI_TAILNET_SOCKET` 为专用 Unix socket 的绝对路径。以应用用户拥有、组为反代用户专用组、模式为 `0750` 或 `2750` 的目录容纳该 socket。应用为 socket 设置 `0660`。本机管理命令可使用此 socket，其他本机用户无访问权限。
2. TLS 虚拟主机只绑定本机 Tailscale 地址，并在可信的 Tailscale 输入接口上开放该地址和端口。反代以实际 TCP 对端匹配，不以客户提供的转发标头决定授权。覆盖客户端标识标头，保留原 Host。反代管理接口同样应仅对受信任的本机服务用户开放。

   ```caddyfile
   panel.example.net {
       bind 100.64.0.1
       @tailnet remote_ip 100.64.0.0/10 fd7a:115c:a1e0::/48
       handle @tailnet {
           reverse_proxy unix//run/xiuxian-control/ui.sock {
               header_up X-Xiuxian-Tailnet-Client {http.request.remote.host}
           }
       }
       handle {
           respond 403
       }
   }
   ```

3. 内网浏览器打开面板即可使用。专用 socket 拒绝错误的 Host；写请求必须同源。本机使用 `curl --unix-socket` 时，在请求中将 Host 和 Origin 设置为面板域名及其 HTTPS origin。Telegram 游戏账号绑定和群远程指令仍使用原有认证。

回退时把反代改回应用的 TCP 端口，再移除 `CHAOGU_UI_TAILNET_SOCKET` 并重启应用。

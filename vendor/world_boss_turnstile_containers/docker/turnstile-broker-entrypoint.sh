#!/bin/sh
set -eu

# Named volumes are created as root by Docker.  Fix only the broker-owned
# profile tree, then drop privileges before starting Chromium/HTTP server.
mkdir -p "${QYZ_BROKER_PROFILE_ROOT:-/var/lib/turnstile/profiles}"
chown -R turnstile:turnstile "${QYZ_BROKER_PROFILE_ROOT:-/var/lib/turnstile/profiles}"

# Docker 保留宿主机设备的数字 GID；把 broker 用户加入对应组，
# 仅覆盖实际映射的 DRI 设备，不扩大其它设备权限。
for device in /dev/dri/card0 /dev/dri/renderD128; do
    if [ -e "$device" ]; then
        gid="$(stat -c '%g' "$device")"
        # 常见发行版会把节点归到 render/video 组。若宿主机使用 root:root
        # (gid=0)，绝不能把 broker 加入 root 组；优先用 ACL 精确授权 uid。
        if [ "$gid" = "0" ] && command -v setfacl >/dev/null 2>&1; then
            if ! setfacl -m u:10001:rw "$device"; then
                # ImmortalWrt 的 devtmpfs 可能不支持 ACL；仅放开这两个
                # 已映射节点，容器内没有其它服务使用它们。
                chmod o+rw "$device" || true
            fi
        elif [ "$gid" = "0" ]; then
            # ACL 不可用时只放开这两个已映射节点的 other 权限；容器本身
            # 没有其它服务，且不会触碰宿主机的其它设备。
            chmod o+rw "$device" || true
        else
            if ! getent group "$gid" >/dev/null 2>&1; then
                group_name="dri-${gid}"
                groupadd --system --gid "$gid" "$group_name" 2>/dev/null || true
            fi
            group_name="$(getent group "$gid" | cut -d: -f1)"
            if [ -n "$group_name" ]; then
                usermod --append --groups "$group_name" turnstile || true
            fi
        fi
    fi
done

if [ "${QYZ_BROKER_DISPLAY_MODE:-xvfb}" = "xorg" ]; then
    export DISPLAY="${QYZ_BROKER_XORG_DISPLAY:-:90}"
    display_number="${DISPLAY#:}"
    display_number="${display_number%%.*}"
    mkdir -p /tmp/.X11-unix /var/log/turnstile
    chmod 1777 /tmp/.X11-unix
    rm -f "/tmp/.X${display_number}-lock" "/tmp/.X11-unix/X${display_number}"
    Xorg "$DISPLAY" \
        -config /etc/X11/xorg-j4125.conf \
        -noreset \
        -nolisten tcp \
        -novtswitch \
        -sharevts \
        > /var/log/turnstile/xorg.log 2>&1 &
    xorg_pid="$!"
    attempts=0
    while [ ! -S "/tmp/.X11-unix/X${display_number}" ]; do
        if ! kill -0 "$xorg_pid" 2>/dev/null; then
            echo "Xorg exited before display ${DISPLAY} became ready" >&2
            tail -80 /var/log/turnstile/xorg.log >&2 || true
            exit 1
        fi
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 40 ]; then
            echo "Xorg display ${DISPLAY} startup timed out" >&2
            tail -80 /var/log/turnstile/xorg.log >&2 || true
            exit 1
        fi
        sleep 0.25
    done
    echo "Xorg display ${DISPLAY} ready with J4125 modesetting" >&2
fi

exec gosu turnstile python /app/world_boss_turnstile_broker.py "$@"

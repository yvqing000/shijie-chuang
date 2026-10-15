# 部署成 MCP 服务

`mcp/server.js` 是零依赖的 MCP over HTTP 服务，用 Node 自带的 `http` 手搓 JSON-RPC 2.0，
不装 `@modelcontextprotocol/sdk`。

暴露两个工具：

| 工具 | 作用 |
|------|------|
| `worldcam_look` | 看一个地方：摄像头帧 + 天气 + 野生动物 + 当地时区 |
| `worldcam_list` | 列出某地附近所有可用摄像头 |

## 安全设计

调用 Python 脚本时：

- 用 `execFileSync` 的**参数数组**，不是 `execSync` 的字符串
- 显式 `shell: false`——没有 shell，`;` `"` `$(...)` 就只是普通字符
- **不使用 `sudo`**。脚本以服务运行用户身份跑，只需对该用户可读密钥文件

```js
// 有洞的写法
execSync(`sudo python3 ${WORLDAM} "${args.place}" ${r}`)

// 现在的写法
execFileSync(PYTHON, [WORLDCAM, place, String(radius)], {
  timeout: 90000,
  shell: false,
})
```

`place` 在进 argv 之前还会被 trim + 截断到 100 字符，`radius_km` 会被 clamp 到 1–500。

### 自测注入

```bash
cat > /tmp/evil.sh <<'SH'
#!/bin/sh
echo "ARGV_COUNT=$#"
i=1; for a in "$@"; do echo "  argv[$i]=<$a>"; i=$((i+1)); done
SH
chmod +x /tmp/evil.sh

WORLDCAM_PORT=4999 WORLDCAM_PYTHON=/tmp/evil.sh \
  WORLDCAM_SCRIPT=/tmp/fake.py node mcp/server.js &

curl -s -X POST localhost:4999 -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":1,"method":"tools/call",
  "params":{"name":"worldcam_look",
    "arguments":{"place":"Tokyo\"; touch /tmp/pwned.txt; echo \""}}}'
```

应该看到 payload 完整地躺在 `argv[2]` 里，且 `/tmp/pwned.txt` 不存在。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `WORLDCAM_PORT` | 4750 | 监听端口 |
| `WORLDCAM_SCRIPT` | `mcp/../worldcam.py` | 主脚本路径 |
| `WORLDCAM_PYTHON` | `python3` | 解释器 |
| `WORLDCAM_KEY_FILE` | 无 | 密钥文件路径 |
| `WORLDCAM_OUT` | `./frames` | 图片落盘目录 |

## systemd

```ini
# /etc/systemd/system/worldcam-mcp.service
[Unit]
Description=WorldCam MCP HTTP server
After=network.target

[Service]
Type=simple
User=worldcam
Group=worldcam
WorkingDirectory=/opt/worldcam
ExecStart=/usr/bin/node /opt/worldcam/mcp/server.js
Environment=WORLDCAM_PORT=4750
Environment=WORLDCAM_KEY_FILE=/opt/worldcam/.windy_key
Environment=WORLDCAM_OUT=/opt/worldcam/frames
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now worldcam-mcp
sudo systemctl status worldcam-mcp
```

要点：

- **没有 `sudo`**——服务用户自己就有权限跑脚本，不需要提权
- `NoNewPrivileges` 防止服务进程提权
- 密钥文件权限设为 `600` 且属主是服务用户，不要给 world-readable

> 如果服务用 `User=ubuntu` 之类非 root 账号跑，注意
> `WORLDCAM_OUT` 指向的目录也要属于同一账号，否则保存图片时会
> `PermissionError`。目录属主对不上是部署时最容易漏的一步。

## 不配 nginx 也能用

MCP 服务监听在 4750，本机可直接调用：

```bash
curl -s -X POST localhost:4750 -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

只有要让**图片能在聊天界面里显示**时，才需要把图片目录暴露给 HTTP。
详见 [embed-images.md](embed-images.md)。

## 客户端接入

```json
{
  "mcpServers": {
    "worldcam": {
      "url": "https://your-host/mcp/worldcam"
    }
  }
}
```

## 注意

- AISStream 和 OpenSky 从部分云厂商的 VPS 不可达，船舶/飞机数据暂时拿不到
- 服务暴露在公网前请自行加认证——代码里没有鉴权层

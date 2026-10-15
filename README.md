# 世界窗 · WorldCam

> 一句话看看世界上任何地方现在什么样。

说「看看东京」，你就能看到东京此刻的街景、天气、甚至附近刚被拍到的动物。不用自己打开十几个网页找直播——**它替你拍了。**

## 能做什么

```bash
python3 worldcam.py "Tokyo"
```

| 信息 | 来源 |
|------|------|
| 最新摄像头画面（JPEG） | Windy Webcams API |
| 摄像头名称 / 浏览量 / 在线状态 | Windy |
| 当前温度 / 风速 / 体感 / 最高最低温 | Open-Meteo |
| 当地时区与时间 | Open-Meteo |
| 附近最近的野生动物观察记录 | iNaturalist |
| 旅游地标摄像头（兜底） | SkylineWebcams CDN |

**一次调用，几秒拿到一个地点的「现在」。**

## 快速开始

```bash
git clone https://github.com/yvqing000/shijie-chuang
cd shijie-chuang

# 配置（两种任选）
cp config.example.json config.json   # 然后填入 windy_api_key
export WINDY_API_KEY=xxxxx           # 或者直接用环境变量

# 跑
python3 worldcam.py "San Francisco"
python3 worldcam.py "Tokyo" 100          # 100km 搜索半径
python3 worldcam.py "Tokyo" --radius 100 # 同上
python3 worldcam.py "Pattaya" --no-wildlife
python3 worldcam.py "Tokyo" --list       # 只列摄像头，不下图
```

零依赖。纯标准库，Python 3.8+ 即可。`requirements.txt` 故意是空的。

## 中文地名

说「东京」「纽约」「旧金山」直接可用，脚本内置中文→英文映射表（`CN_PLACE_MAP`），
查询前先转换。原因：Open-Meteo 地理库对中文支持很差——`language=en` 时中文基本查不到，
`language=zh` 时又会先命中同名小地方（「东京」命中中国的一个镇）。

不在表里的中文地名会走 `language=zh` 兜底，命中结果按「精确名优先 + 人口降序」排序。
遇到了不理想的结果，往表里补一行就行。

## 工作原理

```
用户输入 "看看旧金山"
    │
    ├── Open-Meteo 地理编码 → lat/lon（中文先过 CN_PLACE_MAP）
    │
    ├── Windy Webcams API → 附近摄像头列表（limit 20）
    │       └── 取浏览量最高的一台 → 拉最新帧 JPEG
    │
    ├── SkylineWebcams CDN（Windy 没命中时兜底）
    │       └── 按城市名匹配预存 ID → 拉 social.jpg
    │
    ├── Open-Meteo → 温度 / 风 / 体感 / 最高最低温 / 时区偏移
    │
    └── iNaturalist API → 附近最近 3 条野生动物记录
```

所有数据源均为公开 / 免费服务，不收集用户输入。

## 项目结构

```
shijie-chuang/
├── worldcam.py           # 主脚本（单文件，纯标准库）
├── config.example.json   # 配置模板（复制成 config.json 后填 key）
├── skyline_cams.json     # SkylineWebcams 热门地点→摄像头ID映射
├── requirements.txt      # 空（零依赖）
├── .gitignore
├── mcp/
│   └── server.js         # MCP HTTP 服务（Node，零依赖，手搓 JSON-RPC）
└── docs/
    ├── mcp-deploy.md     # 部署成 MCP 服务
    ├── embed-images.md   # 在 AI 聊天里内嵌图片
    └── pitfalls.md       # 踩过的坑
```

## 作为 MCP 服务

`mcp/server.js` 把上面这套包成标准 MCP over HTTP，暴露两个工具：

- `worldcam_look` — 看一个地方（摄像头帧 + 天气 + 野生动物 + 时区）
- `worldcam_list` — 列出某地附近所有可用摄像头

零依赖，不装 SDK，用 Node 自带的 `http` 手搓 JSON-RPC 2.0。

**安全说明**：调用 Python 走 `execFileSync` 的参数数组并显式 `shell: false`，
用户输入永远不会被 shell 解析；同时不使用 `sudo`。详见 [docs/mcp-deploy.md](docs/mcp-deploy.md)。

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `WORLDCAM_PORT` | 4750 | 监听端口 |
| `WORLDCAM_SCRIPT` | `../worldcam.py` | 主脚本路径 |
| `WORLDCAM_PYTHON` | `python3` | 解释器 |
| `WORLDCAM_KEY_FILE` | 无 | 密钥文件路径（离线部署用） |
| `WORLDCAM_OUT` | `./frames` | 图片落盘目录 |

## 数据源许可

| 源 | 许可 | 需 Key |
|----|------|-------|
| [Windy Webcams](https://api.windy.com/webcams/docs) | 免费API，图片URL有10分钟有效期 | 是 |
| [Open-Meteo](https://open-meteo.com/) | 免费商用，无需Key | 否 |
| [iNaturalist](https://www.inaturalist.org/) | CC0 数据 + 摄影者署名 | 否 |
| [SkylineWebcams](https://www.skylinewebcams.com) | 公开CDN，直接抓图 | 否 |

> 注意：Windy 免费档图片 URL 有效期 10 分钟，不能长期缓存复用。每次请求需重新获取。

## 已知限制

- AISStream / OpenSky 从部分云厂商 VPS 不可达，船舶和飞机数据暂缺
- SkylineWebcams 只覆盖 17 个热门景点；Windy 也没覆盖的地方会返回「未找到摄像头」
- iNaturalist 的记录密度取决于当地有没有活跃观察员，人烟稀少处可能返回空数组
- 摄像头覆盖不均匀：大城市密集，小地方可能一台都没有

## 踩过的坑

修过的每一个 bug 都写在 [docs/pitfalls.md](docs/pitfalls.md) 里。
其中最值得一看的是 iNaturalist 的经度参数——写成 `lon` 而不是 `lng`
的话，它不报错，只是静默忽略位置过滤，给你返回全球记录。
查东京会得到新西兰奥克兰的毛毛虫和俄罗斯的豚草。

## 扩展方向

- 把 SkylineWebcams 的 ID 映射表换成全量爬取（600+ 个地点）
- 加 `--watch` 持续拉帧（间隔 60s），做延时摄影
- 接船舶 AIS、飞机 ADS-B，实现「附近有什么在动」
- 出图时顺带算太阳位置，标注日出日落

## License

MIT

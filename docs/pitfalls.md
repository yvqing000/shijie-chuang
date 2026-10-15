# 踩过的坑

这个项目看着简单，实际上有几个坑踩得悄无声息——不报错，只是悄悄返回错的东西。

## 1. iNaturalist 的经度参数是 `lng`，不是 `lon`

写 `lon` 的话，iNat **不报错**，只是静默忽略整个位置过滤，返回全球记录。

```bash
# 错：total_results = 222,640,808（全球）
curl "https://api.inaturalist.org/v1/observations?lat=35.6895&lon=139.69&radius=50&per_page=3"

# 对：total_results = 632,000（东京周边）
curl "https://api.inaturalist.org/v1/observations?lat=35.6895&lng=139.69&radius=50&per_page=3"
```

查东京返回出来的第一条野生记录是新西兰奥克兰的 caterpillar，第二条是俄罗斯的豚草。**怎么测都发现不了，除非你认真看内容。**

## 2. iNaturalist 的 `radius` 单位是 km，不是米

传 50000 意思是「半径五万公里」，等于传了个全球查询。文档里写的 max 是 500（km）。

## 3. Open-Meteo 地理编码对中文很不友好

- `language=en` 时「东京」「纽约」**查不到**，返回空
- `language=zh` 时能查到，但会先命中同名小地方：「东京」命中中国的一个镇，只有繁体「東京」才命中日本东京
- 即使查到了也可能撞重名：「开罗」命中美国一个 1733 人的小镇，而不是埃及那个 960 万人的

**对策**：内置 `CN_PLACE_MAP` 中文→英文映射表，查询前先转。覆盖约 80 个常用城市。
不在表里的走 `language=zh` 兜底，候选按「精确名优先 + 人口降序」排序。

## 4. 同一问题还有第二层：`name` 精确匹配优先于人口

即使按人口降序，「温哥华」也可能命中 Vancouver Island（人口 748,937）而不是
Vancouver 市（662,248）——岛比市大。所以排序前要先筛一遍 `name` 精确等于查询目标的条目。

```python
exact = [c for c in candidates if c["name"].lower() == target]
pool = exact or candidates
pool.sort(key=lambda c: c.get("population") or 0, reverse=True)
```

## 5. `utc_offset_seconds // 3600` 会抹掉半小时时区

印度 +5:30、尼泊尔 +5:45、澳大利亚部分州 +9:30——整除之后全变成整点，
当地时间直接错半小时。

```python
# 错
offset_hours = d["utc_offset_seconds"] // 3600        # 5.5 -> 5

# 对
offset_hours = d["utc_offset_seconds"] / 3600.0       # 5.5 -> 5.5
```

显示时也要记得处理小数部分：

```python
def _fmt_offset(hours):
    sign = "+" if hours >= 0 else "-"
    h = abs(hours)
    whole = int(h)
    mins = int(round((h - whole) * 60))
    return f"UTC{sign}{whole}:{mins:02d}" if mins else f"UTC{sign}{whole}"
```

## 6. 只重试网络错误，不要重试 HTTP 4xx

重试一个 401 只会白等三倍时间，然后抛出同一个错。`urllib` 里 `HTTPError` 是
`URLError` 的子类，所以必须先单独 `except` 掉它：

```python
except urllib.error.HTTPError:
    raise                                    # 4xx/5xx 直接抛，不重试
except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
    last = e                                  # 超时/DNS/连接被拒才重试
```

DNS 解析失败是 `URLError` 的 `socket.gaierror`，连接被拒是 `ConnectionRefusedError`
（`OSError` 子类），原来的代码两个都没接住——Windy 抽风一次整个查询就废。

## 7. 重试循环走完必须有返回值

老版本 `api_get` 里 `for` 循环走完没有 `return`，隐式返回 `None`，
调用方立刻 `d.get(...)` → `AttributeError: 'NoneType' object has no attribute 'get'`。
错误信息完全指不到真正的原因（网络请求失败了）。

## 8. `config.json` 的占位符要当成「没配」

模板里是 `"YOUR_WINDY_API_KEY_HERE"`。如果只判断「非空」，
拿到这个字符串就会拿着它去请求 Windy，得到 401，
用户看到的报错是认证失败，而真实原因是「你还没填 key」。

```python
key = (cfg.get("windy_api_key") or "").strip()
if not key or key == KEY_PLACEHOLDER:
    key = os.environ.get("WINDY_API_KEY", "").strip()
```

## 9. `execSync` 拼字符串 = 命令注入

```js
// 绝对不要这样
execSync(`sudo python3 ${WORLDAM} "${args.place}" ${r}`)
```

`place` 是用户输入。`Tokyo"; rm -rf /` 会被 shell 解析成两条命令，
而且还带着 `sudo` 权限。

```js
execFileSync(PYTHON, [WORLDCAM, place, String(radius)], {
  shell: false,      // 没有 shell，引号和分号只是普通字符
  timeout: 90000,
})
```

验证方法：让「python」指向一个打印 argv 的假脚本，然后发一条带 `; touch /tmp/x` 的请求。
应该看到整条 payload 原封不动躺在 `argv[2]` 里。

## 10. 子串匹配要设长度门槛

```python
if key.lower() in place_lower or place_lower in key.lower():
```

`"rome"` 会命中 `"rome airport"`，`"york"` 会命中 `"new york"`。
只有两边都 ≥5 个字符时才做双向子串匹配，短名走精确匹配。

## 11. 图片别往仓库根目录扔

一跑就生成 `frame_*.jpg`、`skyline_*.jpg`，下次 `git add .` 就把摄像头截图提交上去了。
`.gitignore` 里 `frames/`、`frame_*.jpg`、`skyline_*.jpg`、`config.json` 都要带上。

## 12. 摄像头 limit 别设太小

`limit=5` 时只能看到热度前 5 的摄像头，调到 20 之后同一个地点经常能拿到
明显更好看的画面（比如东京从只有千代田 5 台，变成包含新宿区俯瞰等 20 台）。

## 检查位置过滤有没有生效

不管哪个 API，只要支持经纬度过滤，都值得这么查一下：

```python
d = http_get(url)
print(d["total_results"])   # 全球量级（比如 2 亿）= 位置过滤没生效
```

这是发现「参数名写错但不报错」最快的方式。

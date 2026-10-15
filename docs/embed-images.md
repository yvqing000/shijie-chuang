# 在 AI 聊天里内嵌图片

脚本落盘的是本地 JPEG 路径，AI 助手读不了本机文件。要在聊天里直接显示，
需要让图片能被 HTTP 访问到。

## 最省事的办法：直接用 Windy 的图片 URL

大多数情况下**不需要自建图床**。Windy API 返回的 `images.current.preview`
本身就是一条可以直接访问的 URL，不需要额外认证：

```
https://imgproxy.windy.com/_/preview/plain/current/1567938478/original.jpg?v=2
```

实测无认证直接 `curl` 返回 `HTTP 200`、`image/jpeg`、400x224。
所以可以直接嵌进 markdown：

```
![东京千代田](https://imgproxy.windy.com/_/preview/plain/current/1567938478/original.jpg?v=2)
```

这是关键——很多聊天界面会把裸链接渲染成一行文字，
但 `![alt](url)` 语法会渲染成图片。

### 拿 URL 的方式

```python
url = f"https://api.windy.com/webcams/api/v3/webcams/{cam_id}?include=images,location,urls"
d = http_get(url, headers={"x-windy-api-key": api_key})
img = d["images"]["current"]["preview"]
```

## 如果要自建（图片需要长期保存时）

1. 把图片写到 nginx 能直接访问的目录

   ```bash
   export WORLDCAM_OUT=/usr/share/nginx/html/cams
   python3 worldcam.py "Tokyo"
   ```

2. 配 nginx location

   ```nginx
   location /cams/ {
       alias /usr/share/nginx/html/cams/;
       expires 10m;
       add_header Cache-Control "public";
   }
   ```

3. 注意 location 优先级。如果已经有 `location /` 在跑反代，
   `/cams/` 这种更具体的路径会优先匹配，会绕过 `/` 上的鉴权规则。
   想加鉴权就得在 `/cams/` 里单独写。

## 过期问题

Windy 免费档给的图片 URL 本身 **10 分钟就过期**，所以：

- 不要把图片 URL 缓存进自己的数据库当永久链接
- nginx 的 `expires` 也设 10 分钟，别设更长的
- 想长期留档就把图另存到对象存储，别指望 CDN 缓存

## 分辨率

免费档给的预览图是 **400x224**，不大。想更清楚得看 Windy 的付费档，
或者用 SkylineWebcams 的 CDN（分辨率高一些，但只有 17 个固定景点）。

## 隐私

图片是公开摄像头拍到的公开画面。转发到聊天界面时，
如果那个房间里有别人，提醒一句这是实时公开画面。

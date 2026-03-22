---
name: wechat-869-media-sender
description: 通过 869 客户端发送微信消息（私聊/群聊）：纯文本、图片、视频、语音（音乐=语音）、链接、文件（附件）。从 ~/.openclaw/credentials 读取 869 服务地址与 key。
---

# wechat-869-media-sender

## 适用范围

- 私聊与群聊发送纯文本
- 私聊与群聊发送媒体：图片 / 视频 / 语音 / 链接 / 文件（附件）
- “音乐”按你的约定等价为“语音发送”（提供 `send-music` 子命令）

## 前置条件

- 必须安装 `ffmpeg`（视频封面抽帧依赖），确保命令行可执行 `ffmpeg -version`。
- 必须配置 869 后端服务地址与 key（否则无法发送）。

在 `".openclaw/credentials/wechat-869.json"` 配置 869 服务地址与 key：

```json
{
  "baseUrl": "http://127.0.0.1:19000",
  "key": "YOUR_869_KEY"
}
```

## 使用方法

脚本位置：

- `".openclaw/skills/wechat-869-media-sender/scripts/send_869_text.py"`
- `".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py"`

### 文本

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_text.py" \
  --to "sxkiss_com" \
  --text "今日复盘摘要"
```

也可通过文件发送，避免 shell 转义问题：

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_text.py" \
  --to "sxkiss_com" \
  --text-file "/tmp/daily-review-summary.txt"
```

### 图片

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-image --to "wxid_xxx" --path "/path/a.png"
```

### 视频（可选缩略图）

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-video --to "xxx@chatroom" --path "/path/a.mp4"
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-video --to "xxx@chatroom" --path "/path/a.mp4" --thumb "/path/t.png"
```

未显式传 `--thumb` 时，脚本会按以下顺序选择封面：

1. 根据 `--thumb-mode` 决定：`auto(默认)`/`frame(原视频首帧)`/`sidecar(同目录图片)`/`fallback(内置封面)`；
2. `auto` 会优先使用 `ffmpeg` 在 `00:00:01` 处抽帧生成封面（若可用），其次使用 sidecar，最后回退内置封面。

说明：
- 若安装了 `pillow`，脚本会将封面归一为 `240x160` 的 JPEG；否则仅在图片文件不大（<=256KB）时直接使用原始 bytes，避免把超大图片当封面导致发送失败。
- 响应会附带 `_derived.thumb_source/thumb_mode/ffmpeg/thumb_len`，用于确认实际使用的封面来源。

### 语音

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-voice --to "wxid_xxx" --path "/path/a.amr" --format "amr" --seconds 3
```

### 音乐（等价语音）

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-music --to "wxid_xxx" --path "/path/a.amr" --format "amr" --seconds 3
```

### 微信音乐卡片（Music Card / type=3）

发送类似“摇一摇搜歌”或原生音乐分享格式的卡片。

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-music-card \
  --to "wxid_xxx" \
  --title "晴天" \
  --singer "周杰伦" \
  --music-url "http://example.com/a.mp3" \
  --jump-url "http://example.com/play" \
  --cover-url "http://example.com/cover.jpg" \
  --card-type "摇一摇搜歌"
```

参数说明：
- `--music-url`: 必填，音频直链。
- `--jump-url`: 点击卡片跳转的页面。
- `--cover-url`: 封面图 URL（通常对应 `songalbumurl`）。
- `--card-type`: 模板选择，支持 `摇一摇搜歌`（默认，带图标）和 `原卡片`。
- `--from-wxid`: 可选，通常填机器人 wxid。

### 链接

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-link --to "wxid_xxx" --url "https://example.com" --title "标题" --desc "描述"
```

### 文件（附件）

```bash
python3 ".openclaw/skills/wechat-869-media-sender/scripts/send_869_media.py" send-file --to "wxid_xxx" --path "/path/a.zip" --name "a.zip"
```

## 备注

- 群聊 wxid 通常以 `@chatroom` 结尾；脚本不区分私聊/群聊，统一以 `--to` 传入。
- 输出为 JSON（或可 JSON 化响应），便于在其他自动化里继续处理。
- 文本脚本支持重复传入 `--at wxid_xxx`，会自动写入 `AtWxIDList`。
- 语音/音乐返回会额外补充 `_derived.ok`：优先以 `newMsgId` 是否非 0 作为派生成功信号（保留 `ret` 供排查）。

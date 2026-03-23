<!-- AUTO-DOC: Update me when files in this folder change -->

# wechat-869-media-sender

OpenClaw skill：提供 869 客户端的消息发送脚本（私聊/群聊：纯文本、图片、视频、语音、链接、文件；“音乐”=语音别名）。语音支持 amr/wav/mp3，其中 wav/mp3 会在本地转 silk；超长语音自动按 59 秒分片发送。

## Files

| File | Role | Function |
|------|------|----------|
| SKILL.md | Doc | Skill 触发说明与使用指南（含文本/媒体发送方法、869 配置、语音 silk 转码与分片规则） |
| scripts/send_869_text.py | Exec | 从 credentials 读取 869 baseUrl/key，发送纯文本消息 |
| scripts/send_869_media.py | Exec | 从 credentials 读取 869 baseUrl/key，发送图片/视频/语音/链接/文件；`wav/mp3` 本地转 silk，超长语音自动分片 |
| scripts/INDEX.md | Doc | scripts 目录索引（auto-doc） |
| assets/fallback.png | Asset | 视频缩略图兜底素材 |
| vendor/ | Lib | send_869_media.py 使用的本地 Python 依赖（如 pydub / pysilk） |

<!-- AUTO-DOC: Update me when files in this folder change -->

# wechat-869-media-sender

OpenClaw skill：提供 869 客户端的非文本媒体发送脚本（私聊/群聊：图片/视频/语音/链接/文件；“音乐”=语音别名）。

## Files

| File | Role | Function |
|------|------|----------|
| SKILL.md | Doc | Skill 触发说明与使用指南（含 ffmpeg 必装与 869 配置提示） |
| scripts/send_869_media.py | Exec | 从 credentials 读取 869 baseUrl/key，发送媒体消息 |
| scripts/INDEX.md | Doc | scripts 目录索引（auto-doc） |
| assets/fallback.png | Asset | 视频缩略图兜底素材 |

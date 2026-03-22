<!-- AUTO-DOC: Update me when project structure or architecture changes -->

# Architecture

该技能以单文件 CLI 封装 869 HTTP 消息接口，统一从 `~/.openclaw/credentials/wechat-869.json` 读取 `baseUrl/key`。
`scripts/send_869_media.py` 负责文本与媒体发送主流程，`assets/fallback.png` 仅作为视频封面兜底素材。
根目录文档描述能力边界与调用方式，子目录索引维护脚本级职责。

- [scripts/INDEX.md](scripts/INDEX.md)

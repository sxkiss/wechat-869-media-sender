<!-- AUTO-DOC: Update me when files in this folder change -->

# scripts

可执行脚本集合：通过 869 HTTP API 发送纯文本与媒体消息；其中 send_869_media.py 负责本地 silk 转码与超长语音分片。

## Files

| File | Role | Function |
|------|------|----------|
| send_869_text.py | Exec | 发送纯文本消息，支持直接传文本或从文件读取，并可附带 AtWxIDList |
| send_869_media.py | Exec | 发送图片/视频/语音/链接/文件（“音乐”=语音别名）；支持 amr/wav/mp3，wav/mp3 本地转 silk，超长语音按 59 秒分片 |

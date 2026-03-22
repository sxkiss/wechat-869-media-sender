#!/usr/bin/env python3
"""
@input: send_869_media.py 中的 DEFAULT_CONFIG_PATH/load_config/request_869/_print_result/_stderr；869 HTTP API /message/SendTextMessage；可选文本文件输入
@output: CLI 脚本：向私聊/群聊发送 869 纯文本消息，stdout 输出响应 JSON
@position: wechat-869-media-sender 的文本发送补充入口，供 cron/自动化直接复用
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from send_869_media import DEFAULT_CONFIG_PATH, _print_result, _stderr, load_config, request_869


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"文本文件不存在：{path}")
    if not path.is_file():
        raise ValueError(f"不是文件：{path}")
    return path.read_text(encoding="utf-8")


def send_text(*, config_path: Path, to_wxid: str, text: str, at_wxids: list[str]) -> object:
    cfg = load_config(config_path)
    clean_to = str(to_wxid).strip()
    if not clean_to:
        raise ValueError("--to 不能为空")

    clean_text = str(text).strip() or "[空消息]"
    clean_at = [item.strip() for item in at_wxids if str(item).strip()]
    if clean_at and not clean_text.startswith("@"):
        clean_text = "\n" + clean_text

    payload = {
        "MsgItem": [
            {
                "ToUserName": clean_to,
                "MsgType": 1,
                "TextContent": clean_text,
                "AtWxIDList": clean_at,
            }
        ]
    }
    return request_869(cfg, method="POST", path="/message/SendTextMessage", body=payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="send_869_text.py")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"869 配置文件路径（默认：{DEFAULT_CONFIG_PATH}）",
    )
    parser.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    parser.add_argument("--text", default="", help="要发送的文本内容")
    parser.add_argument("--text-file", default="", help="从文件读取要发送的文本内容")
    parser.add_argument(
        "--at",
        action="append",
        default=[],
        help="群聊 @ 的 wxid，可重复传入；会自动填充 AtWxIDList",
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    text = str(args.text)
    text_file = str(args.text_file).strip()
    if text_file:
        text = _read_text(Path(text_file))
    if not text.strip():
        raise ValueError("必须提供 --text 或 --text-file")

    result = send_text(
        config_path=Path(args.config),
        to_wxid=str(args.to),
        text=text,
        at_wxids=list(args.at),
    )
    _print_result(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        _stderr(f"ERROR: {exc}")
        raise SystemExit(2)

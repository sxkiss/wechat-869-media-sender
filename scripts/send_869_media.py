#!/usr/bin/env python3
"""
@input: ~/.openclaw/credentials/wechat-869.json（baseUrl/key）；869 HTTP API（/message/*、/other/*）；可选 ffmpeg（用于从视频抽帧生成封面）；可选 pillow（用于将封面归一为 240x160 JPEG）；可选 sidecar 图片（与视频同目录的 jpg/png）
@output: CLI 脚本：发送图片/视频/语音/音乐卡片/链接/文件（附件），stdout 输出响应 JSON
@position: OpenClaw skill wechat-869-media-sender 的可执行入口（非文本媒体发送）
@auto-doc: Update header and folder INDEX.md when this file changes
"""

from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import Request, urlopen

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

try:
    from pydub import AudioSegment  # type: ignore
except Exception:
    AudioSegment = None  # type: ignore

try:
    import pysilk  # type: ignore
except Exception:
    pysilk = None  # type: ignore

from xml.sax.saxutils import escape as xml_escape


DEFAULT_CONFIG_PATH = Path("/home/sxkiss/.openclaw/credentials/wechat-869.json")


@dataclass(frozen=True)
class ClientConfig:
    base_url: str
    key: str


def _stderr(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + "\n")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_card_ids(pool_path: Path) -> list[str]:
    raw = pool_path.read_text(encoding="utf-8", errors="replace")
    ids: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        ids.extend(re.findall(r'[A-Za-z0-9_\-]{8,}', s))
    seen = set()
    out: list[str] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def pick_random_card_id(pool_path: str) -> str:
    p = Path(pool_path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"卡片 ID 池文件不存在：{p}")
    ids = load_card_ids(p)
    if not ids:
        raise ValueError(f"卡片 ID 池文件未解析到可用 ID：{p}")
    return random.choice(ids)


def apply_card_id(value: str, card_id: str) -> str:
    if not value:
        return value
    return value.replace('{card_id}', card_id).replace('{{card_id}}', card_id)



def load_config(config_path: Path) -> ClientConfig:
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")
    raw = _read_text(config_path).strip()
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"配置文件不是合法 JSON：{config_path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("配置文件 JSON 顶层必须是对象")

    base_url = str(payload.get("baseUrl") or "").strip()
    key = str(payload.get("key") or "").strip()
    if not base_url:
        raise ValueError("配置缺少 baseUrl")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        base_url = "http://" + base_url
    base_url = base_url.rstrip("/")
    if not key:
        raise ValueError("配置缺少 key")
    return ClientConfig(base_url=base_url, key=key)


def _coerce_url(base_url: str, path: str, params: Optional[dict[str, Any]] = None) -> str:
    if not path.startswith("/"):
        path = "/" + path
    parsed = urlparse(base_url + path)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if params:
        for k, v in params.items():
            if v is None:
                continue
            query[str(k)] = str(v)
    new_query = urlencode(query)
    return urlunparse(parsed._replace(query=new_query))


def _maybe_parse_json(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    looks_json = "json" in (content_type or "").lower() or text.lstrip().startswith(("{", "["))
    if not looks_json:
        return text
    try:
        return json.loads(text)
    except Exception:
        return text


def request_869(
    cfg: ClientConfig,
    *,
    method: str,
    path: str,
    body: Optional[dict[str, Any]] = None,
    params: Optional[dict[str, Any]] = None,
    timeout_seconds: int = 60,
) -> Any:
    url = _coerce_url(cfg.base_url, path, params={"key": cfg.key, **(params or {})})
    data: Optional[bytes] = None
    headers = {"Accept": "application/json"}
    if method.upper() != "GET":
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body if body is not None else {}).encode("utf-8")

    req = Request(url=url, data=data, method=method.upper(), headers=headers)
    with urlopen(req, timeout=timeout_seconds) as resp:
        content_type = resp.headers.get("Content-Type", "")
        raw = resp.read()

    payload = _maybe_parse_json(raw, content_type)
    if isinstance(payload, dict):
        code = payload.get("Code")
        if code not in (None, 0, 200):
            raise RuntimeError(str(payload.get("Text") or payload.get("Message") or payload.get("message") or "869 请求失败"))
        if code is None and payload.get("Success") is False:
            raise RuntimeError(str(payload.get("Text") or payload.get("Message") or payload.get("message") or "869 请求失败"))
    return payload


def read_bytes(file_path: Path) -> bytes:
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    if not file_path.is_file():
        raise ValueError(f"不是文件：{file_path}")
    return file_path.read_bytes()


def to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _pick_first(d: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _pick_int(d: dict[str, Any], *keys: str) -> int:
    for k in keys:
        v = d.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return 0


def _ensure_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _print_result(result: Any) -> None:
    if isinstance(result, (dict, list)):
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        sys.stdout.write("\n")
        return
    sys.stdout.write(str(result))
    sys.stdout.write("\n")


def _coerce_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return 0


def annotate_voice_result(result: Any) -> Any:
    """为语音/音乐发送的结果补充派生成功判定。

    经验规则：部分场景下 baseResponse.ret 非 0，但消息仍会实际送达；
    因此将 newMsgId 非 0 视为更可靠的成功信号，并保留 ret 供排查。
    """
    if not isinstance(result, dict):
        return result

    data = result.get("Data")
    data_dict = data if isinstance(data, dict) else {}

    base_resp = data_dict.get("baseResponse") if isinstance(data_dict.get("baseResponse"), dict) else None
    if base_resp is None:
        base_resp = data_dict.get("BaseResponse") if isinstance(data_dict.get("BaseResponse"), dict) else {}
    if not isinstance(base_resp, dict):
        base_resp = {}

    new_msg_id = data_dict.get("newMsgId")
    if new_msg_id is None:
        new_msg_id = data_dict.get("NewMsgId")
    ok = _coerce_int(new_msg_id) > 0

    derived = {
        "ok": ok,
        "newMsgId": new_msg_id,
        "ret": base_resp.get("ret"),
    }
    merged = dict(result)
    merged["_derived"] = derived
    return merged


def upload_file(cfg: ClientConfig, file_path: Path) -> dict[str, Any]:
    file_b64 = to_base64(read_bytes(file_path))
    resp = request_869(cfg, method="POST", path="/other/UploadAppAttach", body={"fileData": file_b64})
    if not isinstance(resp, dict):
        return {"raw": resp}
    data = resp.get("Data") if isinstance(resp.get("Data"), dict) else resp
    return data if isinstance(data, dict) else {"raw": data}


def send_app_message(cfg: ClientConfig, *, to_wxid: str, content_xml: str, content_type: int) -> Any:
    payload = {
        "AppList": [
            {
                "ToUserName": to_wxid,
                "ContentType": int(content_type),
                "ContentXML": content_xml,
            }
        ]
    }
    return request_869(cfg, method="POST", path="/message/SendAppMessage", body=payload)


def build_link_appmsg_xml(*, url: str, title: str, desc: str, thumb_url: str) -> str:
    return (
        "<appmsg appid='' sdkver='0'>"
        f"<title>{xml_escape(title or '')}</title>"
        f"<des>{xml_escape(desc or '')}</des>"
        f"<url>{xml_escape(url or '')}</url>"
        f"<thumburl>{xml_escape(thumb_url or '')}</thumburl>"
        "<type>5</type>"
        "</appmsg>"
    )


def build_file_appmsg_xml(*, file_name: str, total_len: int, media_id: str) -> str:
    safe_name = (file_name or "file").strip() or "file"
    file_ext = safe_name.rsplit(".", 1)[-1].lower().strip() if "." in safe_name else ""
    return (
        "<appmsg appid=\"\" sdkver=\"0\">"
        f"<title>{xml_escape(safe_name)}</title><des></des><action></action>"
        "<type>6</type><showtype>0</showtype><content></content><url></url>"
        "<appattach>"
        f"<totallen>{int(total_len)}</totallen>"
        f"<attachid>{xml_escape(media_id)}</attachid>"
        f"<fileext>{xml_escape(file_ext)}</fileext>"
        "</appattach><md5></md5></appmsg>"
    )


def send_link(cfg: ClientConfig, *, to_wxid: str, url: str, title: str, desc: str, thumb_url: str) -> Any:
    xml_payload = build_link_appmsg_xml(url=url, title=title, desc=desc, thumb_url=thumb_url)
    return send_app_message(cfg, to_wxid=to_wxid, content_xml=xml_payload, content_type=5)


def send_file(cfg: ClientConfig, *, to_wxid: str, file_path: Path, file_name: str) -> Any:
    info = upload_file(cfg, file_path)
    media_id = _pick_first(info, "mediaId", "MediaId", "attachId", "AttachId")
    total_len = _pick_int(info, "totalLen", "TotalLen")
    resolved_name = (file_name or _pick_first(info, "fileName", "FileName") or file_path.name).strip()
    xml_payload = build_file_appmsg_xml(file_name=resolved_name, total_len=total_len, media_id=media_id)
    return send_app_message(cfg, to_wxid=to_wxid, content_xml=xml_payload, content_type=6)


def build_music_appmsg_xml(
    *,
    title: str,
    singer: str,
    jump_url: str,
    music_url: str,
    cover_url: str,
    lyric: str,
    card_type: str,
    from_wxid: str,
) -> str:
    title_xml = xml_escape(title or "")
    singer_xml = xml_escape(singer or "")
    jump_url_xml = xml_escape(jump_url or "")
    music_url_xml = xml_escape(music_url or "")
    cover_url_xml = xml_escape(cover_url or "")
    lyric_xml = xml_escape(lyric or "")
    from_wxid_xml = xml_escape(from_wxid or "")
    normalized = (card_type or "摇一摇搜歌").strip()

    if normalized == "原卡片":
        appid = "wx79f2c4418704b4f8"
        app_version = "1"
        app_name = ""
        appmsg = (
            f"<appmsg appid=\"{appid}\" sdkver=\"0\">"
            f"<title>{title_xml}</title>"
            f"<des>{singer_xml}</des>"
            "<action>view</action>"
            "<type>3</type><showtype>0</showtype><content/>"
            f"<url>{jump_url_xml}</url>"
            f"<dataurl>{music_url_xml}</dataurl>"
            f"<lowurl>{jump_url_xml}</lowurl>"
            f"<lowdataurl>{music_url_xml}</lowdataurl>"
            "<recorditem/><thumburl/><messageaction/><laninfo/><extinfo/><sourceusername/><sourcedisplayname/>"
            f"<songlyric>{lyric_xml}</songlyric>"
            "<commenturl/>"
            "<appattach><totallen>0</totallen><attachid/><emoticonmd5/><fileext/><aeskey/></appattach>"
            "<webviewshared><publisherId/><publisherReqId>0</publisherReqId></webviewshared>"
            "<weappinfo><pagepath/><username/><appid/><appservicetype>0</appservicetype></weappinfo>"
            f"<websearch/><songalbumurl>{cover_url_xml}</songalbumurl>"
            "</appmsg>"
        )
    else:
        appid = "wx485a97c844086dc9"
        app_version = "29"
        app_name = "摇一摇搜歌"
        appmsg = (
            f"<appmsg appid=\"{appid}\" sdkver=\"0\">"
            f"<title>{title_xml}</title>"
            f"<des>{singer_xml}</des>"
            "<action>view</action>"
            "<type>3</type><showtype>0</showtype><content/>"
            f"<url>{jump_url_xml}</url>"
            f"<dataurl>{music_url_xml}</dataurl>"
            f"<lowurl>{jump_url_xml}</lowurl>"
            f"<lowdataurl>{music_url_xml}</lowdataurl>"
            "<thumburl/>"
            f"<songlyric>{lyric_xml}</songlyric>"
            f"<songalbumurl>{cover_url_xml}</songalbumurl>"
            "<appattach><totallen>0</totallen><attachid/><emoticonmd5/><fileext/><aeskey/></appattach>"
            "<weappinfo><pagepath/><username/><appid/><appservicetype>0</appservicetype></weappinfo>"
            "</appmsg>"
        )

    tail = (
        f"<fromusername>{from_wxid_xml}</fromusername>"
        "<scene>0</scene>"
        "<appinfo>"
        f"<version>{app_version}</version>"
        f"<appname>{xml_escape(app_name)}</appname>"
        "</appinfo>"
        "<commenturl/>"
    )
    return appmsg + tail


def send_music_card(
    cfg: ClientConfig,
    *,
    to_wxid: str,
    title: str,
    singer: str,
    jump_url: str,
    music_url: str,
    cover_url: str,
    lyric: str,
    card_type: str,
    from_wxid: str,
) -> Any:
    xml_payload = build_music_appmsg_xml(
        title=title,
        singer=singer,
        jump_url=jump_url,
        music_url=music_url,
        cover_url=cover_url,
        lyric=lyric,
        card_type=card_type,
        from_wxid=from_wxid,
    )
    return send_app_message(cfg, to_wxid=to_wxid, content_xml=xml_payload, content_type=3)




def send_app_card(cfg: ClientConfig, *, to_wxid: str, content_xml: str, content_type: int = 5) -> Any:
    return send_app_message(cfg, to_wxid=to_wxid, content_xml=content_xml, content_type=content_type)


MAX_VOICE_SECONDS = 59
VOICE_CHUNK_PADDING_MS = 0
VOICE_CHUNK_SEND_INTERVAL_SEC = 0.35


def _get_closest_frame_rate(frame_rate: int) -> int:
    supported = [8000, 12000, 16000, 24000]
    return min(supported, key=lambda value: abs(value - frame_rate))


def _load_audio_segment(voice_path: Path, fmt_normalized: str):
    if AudioSegment is None:
        raise RuntimeError("缺少 pydub 依赖，无法读取音频并进行分片")
    return AudioSegment.from_file(str(voice_path), format=fmt_normalized).set_channels(1)


def _slice_audio_segment(audio) -> list[Any]:
    total_ms = len(audio)
    chunk_ms = MAX_VOICE_SECONDS * 1000 - VOICE_CHUNK_PADDING_MS
    if chunk_ms <= 0:
        chunk_ms = MAX_VOICE_SECONDS * 1000
    if total_ms <= MAX_VOICE_SECONDS * 1000:
        return [audio]
    chunks = []
    for start in range(0, total_ms, chunk_ms):
        chunks.append(audio[start:start + chunk_ms])
    return chunks


def _audio_chunk_to_silk_payload(audio_chunk) -> tuple[bytes, int, int]:
    if pysilk is None:
        raise RuntimeError("缺少 pysilk 依赖，无法按 allbot 方式把音频转为 silk")
    normalized = audio_chunk.set_channels(1)
    frame_rate = int(getattr(normalized, "frame_rate", 16000) or 16000)
    normalized = normalized.set_frame_rate(_get_closest_frame_rate(frame_rate))
    silk_bytes = pysilk.encode(normalized.raw_data, sample_rate=normalized.frame_rate)
    derived_seconds = max(1, int((len(normalized) + 999) // 1000))
    return silk_bytes, 4, min(MAX_VOICE_SECONDS, derived_seconds)


def _prepare_voice_payloads(voice_path: Path, fmt: str, seconds: int) -> list[dict[str, Any]]:
    fmt_normalized = (fmt or "amr").lower().strip()
    if fmt_normalized not in {"amr", "wav", "mp3"}:
        raise ValueError("语音格式仅支持 amr/wav/mp3")

    if fmt_normalized == "amr":
        try:
            audio = _load_audio_segment(voice_path, fmt_normalized)
        except Exception:
            fallback_seconds = max(1, min(MAX_VOICE_SECONDS, int(seconds)))
            return [{
                "voice_bytes": read_bytes(voice_path),
                "voice_format": 0,
                "voice_seconds": fallback_seconds,
                "wire_codec": "amr",
            }]

        actual_seconds = max(1, int((len(audio) + 999) // 1000))
        if actual_seconds <= MAX_VOICE_SECONDS:
            return [{
                "voice_bytes": read_bytes(voice_path),
                "voice_format": 0,
                "voice_seconds": actual_seconds,
                "wire_codec": "amr",
            }]

        payloads = []
        for chunk in _slice_audio_segment(audio):
            voice_bytes, voice_format, voice_seconds = _audio_chunk_to_silk_payload(chunk)
            payloads.append({
                "voice_bytes": voice_bytes,
                "voice_format": voice_format,
                "voice_seconds": voice_seconds,
                "wire_codec": "silk",
            })
        return payloads

    audio = _load_audio_segment(voice_path, fmt_normalized)
    payloads = []
    for chunk in _slice_audio_segment(audio):
        voice_bytes, voice_format, voice_seconds = _audio_chunk_to_silk_payload(chunk)
        payloads.append({
            "voice_bytes": voice_bytes,
            "voice_format": voice_format,
            "voice_seconds": voice_seconds,
            "wire_codec": "silk",
        })
    return payloads


def send_voice(cfg: ClientConfig, *, to_wxid: str, voice_path: Path, fmt: str, seconds: int) -> Any:
    payloads = _prepare_voice_payloads(voice_path, fmt, seconds)
    total_chunks = len(payloads)
    results = []
    for idx, item in enumerate(payloads, 1):
        payload = {
            "ToUserName": to_wxid,
            "VoiceData": to_base64(item["voice_bytes"]),
            "VoiceFormat": int(item["voice_format"]),
            "VoiceSecond": int(item["voice_seconds"]),
            "VoiceSecond,": int(item["voice_seconds"]),
        }
        result = request_869(cfg, method="POST", path="/message/SendVoice", body=payload)
        if isinstance(result, dict):
            merged = dict(result)
            merged.setdefault("_derived", {})
            if isinstance(merged["_derived"], dict):
                merged["_derived"].update({
                    "inputFormat": (fmt or "amr").lower().strip(),
                    "wireFormat": int(item["voice_format"]),
                    "wireCodec": str(item["wire_codec"]),
                    "seconds": int(item["voice_seconds"]),
                    "chunkIndex": idx,
                    "totalChunks": total_chunks,
                    "chunked": total_chunks > 1,
                    "maxSecondsPerChunk": MAX_VOICE_SECONDS,
                })
            results.append(merged)
        else:
            results.append(result)
        if idx < total_chunks:
            time.sleep(VOICE_CHUNK_SEND_INTERVAL_SEC)

    if total_chunks == 1:
        return results[0]

    return {
        "chunked": True,
        "totalChunks": total_chunks,
        "maxSecondsPerChunk": MAX_VOICE_SECONDS,
        "results": results,
    }


def _fallback_thumb_path() -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    return base_dir / "assets" / "fallback.png"


def _extract_video_thumb_with_ffmpeg(video_path: Path) -> Optional[bytes]:
    """从视频中抽取封面（对齐 VideoDemand/VideoSender：取 1s 处帧并输出 JPEG）。"""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return None
    if not video_path.exists():
        return None

    with tempfile.TemporaryDirectory(prefix="wechat-869-thumb-") as tmp_dir:
        out_path = Path(tmp_dir) / "thumb.jpg"
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-ss",
            "00:00:01",
            "-vframes",
            "1",
            "-vf",
            "scale=240:160:force_original_aspect_ratio=decrease,pad=240:160:(ow-iw)/2:(oh-ih)/2",
            "-q:v",
            "5",
            str(out_path),
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception:
            return None
        if not out_path.exists():
            return None
        try:
            return out_path.read_bytes()
        except Exception:
            return None


def _normalize_thumb_bytes_with_pillow(image_bytes: bytes) -> Optional[bytes]:
    """将任意图片压缩/裁剪为 240x160 JPEG 封面（可选依赖 pillow）。"""
    try:
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return None

    try:
        image = Image.open(BytesIO(image_bytes))
        image = image.convert("RGB")

        resampling = getattr(Image, "Resampling", Image)
        method = getattr(resampling, "LANCZOS", getattr(Image, "LANCZOS", 1))
        image = ImageOps.fit(image, (240, 160), method=method, centering=(0.5, 0.5))

        out = BytesIO()
        image.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue()
    except Exception:
        return None


def _thumb_bytes_from_path(image_path: Path) -> Optional[bytes]:
    if not image_path.exists() or not image_path.is_file():
        return None
    raw = image_path.read_bytes()
    normalized = _normalize_thumb_bytes_with_pillow(raw)
    if normalized:
        return normalized
    if len(raw) <= 256 * 1024:
        return raw
    return None


def _find_sidecar_thumb(video_path: Path) -> Optional[Path]:
    """在视频同目录寻找 sidecar 封面图（确定性规则，避免误选）。

    规则：
    1) 若存在与 video 同 stem 的 .jpg/.jpeg/.png，直接使用；
    2) 否则若目录内仅存在 1 张图片（.jpg/.jpeg/.png），使用该图片；
    3) 其余情况返回 None。
    """
    parent = video_path.parent
    if not parent.exists() or not parent.is_dir():
        return None

    exts = {".jpg", ".jpeg", ".png"}
    candidates = [p for p in parent.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not candidates:
        return None

    stem = video_path.stem
    for ext in (".jpg", ".jpeg", ".png"):
        matched = parent / f"{stem}{ext}"
        if matched.exists() and matched.is_file():
            return matched

    if len(candidates) == 1:
        return candidates[0]
    return None


def _supports_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def send_video(
    cfg: ClientConfig,
    *,
    to_wxid: str,
    video_path: Path,
    thumb_path: Optional[Path],
    thumb_mode: str,
) -> Any:
    video_bytes = read_bytes(video_path)
    thumb_source = "fallback"

    if thumb_path is not None:
        thumb_source = "arg"
        thumb_bytes = _thumb_bytes_from_path(thumb_path) or read_bytes(_fallback_thumb_path())
    else:
        normalized_from_ffmpeg: Optional[bytes] = None
        if _supports_ffmpeg():
            extracted = _extract_video_thumb_with_ffmpeg(video_path)
            normalized_from_ffmpeg = extracted or None

        sidecar_path = _find_sidecar_thumb(video_path)
        sidecar_bytes = _thumb_bytes_from_path(sidecar_path) if sidecar_path else None

        mode = (thumb_mode or "auto").strip().lower()
        if mode == "frame":
            if normalized_from_ffmpeg:
                thumb_source = "ffmpeg"
                thumb_bytes = normalized_from_ffmpeg
            else:
                thumb_source = "fallback"
                thumb_bytes = read_bytes(_fallback_thumb_path())
        elif mode == "sidecar":
            if sidecar_bytes:
                thumb_source = "sidecar"
                thumb_bytes = sidecar_bytes
            else:
                thumb_source = "fallback"
                thumb_bytes = read_bytes(_fallback_thumb_path())
        elif mode == "fallback":
            thumb_source = "fallback"
            thumb_bytes = read_bytes(_fallback_thumb_path())
        else:
            if normalized_from_ffmpeg:
                thumb_source = "ffmpeg"
                thumb_bytes = normalized_from_ffmpeg
            elif sidecar_bytes:
                thumb_source = "sidecar"
                thumb_bytes = sidecar_bytes
            else:
                thumb_source = "fallback"
                thumb_bytes = read_bytes(_fallback_thumb_path())

    upload_payload = {
        "ToUserName": to_wxid,
        "VideoData": list(video_bytes),
        "ThumbData": list(thumb_bytes),
    }
    upload_resp = request_869(cfg, method="POST", path="/message/CdnUploadVideo", body=upload_payload)

    candidates: list[dict[str, Any]] = []
    if isinstance(upload_resp, dict):
        candidates.append(upload_resp)
        if isinstance(upload_resp.get("resp"), dict):
            candidates.append(upload_resp["resp"])
        if isinstance(upload_resp.get("Data"), dict):
            candidates.append(upload_resp["Data"])
    elif isinstance(upload_resp, list) and upload_resp and isinstance(upload_resp[0], dict):
        candidates.append(upload_resp[0])
        if isinstance(upload_resp[0].get("resp"), dict):
            candidates.append(upload_resp[0]["resp"])

    aes_key = ""
    cdn_url = ""
    play_length = 0
    length = 0
    thumb_len = 0
    for item in candidates:
        aes_key = aes_key or _pick_first(item, "aesKey", "AesKey", "aeskey", "FileAesKey", "fileAesKey", "file_aes_key")
        cdn_url = cdn_url or _pick_first(item, "cdnVideoUrl", "CdnVideoUrl", "cdnvideourl", "fileId", "fileID", "FileID", "FileId")
        play_length = play_length or _pick_int(item, "playLength", "PlayLength")
        length = length or _pick_int(item, "length", "Length", "totalLen", "TotalLen", "VideoDataSize", "videoDataSize")
        thumb_len = thumb_len or _pick_int(item, "cdnThumbLength", "CdnThumbLength", "ThumbDataSize", "thumbDataSize")

    if not (aes_key and cdn_url):
        if isinstance(upload_resp, dict):
            merged = dict(upload_resp)
            merged["_derived"] = {
                **(merged.get("_derived") if isinstance(merged.get("_derived"), dict) else {}),
                "thumb_source": thumb_source,
                "thumb_mode": (thumb_mode or "auto").strip().lower(),
                "ffmpeg": _supports_ffmpeg(),
                "thumb_len": len(thumb_bytes),
            }
            return merged
        return upload_resp

    forward_payload = {
        "ForwardVideoList": [
            {
                "AesKey": aes_key,
                "CdnVideoUrl": cdn_url,
                "CdnThumbLength": int(thumb_len),
                "Length": int(length),
                "PlayLength": int(play_length),
                "ToUserName": to_wxid,
            }
        ]
    }
    forward_resp = request_869(cfg, method="POST", path="/message/ForwardVideoMessage", body=forward_payload)
    if isinstance(forward_resp, dict):
        merged = dict(forward_resp)
        merged["_derived"] = {
            **(merged.get("_derived") if isinstance(merged.get("_derived"), dict) else {}),
            "thumb_source": thumb_source,
            "thumb_mode": (thumb_mode or "auto").strip().lower(),
            "ffmpeg": _supports_ffmpeg(),
            "thumb_len": len(thumb_bytes),
        }
        return merged
    return forward_resp


def send_image(cfg: ClientConfig, *, to_wxid: str, image_path: Path) -> Any:
    image_b64 = to_base64(read_bytes(image_path))
    upload_resp: Any = None
    try:
        upload_resp = request_869(cfg, method="POST", path="/message/UploadImageToCDN", body={"imageContent": image_b64})
    except Exception:
        upload_resp = None

    upload_data = _ensure_dict(upload_resp.get("Data")) if isinstance(upload_resp, dict) and isinstance(upload_resp.get("Data"), dict) else _ensure_dict(upload_resp)
    aes_key = _pick_first(upload_data, "aesKey", "AesKey", "aeskey")
    cdn_resp = upload_data.get("cdnResponse") if isinstance(upload_data.get("cdnResponse"), dict) else {}
    cdn_resp = cdn_resp if isinstance(cdn_resp, dict) else {}
    cdn_mid = _pick_first(cdn_resp, "cdnMidImgUrl", "cdnBigImgUrl", "cdnThumbImgUrl", "fileID")
    recv_len = _pick_int(cdn_resp, "recvLen") or _pick_int(upload_data, "totalLen", "TotalLen")

    if aes_key and cdn_mid:
        forward_payload = {
            "ForwardImageList": [
                {
                    "AesKey": aes_key,
                    "CdnMidImgUrl": cdn_mid,
                    "CdnMidImgSize": int(recv_len),
                    "CdnThumbImgSize": int(recv_len),
                    "ToUserName": to_wxid,
                }
            ]
        }
        try:
            return request_869(cfg, method="POST", path="/message/ForwardImageMessage", body=forward_payload)
        except Exception:
            pass

    msg_payload = {"MsgItem": [{"ToUserName": to_wxid, "MsgType": 2, "ImageContent": image_b64}]}
    try:
        return request_869(cfg, method="POST", path="/message/SendImageMessage", body=msg_payload)
    except Exception:
        return request_869(cfg, method="POST", path="/message/SendImageNewMessage", body=msg_payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="send_869_media.py")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"869 配置文件路径（默认：{DEFAULT_CONFIG_PATH}）",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_img = sub.add_parser("send-image", help="发送图片（非文本）")
    p_img.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_img.add_argument("--path", required=True, help="图片文件路径")

    p_vid = sub.add_parser("send-video", help="发送视频（非文本）")
    p_vid.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_vid.add_argument("--path", required=True, help="视频文件路径")
    p_vid.add_argument("--thumb", default="", help="视频封面图片路径（可选）")
    p_vid.add_argument(
        "--thumb-mode",
        default="auto",
        choices=["auto", "frame", "sidecar", "fallback"],
        help="未传 --thumb 时的封面策略：auto(默认)/frame(原视频首帧)/sidecar(同目录图片)/fallback(内置封面)",
    )

    p_voice = sub.add_parser("send-voice", help="发送语音（非文本）")
    p_voice.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_voice.add_argument("--path", required=True, help="语音文件路径")
    p_voice.add_argument("--format", default="amr", choices=["amr", "wav", "mp3"], help="语音格式（默认 amr）")
    p_voice.add_argument("--seconds", type=int, default=2, help="语音时长（秒，默认 2）")

    p_music = sub.add_parser("send-music", help="发送音乐（兼容旧约定：等价语音发送）")
    p_music.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_music.add_argument("--path", required=True, help="语音文件路径")
    p_music.add_argument("--format", default="amr", choices=["amr", "wav", "mp3"], help="语音格式（默认 amr）")
    p_music.add_argument("--seconds", type=int, default=2, help="语音时长（秒，默认 2）")

    p_music_card = sub.add_parser("send-music-card", help="发送微信音乐卡片（appmsg / type=3）")
    p_music_card.add_argument("--card-id-pool", default="", help="可选：卡片 ID 池文件路径；发送前随机抽取一个 ID，并替换 title/singer/jump-url/music-url/cover-url/lyric 中的 {card_id}")
    p_music_card.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_music_card.add_argument("--title", required=True, help="歌曲标题")
    p_music_card.add_argument("--singer", default="", help="歌手/描述")
    p_music_card.add_argument("--jump-url", default="", help="点击卡片后的跳转 URL")
    p_music_card.add_argument("--music-url", required=True, help="音频直链 URL（dataurl / lowdataurl）")
    p_music_card.add_argument("--cover-url", default="", help="封面 URL（songalbumurl）")
    p_music_card.add_argument("--lyric", default="", help="歌词（可选）")
    p_music_card.add_argument("--card-type", default="摇一摇搜歌", choices=["摇一摇搜歌", "原卡片"], help="卡片模板：摇一摇搜歌(默认) / 原卡片")
    p_music_card.add_argument("--from-wxid", default="", help="可选：fromusername，通常填机器人 wxid")

    p_app_card = sub.add_parser("send-app-card", help="发送自定义微信分享卡片/appmsg XML")
    p_app_card.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_app_card.add_argument("--xml", required=True, help="完整 appmsg XML 内容，可使用 {card_id} 占位符")
    p_app_card.add_argument("--content-type", type=int, default=5, help="SendAppMessage contentType，默认 5")
    p_app_card.add_argument("--card-id-pool", default="", help="可选：卡片 ID 池文件路径；发送前随机抽取一个 ID，并替换 XML 中的 {card_id}")

    p_link = sub.add_parser("send-link", help="发送链接卡片（非文本）")
    p_link.add_argument("--card-id-pool", default="", help="可选：卡片 ID 池文件路径；发送前随机抽取一个 ID，并替换 title/desc/url/thumb-url 中的 {card_id}")
    p_link.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_link.add_argument("--url", required=True, help="链接 URL")
    p_link.add_argument("--title", default="", help="标题")
    p_link.add_argument("--desc", default="", help="描述")
    p_link.add_argument("--thumb-url", default="", help="缩略图 URL（可选）")

    p_file = sub.add_parser("send-file", help="发送文件/附件（非文本）")
    p_file.add_argument("--to", required=True, help="接收人 wxid（群聊一般以 @chatroom 结尾）")
    p_file.add_argument("--path", required=True, help="文件路径")
    p_file.add_argument("--name", default="", help="文件名（可选，默认取 path 的文件名）")

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(Path(args.config))

    cmd = args.cmd
    to_wxid = str(getattr(args, "to", "")).strip()
    if not to_wxid:
        raise ValueError("--to 不能为空")

    if cmd == "send-image":
        result = send_image(cfg, to_wxid=to_wxid, image_path=Path(args.path))
        _print_result(result)
        return 0

    if cmd == "send-video":
        thumb = Path(args.thumb) if str(args.thumb).strip() else None
        result = send_video(
            cfg,
            to_wxid=to_wxid,
            video_path=Path(args.path),
            thumb_path=thumb,
            thumb_mode=str(args.thumb_mode),
        )
        _print_result(result)
        return 0

    if cmd in ("send-voice", "send-music"):
        result = send_voice(
            cfg,
            to_wxid=to_wxid,
            voice_path=Path(args.path),
            fmt=str(args.format),
            seconds=int(args.seconds),
        )
        _print_result(annotate_voice_result(result))
        return 0

    if cmd == "send-music-card":
        picked_card_id = pick_random_card_id(str(args.card_id_pool)) if getattr(args, "card_id_pool", "") else ""
        result = send_music_card(
            cfg,
            to_wxid=to_wxid,
            title=apply_card_id(str(args.title), picked_card_id),
            singer=apply_card_id(str(args.singer), picked_card_id),
            jump_url=apply_card_id(str(args.jump_url), picked_card_id),
            music_url=apply_card_id(str(args.music_url), picked_card_id),
            cover_url=apply_card_id(str(args.cover_url), picked_card_id),
            lyric=apply_card_id(str(args.lyric), picked_card_id),
            card_type=str(args.card_type),
            from_wxid=str(args.from_wxid),
        )
        if isinstance(result, dict) and picked_card_id:
            result.setdefault("pickedCardId", picked_card_id)
        _print_result(result)
        return 0

    if cmd == "send-app-card":
        picked_card_id = pick_random_card_id(str(args.card_id_pool)) if getattr(args, "card_id_pool", "") else ""
        xml_payload = apply_card_id(str(args.xml), picked_card_id)
        result = send_app_card(
            cfg,
            to_wxid=to_wxid,
            content_xml=xml_payload,
            content_type=int(args.content_type),
        )
        if isinstance(result, dict) and picked_card_id:
            result.setdefault("pickedCardId", picked_card_id)
        _print_result(result)
        return 0

    if cmd == "send-link":
        result = send_link(
            cfg,
            to_wxid=to_wxid,
            url=str(args.url),
            title=str(args.title),
            desc=str(args.desc),
            thumb_url=str(args.thumb_url),
        )
        _print_result(result)
        return 0

    if cmd == "send-file":
        result = send_file(cfg, to_wxid=to_wxid, file_path=Path(args.path), file_name=str(args.name))
        _print_result(result)
        return 0

    raise ValueError(f"未知命令：{cmd}")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        _stderr(f"ERROR: {exc}")
        raise SystemExit(2)

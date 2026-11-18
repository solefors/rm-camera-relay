import re
from urllib.parse import urljoin, urlsplit

PREFIX = "/api/hls/"
SAFE_TAGS = frozenset(("EXTM3U", "EXT-X-VERSION", "EXT-X-TARGETDURATION", "EXT-X-MEDIA-SEQUENCE", "EXT-X-DISCONTINUITY-SEQUENCE", "EXT-X-ENDLIST", "EXT-X-PLAYLIST-TYPE", "EXT-X-I-FRAMES-ONLY", "EXT-X-INDEPENDENT-SEGMENTS", "EXT-X-START", "EXT-X-KEY", "EXT-X-MAP", "EXT-X-MEDIA", "EXT-X-STREAM-INF", "EXT-X-I-FRAME-STREAM-INF", "EXT-X-BYTERANGE", "EXT-X-DISCONTINUITY", "EXT-X-PROGRAM-DATE-TIME", "EXT-X-GAP", "EXTINF"))


def source_path(value, parent=None):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("resource")
    # Only HA-returned relative HLS references; never accept an absolute origin or encoded traversal.
    if re.search(r"[%\\\s]", value) or any(p in (".", "..") for p in value.split("/")):
        raise ValueError("resource")
    raw = urlsplit(value)
    if raw.scheme or raw.netloc or raw.query or raw.fragment:
        raise ValueError("resource")
    result = urlsplit(urljoin(parent or PREFIX, value)).path
    if not result.startswith(PREFIX) or not re.fullmatch(r"/api/hls/[A-Za-z0-9_./-]+", result):
        raise ValueError("resource")
    return result


def rewrite(text, parent, link):
    if not text.startswith("#EXTM3U") or len(text) > 512 * 1024:
        raise ValueError("playlist")
    output = []
    for line in text.splitlines():
        if not line:
            continue
        if not line.startswith("#"):
            output.append(link(source_path(line, parent)))
            continue
        tag = line[1:].split(":", 1)[0]
        if tag not in SAFE_TAGS:
            # Drop free comments; reject unsupported protocol tags rather than leak embedded resources.
            if not tag.startswith("EXT"):
                continue
            raise ValueError("unsupported playlist")
        if tag == "EXTINF":
            if not re.fullmatch(r"#EXTINF:[0-9.]+,.*", line):
                raise ValueError("playlist")
            line = line.split(",", 1)[0] + ","
        line = re.sub(r'URI="([^"\r\n]+)"', lambda m: 'URI="' + link(source_path(m[1], parent)) + '"', line)
        # No source URLs/paths or unrewritten URI fields in metadata.
        if "://" in line or "/api/" in line or re.search(r'(?<![A-Z-])URI=(?!"/v1/streams/)', line):
            raise ValueError("playlist")
        output.append(line)
    return "\n".join(output) + "\n"
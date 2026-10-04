# -*- coding: utf-8 -*-
"""
群日记管理系统 —— 本地服务 + 独立应用窗口

数据仍是纯文本文件，格式：

    2026.7.13 伊丝塔
    1.鲸鱼娘好可爱
    2.deepseek娘好厉害
    END

只依赖 Python 标准库。启动方式见 启动群日记.bat。
"""

import datetime
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_TAG = "qunriji-diary"      # 认领自己人的标记，见 /api/ping
MAX_BODY_BYTES = 4 * 1024 * 1024   # 请求体上限，防止被超大 body 撑爆内存
MAX_DRAIN_BYTES = 64 * 1024 * 1024  # 超限时最多再读掉这么多，好把 413 干净地回给对方
WEB_DIR = os.path.join(APP_DIR, "web")
DIARY_DIR = os.path.join(APP_DIR, "diaries")
MUSIC_DIR = os.path.join(APP_DIR, "music")
TRASH_DIR = os.path.join(APP_DIR, ".trash")
LOG_DIR = os.path.join(APP_DIR, "logs")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
GITIGNORE_PATH = os.path.join(APP_DIR, ".gitignore")
GITATTRIBUTES_PATH = os.path.join(APP_DIR, ".gitattributes")
PROFILE_DIR = os.path.join(APP_DIR, ".browser-profile")

GITIGNORE_BODY = """config.json
logs/
.trash/
.browser-profile/
__pycache__/
*.pyc
"""

# 统一换行符：否则 core.autocrlf=true 的电脑上，日记被检出成 CRLF，
# 正文每行末尾会多一个 \r。.bat 反过来必须是 CRLF。
GITATTRIBUTES_BODY = """# 统一换行符，避免不同电脑上 git 的 core.autocrlf 设置把文件改来改去，
# 也避免日记被检出成 CRLF（那样正文每行末尾会多一个 \\r）。

* text=auto eol=lf

# 批处理文件必须是 CRLF，否则 Windows 的 cmd 可能解析出错
*.bat text eol=crlf
*.cmd text eol=crlf

# 二进制文件不做任何转换
*.png binary
*.jpg binary
*.jpeg binary
*.gif binary
*.webp binary
*.ico binary
*.mp3 binary
*.wav binary
*.flac binary
*.ogg binary
*.m4a binary
*.aac binary
*.opus binary
*.zip binary
"""

# --------------------------------------------------------------------------
# 版本自检
#
# 界面文件（web/*）是每次请求都从磁盘现读的，而 app.py 是启动时读进内存的。
# 于是会出现「前端已经是新版、后端还是旧版」的错配：点了新按钮却报
# 「服务器返回异常」。这里记下启动那一刻的代码时间戳，用来发现这种情况。
# --------------------------------------------------------------------------
try:
    LOADED_MTIME = os.path.getmtime(os.path.abspath(__file__))
except OSError:
    LOADED_MTIME = 0.0
PROGRAM_START = datetime.datetime.now()


def program_stale():
    """app.py 在本进程启动之后又被改过 —— 说明跑的是旧代码，需要重启。"""
    try:
        return os.path.getmtime(os.path.abspath(__file__)) > LOADED_MTIME + 0.5
    except OSError:
        return False


# --------------------------------------------------------------------------
# 「关掉窗口就退出」
#
# 界面每 3 秒发一次心跳；关窗口（或刷新）时浏览器会补发一个 pagehide 通知。
# 收到通知后先不急，等 12 秒看看还有没有心跳：
#   · 还在跳 → 说明是刷新、或者还有别的窗口开着 → 不退出
#   · 没心跳 → 界面真的都关了 → 自己退出，不留后台进程
# --------------------------------------------------------------------------
CLIENT_HEARTBEAT = 3.0      # 界面心跳间隔
CLIENT_TIMEOUT = 8.0        # 超过这么久没心跳，就认为界面没了
QUIT_GRACE = 12.0           # 收到 pagehide 后等这么久再决定
_last_seen = 0.0
_quit_scheduled = False


def mark_client_alive():
    """界面还在（心跳或任意一次接口调用都算）"""
    global _last_seen
    _last_seen = time.time()


def schedule_auto_quit(server):
    """界面报告"窗口要关了"—— 稍后确认没人了就把自己关掉"""
    global _quit_scheduled
    if _quit_scheduled:
        return
    _quit_scheduled = True

    def worker():
        global _quit_scheduled
        time.sleep(QUIT_GRACE)
        idle = time.time() - _last_seen
        if idle < CLIENT_TIMEOUT:
            _quit_scheduled = False
            log("窗口只是刷新 / 或还有别的窗口开着，继续运行")
            return
        log("界面已全部关闭，自动退出")
        try:
            server.shutdown()
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


DEFAULT_CONFIG = {
    "repo_url": "",
    "token": "",
    "branch": "main",
    # 代理模式：
    #   auto   —— 每次联网前读取 Windows 系统代理（浏览器用的那个），推荐
    #   custom —— 用下面 proxy_value 里手填的地址
    #   none   —— 不使用代理（适合能直连 GitHub 的网络）
    "proxy_mode": "auto",
    "proxy_value": "",
    # 阅读面板的位置与宽度（用户可以在分界线上拖动 / 交换）
    "reader_side": "right",
    "reader_width": 440,
    "reader_font": 15,
    # 大事记的排序：new = 新在前，old = 旧在前
    "milestone_sort": "new",
    "volume": 0.6,
    "loop_mode": "all",
    "theme": "light",
    "music_auto_scan": True,
    "autoplay": True,
}

AUDIO_EXT = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".aac", ".flac", ".opus", ".weba"}

WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def ensure_dirs():
    for d in (WEB_DIR, DIARY_DIR, MUSIC_DIR, TRASH_DIR, LOG_DIR):
        os.makedirs(d, exist_ok=True)


def log(msg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(LOG_DIR, "app.log"), "a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass


def _inside(base, target):
    """target 是否真的落在 base 目录里面。

    不能直接 startswith 比字符串：web_backup / music_private 这种
    「同前缀的兄弟目录」会被误判成"在 web 里面"，从而被读出来。
    """
    base = os.path.normcase(os.path.abspath(base))
    target = os.path.normcase(os.path.abspath(target))
    return target == base or target.startswith(base + os.sep)


def read_text(path):
    """按 UTF-8 读取，容忍 BOM，并把换行统一成 LF。

    必须统一换行：Windows 上的 git（core.autocrlf=true）在检出文件时会把 LF
    换成 CRLF，那样每行末尾都会多一个 \\r，正文内容就会被污染。
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def write_text(path, text):
    """UTF-8 无 BOM、LF 换行 —— 与原文件规格一致。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(text.encode("utf-8"))
    os.replace(tmp, path)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.isfile(CONFIG_PATH):
        try:
            data = json.loads(read_text(CONFIG_PATH))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
        except Exception as exc:
            log(f"读取 config.json 失败：{exc}")
    return cfg


def save_config(cfg):
    write_text(CONFIG_PATH, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def safe_filename(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip()
    name = name.strip(". ")
    return name or "未署名"


# --------------------------------------------------------------------------
# 日记解析 / 生成
# --------------------------------------------------------------------------

DATE_HEAD_RE = re.compile(
    r"^\s*(\d{4})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]\s*(\d{1,2})\s*日?\s*(.*)$"
)
ITEM_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．:：)）]\s*(.*)$")
# 结束标记：必须顶格（缩进行是正文的续行，不能被当成结束），
# 并且只有"后面没有别的内容了"才算数 —— 见 find_end_index()
END_RE = re.compile(r"^(?:END|end|End|完|结束)\s*[.。!！]?\s*$")


def find_end_index(lines):
    """找出真正的结束标记在第几行；没有就返回 -1。

    规则：顶格的 END，且它后面全是空行。这样正文里单独一行 END（或"完"）
    就不会把后面的内容截断掉。
    """
    for i, line in enumerate(lines):
        if END_RE.match(line) and all(not x.strip() for x in lines[i + 1:]):
            return i
    return -1


class Entry(object):
    def __init__(self, path):
        self.path = path
        self.file = os.path.basename(path)
        self.id = os.path.splitext(self.file)[0]
        self.date = None          # datetime.date
        self.recorder = ""
        self.items = []
        self.warnings = []
        self.dirty_disk = False   # 解析结果与文件不一致（如缺 END）
        self.parse()

    # -- 解析 ------------------------------------------------------------
    def parse(self):
        text = read_text(self.path)
        lines = text.split("\n")
        # 去掉末尾空行，保留其余
        while lines and lines[-1].strip() == "":
            lines.pop()

        body = []
        if lines:
            head = lines[0]
            m = DATE_HEAD_RE.match(head)
            if m:
                y, mo, d, rec = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).strip()
                try:
                    self.date = datetime.date(y, mo, d)
                except ValueError:
                    self.warnings.append("日期数值非法：" + head.strip())
                self.recorder = rec
            else:
                self.warnings.append("首行不是「日期 记录人」格式，已尝试从文件名推断")
            body = lines[1:]

        # 文件名兜底：2026.7.13 伊丝塔.txt
        if self.date is None or not self.recorder:
            fm = DATE_HEAD_RE.match(self.id)
            if fm:
                if self.date is None:
                    try:
                        self.date = datetime.date(int(fm.group(1)), int(fm.group(2)), int(fm.group(3)))
                    except ValueError:
                        pass
                if not self.recorder:
                    self.recorder = fm.group(4).strip()

        end_at = find_end_index(body)
        terminated = end_at >= 0
        for line in (body[:end_at] if terminated else body):
            if line.strip() == "":
                continue
            m = ITEM_RE.match(line)
            if m:
                self.items.append(m.group(2).rstrip())
            elif self.items and (line.startswith("    ") or line.startswith("\t")):
                self.items[-1] = self.items[-1] + "\n" + line.strip()
            elif self.items:
                # 没有序号的行，接到上一条
                self.items[-1] = self.items[-1] + "\n" + line.strip()
                self.warnings.append("有一行缺少序号，已并入上一条")
            else:
                self.items.append(line.strip())
                self.warnings.append("正文首行缺少序号")

        if not terminated:
            self.warnings.append("文件末尾缺少 END")
            self.dirty_disk = True
        if not self.items:
            self.warnings.append("正文为空")

        try:
            self.mtime = os.path.getmtime(self.path)
        except OSError:
            self.mtime = 0

    # -- 序列化 ----------------------------------------------------------
    def date_label(self):
        if self.date is None:
            return ""
        return f"{self.date.year}.{self.date.month}.{self.date.day}"

    def to_json(self):
        d = self.date
        return {
            "id": self.id,
            "file": self.file,
            "date": d.isoformat() if d else "",
            "dateLabel": self.date_label(),
            "year": d.year if d else 0,
            "month": d.month if d else 0,
            "day": d.day if d else 0,
            "weekday": d.isoweekday() if d else 0,
            "recorder": self.recorder or "未署名",
            "items": self.items,
            "text": "\n".join(self.items),
            "count": len(self.items),
            "mtime": int(self.mtime),
            "warnings": self.warnings,
        }


def render_entry(date, recorder, items):
    """按原始格式生成文件内容。"""
    head = f"{date.year}.{date.month}.{date.day} {recorder}".rstrip()
    out = [head]
    for i, item in enumerate(items, 1):
        parts = str(item).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out.append(f"{i}.{parts[0].strip()}")
        for extra in parts[1:]:
            if extra.strip():
                out.append("    " + extra.strip())
    out.append("END")
    return "\n".join(out) + "\n"


def entry_filename(date, recorder):
    return f"{date.year}.{date.month}.{date.day} {safe_filename(recorder)}.txt"


# --------------------------------------------------------------------------
# 日记仓库
# --------------------------------------------------------------------------


def list_entries():
    entries = []
    if not os.path.isdir(DIARY_DIR):
        return entries
    for name in os.listdir(DIARY_DIR):
        if not name.lower().endswith(".txt"):
            continue
        path = os.path.join(DIARY_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            entries.append(Entry(path))
        except Exception as exc:
            log(f"解析失败 {name}：{exc}")
    entries.sort(key=lambda e: (e.date or datetime.date(1900, 1, 1), e.recorder), reverse=True)
    return entries


def find_entry(entry_id):
    for e in list_entries():
        if e.id == entry_id:
            return e
    return None


def _to_trash(path):
    """把文件挪进 .trash 而不是直接删掉。任何"销毁"操作都必须先经过这里。"""
    try:
        os.makedirs(TRASH_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(TRASH_DIR, f"{stamp} {os.path.basename(path)}")
        if os.path.exists(dest):
            dest = os.path.join(TRASH_DIR,
                                f"{stamp}-{datetime.datetime.now().microsecond} {os.path.basename(path)}")
        shutil.move(path, dest)
        return True
    except OSError as exc:
        log(f"移动到 .trash 失败（{path}）：{exc}")
        return False


def save_entry(date, recorder, items, original_id=None):
    recorder = (recorder or "").strip() or "未署名"
    items = [str(x).strip() for x in (items or []) if str(x).strip()]
    target = os.path.join(DIARY_DIR, entry_filename(date, recorder))
    old = find_entry(original_id) if original_id else None
    renaming = bool(old and os.path.abspath(old.path) != os.path.abspath(target))

    # 目标文件已经存在，而且不是"正在编辑的这一篇" → 这次保存会覆盖另一篇日记。
    # 先备份到 .trash，绝不静默销毁。（最容易踩：同一天同一个人点了两次「写日记」）
    if os.path.isfile(target) and not (old and os.path.abspath(old.path) == os.path.abspath(target)):
        _to_trash(target)

    # 关键顺序：先写新文件，成功之后才处理旧文件。
    # 反过来的话，一旦写入失败（磁盘满 / 进程被杀），旧日记就凭空消失了。
    write_text(target, render_entry(date, recorder, items))

    # 改了日期或记录人导致文件名变了：旧文件内容已进新文件，挪进 .trash 留个后路
    if renaming:
        _to_trash(old.path)

    return os.path.basename(target)


def delete_entry(entry_id):
    e = find_entry(entry_id)
    if not e:
        return False
    return _to_trash(e.path)


# --------------------------------------------------------------------------
# 大事记
#
# 和日记一样是纯文本，固定文件名 大事记.txt，放在程序根目录（会被一起同步）。
# 一条大事记 = 一个「日期 记录人 / 正文 / END」的块，块之间空行隔开：
#
#     2026.7.13 伊丝塔
#     鲸鱼娘好可爱
#     END
#
#     2026.7.20 夏柚
#     多行正文
#     也可以这样写
#     END
# --------------------------------------------------------------------------

MILESTONE_PATH = os.path.join(APP_DIR, "大事记.txt")


def _split_milestone_blocks(text):
    """把 大事记.txt 切成一块块的原始文本。

    只有当 END 后面（跳过空行）就是文件结尾、或者另一条的头部时，它才算结束符。
    这样正文里单独一行 END 就不会把后面的内容吃掉。
    """
    lines = text.split("\n")
    blocks, cur = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if END_RE.match(line):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or DATE_HEAD_RE.match(lines[j]):
                blocks.append(cur)
                cur = []
                i += 1
                continue
        cur.append(line)
        i += 1
    if cur:
        blocks.append(cur)
    return blocks


def parse_milestones():
    """解析 大事记.txt。文件不存在或没内容就返回空列表。"""
    events = []
    if not os.path.isfile(MILESTONE_PATH):
        return events

    seq = {}
    for raw in _split_milestone_blocks(read_text(MILESTONE_PATH)):
        lines = list(raw)
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue

        head = lines[0].strip()
        date, recorder = None, head
        m = DATE_HEAD_RE.match(head)
        if m:
            recorder = m.group(4).strip()
            try:
                date = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                date = None

        body = "\n".join(ln.rstrip() for ln in lines[1:]).strip()
        key = (date.isoformat() if date else "", recorder)
        seq[key] = seq.get(key, 0) + 1
        events.append({
            # id 用「日期|记录人|同组合内的序号」，稳定且能唯一定位
            "id": "%s|%s|%d" % (key[0], recorder, seq[key]),
            "date": date.isoformat() if date else "",
            "dateLabel": ("%d.%d.%d" % (date.year, date.month, date.day)) if date else "",
            "weekday": date.isoweekday() if date else 0,
            "recorder": recorder or "未署名",
            "text": body,
        })
    return events


def render_milestones(events):
    blocks = []
    for e in events:
        recorder = (e.get("recorder") or "").strip() or "未署名"
        d = (e.get("date") or "").strip()
        head = recorder
        if d:
            try:
                y, mo, dd = (int(x) for x in d.split("-"))
                head = "%d.%d.%d %s" % (y, mo, dd, recorder)
            except Exception:
                head = "%s %s" % (d, recorder)
        body = (e.get("text") or "").strip()
        blocks.append(head + (("\n" + body) if body else "") + "\nEND")
    return ("\n\n".join(blocks) + "\n") if blocks else ""


def save_milestones(events, backup=False):
    """写回 大事记.txt。

    backup=True 时（删除操作）先把旧文件挪进 .trash —— 误删了还能捞回来。
    新增则不必，因为它只是追加，不会弄丢已有的内容。
    """
    if backup and os.path.isfile(MILESTONE_PATH):
        _to_trash(MILESTONE_PATH)
    write_text(MILESTONE_PATH, render_milestones(events))
    return parse_milestones()


def milestone_add(date, recorder, text):
    events = parse_milestones()
    events.append({
        "date": date.isoformat() if date else "",
        "recorder": (recorder or "").strip() or "未署名",
        "text": (text or "").strip(),
    })
    return save_milestones(events)


def milestone_delete(mid):
    events = parse_milestones()
    keep = [e for e in events if e["id"] != mid]
    if len(keep) == len(events):
        return False, events
    return True, save_milestones(keep, backup=True)


# --------------------------------------------------------------------------
# 音乐
# --------------------------------------------------------------------------


def list_music():
    tracks = []
    src_file = os.path.join(MUSIC_DIR, "sources.txt")
    lines = []
    if os.path.isfile(src_file):
        for raw in read_text(src_file).split("\n"):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            lines.append(s)

    if not lines:
        # 没有清单（或清单里只有注释）→ 自动扫描 music 目录
        found = []
        for root, _dirs, files in os.walk(MUSIC_DIR):
            for f in files:
                if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                    rel = os.path.relpath(os.path.join(root, f), MUSIC_DIR)
                    found.append(rel.replace("\\", "/"))
        lines = sorted(found)

    for item in lines:
        if item.lower().startswith(("http://", "https://", "//")):
            parsed = urllib.parse.urlparse(item)
            name = urllib.parse.unquote(os.path.basename(parsed.path)) or item
            tracks.append({"name": name, "src": item, "kind": "url", "exists": True})
        else:
            rel = item.replace("\\", "/")
            full = os.path.normpath(os.path.join(MUSIC_DIR, rel))
            if not full.startswith(os.path.normpath(MUSIC_DIR)):
                continue
            tracks.append({
                "name": os.path.splitext(os.path.basename(rel))[0],
                "src": "/media/" + urllib.parse.quote(rel),
                "kind": "file",
                "exists": os.path.isfile(full),
                "rel": rel,
            })
    return {
        "tracks": tracks,
        "dir": MUSIC_DIR,
        "hasSources": bool(lines),
        "sourcesFile": os.path.isfile(src_file),
    }


# --------------------------------------------------------------------------
# Git 集成
# --------------------------------------------------------------------------


def _clean_url(url):
    """去掉 URL 里可能内嵌的用户名/密码，用于展示。"""
    return re.sub(r"//[^/@]*@", "//", url or "")


def _auth_url(url, token):
    if not token:
        return url
    m = re.match(r"^(https?)://(?:[^/@]*@)?(.+)$", url.strip(), re.I)
    if not m:
        return url
    scheme, rest = m.group(1), m.group(2)
    return f"{scheme}://x-access-token:{urllib.parse.quote(token, safe='')}@{rest}"


def git_available():
    return shutil.which("git") is not None


# --------------------------------------------------------------------------
# 代理
#
# 为什么需要这一套：浏览器会自动读取 Windows 的「系统代理」设置，git 不会。
# 所以浏览器能打开 GitHub，git 却直接撞墙（Recv failure / Connection was reset）。
# 这里负责把该用哪个代理算出来，并在每次联网前写进这个仓库的 git 配置。
# --------------------------------------------------------------------------


def system_proxy():
    """读取 Windows 系统代理（就是浏览器在用的那个），拿不到就返回空串。"""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
    except Exception:
        return ""

    if not enable:
        return ""
    server = str(server or "").strip()
    if not server:
        return ""
    # 可能是 "127.0.0.1:7897"，也可能是 "http=1.2.3.4:80;https=1.2.3.4:443" 这种分协议写法
    if "=" in server:
        parts = {}
        for chunk in server.split(";"):
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                parts[k.strip().lower()] = v.strip()
        server = parts.get("https") or parts.get("http") or ""
    if not server:
        return ""
    if "://" not in server:
        server = "http://" + server
    return server


def resolve_proxy(cfg):
    """按当前设置算出这次该用哪个代理。空串 = 不用代理。"""
    mode = str(cfg.get("proxy_mode") or "auto").strip().lower()
    if mode == "none":
        return ""
    if mode == "custom":
        val = str(cfg.get("proxy_value") or "").strip()
        if val and "://" not in val:
            val = "http://" + val
        return val
    return system_proxy()


def apply_proxy(cfg):
    """把生效的代理写进本仓库的 git 配置；不用代理就清掉。

    每次联网操作前都会调用，所以换端口、换机器、从别人那里拷来的文件夹，
    都会在第一次同步时自动纠正成当前这台电脑该用的值。
    """
    if not is_repo():
        return ""
    url = resolve_proxy(cfg)
    cur = run_git(["config", "--local", "--get", "http.proxy"])[1].strip()
    if url:
        if cur != url:
            run_git(["config", "--local", "http.proxy", url])
            run_git(["config", "--local", "https.proxy", url])
    elif cur:
        run_git(["config", "--local", "--unset", "http.proxy"])
        run_git(["config", "--local", "--unset", "https.proxy"])
    return url


# --------------------------------------------------------------------------
# 把 git 的英文报错翻成人话
# --------------------------------------------------------------------------

GIT_HINTS = [
    # 顺序有意义：越具体的放越前面
    (("recv failure", "connection was reset", "connection reset",
      "failed to connect", "could not connect to server",
      "connection timed out", "operation timed out",
      "network is unreachable", "could not resolve host"),
     "连不上 GitHub —— 网络到不了这个站点。请依次检查：\n"
     "   ① 代理软件开着吗？\n"
     "   ② 把「代理模式」改成「自动（读取系统代理）」，再点一次「一键同步」；\n"
     "   ③ 如果你的网络本来就能直连 GitHub，把代理模式改成「不使用代理」。"),
    (("authentication failed", "invalid username or password", "bad credentials",
      "401 unauthorized", "403 forbidden",
      "support for password authentication was removed"),
     "认证失败 —— 访问令牌不对或者已经过期。\n"
     "   去 GitHub 重新生成一个令牌（权限要含 Contents: Read and write），\n"
     "   填进「访问令牌」后重新点「保存并绑定远程」。"),
    (("repository not found", "returned error: 404", "remote: not found"),
     "找不到这个仓库。请核对「仓库地址」是否写对，\n"
     "   以及令牌的 Repository access 有没有勾选这个仓库。"),
    (("could not read username", "could not read password", "terminal prompts disabled",
      "no anonymous write access"),
     "没有可用凭据。请在「访问令牌」里填上令牌，再点一次。"),
    (("non-fast-forward", "fetch first", "rejected"),
     "远程有你还没下载的提交，所以被拒绝了。\n"
     "   先点「拉取下载」，再点「上传推送」（「一键同步」会自动按这个顺序做）。"),
    (("couldn't find remote ref", "no such ref was fetched"),
     "远程仓库还是空的，暂时没有可拉取的内容（第一次同步时正常）。"),
    (("ssl", "certificate", "schannel", "tls"),
     "TLS / 证书出错，通常说明代理没走通。\n"
     "   把代理模式改成「自动」再试，或确认代理软件已正常工作。"),
]


def explain_git_error(text, proxy=""):
    """把 git 的英文报错映射成一句中文建议；没有匹配就返回空串。"""
    low = (text or "").lower()
    if not low:
        return ""
    # 先看报错里出现的是不是「我们自己设置的那个代理地址」——
    # 这样不管用户在哪个端口，都能准确判断成代理问题
    if proxy:
        hostport = proxy.split("://")[-1].strip().lower()
        hostname = hostport.split(":")[0]
        if hostport and hostport in low:
            return proxy_down_hint(proxy)
        if hostname and hostname in low:
            return proxy_down_hint(proxy)
    for keys, hint in GIT_HINTS:
        if any(k in low for k in keys):
            return hint
    return ""


def proxy_down_hint(proxy):
    return ("连不上你设置的代理 " + proxy + "。请检查：\n"
            "   ① 代理软件是否正在运行？\n"
            "   ② 「代理」里的端口是否和代理软件里显示的一致？\n"
            "      （常见默认值：Clash Verge 7897、Clash for Windows 7890、v2rayN 10809）\n"
            "   ③ 也可以把代理模式改成「自动」，让程序自己去读系统代理。")


def run_git(args, token=None, timeout=180, cwd=None, strip=True):
    """执行 git 命令，返回 (ok, stdout, stderr)，输出里的令牌会被抹掉。

    strip=False 用于 `--porcelain -z` 这类机器可读输出：那些行的状态码前面
    可能是一个有意义的空格（如 " M 文件"），strip() 会把第一个条目的空格吃掉，
    导致路径被解析错位。
    """
    # core.quotepath=false：让中文文件名原样输出，而不是被转义成 \345\257\274 这种八进制
    cmd = ["git", "-c", "core.quotepath=false"] + args
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["LC_ALL"] = env.get("LC_ALL", "")
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or APP_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out, err = proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return False, "", "找不到 git 命令，请先安装 Git for Windows。"
    except subprocess.TimeoutExpired:
        return False, "", "git 命令超时（可能是网络问题或需要交互式登录）。"
    if token:
        out = out.replace(token, "***")
        err = err.replace(token, "***")
    if strip:
        return proc.returncode == 0, out.strip(), err.strip()
    return proc.returncode == 0, out.rstrip("\r\n"), err.rstrip("\r\n")


def is_repo():
    ok, out, _ = run_git(["rev-parse", "--is-inside-work-tree"])
    return ok and out.strip() == "true"


def git_status(cfg):
    st = {
        "available": git_available(),
        "isRepo": False,
        "branch": "",
        "remote": "",
        "changes": [],
        "ahead": 0,
        "behind": 0,
        "lastCommit": "",
        "inConflict": False,
    }
    if not st["available"]:
        return st
    if not is_repo():
        return st
    st["isRepo"] = True

    ok, out, _ = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if ok:
        st["branch"] = out.strip()

    ok, out, _ = run_git(["remote", "get-url", "origin"])
    st["remote"] = _clean_url(out.strip()) if ok else ""

    ok, out, _ = run_git(["status", "--porcelain"])
    if ok:
        st["changes"] = [ln for ln in out.split("\n") if ln.strip()]

    ok, out, _ = run_git(["log", "-1", "--pretty=%h %ad %s", "--date=format:%Y-%m-%d %H:%M"])
    if ok:
        st["lastCommit"] = out.strip()

    ok, out, _ = run_git(["rev-list", "--left-right", "--count", "@{u}...HEAD"])
    if ok and out:
        parts = out.split()
        if len(parts) == 2:
            st["behind"], st["ahead"] = int(parts[0]), int(parts[1])

    st["inConflict"] = any(
        ln[:2] in ("UU", "AA", "DD", "AU", "UA", "DU", "UD") for ln in st["changes"]
    )
    return st


def _ensure_identity():
    ok, out, _ = run_git(["config", "user.name"])
    if not ok or not out.strip():
        run_git(["config", "user.name", "群日记"])
    ok, out, _ = run_git(["config", "user.email"])
    if not ok or not out.strip():
        run_git(["config", "user.email", "diary@localhost"])


def git_init(cfg):
    if is_repo():
        return True, "当前目录已经是一个 Git 仓库。", ""
    log_lines = []
    ok, out, err = run_git(["init", "-b", cfg.get("branch") or "main"])
    if not ok:
        ok, out, err = run_git(["init"])
        if not ok:
            return False, out, err
    log_lines.append(out)
    write_text(GITIGNORE_PATH, GITIGNORE_BODY)
    write_text(GITATTRIBUTES_PATH, GITATTRIBUTES_BODY)
    _ensure_identity()
    run_git(["add", "-A"])
    run_git(["commit", "-m", "初始化群日记仓库"])
    if cfg.get("repo_url"):
        run_git(["remote", "remove", "origin"])
        run_git(["remote", "add", "origin", cfg["repo_url"]])
    apply_proxy(cfg)
    return True, "仓库已初始化。\n" + "\n".join(log_lines), ""


def git_set_remote(cfg):
    url = (cfg.get("repo_url") or "").strip()
    if not url:
        return False, "", "请先填写 GitHub 仓库地址。"
    if not is_repo():
        ok, out, err = git_init(cfg)
        if not ok:
            return ok, out, err
    run_git(["remote", "remove", "origin"])
    ok, out, err = run_git(["remote", "add", "origin", url])
    if not ok:
        return False, out, err
    px = apply_proxy(cfg)
    note = f"远程仓库已设置为 {_clean_url(url)}"
    note += f"\n代理：{px}" if px else "\n代理：不使用"
    return True, note, ""


def _pending_paths():
    """列出所有待提交的路径（含未跟踪），返回 {路径: 状态码}。

    -z     ：NUL 分隔的原始路径，中文名不会被转义或加引号
    -uall  ：把未跟踪的「目录」展开成一个个文件，否则拿到的是 "web/" 这种空名字
    strip=False：保留状态码前的空格，否则第一个条目的路径会错位
    """
    paths = {}
    ok, out, _ = run_git(["status", "--porcelain", "-z", "-uall"], strip=False)
    if not ok or not out:
        return paths
    items = [x for x in out.split("\x00") if x]
    i = 0
    while i < len(items):
        entry = items[i]
        if len(entry) > 3:
            code, path = entry[:2], entry[3:]
            paths[path] = code
            # 重命名时 -z 会再跟一条"原路径"
            if code[:1] in ("R", "C") and i + 1 < len(items):
                paths[items[i + 1]] = code
                i += 1
        i += 1
    return paths


def _describe_content(paths):
    """给「日记 / 音乐」这类用户内容生成一句看得懂的提交说明。"""
    diary_new, diary_edit, music_new, music_edit = [], [], [], []
    sources = False
    milestone = False
    for p, code in sorted(paths.items()):
        base = os.path.basename(p)
        is_new = code.strip() == "??"
        if p.startswith("diaries/"):
            (diary_new if is_new else diary_edit).append(os.path.splitext(base)[0])
        elif p == "music/sources.txt":
            sources = True
        elif p.startswith("music/"):
            (music_new if is_new else music_edit).append(os.path.splitext(base)[0])
        elif p == "大事记.txt":
            milestone = True
    bits = []
    if diary_new:
        bits.append("新增日记 " + "、".join(diary_new))
    if diary_edit:
        bits.append("修改日记 " + "、".join(diary_edit))
    if milestone:
        bits.append("更新大事记")
    if music_new:
        bits.append("新增音源 " + "、".join(music_new))
    if music_edit:
        bits.append("调整音源 " + "、".join(music_edit))
    if sources:
        bits.append("更新音源清单")
    return "；".join(bits) or "内容更新"


def git_commit(cfg, message=None):
    """把改动拆成两次提交，让历史看得懂：

      ① 日记 / 音乐（用户内容）—— 说明形如「新增日记 2026.10.4 伊丝塔」
      ② 程序与文档 —— 单独一次，列出改了哪些文件

    这样翻历史时能一眼分清"哪次是写了日记、哪次是改了程序"，
    而不是所有东西混进一个叫「更新日记」的提交里。
    """
    if not is_repo():
        return False, "", "还没有初始化 Git 仓库。"

    # 先把这两个文件补上，再读状态 —— 否则刚生成的它们不会进入本次提交
    write_text(GITIGNORE_PATH, GITIGNORE_BODY)
    write_text(GITATTRIBUTES_PATH, GITATTRIBUTES_BODY)

    paths = _pending_paths()
    if not paths:
        return True, "没有需要提交的改动。", ""

    _ensure_identity()

    content = {p: c for p, c in paths.items()
               if p.startswith(("diaries/", "music/")) or p == "大事记.txt"}
    other = {p: c for p, c in paths.items() if p not in content}

    made = []
    if content:
        run_git(["add", "-A", "--"] + sorted(content))
        msg1 = message or _describe_content(content)
        ok, out, err = run_git(["commit", "-m", msg1])
        if ok:
            made.append(msg1)
        elif "nothing to commit" not in (out + err):
            return False, out, err

    if other:
        run_git(["add", "-A", "--"] + sorted(other))
        names = sorted(other)
        head = "程序与文档：" + "、".join(names[:8]) + ("…" if len(names) > 8 else "")
        ok, out, err = run_git(["commit", "-m", head])
        if ok:
            made.append(head)
        elif "nothing to commit" not in (out + err):
            return False, out, err

    if not made:
        return True, "没有需要提交的改动。", ""
    return True, "已提交：\n  " + "\n  ".join(made), ""


def _resolve_conflicts(cfg):
    """冲突时：远程版本留在原文件，本地版本另存为「(本机副本)」，两边都不丢。"""
    # -z 输出以 NUL 分隔的原始路径，避免中文/空格路径被引号或八进制转义包住
    ok, out, _ = run_git(["diff", "--name-only", "--diff-filter=U", "-z"], strip=False)
    files = [f for f in out.split("\x00") if f.strip()]
    saved = []
    for rel in files:
        rel_posix = rel.replace("\\", "/")
        ok_ours, ours_text, _ = run_git(["show", f":2:{rel_posix}"])
        if not ok_ours:
            return False, saved, f"无法读取本地版本：{rel}"
        base = os.path.basename(rel_posix)
        stem = os.path.splitext(base)[0]
        stamp = datetime.datetime.now().strftime("%m%d-%H%M")
        dest_name = f"{stem} (本机副本 {stamp}).txt"
        dest_dir = os.path.dirname(os.path.join(APP_DIR, rel_posix))
        if not os.path.isdir(dest_dir):
            dest_dir = DIARY_DIR
        dest = os.path.join(dest_dir, dest_name)
        write_text(dest, ours_text)
        saved.append(os.path.basename(dest))
        ok_th, out_th, err_th = run_git(["checkout", "--theirs", "--", rel_posix])
        if not ok_th:
            return False, saved, f"切换远程版本失败：{rel} {err_th}"
        run_git(["add", "--", rel_posix])
    return True, saved, ""


def git_pull(cfg, auto_resolve=True):
    if not is_repo():
        return False, "", "还没有初始化 Git 仓库。"
    _ensure_clean_origin(cfg)
    apply_proxy(cfg)
    branch = cfg.get("branch") or "main"
    url = _auth_url(cfg.get("repo_url") or "", cfg.get("token") or "")
    args = ["pull", "--no-rebase"]
    if cfg.get("repo_url"):
        args += [url or "origin", branch]
    ok, out, err = run_git(args, token=cfg.get("token") or None)

    if ok:
        return True, "拉取完成。\n" + out, ""

    combined = out + "\n" + err
    low = combined.lower()

    # 全新空仓库：远程还没有这个分支，属于首次同步的正常现象，不能当成错误
    # （否则「一键同步」会在拉取这步中止，导致第一次永远推不上去）
    if ("couldn't find remote ref" in low or "no such ref was fetched" in low
            or "couldn't find remote ref head" in low):
        return True, "远程仓库还是空的，暂时没有可拉取的内容（第一次同步时正常）。", ""

    if "conflict" in low:
        if not auto_resolve:
            run_git(["merge", "--abort"])
            return False, "存在冲突，已放弃合并。", combined
        ok_r, saved, msg = _resolve_conflicts(cfg)
        if not ok_r:
            run_git(["merge", "--abort"])
            return False, "冲突自动处理失败，已放弃合并。", msg
        ok_c, out_c, err_c = run_git(["commit", "--no-edit"])
        if not ok_c:
            return False, "冲突已解决但提交失败。", out_c + err_c
        note = "检测到冲突，已自动处理：远程版本保留在原文件，本地版本另存为\n  " + "\n  ".join(saved)
        return True, note + "\n请检查这些副本后再推送。", ""
    return False, out, err


def _ensure_clean_origin(cfg):
    """保证 origin 指向不含令牌的干净地址。

    顺便修掉历史版本可能写进 .git/config 的带令牌地址 —— 把整个文件夹打包
    发给别人时，那等于直接把令牌送出去了。
    """
    url = (cfg.get("repo_url") or "").strip()
    if not url or not is_repo():
        return
    cur = run_git(["remote", "get-url", "origin"])[1].strip()
    if cur != url:
        run_git(["remote", "remove", "origin"])
        run_git(["remote", "add", "origin", url])
    branch = cfg.get("branch") or "main"
    rem = run_git(["config", "--local", "--get", f"branch.{branch}.remote"])[1].strip()
    if rem and rem != "origin":
        run_git(["config", f"branch.{branch}.remote", "origin"])


def git_push(cfg):
    if not is_repo():
        return False, "", "还没有初始化 Git 仓库。"
    branch = cfg.get("branch") or "main"
    token = cfg.get("token") or None
    url = _auth_url(cfg.get("repo_url") or "", cfg.get("token") or "")
    if not url:
        return False, "", "请先填写 GitHub 仓库地址。"

    _ensure_clean_origin(cfg)
    px = apply_proxy(cfg)

    # 刻意不使用 -u：git 会把带令牌的地址明文写进 .git/config，
    # 那样整个文件夹一打包分享，令牌就跟着泄露了。
    # --follow-tags：把本地打的版本标签（v1.0 之类）一起推上去，
    # 这样"回退到某个版本"才有明确的目标可用。
    ok, out, err = run_git(["push", "--follow-tags", url, f"HEAD:refs/heads/{branch}"], token=token)
    if not ok:
        return False, out, err

    # 手工建立 origin/<branch> 跟踪分支，并把上游指回干净的 origin
    if (cfg.get("repo_url") or "").strip():
        run_git(["fetch", url, f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], token=token)
        run_git(["config", f"branch.{branch}.remote", "origin"])
        run_git(["config", f"branch.{branch}.merge", f"refs/heads/{branch}"])
    return True, "推送完成。\n" + (out or "Everything up-to-date"), ""


def git_sync(cfg, message=None):
    """一键同步：提交 → 拉取 → 推送。任何一步真失败就停下并报告。"""
    steps = []
    ok1, out1, err1 = git_commit(cfg, message)
    steps.append("提交：" + ("成功" if ok1 else "失败"))
    if not ok1:
        return False, "\n".join(steps), err1
    ok2, out2, err2 = git_pull(cfg)
    steps.append("拉取：" + ("成功" if ok2 else "失败"))
    if not ok2:
        return False, "\n".join(steps) + "\n" + out2, err2
    ok3, out3, err3 = git_push(cfg)
    steps.append("推送：" + ("成功" if ok3 else "失败"))
    return ok3, "\n".join(steps) + "\n" + out3, err3


def git_clone(cfg, url, token):
    """克隆到临时目录，再把内容并入当前文件夹（保留本机 config.json）。"""
    if is_repo():
        return False, "", ("当前文件夹已经是一个 Git 仓库了，不需要再克隆。\n"
                           "如果只是想跟远程对齐，直接点「一键同步」就行。")
    tmp = os.path.join(APP_DIR, "_clone_tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    auth = _auth_url(url, token)
    # 克隆时也要走代理，用 -c 临时传给这一条命令
    px = resolve_proxy(cfg)
    clone_args = []
    if px:
        clone_args = ["-c", "http.proxy=" + px, "-c", "https.proxy=" + px]
    ok, out, err = run_git(clone_args + ["clone", auth, tmp], token=token, timeout=600)
    if not ok:
        shutil.rmtree(tmp, ignore_errors=True)
        return False, out, err

    merged = []
    src_git = os.path.join(tmp, ".git")
    dst_git = os.path.join(APP_DIR, ".git")
    if os.path.isdir(dst_git):
        shutil.rmtree(dst_git, ignore_errors=True)
    if os.path.isdir(src_git):
        shutil.move(src_git, dst_git)

    skip = {"config.json", ".git", "logs", ".trash", ".browser-profile", "__pycache__"}
    for name in os.listdir(tmp):
        if name in skip:
            continue
        s, d = os.path.join(tmp, name), os.path.join(APP_DIR, name)
        if os.path.isdir(s):
            if os.path.isdir(d):
                for root, _dirs, files in os.walk(s):
                    rel = os.path.relpath(root, s)
                    tgt_dir = os.path.join(d, rel) if rel != "." else d
                    os.makedirs(tgt_dir, exist_ok=True)
                    for f in files:
                        shutil.copy2(os.path.join(root, f), os.path.join(tgt_dir, f))
            else:
                shutil.move(s, d)
        else:
            shutil.copy2(s, d)
        merged.append(name)
    shutil.rmtree(tmp, ignore_errors=True)
    px = apply_proxy(cfg)
    note = "克隆完成，已合并：" + "、".join(merged)
    note += ("\n代理：" + px) if px else "\n代理：不使用"
    return True, note, ""


# --------------------------------------------------------------------------
# HTTP 服务
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "QunRiJi/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    # -- 输出辅助 --------------------------------------------------------
    def _headers(self, status, ctype, length, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_text(self, text, status=200, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self._headers(status, ctype, len(body))
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_file(self, path, ctype=None):
        if not os.path.isfile(path):
            self.send_text("404 Not Found", 404)
            return
        with open(path, "rb") as fh:
            body = fh.read()
        ctype = ctype or (mimetypes.guess_type(path)[0] or "application/octet-stream")
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._headers(200, ctype, len(body))
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_audio(self, path):
        if not os.path.isfile(path):
            self.send_text("404 音频不存在", 404)
            return
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end, status = 0, max(size - 1, 0), 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(size - int(m.group(2)), 0)
                    end = size - 1
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        extra = {"Accept-Ranges": "bytes"}
        if status == 206:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self._headers(status, ctype, length, extra)
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- 路由 ------------------------------------------------------------
    def do_GET(self):
        try:
            self.route("GET")
        except Exception:
            log("GET 处理异常：\n" + traceback.format_exc())
            self.send_json({"ok": False, "error": "服务器内部错误，详见 logs/app.log"}, 500)

    def do_POST(self):
        try:
            self.route("POST")
        except Exception:
            log("POST 处理异常：\n" + traceback.format_exc())
            self.send_json({"ok": False, "error": "服务器内部错误，详见 logs/app.log"}, 500)

    # ------------------------------------------------------------------
    # 安全闸门
    #
    # 服务只监听 127.0.0.1，但"仅本机"不等于安全：
    #  · 浏览器里任何一个网页都能往 http://127.0.0.1:8756 发跨站请求
    #    （text/plain 的 POST 属于"简单请求"，不触发预检，直接生效）
    #  · DNS rebinding 还能让恶意域名解析到 127.0.0.1，从而绕过同源策略
    # 所以下面三道闸门缺一不可。
    # ------------------------------------------------------------------

    def host_ok(self):
        """Host 必须是本机地址 —— 挡 DNS rebinding。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        if host.startswith("["):                 # [::1]:8756
            name = host.split("]")[0] + "]"
        elif ":" in host:
            name = host.rsplit(":", 1)[0]
        else:
            name = host
        return name in ("127.0.0.1", "localhost", "[::1]")

    def origin_ok(self):
        """带了 Origin 就必须正好是本站（含端口）—— 挡跨站请求伪造（CSRF）。

        同源页面自己发的 fetch 带的就是本站 Origin；没有 Origin 的（地址栏直接
        访问、本机脚本、curl）也放行，那不属于跨站场景。
        """
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        if origin.lower() == "null":
            return False
        try:
            port = self.server.server_address[1]
        except Exception:
            port = None
        allowed = {"http://127.0.0.1:%s" % port, "http://localhost:%s" % port}
        return origin.lower() in allowed

    def read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            # 必须把超出的部分读掉再回错：否则客户端还在发送、连接就被重置，
            # 它看到的是"连接中断"而不是一个清楚的 413。
            # 读的时候只丢弃、不缓存，所以不会占内存。
            remaining = min(length, MAX_DRAIN_BYTES)
            while remaining > 0:
                chunk = self.rfile.read(min(262144, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            return None                      # 调用方负责回 413
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def route(self, method):
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        cfg = load_config()

        # 闸门一：Host
        if not self.host_ok():
            self.send_json({"ok": False, "error": "非法的 Host，服务只接受本机访问"}, 403)
            return

        # 闸门二：跨站请求一律拒绝
        if not self.origin_ok():
            self.send_json({"ok": False, "error": "跨站请求已被拒绝（服务只对本机页面开放）"}, 403)
            return

        # ---- 静态资源 ----
        if method == "GET" and (path == "/" or path == "/index.html"):
            self.send_file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
            return
        if method == "GET" and path.startswith("/static/"):
            rel = path[len("/static/"):].replace("\\", "/")
            full = os.path.normpath(os.path.join(WEB_DIR, rel))
            if _inside(WEB_DIR, full):
                self.send_file(full)
            else:
                self.send_text("403", 403)
            return

        # ---- 音频流 ----
        if method == "GET" and path.startswith("/media/"):
            rel = path[len("/media/"):].replace("\\", "/")
            full = os.path.normpath(os.path.join(MUSIC_DIR, rel))
            if _inside(MUSIC_DIR, full):
                self.send_audio(full)
            else:
                self.send_text("403 越权访问", 403)
            return

        # 闸门三：改数据的请求必须是 JSON —— 逼浏览器发预检，跨站自然过不来。
        # 例外：页面卸载时用 sendBeacon 补发的那两条，它的 Content-Type 由浏览器
        # 决定、不好控；这两条也无害（最多让"没人了"时退出），Origin 闸门仍然管着。
        BEACON = ("/api/pagehide", "/api/alive")
        if path.startswith("/api/") and method == "POST" and path not in BEACON:
            ctype = (self.headers.get("Content-Type") or "").lower()
            if not ctype.startswith("application/json"):
                self.send_json({"ok": False,
                                "error": "Content-Type 必须是 application/json"}, 415)
                return

        # ---- API ----
        if path.startswith("/api/"):
            mark_client_alive()          # 有界面在调接口，就算它还活着

        if path == "/api/alive" and method == "POST":
            self.send_json({"ok": True, "timeout": CLIENT_TIMEOUT})
            return

        if path == "/api/pagehide":
            # 浏览器在页面卸载时补发的最后一条消息（sendBeacon）
            schedule_auto_quit(self.server)
            self.send_json({"ok": True, "grace": QUIT_GRACE})
            return

        if path == "/api/ping" and method == "GET":
            # 供新实例判断"是不是已经有一个群日记在跑"
            self.send_json({
                "ok": True,
                "app": APP_TAG,
                "pid": os.getpid(),
                "startedAt": PROGRAM_START.strftime("%Y-%m-%d %H:%M:%S"),
                "stale": program_stale(),
            })
            return

        if path == "/api/state" and method == "GET":
            entries = [e.to_json() for e in list_entries()]
            recorders = sorted({e["recorder"] for e in entries})
            years = sorted({e["year"] for e in entries if e["year"]}, reverse=True)
            self.send_json({
                "ok": True,
                "entries": entries,
                "recorders": recorders,
                "years": years,
                "music": list_music(),
                "milestones": parse_milestones(),
                "git": git_status(cfg),
                "proxy": {
                    "mode": cfg.get("proxy_mode") or "auto",
                    "value": cfg.get("proxy_value") or "",
                    "system": system_proxy(),
                    "effective": resolve_proxy(cfg),
                    "inRepo": (run_git(["config", "--local", "--get", "http.proxy"])[1].strip()
                               if is_repo() else ""),
                },
                "program": {
                    "stale": program_stale(),
                    "startedAt": PROGRAM_START.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "config": {k: v for k, v in cfg.items() if k != "token"},
                "hasToken": bool(cfg.get("token")),
                "paths": {"root": APP_DIR, "diaries": DIARY_DIR, "music": MUSIC_DIR},
            })
            return

        if path == "/api/entry" and method == "GET":
            eid = (query.get("id") or [""])[0]
            e = find_entry(eid)
            if not e:
                self.send_json({"ok": False, "error": "找不到这篇日记"}, 404)
                return
            self.send_json({"ok": True, "entry": e.to_json(), "raw": read_text(e.path)})
            return

        if path == "/api/milestones" and method == "GET":
            self.send_json({
                "ok": True,
                "milestones": parse_milestones(),
                "file": MILESTONE_PATH,
                "sort": cfg.get("milestone_sort") or "new",
            })
            return

        if path == "/api/music" and method == "GET":
            self.send_json({"ok": True, "music": list_music()})
            return

        if path == "/api/music/sources" and method == "GET":
            src = os.path.join(MUSIC_DIR, "sources.txt")
            text = read_text(src) if os.path.isfile(src) else ""
            self.send_json({"ok": True, "text": text})
            return

        if path == "/api/git/status" and method == "GET":
            self.send_json({"ok": True, "git": git_status(cfg)})
            return

        if path == "/api/config" and method == "GET":
            self.send_json({
                "ok": True,
                "config": {k: v for k, v in cfg.items() if k != "token"},
                "hasToken": bool(cfg.get("token")),
            })
            return

        if method != "POST":
            # 同样用 JSON 回，前端才能读懂并提示"该重启了"
            stale = program_stale()
            self.send_json({
                "ok": False,
                "stale": stale,
                "error": ("没有 " + (path or "/") + " 这个接口（也可能是程序版本不匹配）。"
                          + ("界面已经更新、但后台还是旧版程序，请点右上角 ⏻ 退出后重新打开。"
                             if stale else "")),
            }, 404)
            return

        body = self.read_json_body()
        if body is None:
            self.send_json({"ok": False,
                            "error": "请求体过大（上限 %d MB）" % (MAX_BODY_BYTES // 1024 // 1024)}, 413)
            return

        if path == "/api/save":
            try:
                y, m, d = (int(x) for x in str(body.get("date", "")).split("-"))
                date = datetime.date(y, m, d)
            except Exception:
                self.send_json({"ok": False, "error": "日期格式不正确"}, 400)
                return
            recorder = str(body.get("recorder", "")).strip()
            if not recorder:
                self.send_json({"ok": False, "error": "请填写记录人"}, 400)
                return
            items = body.get("items") or []
            if not isinstance(items, list):
                items = []
            try:
                name = save_entry(date, recorder, items, body.get("originalId") or None)
            except Exception as exc:
                log("保存失败：\n" + traceback.format_exc())
                self.send_json({"ok": False, "error": f"保存失败：{exc}"}, 500)
                return
            self.send_json({"ok": True, "file": name, "entries": [e.to_json() for e in list_entries()]})
            return

        if path == "/api/delete":
            eid = body.get("id") or ""
            if delete_entry(eid):
                self.send_json({"ok": True, "entries": [e.to_json() for e in list_entries()]})
            else:
                self.send_json({"ok": False, "error": "找不到这篇日记"}, 404)
            return

        if path == "/api/config":
            changed = {}
            for key in DEFAULT_CONFIG:
                if key in body and key != "token":
                    changed[key] = body[key]
            if "token" in body:
                changed["token"] = str(body["token"])
            cfg.update(changed)
            save_config(cfg)
            self.send_json({"ok": True, "config": {k: v for k, v in cfg.items() if k != "token"},
                            "hasToken": bool(cfg.get("token"))})
            return

        if path == "/api/music/sources":
            # 把音源清单写回 music/sources.txt
            text = body.get("text")
            if not isinstance(text, str):
                self.send_json({"ok": False, "error": "缺少 text"}, 400)
                return
            write_text(os.path.join(MUSIC_DIR, "sources.txt"), text.rstrip("\n") + "\n")
            self.send_json({"ok": True, "music": list_music()})
            return

        if path == "/api/quit":
            self.send_json({"ok": True, "message": "程序正在退出"})
            # 交给另一个线程关闭 serve_forever，避免在处理器线程里自锁
            threading.Thread(target=lambda: (time.sleep(0.3), self.server.shutdown()),
                             daemon=True).start()
            return

        if path == "/api/reveal":
            e = find_entry(body.get("id") or "")
            if not e:
                self.send_json({"ok": False, "error": "找不到这篇日记"}, 404)
                return
            try:
                subprocess.Popen(["explorer", "/select," + os.path.normpath(e.path)])
                self.send_json({"ok": True})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return

        if path == "/api/milestone":
            action = body.get("action") or ""
            if action == "add":
                recorder = str(body.get("recorder") or "").strip()
                text = str(body.get("text") or "").strip()
                if not recorder:
                    self.send_json({"ok": False, "error": "请填写记录人"}, 400)
                    return
                if not text:
                    self.send_json({"ok": False, "error": "正文不能为空"}, 400)
                    return
                date = None
                raw = str(body.get("date") or "").strip()
                if raw:
                    try:
                        y, mo, d = (int(x) for x in raw.split("-"))
                        date = datetime.date(y, mo, d)
                    except Exception:
                        self.send_json({"ok": False, "error": "日期格式不正确"}, 400)
                        return
                events = milestone_add(date, recorder, text)
                self.send_json({"ok": True, "milestones": events, "file": MILESTONE_PATH})
                return
            if action == "delete":
                ok2, events = milestone_delete(str(body.get("id") or ""))
                if not ok2:
                    self.send_json({"ok": False, "error": "找不到这条大事记"}, 404)
                    return
                self.send_json({"ok": True, "milestones": events, "file": MILESTONE_PATH})
                return
            if action == "sort":
                cfg["milestone_sort"] = "old" if str(body.get("sort")) == "old" else "new"
                save_config(cfg)
                self.send_json({"ok": True, "sort": cfg["milestone_sort"],
                                "milestones": parse_milestones(), "file": MILESTONE_PATH})
                return
            self.send_json({"ok": False, "error": "未知的大事记操作"}, 400)
            return

        if path == "/api/cleanup":
            killed = kill_stale_instances()
            self.send_json({
                "ok": True,
                "count": len(killed),
                "killed": killed,
                "message": ("已结束 %d 个残留进程：%s" % (len(killed), "、".join(killed))
                            if killed else "没有发现残留的其他实例。"),
            })
            return

        if path == "/api/open-folder":
            which = body.get("which") or "diaries"
            target = {"diaries": DIARY_DIR, "music": MUSIC_DIR, "root": APP_DIR}.get(which, APP_DIR)
            os.makedirs(target, exist_ok=True)
            try:
                os.startfile(target)  # noqa: S606 - Windows 专用
                self.send_json({"ok": True})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return

        if path == "/api/git":
            action = body.get("action") or ""
            if "token" in body:
                cfg["token"] = str(body["token"])
            if "repo_url" in body:
                cfg["repo_url"] = str(body["repo_url"]).strip()
            if "branch" in body:
                cfg["branch"] = str(body["branch"]).strip() or "main"
            if "proxy_mode" in body:
                cfg["proxy_mode"] = str(body["proxy_mode"]).strip().lower() or "auto"
            if "proxy_value" in body:
                cfg["proxy_value"] = str(body["proxy_value"]).strip()
            save_config(cfg)

            if action == "init":
                result = git_init(cfg)
            elif action == "set_remote":
                result = git_set_remote(cfg)
            elif action == "commit":
                result = git_commit(cfg, body.get("message"))
            elif action == "pull":
                result = git_pull(cfg, body.get("autoResolve", True))
            elif action == "push":
                result = git_push(cfg)
            elif action == "sync":
                result = git_sync(cfg, body.get("message"))
            elif action == "clone":
                result = git_clone(cfg, cfg.get("repo_url") or "", cfg.get("token") or "")
            elif action == "proxy":
                px = apply_proxy(cfg)
                syspx = system_proxy()
                msg = ("当前生效代理：" + px) if px else "当前不使用代理。"
                if not px and syspx:
                    msg += "\n（检测到系统代理 " + syspx + "，但当前模式是「不使用代理」）"
                if px and px == syspx:
                    msg += "\n（来自 Windows 系统代理，自动检测）"
                result = (True, msg, "")
            elif action == "status":
                result = (True, "已刷新仓库状态。", "")
            else:
                self.send_json({"ok": False, "error": "未知的 Git 操作"}, 400)
                return

            ok, out, err = result
            combined = (out or "") + "\n" + (err or "")
            self.send_json({
                "ok": ok,
                "output": out,
                "error": err,
                "hint": explain_git_error(combined, resolve_proxy(cfg)) if not ok else "",
                "git": git_status(cfg),
                "proxy": {
                    "mode": cfg.get("proxy_mode") or "auto",
                    "value": cfg.get("proxy_value") or "",
                    "system": system_proxy(),
                    "effective": resolve_proxy(cfg),
                    "inRepo": (run_git(["config", "--local", "--get", "http.proxy"])[1].strip()
                               if is_repo() else ""),
                },
                "entries": [e.to_json() for e in list_entries()],
            })
            return

        # 兜底：用 JSON 回，前端才读得懂；顺便提示"程序该重启了"
        stale = program_stale()
        self.send_json({
            "ok": False,
            "stale": stale,
            "error": ("没有 " + (path or "/") + " 这个接口。"
                      + ("界面已经更新、但后台还是旧版程序，请点右上角 ⏻ 退出后重新打开。"
                         if stale else "可能是程序版本不匹配。")),
        }, 404)
        return


class DiaryServer(ThreadingHTTPServer):
    """自定义服务器类。

    关键：allow_reuse_address 必须关掉。在 Windows 上 SO_REUSEADDR 的语义是
    「允许抢占已被别人监听的端口」，于是每次启动都能"成功"绑到 8756，
    结果十几个实例同时监听同一个端口、请求随机落到某个旧实例上
    —— 表现出来就是"新功能点了没反应"。
    """
    daemon_threads = True
    allow_reuse_address = False


def port_open(port):
    """真实连一下，判断端口上是不是已经有服务在监听（比 bind 可靠）"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def can_bind(port):
    """不带 SO_REUSEADDR 的试探绑定；Windows 上这样才准"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def find_free_port(preferred=8756):
    for port in [preferred] + list(range(preferred + 1, preferred + 40)):
        if port_open(port):
            continue
        if can_bind(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_running_instance(preferred=8756):
    """看看是不是已经有一个群日记在跑了；是的话返回它的地址。"""
    for port in [preferred] + list(range(preferred + 1, preferred + 40)):
        if not port_open(port):
            continue
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/ping" % port, timeout=1.2) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("app") == APP_TAG:
                return "http://127.0.0.1:%d/" % port
        except Exception:
            continue
    return ""


def kill_stale_instances():
    """结束掉还在跑本程序的其它进程。

    旧版本实例不认识 /api/ping，会一直霸占端口让请求打到错的地方，
    所以新版本启动时主动把它们清掉。只杀命令行里明确含本目录 app.py 的进程。
    """
    me = os.getpid()
    safe_dir = APP_DIR.replace("'", "''")
    script = (
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.ProcessId -ne %d -and $_.CommandLine -and "
        "$_.CommandLine -like '*%s*app.py*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; "
        "Write-Output $_.ProcessId }" % (me, safe_dir)
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [ln.strip() for ln in (proc.stdout or "").split("\n") if ln.strip()]
    except Exception as exc:
        log(f"清理旧实例失败：{exc}")
        return []


def listening_map():
    """{端口号: {进程号}} —— 一次 netstat 全拿到。

    netstat 只报端口和 PID，不需要读别的进程的命令行，
    权限要求比 WMI 低得多，所以在普通账户下也靠得住。
    """
    table = {}
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (proc.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                try:
                    port = int(parts[1].rsplit(":", 1)[-1])
                except ValueError:
                    continue
                table.setdefault(port, set()).add(parts[4])
    except Exception as exc:
        log(f"netstat 失败：{exc}")
    return table


def is_python_process(pid):
    """这个进程是不是 python？

    返回 True / False / None（判断不了）。用两条路子问，任一条答上来就行；
    都答不上来就返回 None —— 调用方只在明确 True 时才动手，绝不误杀。
    """
    # 路子一：tasklist（最快）
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "PID eq %s" % pid, "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = [x for x in (proc.stdout or "").strip().splitlines() if x.strip()]
        if rows and rows[0].lstrip().startswith('"'):
            return rows[0].lstrip('"').lower().startswith("python")
    except Exception:
        pass
    # 路子二：WMI
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"ProcessId=%s\").Name" % pid],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        name = (proc.stdout or "").strip().lower()
        if name and " " not in name:
            return name.startswith("python")
    except Exception:
        pass
    return None


def is_diary_instance(port):
    """这个端口上跑的是不是「本程序的正常实例」。

    返回 True（是本程序）/ False（确定不是，比如旧版本不认 /api/ping）/
    None（连不上，判断不了）。只有明确 False 才允许动手，宁可漏杀不可错杀。
    """
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/ping" % port, timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == APP_TAG
    except urllib.error.HTTPError:
        return False            # 服务器有应答，只是不认这个接口 → 旧版本
    except Exception:
        return None             # 连不上/超时 → 不确定，别乱动


def kill_port_squatters(low=8756, high=8796):
    """结束霸占端口的残留实例。

    只按「端口号 + 进程号」动手，不读别人的命令行，普通账户下也靠得住。
    两道保险：只杀 python 进程；能正常应答 /api/ping 的实例一律不动。
    """
    killed, me = [], str(os.getpid())
    table = listening_map()
    for port in sorted(table):
        if not (low <= port < high):
            continue
        if is_diary_instance(port) is not False:
            continue                       # 是本程序、或判断不了 → 都留着
        for pid in sorted(table[port]):
            if pid == me or pid == "0":
                continue
            if is_python_process(pid) is not True:
                log(f"端口 {port} 的进程 {pid} 不是 python 或判断不了，不动它")
                continue
            try:
                r = subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode == 0:
                    killed.append(pid)
                    log(f"已结束残留实例 PID {pid}（占着端口 {port}）")
                else:
                    log(f"结束 PID {pid} 失败：{((r.stdout or '') + (r.stderr or ''))[:200]}")
            except Exception as exc:
                log(f"结束 PID {pid} 异常：{exc}")
    return killed


BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]


def find_browser():
    for p in BROWSER_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    for exe in ("msedge", "chrome"):
        found = shutil.which(exe)
        if found:
            return found
    return None


def open_app_window(url):
    """用 Edge/Chrome 的应用模式打开，窗口独立、有自己的任务栏图标。"""
    browser = find_browser()
    if not browser:
        import webbrowser
        webbrowser.open(url)
        return "browser"
    args = [
        browser,
        f"--app={url}",
        "--window-size=1460,940",
        "--window-position=80,40",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--no-service-autorun",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-features=msEdgeIdentityFRE,msEdgeSignInFRE,msEdgeSyncPromo,EdgeIdentityFRE",
    ]
    try:
        subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "app"
    except Exception as exc:
        log(f"启动应用窗口失败：{exc}")
        import webbrowser
        webbrowser.open(url)
        return "browser"


def say(text=""):
    """安全地向控制台输出。

    pythonw 由批处理启动时会继承父进程的控制台句柄，而双击运行时那个控制台
    在批处理结束的瞬间就销毁了 —— 此时 print 可能抛 OSError，直接把程序崩在
    启动横幅上。这里一旦写失败就把 stdout/stderr 改接到日志文件，保证任何一句
    输出都不可能让程序起不来。
    """
    try:
        print(text)
        return
    except Exception:
        pass
    try:
        fh = open(os.path.join(LOG_DIR, "console.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = fh
        sys.stderr = fh
    except Exception:
        sys.stdout = None
        sys.stderr = None
    try:
        print(text)
    except Exception:
        pass


def main():
    ensure_dirs()
    if not os.path.isfile(GITIGNORE_PATH):
        write_text(GITIGNORE_PATH, GITIGNORE_BODY)
    if not os.path.isfile(GITATTRIBUTES_PATH):
        write_text(GITATTRIBUTES_PATH, GITATTRIBUTES_BODY)

    argv = sys.argv[1:]
    no_open = "--no-open" in argv or "--headless" in argv
    force_new = "--new" in argv          # 强制再起一个：不清理、不复用
    port = None
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                pass

    ensure_dirs()

    if not force_new and port is None:
        # ① 先按端口清残留：只认端口号和进程号，普通账户下也靠得住。
        #    能正常应答 /api/ping 的实例不动，所以不会误伤正在用的那一个。
        squatters = kill_port_squatters()
        if squatters:
            say("清理了 %d 个残留实例：%s" % (len(squatters), "、".join(squatters)))
            log("按端口清理残留实例：" + "、".join(squatters))
            time.sleep(0.8)

        # ② 已经有一个「新版群日记」在跑 → 直接把窗口指过去，不再重复启动
        running = find_running_instance()
        if running:
            say("检测到群日记已经在运行：" + running)
            say("直接把窗口切过去，不再重复启动。")
            log("发现已有实例，复用：" + running)
            if not no_open:
                open_app_window(running)
            return

        # ③ 再用 WMI 兜一次（有些实例可能没监听端口，比如刚好卡在启动阶段）
        killed = kill_stale_instances()
        if killed:
            say("清理了 %d 个残留的旧实例：%s" % (len(killed), "、".join(killed)))
            log("清理旧实例：" + "、".join(killed))
            time.sleep(0.8)

    if port is None:
        port = find_free_port()
    url = f"http://127.0.0.1:{port}/"

    try:
        httpd = DiaryServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        say("端口 %d 被别的程序占用了（%s），换一个端口再试。" % (port, exc))
        port = find_free_port(port + 1)
        url = f"http://127.0.0.1:{port}/"
        httpd = DiaryServer(("127.0.0.1", port), Handler)
    log(f"服务启动：{url}")

    say("=" * 56)
    say("  群日记管理系统")
    say(f"  地址：{url}")
    say(f"  日记目录：{DIARY_DIR}")
    say(f"  音乐目录：{MUSIC_DIR}")
    say("  退出程序：点界面右上角的 ⏻ 按钮。")
    say("=" * 56)

    if not no_open:
        threading.Thread(target=lambda: (time.sleep(0.6), open_app_window(url)), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        log("服务已停止")


if __name__ == "__main__":
    main()

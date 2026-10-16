"""docread — 添付された要件書（RFI・RFP・仕様書）から文字を取り出す（標準ライブラリのみ）。

  read(name, data) → {"text", "method", "pages", "warning"}

- .txt / .md：UTF-8（BOM つき可）か Shift_JIS。
- .docx：word/document.xml の段落と表（表のセルは「 | 」でつなぐ）。
- .pdf：ここにある小さな読み取り器で読む。読めない・ほとんど読めないときだけ、pdftotext（poppler）があればそれも試し、多く読めた方を使う。
  Word や一太郎が書き出した PDF（日本語の CID フォント＋ToUnicode、オブジェクトストリーム、FlateDecode）を想定している。
  スキャンした画像の PDF（文字が入っていない）は読めない。そのときは warning を返すので、本文を貼ってもらう。

取り出した文字は画面に出して、人が直してから使う（読み取りの誤りをそのまま要件にしないため）。
"""
import io
import re
import shutil
import subprocess
import tempfile
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_BYTES = 20 * 1024 * 1024


class ReadError(Exception):
    pass


def read(name, data):
    if len(data) > MAX_BYTES:
        raise ReadError(f"ファイルが大きすぎます（{len(data) // 1024 // 1024}MB。20MB まで）")
    ext = Path(name or "").suffix.lower()
    if ext in (".txt", ".md", ".csv", ""):
        return {"text": _decode(data), "method": "text", "pages": None, "warning": ""}
    if ext == ".docx":
        return {"text": docx_text(data), "method": "docx", "pages": None, "warning": ""}
    if ext == ".pdf":
        return pdf_read(data)
    raise ReadError(f"この形式は読めません（{ext}）。PDF・Word（.docx）・テキスト（.txt / .md）にしてください。")


def _decode(data):
    for enc in ("utf-8-sig", "cp932", "euc_jp"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# ---------------------------------------------------------------- Word（.docx）
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(data):
    try:
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise ReadError(f"Word ファイルとして読めません：{e}")
    body = ET.fromstring(xml).find(f"{W}body")
    out = []

    def para(p):
        s = []
        for el in p.iter():
            if el.tag == f"{W}t":
                s.append(el.text or "")
            elif el.tag == f"{W}tab":
                s.append("\t")
            elif el.tag in (f"{W}br", f"{W}cr"):
                s.append("\n")
        return "".join(s)
    for el in list(body if body is not None else []):
        if el.tag == f"{W}p":
            out.append(para(el))
        elif el.tag == f"{W}tbl":
            for tr in el.iter(f"{W}tr"):
                out.append(" | ".join(" ".join(para(p) for p in tc.iter(f"{W}p")).strip() for tc in tr.findall(f"{W}tc")))
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------- PDF
def pdf_read(data):
    """内蔵の読み取り器を先に使う（項目の記号「ア」「(1)」と本文が同じ行に残る）。読めない・ほとんど読めないときだけ pdftotext に頼る。"""
    if not data.startswith(b"%PDF"):
        raise ReadError("PDF として読めません")
    best, err = None, None
    try:
        pages = MiniPDF(data).pages_text()
        text = _tidy("\n\n".join(pages))
        best = {"text": text, "method": "builtin", "pages": len(pages), "warning": _warn(text)}
    except Exception as e:  # noqa: BLE001 未対応の作りの PDF：pdftotext に回す
        err = f"{type(e).__name__}: {e}"
    exe = shutil.which("pdftotext")
    if exe and (best is None or best["warning"]):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "in.pdf"
            f.write_bytes(data)
            r = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(f), "-"], capture_output=True, timeout=120)
        if r.returncode == 0 and r.stdout.strip():
            raw = r.stdout.decode("utf-8", "replace")
            text = _tidy(re.sub(r"(?<=\S)[ \u3000]{2,}(?=\S)", " ", raw.replace("\f", "\n\n")))  # 段組みの空白を詰める
            alt = {"text": text, "method": "pdftotext", "pages": raw.count("\f") or None, "warning": _warn(text)}
            if best is None or _score(alt["text"]) > _score(best["text"]):
                best = alt
    if best is None:
        raise ReadError(f"PDF から文字を取り出せませんでした（{err}）。PDF を開いて本文をコピーし、貼り付けてください。")
    return best


def _score(text):
    return len(re.sub(r"\s", "", text)) - 50 * text.count("\ufffd")


def _warn(text):
    n = len(re.sub(r"\s", "", text))
    if n < 50:
        return "文字がほとんど取れませんでした（スキャンした画像の PDF かもしれません）。PDF を開いて本文をコピーし、貼り付けてください。"
    if text.count("�") > n * 0.02:
        return "読めない文字が混じっています。下の欄で確かめて、必要なら直してください。"
    return ""


def _tidy(text):
    lines = [re.sub(r"[ \t　]+$", "", ln) for ln in text.replace("\r\n", "\n").split("\n")]
    out, blank = [], 0
    for ln in lines:
        if ln.strip():
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip() + "\n"


TOKEN = re.compile(rb"\s*(?:(%[^\r\n]*)|(\[)|(\])|(<<)|(>>)|(<[0-9A-Fa-f\s]*>)|(\()|(/[^\s/\[\]()<>{}%]*)|([+-]?(?:\d+\.?\d*|\.\d+))|([A-Za-z'\"*][A-Za-z0-9'\"*]*))")


class MiniPDF:
    """テキストを取り出すだけの、小さな PDF の読み取り器。描画はしない。"""

    def __init__(self, data):
        self.data = data
        self.objs = {}  # num -> bytes（オブジェクトの中身。ストリームは dict と生データを分けて持つ）
        self.streams = {}
        self._load()
        self.fonts = {}

    # -------- オブジェクト
    def _load(self):
        d = self.data
        for m in re.finditer(rb"(\d+)\s+(\d+)\s+obj\b", d):
            num = int(m.group(1))
            start = m.end()
            end = d.find(b"endobj", start)
            if end < 0:
                continue
            body = d[start:end]
            s = re.search(rb"stream\r?\n", body)
            if s and b"/Length" in body[:s.start()]:
                head = body[:s.start()]
                raw = body[s.end():]
                ln = re.search(rb"/Length\s+(\d+)(\s+\d+\s+R)?", head)
                if ln and not ln.group(2):
                    raw = raw[:int(ln.group(1))]
                else:
                    e = raw.rfind(b"endstream")
                    raw = raw[:e] if e >= 0 else raw
                self.objs[num] = head
                self.streams[num] = raw
            else:
                self.objs[num] = body
        for num, head in list(self.objs.items()):  # オブジェクトストリームの中身を広げる
            if num in self.streams and b"/ObjStm" in head:
                try:
                    s = self.stream(num)
                    first = int(re.search(rb"/First\s+(\d+)", head).group(1))
                    n = int(re.search(rb"/N\s+(\d+)", head).group(1))
                    nums = [int(x) for x in s[:first].split()]
                    pairs = [(nums[i], nums[i + 1]) for i in range(0, 2 * n, 2)]
                    for i, (on, off) in enumerate(pairs):
                        a = first + off
                        b = first + pairs[i + 1][1] if i + 1 < len(pairs) else len(s)
                        self.objs.setdefault(on, s[a:b])
                except (AttributeError, ValueError, IndexError, zlib.error):
                    continue

    def stream(self, num):
        raw, head = self.streams.get(num, b""), self.objs.get(num, b"")
        if b"/FlateDecode" in head or b"/Fl " in head or head.rstrip().endswith(b"/Fl"):
            try:
                return zlib.decompressobj().decompress(raw)
            except zlib.error:
                return zlib.decompressobj(-15).decompress(raw[2:])
        return raw

    def ref(self, body, key):
        m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", body)
        return int(m.group(1)) if m else None

    def sub(self, body, key):
        """/Key << ... >> の中身か、/Key n 0 R の先の中身。"""
        r = self.ref(body, key)
        if r is not None:
            return self.objs.get(r, b"")
        m = re.search(rb"/" + key + rb"\s*<<", body)
        if not m:
            return b""
        i, depth = m.end(), 1
        while i < len(body) and depth:
            if body[i:i + 2] == b"<<":
                depth, i = depth + 1, i + 2
            elif body[i:i + 2] == b">>":
                depth, i = depth - 1, i + 2
            else:
                i += 1
        return body[m.end():i - 2]

    # -------- ページ
    def pages(self):
        cat = next((n for n, b in self.objs.items() if re.search(rb"/Type\s*/Catalog", b)), None)
        out = []

        def walk(n, inherited, seen):
            if n in seen or n not in self.objs:
                return
            seen.add(n)
            b = self.objs[n]
            res = self.sub(b, b"Resources") or inherited
            if re.search(rb"/Type\s*/Pages", b):
                m = re.search(rb"/Kids\s*\[([^\]]*)\]", b)
                for k in re.findall(rb"(\d+)\s+\d+\s+R", m.group(1) if m else b""):
                    walk(int(k), res, seen)
            elif re.search(rb"/Type\s*/Page\b", b):
                out.append((n, res))
        if cat is not None:
            walk(self.ref(self.objs[cat], b"Pages"), b"", set())
        if not out:  # 目録がたどれないとき：ページらしいものを番号順に
            out = [(n, self.sub(b, b"Resources")) for n, b in sorted(self.objs.items()) if re.search(rb"/Type\s*/Page\b", b)]
        return out

    def pages_text(self):
        texts = []
        for n, res in self.pages():
            b = self.objs[n]
            m = re.search(rb"/Contents\s*\[([^\]]*)\]", b)
            refs = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", m.group(1))] if m else [self.ref(b, b"Contents")]
            content = b"\n".join(self.stream(r) for r in refs if r is not None)
            texts.append(self.run(content, res, 0))
        return texts

    # -------- フォント
    def font(self, res, name):
        fonts = self.sub(res, b"Font")
        r = re.search(rb"/" + re.escape(name) + rb"\s+(\d+)\s+\d+\s+R", fonts)
        key = int(r.group(1)) if r else name
        if key in self.fonts:
            return self.fonts[key]
        body = self.objs.get(key, b"") if r else b""
        two = b"/Type0" in body or b"Identity-H" in body or b"Identity-V" in body
        cmap = {}
        tu = self.ref(body, b"ToUnicode")
        if tu is not None:
            cmap = self.tounicode(self.stream(tu))
        self.fonts[key] = (2 if two else 1, cmap)
        return self.fonts[key]

    @staticmethod
    def _u(hexs):
        b = bytes.fromhex(hexs.decode() if isinstance(hexs, bytes) else hexs)
        try:
            return b.decode("utf-16-be")
        except UnicodeDecodeError:
            return "�"

    def tounicode(self, s):
        m = {}
        for blk in re.findall(rb"beginbfchar(.*?)endbfchar", s, re.S):
            for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                m[int(a, 16)] = self._u(b)
        for blk in re.findall(rb"beginbfrange(.*?)endbfrange", s, re.S):
            for a, b, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", blk):
                lo, hi = int(a, 16), int(b, 16)
                if rest.startswith(b"["):
                    for i, h in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", rest)):
                        m[lo + i] = self._u(h)
                else:
                    base = int(rest[1:-1], 16)
                    width = len(rest[1:-1])
                    for i in range(hi - lo + 1):
                        m[lo + i] = self._u(format(base + i, f"0{width}X"))
        return m

    def decode(self, raw, font):
        width, cmap = font
        out = []
        if width == 2:
            for i in range(0, len(raw) - 1, 2):
                c = raw[i] << 8 | raw[i + 1]
                out.append(cmap.get(c, "�" if cmap else ""))
        else:
            for c in raw:
                out.append(cmap.get(c) or (chr(c) if 32 <= c < 127 or c >= 160 else ""))
        return "".join(out)

    # -------- 中身のストリーム
    @staticmethod
    def literal(s, i):
        """( から始まる文字列を読み、(bytes, 次の位置) を返す。"""
        out, depth, i = bytearray(), 1, i + 1
        esc = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12, ord("("): 40, ord(")"): 41, ord("\\"): 92}
        while i < len(s) and depth:
            c = s[i]
            if c == 92 and i + 1 < len(s):
                n = s[i + 1]
                if n in esc:
                    out.append(esc[n])
                    i += 2
                elif 48 <= n <= 55:
                    j = i + 1
                    while j < len(s) and j < i + 4 and 48 <= s[j] <= 55:
                        j += 1
                    out.append(int(s[i + 1:j], 8) & 255)
                    i = j
                else:
                    i += 2  # 行の継続など
                continue
            if c == 40:
                depth += 1
            elif c == 41:
                depth -= 1
                if depth == 0:
                    return bytes(out), i + 1
            out.append(c)
            i += 1
        return bytes(out), i

    def tokens(self, s):
        i = 0
        while i < len(s):
            m = TOKEN.match(s, i)
            if not m or m.end() == i:
                i += 1
                continue
            i = m.end()
            g = m.lastindex
            if g == 1:
                continue
            if g == 7:
                v, i = self.literal(s, m.end() - 1)
                yield ("str", v)
            elif g == 6:
                yield ("str", bytes.fromhex(re.sub(rb"\s", b"", m.group(6)[1:-1]).decode() + ("0" if len(re.sub(rb"\s", b"", m.group(6)[1:-1])) % 2 else "")))
            elif g == 8:
                yield ("name", m.group(8)[1:])
            elif g == 9:
                yield ("num", float(m.group(9)))
            elif g == 10:
                if m.group(10) == b"BI":  # 埋め込み画像は飛ばす
                    e = s.find(b"EI", i)
                    i = e + 2 if e >= 0 else len(s)
                    continue
                yield ("op", m.group(10))
            else:
                yield ("punct", m.group(g))

    def run(self, content, res, depth):
        out, line, stack, arr = [], [], [], None
        font, y, lead = (1, {}), None, 0.0

        def newline():
            if line:
                out.append("".join(line))
                line.clear()
        for kind, v in self.tokens(content):
            if kind == "punct":
                if v == b"[":
                    arr = []
                elif v == b"]" and arr is not None:
                    stack.append(("arr", arr))
                    arr = None
                continue
            if kind != "op":
                (arr if arr is not None else stack).append((kind, v))
                continue
            op, args = v, stack
            stack = []
            if op == b"Tf" and len(args) >= 2 and args[0][0] == "name":
                font = self.font(res, args[0][1])
            elif op in (b"Tj", b"'", b'"'):
                if op != b"Tj":
                    newline()
                for k, a in args:
                    if k == "str":
                        line.append(self.decode(a, font))
            elif op == b"TJ" and args and args[-1][0] == "arr":
                for k, a in args[-1][1]:
                    if k == "str":
                        line.append(self.decode(a, font))
                    elif k == "num" and a < -300 and line and line[-1][-1:].isascii() and line[-1][-1:].isalnum():
                        line.append(" ")
            elif op in (b"Td", b"TD") and len(args) >= 2:
                if op == b"TD":
                    lead = -args[1][1]
                if abs(args[1][1]) > 0.5:
                    newline()
            elif op == b"Tm" and len(args) >= 6:
                ny = args[5][1]
                if y is not None and abs(ny - y) > 0.5:
                    newline()
                y = ny
            elif op == b"T*":
                newline()
            elif op == b"ET":
                pass
            elif op == b"Do" and args and args[0][0] == "name" and depth < 3:
                xo = self.sub(res, b"XObject")
                r = re.search(rb"/" + re.escape(args[0][1]) + rb"\s+(\d+)\s+\d+\s+R", xo)
                if r and b"/Form" in self.objs.get(int(r.group(1)), b""):
                    n = int(r.group(1))
                    newline()
                    t = self.run(self.stream(n), self.sub(self.objs[n], b"Resources") or res, depth + 1)
                    if t.strip():
                        out.append(t)
        newline()
        del lead
        return "\n".join(out)

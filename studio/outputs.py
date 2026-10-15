"""outputs — 原案とたたきの描き出し・機械検査・書き出し（標準ライブラリのみ）。

たたき（Issue #10）は、それ自体をスライドにはしない。PowerPoint の Copilot・Claude・ChatGPT・Gemini などの
「スライドを作れる道具」に渡す材料として、1枚＝1メッセージの構成案と、道具ごとの依頼文を出す。
  - Markdown：Claude・ChatGPT・Gemini に貼る／添付する（依頼文つき）
  - Word（.docx）：PowerPoint の Copilot の「ファイルからプレゼンテーションを作成」に渡す（見出し1＝1枚）
  - Marp：AI を通さずに、そのままスライドとして見る・PDF にする（Marp CLI・VS Code の Marp 拡張）
数字は原案にあるものだけを使わせ、原案にない数字が出たら機械で警告する（deck_lint）。
"""
import datetime
import io
import re
import zipfile
from xml.sax.saxutils import escape

TOOLS = {"copilot": "PowerPoint の Copilot", "claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini"}


# ---------------------------------------------------------------- 原案
def draft_markdown(d, sections, meta=None):
    out = [f"# {d.get('title', '（無題）')}"]
    if d.get("catch"):
        out.append(f"\n> {d['catch']}")
    if meta:
        out.append("\n" + "\n".join(f"- {k}：{v}" for k, v in meta.items()))
    for k, label, _ in sections:
        out.append(f"\n## {label}\n\n{str(d.get(k) or '').strip()}")
    checks = d.get("checks") or []
    if checks:
        out.append("\n## 要確認事項（推定・仮定・未調査）\n\n" + "\n".join(f"- {c}" for c in checks))
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------- たたきの機械検査
NUM = re.compile(r"\d[\d,，.]*(?:\.\d+)?")


def _nums(text):
    """2桁以上か小数の数字（1〜9 の数は段落番号などと紛れるので見ない）。カンマは外して比べる。"""
    out = set()
    for m in NUM.findall(str(text or "")):
        n = re.sub(r"[,，]", "", m).rstrip(".")
        if len(n.replace(".", "")) >= 2 or "." in n:
            out.add(n)
    return out


def deck_lint(deck, draft, want_slides):
    errors, warnings = [], []
    slides = (deck or {}).get("slides") or []
    if not slides:
        return {"ok": False, "errors": ["スライドがありません"], "warnings": [], "new_numbers": []}
    if abs(len(slides) - int(want_slides)) > max(1, int(want_slides) // 6):
        warnings.append(f"枚数が指定と違います（{len(slides)}枚／指定{want_slides}枚）")
    for i, s in enumerate(slides, 1):
        if not str(s.get("message", "")).strip():
            errors.append(f"{i}枚目：言いたいこと（message）がありません")
        elif len(str(s["message"])) > 60:
            warnings.append(f"{i}枚目：言いたいことが長い（{len(str(s['message']))}字。一文・40字前後に）")
        b = s.get("bullets") or []
        if len(b) > 5:
            warnings.append(f"{i}枚目：箇条が多い（{len(b)}個。5個まで）")
        long_b = [x for x in b if len(str(x)) > 50]
        if long_b:
            warnings.append(f"{i}枚目：長い箇条が {len(long_b)} 個（50字超）")
    src = _nums(" ".join(str(v) for v in (draft or {}).values() if isinstance(v, str)) + " " + " ".join(map(str, (draft or {}).get("checks") or [])))
    used = set()
    for s in slides:
        used |= _nums(" ".join([str(s.get("title", "")), str(s.get("message", "")), " ".join(map(str, s.get("bullets") or [])), str(s.get("visual", ""))]))
    new = sorted(used - src)
    if new:
        warnings.append("原案にない数字がスライドにあります（作られた数字かもしれません）：" + "、".join(new[:12]))
    return {"ok": not errors, "errors": errors, "warnings": warnings, "new_numbers": new}


# ---------------------------------------------------------------- 依頼文
COMMON_RULES = """- 1枚のスライドは構成案の{UNIT}1つです。順番・枚数を変えないでください。
- 「言いたいこと」はそのスライドの結論です。タイトルの下に大きく置いてください。
- 「箇条」は本文です。言い回しは整えてよいですが、意味と数字は変えないでください。
- 「図・表」の指示があるスライドは、指示どおりの図・表・グラフを作ってください。数字は構成案にあるものだけを使い、無い数字を作らないでください。
- 「話すこと」は発表者ノートに入れてください（スライドには載せない）。
- 日本語のフォントで、1枚あたりの文字を少なく。全体のトーンは{TONE}。"""

TONES = {"approve": "落ち着いた社内向け（判断材料が一目で分かる）", "customer": "顧客向けの明るく信頼できる印象", "share": "軽く、議論を誘う"}


def tool_prompt(tool, deck, opts):
    tone = TONES.get(opts.get("purpose"), TONES["approve"])
    rules = COMMON_RULES.replace("{TONE}", tone).replace("{UNIT}", "見出し1" if tool == "copilot" else "「## 」")
    head = f"「{deck.get('title', '')}」の企画書を、{opts['audience']}向けに {len(deck.get('slides') or [])} 枚・{opts['minutes']} 分で発表するスライドにしてください。"
    if tool == "copilot":
        return (f"{head}\n添付の Word ファイルは構成案です。見出し1がスライドのタイトル、その下の見出し2が「言いたいこと」です。\n{rules}\n\n"
                "（使い方：この .docx を OneDrive か SharePoint に置き、PowerPoint の Copilot で「ファイルからプレゼンテーションを作成」を選んでこのファイルを指定し、"
                "上の依頼文を追加の指示として貼ります。デザインはそのあと Designer で選べます。）")
    if tool == "claude":
        return f"{head}\n下（または添付）の Markdown が構成案です。PowerPoint ファイル（.pptx）として作ってください。\n{rules}"
    if tool == "chatgpt":
        return f"{head}\n下（または添付）の Markdown が構成案です。PowerPoint ファイル（.pptx）を作って、ダウンロードできるようにしてください。\n{rules}"
    return f"{head}\n下（または添付）の Markdown が構成案です。Google スライドに書き出せる形でスライドを作ってください。\n{rules}"


# ---------------------------------------------------------------- Markdown・Marp
def _slide_md(i, s, level="##"):
    out = [f"{level} {i}. {s.get('title', '')}", "", f"**言いたいこと：** {s.get('message', '')}"]
    b = s.get("bullets") or []
    if b:
        out += ["", "箇条："] + [f"- {x}" for x in b]
    if s.get("visual"):
        out += ["", f"図・表：{s['visual']}"]
    if s.get("notes"):
        out += ["", f"話すこと：{s['notes']}"]
    if s.get("source"):
        out += ["", f"<!-- 原案の出どころ：{s['source']} -->"]
    return "\n".join(out)


def deck_markdown(deck, opts, tool="claude"):
    slides = deck.get("slides") or []
    parts = ["<!-- 依頼文（この部分を最初に貼ってください） -->", tool_prompt(tool, deck, opts), "", "---", "",
             f"# {deck.get('title', '')}"]
    if deck.get("subtitle"):
        parts.append(f"\n{deck['subtitle']}")
    parts.append(f"\n- 聞き手：{opts['audience']}\n- 枚数：{len(slides)} 枚／{opts['minutes']} 分")
    for i, s in enumerate(slides, 1):
        parts.append("\n" + _slide_md(i, s))
    return "\n".join(parts).strip() + "\n"


def deck_marp(deck):
    out = ["---", "marp: true", "paginate: true", f"title: {deck.get('title', '')}", "---", ""]
    for i, s in enumerate(deck.get("slides") or [], 1):
        if i > 1:
            out += ["", "---", ""]
        out += [f"# {s.get('title', '')}", "", f"**{s.get('message', '')}**", ""]
        out += [f"- {x}" for x in (s.get("bullets") or [])]
        if s.get("visual"):
            out += ["", f"> 図・表：{s['visual']}"]
        if s.get("notes"):
            out += ["", f"<!-- {str(s['notes']).replace('-->', '→')} -->"]  # Marp では HTML コメントが発表者ノートになる
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------- Word（.docx）
_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""
_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _style(sid, name, size, bold=False, color=None, before=0, after=120, outline=None, ind=None, based="Normal"):
    ppr = f'<w:spacing w:before="{before}" w:after="{after}"/>' + (f'<w:outlineLvl w:val="{outline}"/>' if outline is not None else "") + \
          (f'<w:ind w:left="{ind}" w:hanging="240"/>' if ind else "") + ('<w:keepNext/>' if outline is not None else "")
    rpr = ('<w:b/>' if bold else "") + (f'<w:color w:val="{color}"/>' if color else "") + f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="{based}"/>'
            f'<w:qFormat/><w:pPr>{ppr}</w:pPr><w:rPr>{rpr}</w:rPr></w:style>')


_STYLES = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {_W}>'
           '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Yu Gothic" w:hAnsi="Yu Gothic" w:eastAsia="游ゴシック" w:cs="Arial"/>'
           '<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="ja-JP" w:eastAsia="ja-JP"/></w:rPr></w:rPrDefault>'
           '<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
           '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
           + _style("Title", "Title", 44, True, "1F2429", 0, 120)
           + _style("Subtitle", "Subtitle", 26, False, "555D63", 0, 240)
           + _style("Heading1", "heading 1", 32, True, "2C4A6E", 360, 120, outline=0)
           + _style("Heading2", "heading 2", 24, True, "1F2429", 160, 60, outline=1)
           + _style("ListBullet", "List Bullet", 21, ind=480)
           + _style("Note", "Note", 19, False, "555D63", 60, 60)
           + "</w:styles>")


def _p(text, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{escape(str(text))}</w:t></w:r></w:p>'


def deck_docx(deck, opts):
    """見出し1＝スライド1枚。言いたいこと・箇条・図表の指示は本文、話すことは「発表者ノート：」の段落にする。"""
    body = [_p(deck.get("title", ""), "Title")]
    sub = deck.get("subtitle") or f"{opts['audience']}向け・{len(deck.get('slides') or [])}枚・{opts['minutes']}分"
    body.append(_p(sub, "Subtitle"))
    for s in deck.get("slides") or []:
        body.append(_p(s.get("title", ""), "Heading1"))
        body.append(_p(s.get("message", ""), "Heading2"))
        for x in s.get("bullets") or []:
            body.append(_p("・" + str(x), "ListBullet"))
        if s.get("visual"):
            body.append(_p("図・表：" + str(s["visual"]), "Note"))
        if s.get("notes"):
            body.append(_p("発表者ノート：" + str(s["notes"]), "Note"))
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_W}><w:body>' + "".join(body) +
           '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
           '</w:body></w:document>')
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(str(deck.get("title", "")))}</dc:title><dc:creator>plan_studio</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{ts}</dcterms:created></cp:coreProperties>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()

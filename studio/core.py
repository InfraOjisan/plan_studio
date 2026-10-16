"""core — plan_studio の工程（テーマ → 提案 → 分類・選ぶ → 企画原案 → 企画書たたき）。

Web 画面（server.py）とハーネス向けの CLI（cli.py）の両方がこのモジュールだけを呼ぶ。
状態はすべて works/<slug>/studio.json とその下のファイルにあり、会話や画面の記憶に依存しない。
novel_studio v1.1.1 の studio から分けた（提案のコンペ・骨格での分類・費用の記録・テーマの派生はそのまま）。
"""
import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKS = Path(os.environ.get("PLAN_WORKS_DIR") or os.environ.get("NOVEL_WORKS_DIR") or REPO / "works")
sys.path.insert(0, str(HERE))
import llm  # noqa: E402
import similarity  # noqa: E402
import outputs  # noqa: E402

LOCKS = {}
LOCKS_GUARD = threading.Lock()
SETTINGS_FILE = HERE / "settings.json"
DEFAULT_SETTINGS = {
    "propose_models": ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna-pro", "deepseek/deepseek-v4.1-flash"],
    "skeleton_model": "openai/gpt-6-luna-pro",
    "draft_model": "anthropic/claude-opus-5.5",   # 原案：中身を作る工程なので上位モデル
    "deck_model": "openai/gpt-6-luna-pro",        # たたき：中身は原案で決まっている。並べ替えと要約だけなので中位で足りる
    "threshold": 0.5,
    "skeleton_batch": 3,      # 骨格の抜き出しで、1回に頼む案の数（多いと上流のプロバイダが時間切れで諦める）
    "skeleton_parallel": 2,   # 骨格の抜き出しを同時に何本まで走らせるか
    "model_recent_days": 90,  # モデルの一覧に、ふだん出す「新しいモデル」の日数（ほかは検索か「すべて表示」で）
}


class StudioError(Exception):
    pass


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def settings():
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    return s


def lock(slug):
    with LOCKS_GUARD:
        return LOCKS.setdefault(slug, threading.RLock())


def wdir(slug):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", slug or ""):
        raise StudioError(f"企画フォルダの名前「{slug}」は英数字・-・_ の60字以内にしてください")
    return WORKS / slug


def load(slug):
    f = wdir(slug) / "studio.json"
    if not f.exists():
        raise StudioError(f"企画 {slug} がありません")
    st = json.loads(f.read_text(encoding="utf-8"))
    st.setdefault("drafts", [])
    st.setdefault("decks", [])
    return st


def save(slug, st):
    f = wdir(slug) / "studio.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def record_cost(slug, stage, rec, note=""):
    d = wdir(slug) / "studio"
    d.mkdir(exist_ok=True)
    with open(d / "usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "stage": stage, "note": note, **rec}, ensure_ascii=False) + "\n")


def costs(slug):
    """工程ごとの費用。"""
    out = {"studio": {}, "studio_usd": 0.0, "estimated": 0, "unpriced": 0}
    f = wdir(slug) / "studio" / "usage.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = out["studio"].setdefault(r["stage"], {"usd": 0.0, "calls": 0})
            b["usd"] += float(r.get("usd") or 0)
            b["calls"] += 1
            out["studio_usd"] += float(r.get("usd") or 0)
            out["estimated"] += 1 if r.get("price") == "estimated" else 0
            out["unpriced"] += 1 if r.get("price") == "unknown" else 0
    for b in out["studio"].values():
        b["usd"] = round(b["usd"], 5)
    out["studio_usd"] = out["total_usd"] = round(out["studio_usd"], 5)
    return out


def prompt(name, **kw):
    t = (HERE / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", str(v))  # .format は JSON の { } で壊れるので使わない
    return t


# ---------------------------------------------------------------- 一 テーマ（Issue #7。v0.3.0 で 5W1H に定義し直した）
# What（要件。必ず守る）＝ ①添付の要件書（RFI など：theme["rfi"]）→ ②テーマ・コンセプト・題材 の順に優先する。
# How＝ジャンル（実現手段）。誰に＝顧客層、誰が＝チーム人数、いつ＝リリース時期。
# 必ず入れたいこと＝要件以外で特色を出すところ。入れたくないこと＝競っても無駄・考えなくてよいこと。
THEME_FIELDS = {"genre": "ジャンル", "customer": "顧客層", "theme": "テーマ・コンセプト・題材",
                "team": "開発規模（チーム人数）", "period": "開発規模（リリースまでの期間）",
                "must": "必ず入れたいこと", "must_not": "入れたくないこと",
                "research": "その他・マーケット情報など調査事項",
                "company": "自社の前提（強み・既存顧客・使える技術・避けたい領域）", "goal": "目標の規模感"}
THEME_DEFAULTS = {}
THEME_LABELS = {**THEME_FIELDS, "rfi": "添付の要件書（RFI など）"}
RFI_MAX = 80000  # 要件書の文字数の上限（すべてのモデルに毎回渡すので）
THEME_GROUPS = [  # (見出し, [(欄, 依頼文での名前)])
    ("What（要件。必ず守る。①→②の順に優先し、食い違ったら①に従う）",
     [("theme", "② テーマ・コンセプト・題材")]),
    ("How（実現手段。要件を満たすための手段で、要件そのものではない）", [("genre", "ジャンル・実現手段")]),
    ("誰に・誰が・いつ", [("customer", "誰に（顧客層）"), ("team", "誰が（開発体制・チーム人数）"), ("period", "いつ（リリース時期）")]),
    ("特色と割り切り", [("must", "差別化のポイント（要件以外で特色を出すところ。必ず入れる）"),
                     ("must_not", "競わない・考えなくてよいこと（ここに力を割かない。案に入れない）")]),
    ("そのほか", [("research", "調べてほしいこと（競合・国内外の市場差など）"), ("company", "自社の前提（強み・既存顧客・使える技術・避けたい領域）"),
              ("goal", "目標の規模感")]),
]


def theme_text(theme):
    """依頼文に入れるテーマ（5W1H の順）。要件書の本文は rfi_block で別の資料として渡す。"""
    out = []
    rfi = has_rfi(theme)
    for head, items in THEME_GROUPS:
        lines = [f"- {label}：{theme[k]}" for k, label in items if str(theme.get(k, "")).strip()]
        if head.startswith("What"):
            if rfi:
                lines.insert(0, f"- ① 添付の要件書「{theme.get('rfi_name') or '要件書'}」：別の資料「要件①」の全文（最優先）")
            else:  # 要件書がないときは、順番の話をしない
                head = "What（要件。必ず守る）"
                lines = [ln.replace("- ② ", "- ") for ln in lines]
            if not lines:
                lines = ["- （要件の指定なし。テーマの他の欄から自由に）"]
        if lines:
            out.append(f"## {head}\n" + "\n".join(lines))
    return "\n\n".join(out) or "- （指定なし。自由に）"


def rfi_block(theme):
    """添付の要件書の本文（あれば）。依頼文のテーマより前に置く。"""
    t = str((theme or {}).get("rfi", "")).strip()
    if not t:
        return ""
    return (f"\n<資料 名前=\"要件①（最優先・必ず守る）：添付の要件書「{theme.get('rfi_name') or '要件書'}」\">\n{t[:RFI_MAX]}\n</資料>\n")


def has_rfi(theme):
    return bool(str((theme or {}).get("rfi", "")).strip())


def theme_kw(theme):
    return {"THEME": theme_text(theme), "RFI": rfi_block(theme)}


def clean_theme(theme):
    """画面・CLI から来たテーマを整える（要件書は長さを切り、取り出し方を残す）。"""
    t = {k: (str(v).strip() if isinstance(v, str) else v) for k, v in (theme or {}).items()}
    if t.get("rfi"):
        t["rfi"] = str(t["rfi"])[:RFI_MAX]
    return t


def theme_title(theme):
    return (theme.get("theme") or theme.get("rfi_name") or theme.get("genre") or "")[:40]


def create_work(slug, theme):
    theme = clean_theme(theme)
    d = wdir(slug)
    if (d / "studio.json").exists():
        raise StudioError(f"企画 {slug} はすでにあります")
    if d.exists() and any(d.iterdir()):
        raise StudioError(f"フォルダ works/{slug} にはすでに何か入っています。別の名前にしてください")
    d.mkdir(parents=True, exist_ok=True)
    st = {"slug": slug, "created": now(), "theme": theme, "theme_version": 1, "rounds": [], "proposals": {},
          "classify": None, "selected": None, "drafts": [], "decks": [], "next_pid": 1, "log": [f"{now()} 企画を作成"]}
    save(slug, st)
    return st


def list_works():
    out = []
    if WORKS.exists():
        for d in sorted(WORKS.iterdir()):
            f = d / "studio.json"
            if f.exists():
                st = json.loads(f.read_text(encoding="utf-8"))
                out.append({"slug": d.name, "title": theme_title(st["theme"]), "created": st["created"], "stage": stage_of(st)})
    return out


def theme_diff(old, new):
    """変えた欄の名前（日本語）。"""
    def val(t, k):
        v = t.get(k)
        return str(THEME_DEFAULTS.get(k, "") if v in (None, "") else v).strip()
    return [THEME_LABELS[k] for k in THEME_LABELS if val(old, k) != val(new, k)]


def suggest_slug(slug):
    base = re.sub(r"-\d+$", "", slug)[:56]
    for i in range(2, 1000):
        cand = f"{base}-{i}"
        if not (WORKS / cand).exists():
            return cand
    return base + "-x"


def derive_work(src, new_slug, theme):
    """テーマを直して、新しい企画フォルダで提案からやり直す。元の企画には一切触れない。"""
    s = load(src)
    if (new_slug or "").strip() == src:
        raise StudioError(f"新しい企画フォルダの名前は、いまの「{src}」とは別にしてください")
    if wdir(new_slug).exists():
        raise StudioError(f"企画フォルダ「{new_slug}」はもうあります。別の名前にしてください（候補：{suggest_slug(new_slug)}）")
    st = create_work(new_slug, theme)
    changed = theme_diff(s["theme"], clean_theme(theme))
    st["derived_from"] = {"slug": src, "theme_version": s["theme_version"], "at": now(), "changed": changed}
    st["log"].append(f"{now()} 《{src}》のテーマ（版{s['theme_version']}）を直して作成。変えた欄：{'・'.join(changed) or 'なし（同じテーマで出し直し）'}")
    save(new_slug, st)
    return st


def derived_children(slug):
    out = []
    if WORKS.exists():
        for d in WORKS.iterdir():
            f = d / "studio.json"
            if f.exists():
                try:
                    df = json.loads(f.read_text(encoding="utf-8")).get("derived_from") or {}
                except ValueError:
                    continue
                if df.get("slug") == slug:
                    out.append({"slug": d.name, "at": df.get("at"), "changed": df.get("changed", [])})
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)


def change_theme(slug, theme):
    """テーマを変える＝最初からやり直す。これまでの提案・分類・原案・たたきは studio/history/ に退避する。"""
    with lock(slug):
        st = load(slug)
        h = wdir(slug) / "studio" / "history"
        h.mkdir(parents=True, exist_ok=True)
        (h / f"theme_v{st['theme_version']}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        st.update({"theme": clean_theme(theme), "theme_version": st["theme_version"] + 1, "rounds": [], "proposals": {}, "classify": None,
                   "selected": None, "drafts": [], "decks": []})
        st["log"].append(f"{now()} テーマを変更（v{st['theme_version']}）。前のテーマの記録は studio/history/")
        save(slug, st)
        return st


# ---------------------------------------------------------------- 二 提案（Issue #8）
PROPOSAL_FIELDS = ["kind", "genre", "title", "verdict", "summary", "fit", "resources", "market", "judgment", "sources"]
# 字数の上限（Issue #8）。verdict（判断の一言）は一覧で見比べるために足した
LIMITS = {"title": 50, "verdict": 30, "summary": 1000, "fit": 500, "resources": 500, "market": 500, "judgment": 500}
SLACK = 1.15  # この倍率までは許す（日本語の字数はモデルが数え違えやすい）


def over_limits(p):
    return [f"{f}（{len(str(p.get(f) or ''))}字／上限{n}字）" for f, n in LIMITS.items() if len(str(p.get(f) or "")) > n * SLACK]


def _check_props(obj, n):
    if not isinstance(obj, dict) or not isinstance(obj.get("proposals"), list) or not obj["proposals"]:
        return "proposals の配列がない"
    for i, p in enumerate(obj["proposals"]):
        if not isinstance(p, dict):
            return f"{i + 1}件目がオブジェクトでない"
        miss = [f for f in ("title", "summary", "market", "judgment") if not str(p.get(f, "")).strip()]
        if miss:
            return f"{i + 1}件目に {', '.join(miss)} がない"
    return None


def _soft_props(obj, need_fit=False):
    bad = [f"{i + 1}件目の " + "、".join(o) for i, p in enumerate(obj["proposals"]) if (o := over_limits(p))]
    msg = []
    if bad:
        msg.append("字数の上限を超えています：" + "；".join(bad) + "。内容を削らず、言い回しを詰めて上限に収めてください")
    nofit = [str(i + 1) for i, p in enumerate(obj["proposals"]) if need_fit and not str(p.get("fit", "")).strip()]
    if nofit:
        msg.append(f"{'・'.join(nofit)}件目に fit（要件への適合）がありません。要件①の求める事項ごとに、満たす／一部／満たさないを書いてください")
    return "。".join(msg) or None


def _call_json(slug, stage, model, text, check, temperature=0.7, note="", reasoning=None, max_tokens=16000, soft=None):
    """書式の誤りは一回だけやり直しを求める（compe の規則）。soft（字数など）は一回だけ直させ、直らなければそのまま受け取る。
    費用は失敗も含めて記録する。"""
    last, first = None, None
    for attempt in (1, 2):
        try:
            raw, rec = llm.chat(model, text, temperature=temperature, reasoning=reasoning, max_tokens=max_tokens,
                                session=f"plan-studio-{slug}")
        except llm.LLMError as e:
            if first:  # 字数の直しを頼んだ二回目だけが失敗した：一回目の答えを使う
                return first
            raise StudioError(f"{model}: {e}")
        record_cost(slug, stage, rec, note)
        obj = llm.extract_json(raw)
        err = check(obj)
        if not err:
            s = soft(obj) if soft else None
            if s and attempt == 1:
                text = text + f"\n\n# 前回の出力の問題\n{s}。指定の JSON の形だけで、もう一度返してください。"
                first = (obj, rec)
                continue
            return obj, rec
        last = err
        text = text + f"\n\n# 前回の出力の問題\n{err}。指定の JSON の形だけで、もう一度返してください。"
    if first:
        return first
    raise StudioError(f"{model}: 書式どおりの JSON が返らない（{last}）")


def web_model(m, web):
    """Web 検索つきで呼ぶ（OpenRouter の :online）。OpenRouter 以外の窓口では何もしない。"""
    if web and "openrouter.ai" in llm.base_url() and not m.endswith(":online"):
        return m + ":online"
    return m


def propose(slug, models, n=2, instruction="", mode="new", temperature=0.8, avoid=None, web=False):
    """models それぞれに n 案ずつ出させる（並列）。mode = new | resubmit | rolling。
    rolling：分類で「収束」になった骨格を『避ける骨格』として渡し、その外の案だけを求める。
    web：OpenRouter の Web 検索（:online）をつけて、競合・市場の調査事項を調べさせる。"""
    st = load(slug)
    n = max(1, min(int(n), 4))
    kind_rule = "本線と考える案を1つ以上と、まったく違う観点（顧客・解き方・儲け方のどれかが違う）の案を1つ以上" if n >= 2 else "本線と考える案を1つ"
    extra = []
    if instruction.strip():
        extra.append(f"- 企画責任者からの追加の指示：{instruction.strip()}")
    if mode == "rolling":
        av = avoid if avoid is not None else avoid_list(st)
        if av:
            extra.append("- 次の骨格に寄った案は出さないでください（すでに何度も出た型です）。骨格（誰の・何の課題を・どう解き・どこで儲けるか）を一切まねないこと。\n"
                         "  避ける骨格：\n" + "\n".join(f"  - {x}" for x in av))
    web_rule = ("- Web 検索が使えます。競合・市場規模・国内と海外の差は、検索して確かめた事実を使い、sources に URL を書いてください。"
                if web and "openrouter.ai" in llm.base_url() else
                "- Web 検索は使えません。市場の数字や競合は、確かな知識だけを書き、確かでないものは「推定」と書いて推定の筋道を一言添えてください。")
    text = propose_text(st, n, kind_rule, extra, web_rule)
    rid = f"r{len(st['rounds']) + 1}"
    results, errors = {}, {}
    calls = {m: web_model(m, web) for m in models}

    def one(m):
        obj, rec = _call_json(slug, "propose", calls[m], text, lambda o: _check_props(o, n), temperature=temperature, note=rid,
                              soft=lambda o: _soft_props(o, has_rfi(st["theme"])))
        return obj["proposals"][:n], rec
    with cf.ThreadPoolExecutor(max_workers=min(8, len(models) or 1)) as ex:
        futs = {ex.submit(one, m): m for m in models}
        for f in cf.as_completed(futs):
            m = futs[f]
            try:
                results[m] = f.result()
            except StudioError as e:
                errors[m] = str(e)
    with lock(slug):
        st = load(slug)
        pids = []
        for m in models:
            if m not in results:
                continue
            props, rec = results[m]
            each = round(rec["usd"] / max(1, len(props)), 6)
            for p in props:
                pid = f"P{st['next_pid']:02d}"
                st["next_pid"] += 1
                body = {f: _flat(p.get(f, "")) for f in PROPOSAL_FIELDS}
                st["proposals"][pid] = {"id": pid, "round": rid, "model": calls[m], "usd": each, "sec": rec["sec"], "origin": "model",
                                        **body, "over": over_limits(body)}
                pids.append(pid)
        st["rounds"].append({"id": rid, "ts": now(), "models": models, "n": n, "mode": mode, "instruction": instruction, "web": bool(web),
                             "proposals": pids, "errors": errors, "theme_version": st["theme_version"]})
        st["log"].append(f"{now()} 提案 {rid}（{mode}{'・Web検索' if web else ''}）：{len(pids)}案、失敗 {len(errors)}モデル")
        save(slug, st)
    return {"round": rid, "proposals": pids, "errors": errors}


def propose_text(st, n, kind_rule, extra, web_rule):
    fit_rule = ("- fit（要件への適合）：要件①の求める事項（章・項目番号で）それぞれに、この案が「満たす／一部／満たさない」と、その理由を短く。満たさない事項を隠さない。"
                if has_rfi(st["theme"]) else "- fit（要件への適合）：What の要件をこの案がどう満たすか（満たさない点があれば書く）。")
    return prompt("propose", **theme_kw(st["theme"]), N=n, KIND_RULE=kind_rule, EXTRA="\n".join(extra), WEB_RULE=web_rule, FIT_RULE=fit_rule)


def _flat(v):
    """モデルが配列で返した欄（sources など）を、1行ずつの文字列にする。"""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    return str(v or "")


def avoid_list(st):
    c = st.get("classify") or {}
    out = []
    for cl in c.get("clusters", []):
        if cl.get("label") == "収束":
            out += [x for x in cl.get("one_lines", []) if x]
            if cl.get("type"):
                out.append(cl["type"])
    return list(dict.fromkeys(out))[:12]


def edit_proposal(slug, pid, fields):
    """人が案を直接直す。元の案は残し、直した版を新しい案として足す。"""
    with lock(slug):
        st = load(slug)
        src = st["proposals"].get(pid)
        if not src:
            raise StudioError(f"{pid} がありません")
        new = f"P{st['next_pid']:02d}"
        st["next_pid"] += 1
        body = {**{f: src.get(f, "") for f in PROPOSAL_FIELDS}, **{k: str(v) for k, v in fields.items() if k in PROPOSAL_FIELDS}}
        st["proposals"][new] = {**src, **body, "id": new, "origin": "human", "parent": pid, "usd": 0.0, "round": src["round"],
                                "over": over_limits(body)}
        st["log"].append(f"{now()} {pid} を人が編集 → {new}")
        save(slug, st)
        return new


def revise_proposal(slug, pid, model, instruction):
    st = load(slug)
    src = st["proposals"].get(pid)
    if not src:
        raise StudioError(f"{pid} がありません")
    body = json.dumps({f: src.get(f, "") for f in PROPOSAL_FIELDS}, ensure_ascii=False, indent=1)
    text = prompt("revise", **theme_kw(st["theme"]), PROPOSAL=body, INSTRUCTION=instruction)

    def chk(o):
        return None if isinstance(o, dict) and isinstance(o.get("proposal"), dict) and o["proposal"].get("summary") else "proposal がない"

    def soft(o):
        b = over_limits(o["proposal"])
        return ("字数の上限を超えています：" + "、".join(b)) if b else None
    obj, rec = _call_json(slug, "revise", model, text, chk, temperature=0.6, note=pid, soft=soft)
    with lock(slug):
        st = load(slug)
        new = f"P{st['next_pid']:02d}"
        st["next_pid"] += 1
        body = {f: _flat(obj["proposal"].get(f, src.get(f, ""))) for f in PROPOSAL_FIELDS}
        st["proposals"][new] = {**body, "id": new, "round": src["round"], "model": model, "usd": rec["usd"], "sec": rec["sec"], "origin": "revise",
                                "parent": pid, "instruction": instruction, "changes": str(obj.get("changes", "")), "over": over_limits(body)}
        st["log"].append(f"{now()} {pid} を {model} で改稿 → {new}")
        save(slug, st)
    return new


def active_proposals(st):
    return [p for p in st["proposals"].values() if not p.get("hidden")]


def hide_proposal(slug, pid, hidden=True):
    with lock(slug):
        st = load(slug)
        st["proposals"][pid]["hidden"] = hidden
        save(slug, st)


# ---------------------------------------------------------------- 三 分類（骨格から。Issue #8 の「分類は小説同様に骨から判断」）
CLASSIFY_FIELDS = ["genre", "title", "verdict", "summary", "market", "judgment"]


def _props_block(props, fields=None):
    fields = fields or CLASSIFY_FIELDS
    return "\n\n".join(f"### {p['id']}\n" + "\n".join(f"{f}: {p.get(f, '')}" for f in fields if p.get(f)) for p in props)


def skeleton_store(st):
    """抜き出し済みの骨格 {pid: {...}}。分類の結果とは別に持ち、バッチごとに書き足す（途中で落ちても、取れた分は残る）。"""
    sk = dict((st.get("classify") or {}).get("skeletons") or {})  # 古い版は分類の結果の中に持っていた
    sk.update(st.get("skeletons") or {})
    return sk


def _pick(st, ids):
    """分析する案。ids があればその案（外した案も、選ばれていれば含める）、なければ外していない案すべて。"""
    if ids:
        missing = [i for i in ids if i not in st["proposals"]]
        if missing:
            raise StudioError(f"{', '.join(missing)} がありません")
        return [st["proposals"][i] for i in dict.fromkeys(ids)]
    return active_proposals(st)


def _batches(items, size):
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


def extract_skeletons(slug, props, model, refresh=False, progress=None):
    """骨格を小分けに抜き出す。1回の依頼は batch 案まで（既定 3）、同時に parallel 本まで（既定 2）。
    バッチが失敗したら、その案を1案ずつに分けてもう一度だけ頼む。取れた骨格はその都度 studio.json に書く。
    返り値：(骨格 {pid: ...}, 取れなかった案 [pid], エラー {pid: 理由})。"""
    cfg = settings()
    store = skeleton_store(load(slug))
    if refresh:  # 選んだ案の骨格だけを抜き出し直す（ほかの案の骨格はそのまま。新しい骨格で上書きされる）
        for p in props:
            store.pop(p["id"], None)
    need = [p for p in props if p["id"] not in store]
    total, done = len(props), len(props) - len(need)
    errors = {}
    if progress:
        progress(done, total, "骨格")

    def one(batch):
        ids = [p["id"] for p in batch]

        def chk(o):
            got = {s.get("id") for s in (o or {}).get("skeletons", []) if isinstance(s, dict)}
            miss = [i for i in ids if i not in got]
            return f"{', '.join(miss)} の骨格がない" if miss else None
        obj, rec = _call_json(slug, "skeleton", model, prompt("skeleton", PROPOSALS=_props_block(batch)), chk, temperature=0.1,
                              note=",".join(ids))
        return {s["id"]: {k: s.get(k, "") for k in similarity.SK_FIELDS + ["one_line"]} for s in obj["skeletons"] if s.get("id") in ids}

    def keep(got):
        nonlocal done
        with lock(slug):
            st = load(slug)
            st.setdefault("skeletons", {}).update(got)
            save(slug, st)
        store.update(got)
        done += len(got)
        if progress:
            progress(done, total, "骨格")

    retry = []
    batches = _batches(need, cfg.get("skeleton_batch", 3))
    with cf.ThreadPoolExecutor(max_workers=max(1, int(cfg.get("skeleton_parallel", 2)))) as ex:
        futs = {ex.submit(one, b): b for b in batches}
        for f in cf.as_completed(futs):
            b = futs[f]
            try:
                keep(f.result())
            except StudioError as e:
                if len(b) > 1:
                    retry += b  # まとめて頼んで落ちた：1案ずつに分けてもう一度
                else:
                    errors[b[0]["id"]] = str(e)
    if retry:
        with cf.ThreadPoolExecutor(max_workers=max(1, int(cfg.get("skeleton_parallel", 2)))) as ex:
            futs = {ex.submit(one, [p]): p for p in retry}
            for f in cf.as_completed(futs):
                try:
                    keep(f.result())
                except StudioError as e:
                    errors[futs[f]["id"]] = str(e)
    missing = [p["id"] for p in props if p["id"] not in store]
    return {p["id"]: store[p["id"]] for p in props if p["id"] in store}, missing, errors


def _skeleton_block(props, sk):
    """AI に読ませる要約：題・判断の一言・骨格だけ（本文は送らない＝案が多くても依頼が重くならない）。"""
    out = []
    for p in props:
        s = sk.get(p["id"]) or {}
        out.append(f"### {p['id']}\n" + "\n".join(f"{k}: {v}" for k, v in
                   [("title", p.get("title")), ("verdict", p.get("verdict")), ("genre", p.get("genre"))] +
                   [(k, s.get(k)) for k in similarity.SK_FIELDS + ["one_line"]] if v))
    return "\n\n".join(out)


def classify(slug, model=None, method="tool", threshold=None, w_text=0.4, refresh_skeletons=False, ids=None, progress=None):
    """選んだ案（ids。なければ外していない案すべて）を分類する。
    method = tool（骨格を抜き出してルールで分類。既定）| ai（骨格をモデルに読ませて分類）| both。
    骨格は小分けに抜き出して途中保存する。AI の分類は、本文ではなく骨格の一覧だけを渡す（小分けの結果で投げる）。"""
    st = load(slug)
    props = _pick(st, ids)
    if len(props) < 2:
        raise StudioError("分類するには案を2つ以上選んでください")
    model = model or settings()["skeleton_model"]
    threshold = float(threshold if threshold is not None else settings()["threshold"])
    sk, missing, errors = extract_skeletons(slug, props, model, refresh_skeletons, progress)
    ok = [p for p in props if p["id"] in sk]
    if len(ok) < 2:
        raise StudioError("骨格を抜き出せた案が2つ未満です（" + "；".join(f"{k}：{v[:160]}" for k, v in errors.items()) + "）。もう一度押すと、取れなかった案だけを頼み直します")
    result = {"ts": now(), "method": method, "model": model, "skeletons": sk, "ids": [p["id"] for p in ok],
              "missing": missing, "errors": errors}
    if method in ("tool", "both", "ai"):
        result.update(similarity.classify(ok, sk, threshold, w_text))
    if method in ("ai", "both"):
        if progress:
            progress(len(ok), len(props), "AI の読み")

        def chk2(o):
            return None if isinstance(o, dict) and isinstance(o.get("labels"), list) else "labels がない"
        try:
            obj, rec = _call_json(slug, "classify", model, prompt("classify", PROPOSALS=_skeleton_block(ok, sk)), chk2, temperature=0.1)
            result["ai"] = {"labels": {x["id"]: {"label": x.get("label"), "reason": x.get("reason", "")} for x in obj["labels"] if x.get("id")},
                            "types": obj.get("types", [])}
        except StudioError as e:  # 骨格の分類は済んでいるので、AI の読みが落ちても結果は残す
            result["ai_error"] = str(e)
        if method == "ai" and result.get("ai"):
            result["labels"] = {k: {"label": v["label"], "cluster": None, "nearest": None, "sim": None} for k, v in result["ai"]["labels"].items()}
            result["clusters"] = []
    with lock(slug):
        st = load(slug)
        st["classify"] = result
        st["log"].append(f"{now()} 分類（{method}、{len(ok)}案、しきい値 {threshold}{'、骨格の取れない案 ' + ','.join(missing) if missing else ''}）")
        save(slug, st)
    return result


def reclassify(slug, threshold, w_text=0.4):
    """骨格は抜き出し済みのものを使い、しきい値だけ変えて分類し直す（モデルを呼ばない）。分類した案の組はそのまま。"""
    st = load(slug)
    c = st.get("classify")
    sk = skeleton_store(st)
    if not c or not sk:
        raise StudioError("先に分類してください")
    ids = [i for i in (c.get("ids") or [p["id"] for p in active_proposals(st)]) if i in sk and i in st["proposals"]]
    c.update(similarity.classify([st["proposals"][i] for i in ids], sk, float(threshold), float(w_text)))
    with lock(slug):
        st = load(slug)
        st["classify"] = c
        save(slug, st)
    return c


def select(slug, pid):
    """人が原案に進める案を選ぶ（いつでも選び直せる。作った原案は選んだ案の記録つきで残る）。"""
    with lock(slug):
        st = load(slug)
        if pid not in st["proposals"]:
            raise StudioError(f"{pid} がありません")
        st["selected"] = pid
        st["log"].append(f"{now()} {pid} を選択")
        save(slug, st)


# ---------------------------------------------------------------- 四 企画原案（Issue #9）
DRAFT_SECTIONS = [  # (key, 見出し, 目安の字数)
    ("overview", "サービスの概要", 2000),
    ("market", "マーケットの状況とインパクト", 1000),
    ("resources", "当社の必要リソースと目標売上・目標利益", 1000),
    ("risks", "リスクと対策", 1000),
    ("design", "概要設計", 3000),
    ("sales", "販売方法・アプローチ", 1000),
]


def draft_lint(d, rfi=False):
    """原案の機械検査。errors は欠け（直させる）、warnings は字数の偏りなど（人に見せる）。
    rfi：要件書が添付されている。そのときは「要件への対応」（reqmap）が空なら警告する。"""
    errors, warnings = [], []
    if not isinstance(d, dict):
        return {"ok": False, "errors": ["draft がオブジェクトではありません"], "warnings": [], "chars": {}}
    if not str(d.get("title", "")).strip():
        errors.append("title（企画名）がありません")
    chars = {}
    for k, label, target in DRAFT_SECTIONS:
        n = len(str(d.get(k) or "").strip())
        chars[k] = n
        if n == 0:
            errors.append(f"「{label}」（{k}）がありません")
        elif n < target * 0.6:
            warnings.append(f"「{label}」が短い（{n}字／目安{target}字）")
        elif n > target * 1.4:
            warnings.append(f"「{label}」が長い（{n}字／目安{target}字）")
    checks = d.get("checks")
    if not isinstance(checks, list) or not checks:
        warnings.append("要確認事項（推定の数字・仮定・未調査の点）が空です。数字を書いたなら、確かめ方も残してください")
    if rfi and not [x for x in (d.get("reqmap") or []) if str(x).strip()]:
        warnings.append("要件への対応（要件①の求める事項ごとに、どの節でどう応えたか）が空です。要件書の漏れを確かめられません")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "chars": chars}


def _draft_check(o):
    d = (o or {}).get("draft")
    r = draft_lint(d)
    return "；".join(r["errors"][:5]) if r["errors"] else None


def _proposal_json(p):
    return json.dumps({f: p.get(f, "") for f in PROPOSAL_FIELDS if p.get(f)}, ensure_ascii=False, indent=1)


def make_draft(slug, model=None, instruction="", base_version=None, borrow=None):
    """選んだ案から企画原案を作る。base_version があれば、その版に指示を足して改訂する。
    borrow：ほかの案（外れ値など）から取り込みたい要素がある案の id。"""
    st = load(slug)
    model = model or settings()["draft_model"]
    secs = "\n".join(f"- {k}：{label}（{t}字前後）" for k, label, t in DRAFT_SECTIONS)
    if base_version:
        base = next((x for x in st["drafts"] if x["v"] == int(base_version)), None)
        if not base:
            raise StudioError(f"原案 v{base_version} がありません")
        text = prompt("draft_revise", **theme_kw(st["theme"]), DRAFT=json.dumps(base["draft"], ensure_ascii=False, indent=1),
                      INSTRUCTION=instruction or "機械検査の指摘を直す", SECTIONS=secs,
                      LINT="\n".join(f"- {e}" for e in base["lint"]["errors"] + base["lint"]["warnings"]) or "- なし")
        pid, borrow, stage = base["from"], base.get("borrow", []), "draft-revise"
    else:
        pid = st.get("selected")
        if not pid:
            raise StudioError("先に案を選んでください")
        borrow = [b for b in (borrow or []) if b in st["proposals"] and b != pid]
        bb = "".join(f"\n<資料 名前=\"取り込みたい要素がある案 {b}\">\n{_proposal_json(st['proposals'][b])}\n</資料>\n" for b in borrow)
        ib = f"\n<資料 名前=\"企画責任者の追加の指示\">\n{instruction}\n</資料>\n" if instruction.strip() else ""
        text = prompt("draft", **theme_kw(st["theme"]), PROPOSAL=_proposal_json(st["proposals"][pid]),
                      BORROW_BLOCK=bb, INSTRUCTION_BLOCK=ib, SECTIONS=secs)
        stage = "draft"
    obj, rec = _call_json(slug, stage, model, text, _draft_check, temperature=0.5, max_tokens=32000, reasoning="medium")
    return _add_draft(slug, obj["draft"], pid, model, instruction, rec["usd"], base_version, str(obj.get("changes", "")), borrow=borrow)


def _add_draft(slug, d, pid, model, instruction, usd, base_version=None, changes="", origin="model", borrow=None):
    with lock(slug):
        st = load(slug)
        v = max([x["v"] for x in st["drafts"]] or [0]) + 1
        st["drafts"].append({"v": v, "ts": now(), "from": pid, "borrow": borrow or [], "model": model, "instruction": instruction,
                             "base": base_version, "usd": usd, "origin": origin, "changes": changes, "draft": d,
                             "lint": draft_lint(d, has_rfi(st["theme"]))})
        st["log"].append(f"{now()} 原案 v{v}（{origin}{'、v' + str(base_version) + ' から' if base_version else ''}）")
        save(slug, st)
    return v


def edit_draft(slug, v, fields):
    """人が原案を直接直す（節ごと）。新しい版として保存する。"""
    st = load(slug)
    base = next((x for x in st["drafts"] if x["v"] == int(v)), None)
    if not base:
        raise StudioError(f"原案 v{v} がありません")
    d = dict(base["draft"])
    for k, val in (fields or {}).items():
        if k in ("checks", "reqmap"):
            d[k] = [x.strip() for x in (val if isinstance(val, list) else str(val).splitlines()) if str(x).strip()]
        elif k in ("title", "catch") or k in dict((s[0], 1) for s in DRAFT_SECTIONS):
            d[k] = str(val)
    return _add_draft(slug, d, base["from"], "human", "人が編集", 0.0, int(v), origin="human", borrow=base.get("borrow"))


def get_draft(st, v):
    d = next((x for x in st["drafts"] if x["v"] == int(v)), None)
    if not d:
        raise StudioError(f"原案 v{v} がありません")
    return d


# ---------------------------------------------------------------- 五 企画書たたき（Issue #10）
DECK_PURPOSES = {"approve": "社内で承認を得る（予算・体制の意思決定）", "customer": "顧客に提案する", "share": "アイデアを共有して意見をもらう"}


def deck_opts(o):
    o = o or {}
    purpose = o.get("purpose") if o.get("purpose") in DECK_PURPOSES else "approve"
    slides = max(4, min(int(o.get("slides") or 12), 30))
    minutes = max(3, min(int(o.get("minutes") or 15), 120))
    clean = lambda k, n: re.sub(r"\s+", " ", str(o.get(k) or "")).strip()[:n]  # noqa: E731
    return {"audience": str(o.get("audience") or "社内の経営会議")[:80], "purpose": purpose, "slides": slides, "minutes": minutes,
            # 表紙とフッターに入れるもの。空なら「入れない」と渡す（渡した先が仮の社名や日付を勝手に埋めないように。v0.2.1）
            "company": clean("company", 60), "date": clean("date", 20), "presenter": clean("presenter", 40)}


def _deck_check(o):
    d = (o or {}).get("deck")
    if not isinstance(d, dict) or not isinstance(d.get("slides"), list) or not d["slides"]:
        return "deck.slides がない"
    miss = [str(i + 1) for i, s in enumerate(d["slides"]) if not isinstance(s, dict) or not str(s.get("title", "")).strip() or not str(s.get("message", "")).strip()]
    return f"{', '.join(miss)} 枚目に title か message がない" if miss else None


def make_deck(slug, draft_v, opts=None, model=None, instruction="", base_version=None):
    st = load(slug)
    model = model or settings()["deck_model"]
    if base_version:
        base = next((x for x in st["decks"] if x["v"] == int(base_version)), None)
        if not base:
            raise StudioError(f"たたき v{base_version} がありません")
        draft_v, o = base["draft_v"], base["opts"]
        dr = get_draft(st, draft_v)
        text = prompt("deck_revise", DRAFT=outputs.draft_markdown(dr["draft"], DRAFT_SECTIONS),
                      DECK=json.dumps(base["deck"], ensure_ascii=False, indent=1), INSTRUCTION=instruction or "機械検査の指摘を直す",
                      LINT="\n".join(f"- {e}" for e in base["lint"]["errors"] + base["lint"]["warnings"]) or "- なし", **_deck_kw(o))
        stage = "deck-revise"
    else:
        dr = get_draft(st, draft_v)
        o = deck_opts(opts)
        ib = f"\n<資料 名前=\"企画責任者の追加の指示\">\n{instruction}\n</資料>\n" if instruction.strip() else ""
        text = prompt("deck", DRAFT=outputs.draft_markdown(dr["draft"], DRAFT_SECTIONS), INSTRUCTION_BLOCK=ib, **_deck_kw(o))
        stage = "deck"
    obj, rec = _call_json(slug, stage, model, text, _deck_check, temperature=0.4, max_tokens=24000)
    return _add_deck(slug, obj["deck"], int(draft_v), o, model, instruction, rec["usd"], base_version, str(obj.get("changes", "")))


def _deck_kw(o):
    return {"AUDIENCE": o["audience"], "PURPOSE": DECK_PURPOSES[o["purpose"]], "SLIDES": o["slides"], "MINUTES": o["minutes"],
            "COVER": outputs.cover_line(o)}


def _add_deck(slug, deck, draft_v, o, model, instruction, usd, base_version=None, changes="", origin="model"):
    with lock(slug):
        st = load(slug)
        dr = get_draft(st, draft_v)
        v = max([x["v"] for x in st["decks"]] or [0]) + 1
        st["decks"].append({"v": v, "ts": now(), "draft_v": draft_v, "opts": o, "model": model, "instruction": instruction, "base": base_version,
                            "usd": usd, "origin": origin, "changes": changes, "deck": deck,
                            "lint": outputs.deck_lint(deck, dr["draft"], o["slides"], o)})
        st["log"].append(f"{now()} たたき v{v}（原案 v{draft_v}、{origin}{'、v' + str(base_version) + ' から' if base_version else ''}）")
        save(slug, st)
    return v


def edit_deck(slug, v, deck):
    """人がたたきを直接直す（JSON）。新しい版として保存する。"""
    st = load(slug)
    base = next((x for x in st["decks"] if x["v"] == int(v)), None)
    if not base:
        raise StudioError(f"たたき v{v} がありません")
    if isinstance(deck, str):
        try:
            deck = json.loads(deck)
        except ValueError as e:
            raise StudioError(f"JSON として読めません：{e}")
    if _deck_check({"deck": deck}):
        raise StudioError(_deck_check({"deck": deck}))
    return _add_deck(slug, deck, base["draft_v"], base["opts"], "human", "人が編集", 0.0, int(v), origin="human")


# ---------------------------------------------------------------- 書き出し
EXPORTS = {"draft_md", "deck_md", "deck_docx", "deck_marp"}


def export(slug, kind, v, tool="claude"):
    """(ファイル名, bytes, content-type) を返す。ファイルは保存せず、状態から毎回作る。"""
    if kind not in EXPORTS:
        raise StudioError(f"不明な書き出し {kind}")
    st = load(slug)
    if kind == "draft_md":
        d = get_draft(st, v)
        return f"{slug}_原案_v{d['v']}.md", outputs.draft_markdown(d["draft"], DRAFT_SECTIONS, meta=_draft_meta(st, d)).encode("utf-8"), "text/markdown; charset=utf-8"
    k = next((x for x in st["decks"] if x["v"] == int(v)), None)
    if not k:
        raise StudioError(f"たたき v{v} がありません")
    if kind == "deck_md":
        tool = tool if tool in outputs.TOOLS else "claude"
        return (f"{slug}_たたき_v{k['v']}_{tool}.md", outputs.deck_markdown(k["deck"], k["opts"], tool=tool).encode("utf-8"),
                "text/markdown; charset=utf-8")
    if kind == "deck_marp":
        return f"{slug}_たたき_v{k['v']}_marp.md", outputs.deck_marp(k["deck"], k["opts"]).encode("utf-8"), "text/markdown; charset=utf-8"
    return (f"{slug}_たたき_v{k['v']}.docx", outputs.deck_docx(k["deck"], k["opts"]),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def _draft_meta(st, d):
    p = st["proposals"].get(d["from"], {})
    return {"案": f"{d['from']}「{p.get('title', '')}」" + (f"（取り込み：{'・'.join(d['borrow'])}）" if d.get("borrow") else ""),
            "版": f"v{d['v']}（{'人が編集' if d['origin'] == 'human' else d['model']}、{d['ts']}）"}


PROPOSAL_EXPORTS = {"md": ("md", "text/markdown; charset=utf-8"), "csv": ("csv", "text/csv; charset=utf-8"),
                    "json": ("json", "application/json; charset=utf-8")}


def export_proposals(slug, ids, fmt="md"):
    """選んだ素案を一つのファイルに書き出す（見比べるため）。分類のラベルと骨格があれば一緒に入れる。
    md＝読む用（一覧表＋全文）、csv＝Excel で並べる用（BOM つき UTF-8）、json＝ほかの道具に渡す用。"""
    if fmt not in PROPOSAL_EXPORTS:
        raise StudioError(f"不明な形式 {fmt}")
    st = load(slug)
    props = _pick(st, ids)
    if not props:
        raise StudioError("書き出す案を選んでください")
    c = st.get("classify") or {}
    labels, sk = c.get("labels") or {}, skeleton_store(st)
    ai = (c.get("ai") or {}).get("labels") or {}
    rows = []
    for p in props:
        lab, s = labels.get(p["id"]) or {}, sk.get(p["id"]) or {}
        rows.append({"id": p["id"], "round": p.get("round", ""), "model": p.get("model", ""), "origin": p.get("origin", ""),
                     "parent": p.get("parent", ""), **{f: p.get(f, "") for f in PROPOSAL_FIELDS},
                     "label": lab.get("label", ""), "nearest": lab.get("nearest", "") or "", "sim": lab.get("sim", "") if lab.get("sim") is not None else "",
                     "ai_label": (ai.get(p["id"]) or {}).get("label", ""), "ai_reason": (ai.get(p["id"]) or {}).get("reason", ""),
                     **{f"sk_{k}": s.get(k, "") for k in similarity.SK_FIELDS + ["one_line"]},
                     "over": "、".join(p.get("over") or []), "usd": p.get("usd", 0)})
    ext, ctype = PROPOSAL_EXPORTS[fmt]
    name = f"{slug}_素案_{len(rows)}案.{ext}"
    if fmt == "json":
        return name, json.dumps({"slug": slug, "theme": st["theme"], "exported": now(), "proposals": rows}, ensure_ascii=False, indent=1).encode("utf-8"), ctype
    if fmt == "csv":
        import csv
        import io
        heads = {"id": "案", "round": "ラウンド", "model": "モデル", "origin": "出どころ", "parent": "元の案", "kind": "種類", "genre": "ジャンル",
                 "title": "企画タイトル", "verdict": "判断の一言", "summary": "企画の概要", "fit": "要件への適合", "resources": "必要なリソース", "market": "マーケット",
                 "judgment": "判断", "sources": "出典", "label": "分類", "nearest": "いちばん近い案", "sim": "近さ", "ai_label": "AIの分類",
                 "ai_reason": "AIの理由", "sk_customer": "骨格：誰の", "sk_pain": "骨格：課題", "sk_solution": "骨格：解き方",
                 "sk_revenue": "骨格：儲け方", "sk_edge": "骨格：勝ち筋", "sk_market": "骨格：市場の型", "sk_one_line": "骨格：一行",
                 "over": "字数超過", "usd": "費用（ドル）"}
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(heads), extrasaction="ignore", lineterminator="\r\n")
        buf.write(",".join(heads.values()) + "\r\n")
        w.writerows(rows)
        return name, ("\ufeff" + buf.getvalue()).encode("utf-8"), ctype  # BOM：Excel で開いたとき文字化けしないように
    th = theme_text(st["theme"])
    out = [f"# 素案の一覧：{theme_title(st['theme'])}", "", f"- 企画フォルダ：{slug}", f"- 書き出し：{now()}　{len(rows)}案", "", "## テーマ", "", th, "",
           "## 一覧", "", "| 案 | 企画タイトル | 判断の一言 | 分類 | 骨格（一行） | モデル |", "|---|---|---|---|---|---|"]
    cell = lambda x: str(x or "").replace("|", "／").replace("\n", " ")  # noqa: E731
    out += [f"| {r['id']} | {cell(r['title'])} | {cell(r['verdict'])} | {cell(r['label'])} | {cell(r['sk_one_line'])} | {cell(r['model'].split('/')[-1])} |" for r in rows]
    for r in rows:
        out += ["", "---", "", f"## {r['id']}　{r['title']}", "",
                f"- モデル：{r['model']}　ラウンド：{r['round']}　種類：{r['kind']}　ジャンル：{r['genre']}" + (f"　元の案：{r['parent']}" if r["parent"] else ""),
                f"- 判断の一言：{r['verdict']}"]
        if r["label"]:
            out.append(f"- 分類：{r['label']}" + (f"（いちばん近い案 {r['nearest']}・近さ {r['sim']}）" if r["nearest"] else ""))
        if r["sk_one_line"]:
            out.append("- 骨格：" + "／".join(f"{v}" for v in (r[f"sk_{k}"] for k in similarity.SK_FIELDS) if v) + f"（{r['sk_one_line']}）")
        if r["over"]:
            out.append(f"- 字数超過：{r['over']}")
        for f, label in (("summary", "企画の概要"), ("fit", "要件への適合"), ("resources", "企画実行に必要なリソース"), ("market", "マーケットの状況、規模"),
                         ("judgment", "判断"), ("sources", "出典")):
            if r[f]:
                out += ["", f"### {label}", "", str(r[f]).strip()]
    return name, ("\n".join(out).strip() + "\n").encode("utf-8"), ctype


def tool_prompts(slug, v):
    st = load(slug)
    k = next((x for x in st["decks"] if x["v"] == int(v)), None)
    if not k:
        raise StudioError(f"たたき v{v} がありません")
    return {t: outputs.tool_prompt(t, k["deck"], k["opts"]) for t in outputs.TOOLS}


# ---------------------------------------------------------------- 状態
def stage_of(st, d=None):
    if st.get("decks"):
        return "deck"
    if st.get("drafts"):
        return "draft"
    if st.get("classify"):
        return "classify"
    if st.get("proposals"):
        return "propose"
    return "theme"


def snapshot(slug):
    """画面に渡す状態一式。"""
    st = load(slug)
    out = {**st, "stage": stage_of(st), "costs": costs(slug), "avoid": avoid_list(st),
           "derived": derived_children(slug), "suggest_slug": suggest_slug(slug),
           "sections": [{"key": k, "label": label, "target": t} for k, label, t in DRAFT_SECTIONS],
           "limits": LIMITS, "purposes": DECK_PURPOSES, "web_ok": "openrouter.ai" in llm.base_url()}
    for d in out["drafts"]:
        d["lint"] = draft_lint(d["draft"], has_rfi(st["theme"]))
    for k in out["decks"]:
        try:
            k["lint"] = outputs.deck_lint(k["deck"], get_draft(st, k["draft_v"])["draft"], k["opts"]["slides"], k["opts"])
        except StudioError:
            pass
    return out

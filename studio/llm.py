"""llm — studio のモデル呼び出し（OpenRouter・OpenCode・社内のモデルルーターなど OpenAI 互換の窓口。標準ライブラリのみ）。

依頼（提案・骨格・原案・たたき）は JSON を一つ返すだけなので、ストリーミングなしで受け、usage.cost（ドル）を記録する。
cost が返らないプロバイダのときは、公開価格の表か /models の単価から計算する。

環境変数は PLAN_*（PLAN_LLM_BASE_URL・PLAN_PROVIDER_APIKEY・PLAN_LLM_PROTOCOLS・PLAN_LLM_PRICES・PLAN_WORKS_DIR）。
novel_studio と同じ NOVEL_* の名前もそのまま読む（同じ .env を使い回せるように）。
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_wire as wire  # noqa: E402  窓口ごとの違い（ヘッダ・/responses・/messages）
for _k, _v in list(os.environ.items()):  # PLAN_* を NOVEL_* としても読めるように（llm_wire は NOVEL_* を見る）
    if _k.startswith("PLAN_"):
        os.environ.setdefault("NOVEL_" + _k[5:], _v)
KEY_NAMES = ["NOVEL_PROVIDER_APIKEY", "NOVEL_LLM_API_KEY", "OPENROUTER_API_KEY", "HERMES_CUSTOM_OPENROUTER_API_KEY"]
CACHE = Path(__file__).resolve().parent / ".cache"


def _dotenv(name):
    """リポジトリ直下・studio/・企画置き場（PLAN_WORKS_DIR）の .env から値を読む。NOVEL_X を聞かれたら PLAN_X も探す（PLAN_ が先）。"""
    names = (["PLAN_" + name[6:]] if name.startswith("NOVEL_") else []) + [name]
    places = [REPO / ".env", REPO / "studio" / ".env", REPO / "works" / ".env"]
    wd = os.environ.get("PLAN_WORKS_DIR") or os.environ.get("NOVEL_WORKS_DIR")
    if wd:
        places.insert(0, Path(wd) / ".env")
    for nm in names:
        for env in places:
            if env.exists():
                for line in env.read_text(encoding="utf-8").splitlines():
                    k, _, v = line.strip().partition("=")
                    if k.strip().removeprefix("export ").strip() == nm and v.strip():
                        return v.strip().strip('"').strip("'")
    return None


def _base_tag():
    """キャッシュを窓口ごとに分ける（OpenRouter と OpenCode を切り替えても一覧が混ざらない）。"""
    import hashlib
    return hashlib.sha1(base_url().encode()).hexdigest()[:8]


def base_url():
    return (os.environ.get("NOVEL_LLM_BASE_URL") or _dotenv("NOVEL_LLM_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")


def find_key():
    for n in ["PLAN_PROVIDER_APIKEY", "PLAN_LLM_API_KEY"] + KEY_NAMES:
        if os.environ.get(n):
            return os.environ[n].strip()
    for n in KEY_NAMES:
        v = _dotenv(n)
        if v:
            return v
    return None


def models(refresh=False):
    """[{id, name, in_per_m, out_per_m, context, created, free, expires}]。OpenRouter の /models は鍵なしで読める。1日キャッシュ（refresh で取り直す）。
    文章を返さないモデル（画像だけなど）、:batch（チャットでは使えない）、提供が終わったモデルは外す。
    created（出た日）・free（無料）・expires（提供の終わる日）は、画面でモデルの一覧を絞るのに使う。"""
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"models_v2_{_base_tag()}.json"  # v2：created などを持つ形（古いキャッシュは読まない）
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 86400:
        cached = _read_cache(f)
        if cached is not None:
            return cached
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url() + "/models", headers={"User-Agent": wire.UA}), timeout=20) as r:
            data = json.loads(r.read().decode("utf-8")).get("data", [])
        out = [e for e in (_model_entry(m) for m in data) if e]
        out.sort(key=lambda x: x["id"])
        # 並列の提案が同時に読み書きするので、書きかけを読ませない（別名に書いてから差し替える）
        tmp = f.with_name(f"{f.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)
        return out
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return _read_cache(f) or []


def _model_entry(m):
    """/models の1件を、画面で使う形にする。使えないモデルは None。"""
    mid = m.get("id") or ""
    if not mid or mid.endswith(":batch"):
        return None
    arch = m.get("architecture") or {}
    outs = arch.get("output_modalities")
    if outs and "text" not in outs:
        return None
    exp = str(m.get("expiration_date") or "")[:10]
    if exp and exp < time.strftime("%Y-%m-%d"):
        return None
    p = m.get("pricing") or {}
    try:
        pin, pout = float(p.get("prompt") or 0) * 1e6, float(p.get("completion") or 0) * 1e6
    except ValueError:
        pin = pout = 0.0
    if pin < 0 or pout < 0:  # 振り分け役のモデル（openrouter/auto など）は -1：価格は決まっていない
        return {"id": mid, "name": m.get("name", mid), "in_per_m": None, "out_per_m": None, "context": m.get("context_length"),
                "created": int(m.get("created") or 0) or None, "free": False, "expires": exp or None}
    free = mid.endswith(":free") or ("prompt" in p and pin == 0 and pout == 0)
    if not pin and not pout:  # OpenCode の /models は価格を返さない：公開価格の表で補う
        pin, pout = wire._prices().get(mid, (0.0, 0.0))
    return {"id": mid, "name": m.get("name", mid), "in_per_m": round(pin, 4), "out_per_m": round(pout, 4),
            "context": m.get("context_length"), "created": int(m.get("created") or 0) or None, "free": bool(free), "expires": exp or None}


def _read_cache(f):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def price_of(model):
    for m in models():
        if m["id"] == model and m.get("in_per_m") is not None:
            return m["in_per_m"] / 1e6, m["out_per_m"] / 1e6
    return None


class LLMError(Exception):
    pass


def _body(proto, base, model, system, prompt, temperature, max_tokens, json_mode, reasoning):
    if proto != "chat":
        return wire.build(proto, model, system, prompt, temperature, max_tokens, reasoning, json_mode)
    body = {"model": model, "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens}
    if not wire.is_opencode(base):
        body["usage"] = {"include": True}  # OpenRouter：費用を返してもらう
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if reasoning:
        body["reasoning"] = {"effort": reasoning}
    return "/chat/completions", body


def chat(model, prompt, system="", temperature=0.7, max_tokens=16000, json_mode=True, reasoning=None, timeout=600, retries=2, session=None):
    """返り値：(本文, 記録)。記録 = {model, in, out, usd, sec, finish, protocol, est}。失敗は LLMError。
    窓口（/chat/completions・/responses・/messages）はモデルと base_url から決める（llm_wire.py）。
    OpenCode で窓口が合わなかった（404 など）ときは、ほかの窓口を一度ずつ試す。"""
    key = find_key()
    if not key:
        raise LLMError("APIキーが見つかりません。リポジトリ直下の .env に OPENROUTER_API_KEY=... を置くか、環境変数で渡してください。")
    base = base_url()
    proto = wire.protocol_for(base, model)
    path, body = _body(proto, base, model, system, prompt, temperature, max_tokens, json_mode, reasoning)
    spare = wire.alternatives(proto) if wire.is_opencode(base) else []
    last = None
    attempt = 0
    while attempt <= retries:
        t0 = time.time()
        req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers=wire.headers(key, proto, session=session))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = f"HTTP {e.code}: {detail}"
            low = detail.lower()
            if spare and (e.code == 404 or "endpoint" in low or "not supported" in low or "unsupported" in low):
                proto = spare.pop(0)  # 窓口の取り違え：別の窓口で同じ依頼を出し直す（試行回数には数えない）
                path, body = _body(proto, base, model, system, prompt, temperature, max_tokens, json_mode, reasoning)
                continue
            fixable = e.code == 400 and "MissingSessionID" not in detail
            if fixable and ("response_format" in body or "text" in body):
                body.pop("response_format", None)
                body.pop("text", None)
                attempt += 1
                continue
            if fixable and "temperature" in body and "temperature" in low:
                body.pop("temperature")  # 推論型のモデルは temperature を受け付けないことがある
                attempt += 1
                continue
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(60, 10 * 2 ** attempt))
                attempt += 1
                continue
            raise LLMError(last + wire.hint(e.code, detail, proto))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(5)
                attempt += 1
                continue
            raise LLMError(last)
        try:
            text, finish, u, got = wire.parse(proto, d)
        except ValueError as e:
            raise LLMError(str(e))
        text = (text or "").strip()
        usd, price = u.get("cost"), "reported"
        if usd is None:  # OpenCode は費用を返さない：公開価格の表で見積もる → だめならモデル一覧の価格
            usd, price = wire.estimate_cost(got or model, u), "estimated"
        if usd is None:
            pr = price_of(got or model)
            usd = ((u.get("prompt_tokens") or 0) * pr[0] + (u.get("completion_tokens") or 0) * pr[1]) if pr and any(pr) else None
        if usd is None:
            usd, price = 0.0, "unknown"
        rec = {"model": got or model, "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
               "usd": round(float(usd or 0), 6), "sec": round(time.time() - t0, 1), "finish": finish, "protocol": proto, "price": price}
        if not text:
            last = f"空の応答（finish_reason={finish}。推論で出力枠を使い切った可能性）"
            if attempt < retries:
                key_mt = "max_output_tokens" if proto == "responses" else "max_tokens"
                body[key_mt] = int(body[key_mt] * 1.5)
                if reasoning and proto != "messages":
                    body["reasoning"] = {"effort": "low"}
                attempt += 1
                continue
            raise LLMError(last)
        return text, rec
    raise LLMError(last or "不明なエラー")


def extract_json(raw):
    """前置きやコードブロックが混ざっても、最初に読める JSON オブジェクトを取り出す。"""
    import re
    raw = re.sub(r"```(?:json)?", "", raw or "")
    i = raw.find("{")
    while i >= 0:
        depth, instr, esc = 0, False, False
        for j in range(i, len(raw)):
            c = raw[j]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
                continue
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[i:j + 1])
                    except json.JSONDecodeError:
                        break
        i = raw.find("{", i + 1)
    return None

#!/usr/bin/env python3
"""mock_openrouter — OpenRouter（OpenAI 互換 Chat Completions）の偽物。費用をかけずに plan_studio を端から端まで通すため。

  python3 studio/devtools/mock_openrouter.py [--port 8765]
  PLAN_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 PLAN_PROVIDER_APIKEY=mock python3 studio/server.py

依頼文の目印で答えを変える:
  <!-- STUDIO: propose|revise|skeleton|classify|draft|draft-revise|deck|deck-revise -->
環境変数:
  MOCK_LONG=1     提案の summary を上限より長く返す（字数の直しの経路を通すため。直しの依頼には上限内で返す）
  MOCK_SKELETON_MAX=N   骨格の抜き出しで、1回に N 案より多く頼まれたら失敗する（上流が時間切れで諦めるのを真似る。小分けの経路を通すため）
  MOCK_FAIL_SKELETON=P03,P05  この案を含む骨格の依頼は必ず失敗する（取れない案が残る経路を通すため）
  MOCK_DELAY=秒   1回の応答を遅らせる（進み具合の表示を見るため）
OpenCode Go の真似（/zen/go/v1：User-Agent と session の門、/responses・/messages、費用なしの応答）も残してある。
"""
import argparse
import hashlib
import json
import os
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()

MODELS = [
    ("anthropic/claude-opus-5.5", 5.0, 25.0), ("anthropic/claude-haiku-5.5", 0.1, 0.5),
    ("openai/gpt-6-luna-pro", 0.1, 0.5), ("deepseek/deepseek-v4.1-flash", 0.07, 0.28),
    ("aion-labs/aion-3.5-mini", 0.7, 1.4), ("meta/muse-spark-1.3-contributor", 0.2, 0.8),
    ("moonshotai/kimi-k3", 0.5, 2.0), ("z-ai/glm-5.3", 0.4, 1.6), ("qwen/qwen3.8-flash", 0.05, 0.2),
]
PRICE = {m: (i / 1e6, o / 1e6) for m, i, o in MODELS}


WORDS = ["霧", "灯", "石段", "川面", "鈴", "外套", "指先", "帳面", "窓", "足音", "古い地図", "湯気", "鍵", "蝋燭", "砂", "風見",
         "階段", "手紙", "硝子", "縄", "舟", "鐘楼", "棚", "籠", "影", "火床", "泥", "袖", "羽根", "皿"]
VERBS = ["揺れていた", "冷えていた", "鳴った", "黙っていた", "見えなくなった", "光った", "残っていた", "傾いた", "濡れていた", "ほどけた"]


def section(text, name):
    m = re.search(rf'<資料 名前="{re.escape(name)}[^"]*">\n(.*?)\n</資料>', text, flags=re.S)
    return m.group(1) if m else ""


def sections_like(text, prefix):
    return re.findall(rf'<資料 名前="{re.escape(prefix)}[^"]*">\n(.*?)\n</資料>', text, flags=re.S)


def prose(n_chars, seed):
    rnd = random.Random(seed)
    out, total, k = [], 0, 0
    while total < n_chars:
        k += 1
        a, b = rnd.sample(WORDS, 2)
        s = f"{a}のそばで{b}が{rnd.choice(VERBS)}。"
        if k % 5 == 0:
            s = f"「{rnd.choice(WORDS)}は、{rnd.choice(WORDS)}の{k}番目の棚にある」と誰かが言った。"
        if k % 9 == 0:
            s += "\n\n"
        out.append(s)
        total += len(s)
    text = "".join(out).strip()
    if not text.endswith("。"):
        text += "。"
    return text


CORES = [  # 偽モデルでも「収束」を再現する：多くの案が CORES[0] に寄り、たまに外れる
    ("中小製造業の経理", "請求書の突き合わせの手間", "SaaS", "月額", "価格", "既存の置き換え", "経理の突き合わせ自動化SaaS"),
    ("自治体の窓口", "問い合わせの多さ", "代行・BPO", "従量", "規制対応", "制度・規制起点", "自治体窓口のAI一次受付代行"),
    ("介護施設の夜勤", "夜間の人手不足", "ハード＋保守", "保守", "品質・精度", "ニッチの深掘り", "夜間見守りセンサーと保守"),
    ("情報システム部門", "監査対応の手間", "統合SI", "売り切り", "既存顧客と販路", "制度・規制起点", "監査ログ統合の導入支援"),
]


FAIL = "__MOCK_FAIL__"  # 上流のプロバイダが諦めた（504 の代わりに、再試行されない 400 で返す。テストを速くするため）


def studio_answer(kind, text, model):
    seed = int(hashlib.md5((model + text[-200:]).encode()).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    if kind == "propose":
        m = re.search(r"案の数：(\d+)", text)
        n = int(m.group(1)) if m else 2
        props = []
        for i in range(n):
            c = CORES[0] if rnd.random() < 0.6 else rnd.choice(CORES[1:])
            if "避ける骨格" in text and c == CORES[0]:
                c = CORES[2]
            long = os.environ.get("MOCK_LONG") and "字数の上限を超えています" not in text
            props.append({"kind": "本線" if i == 0 else "別観点", "genre": "ITサービス", "title": f"{c[6]}（{rnd.choice(WORDS)}）",
                          "verdict": "国内は3年ブルー、海外は既にレッド",
                          "summary": f"{c[0]}の{c[1]}を{c[2]}で解く。" + prose(1300 if long else 400, seed + i),
                          "resources": "開発5名・6か月。初期費用は推定3,000万円（人件費×期間）。",
                          "market": f"{c[0]}は国内に推定12,000社。単価月額5万円で年72億円（推定：社数×単価×12）。",
                          "judgment": "いまは国内に専業が少なくブルー。3年後は大手の参入でレッドに近づく。",
                          "sources": ""})
        return json.dumps({"proposals": props}, ensure_ascii=False)
    if kind == "revise":
        m = re.search(r'<資料 名前="元の案">\n(.*?)\n</資料>', text, flags=re.S)
        p = json.loads(m.group(1)) if m else {}
        p["title"] = (p.get("title") or "無題")[:44] + "（改）"
        p["summary"] = (p.get("summary") or "")[:900] + " 指示を受けて顧客を絞った。"
        return json.dumps({"proposal": p, "changes": "顧客を絞った（偽モデル）"}, ensure_ascii=False)
    if kind == "skeleton":
        items = re.findall(r'^### (P\d+)\n(.*?)(?=^### P\d+|\Z)', section(text, "提案"), flags=re.S | re.M)
        cap = int(os.environ.get("MOCK_SKELETON_MAX", "0") or 0)
        bad = {x for x in os.environ.get("MOCK_FAIL_SKELETON", "").split(",") if x}
        if (cap and len(items) > cap) or any(pid in bad for pid, _ in items):
            return FAIL
        out = []
        for pid, body in items:
            c = next((c for c in CORES if c[0] in body), CORES[-1])
            sk = dict(zip(["customer", "pain", "solution", "revenue", "edge", "market"], c[:6]))
            out.append({"id": pid, **sk, "one_line": c[6]})
        return json.dumps({"skeletons": out}, ensure_ascii=False)
    if kind == "classify":
        ids = re.findall(r"^### (P\d+)", section(text, "提案"), flags=re.M)
        return json.dumps({"labels": [{"id": i, "label": rnd.choice(["収束", "バリエーション", "外れ値"]), "reason": "偽モデルの判断"} for i in ids],
                           "types": ["偽モデルの型 ×2"]}, ensure_ascii=False)
    if kind in ("draft", "draft-revise"):
        targets = {k: int(t) for k, t in re.findall(r"^- (\w+)：[^（\n]*（(\d+)字前後）", text, flags=re.M)}
        d = {"title": "経理の突き合わせ自動化", "catch": "月末の三日を、半日に。"}
        for k, t in targets.items():
            d[k] = f"{k}の本文（偽モデル）。国内に推定12,000社、月額5万円。" + prose(int(t * 0.9), seed + len(k))
        d["checks"] = ["国内12,000社：業界団体の統計で確かめる", "月額5万円：既存顧客10社に聞く"]
        if kind == "draft-revise":
            d["catch"] += "（指示を反映）"
        return json.dumps({"draft": d, "changes": "偽モデルの変更"}, ensure_ascii=False)
    if kind in ("deck", "deck-revise"):
        m = re.search(r"枚数：(\d+) 枚", text)
        n = int(m.group(1)) if m else 8
        slides = []
        for i in range(1, n + 1):
            slides.append({"n": i, "title": "表紙" if i == 1 else f"論点{i}", "message": f"{i}枚目の結論（偽モデル）",
                           "bullets": ["国内に推定12,000社", "月額5万円", "月末の三日を半日に"][: 3 if i > 1 else 1],
                           "visual": "社数×単価の棒グラフ" if i == 3 else "", "notes": "話すこと（偽モデル）。" + prose(120, seed + i),
                           "source": "market" if i == 3 else "overview"})
        if os.environ.get("MOCK_NEW_NUMBER") and kind == "deck":
            slides[-1]["bullets"].append("売上は3年で48億円")  # 原案にない数字（deck_lint の警告を通すため）
        return json.dumps({"deck": {"title": "経理の突き合わせ自動化", "subtitle": "企画のご提案", "slides": slides}, "changes": "偽モデル"},
                          ensure_ascii=False)
    return "{}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _gate(self):
        """本物の OpenCode Go で確かめた門（2026-10-13）を真似る。通さないときは応答を返して True。"""
        if self.headers.get("User-Agent", "Python-urllib").startswith("Python-urllib"):
            data = b"error code: 1010"  # Cloudflare：ライブラリ名の User-Agent は弾かれる
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True
        if self.command == "POST" and "/zen/go/" in self.path and not self.headers.get("x-opencode-session"):
            self._json(400, {"type": "error", "error": {"type": "MissingSessionID",
                                                         "message": "Request is missing x-opencode-session and cannot be routed efficiently."}})
            return True
        return False

    def do_GET(self):
        if self._gate():
            return
        if "/zen/" in self.path and self.path.rstrip("/").endswith("/models"):  # OpenCode：価格なしの一覧
            return self._json(200, {"object": "list", "data": [{"id": m.split("/")[-1], "object": "model", "owned_by": "opencode"}
                                                               for m, _, _ in MODELS] + [{"id": "mockwire-odd", "object": "model"}]})
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"data": [{"id": m, "name": m, "context_length": 200000,
                                              "pricing": {"prompt": f"{i / 1e6:.10f}", "completion": f"{o / 1e6:.10f}"}} for m, i, o in MODELS]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self._gate():
            return
        path = self.path.rstrip("/")
        if path.endswith("/responses") or path.endswith("/messages") or "/zen/" in path:
            return self._opencode(path, body)
        text = body["messages"][-1]["content"]
        model = (body.get("models") or [body.get("model")])[0]
        if model == "mock/broken":
            return self._json(400, {"error": {"message": "mock: this model always fails"}})
        s = re.search(r"<!-- STUDIO: (\S+) -->", text)
        if s:
            out = studio_answer(s.group(1), text, model.removesuffix(":online"))
        else:
            out = "OK"
        time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
        if out == FAIL:
            return self._json(400, {"error": {"message": "mock: upstream provider gave up (request too large)"}})
        pin, pout = PRICE.get(model.removesuffix(":online"), (1e-6, 2e-6))
        usage = {"prompt_tokens": len(text) // 2, "completion_tokens": len(out) // 2}
        usage["cost"] = round(usage["prompt_tokens"] * pin + usage["completion_tokens"] * pout, 8)
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b": OPENROUTER PROCESSING\n\n")
            for i in range(0, len(out), 400):
                chunk = {"model": model, "choices": [{"delta": {"content": out[i:i + 400]}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            fin = {"model": model, "choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage}
            self.wfile.write(f"data: {json.dumps(fin)}\n\ndata: [DONE]\n\n".encode())
            return
        self._json(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}], "usage": usage})


def zen_route(model):
    """偽 OpenCode の窓口の割り当て。mockwire-odd は道具の規則（chat）と違う /messages にだけある＝窓口の切り替えを試すため。"""
    m = model.split("/")[-1]
    if re.match(r"^(gpt|grok|muse)", m):
        return "responses"
    if re.match(r"^(claude-|minimax|qwen|mockwire-odd)", m):
        return "messages"
    return "chat"


def _opencode_handler(self, path, body):
    proto = "responses" if path.endswith("/responses") else "messages" if path.endswith("/messages") else "chat"
    model = body.get("model", "")
    if "/zen/" in path and zen_route(model) != proto:
        return self._json(404, {"type": "error", "error": {"type": "NotFound", "message": f"Model {model} is not supported on this endpoint"}})
    if proto == "messages" and not self.headers.get("anthropic-version"):
        return self._json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "anthropic-version header is required"}})
    if proto == "responses":
        inp = body.get("input")
        text = inp if isinstance(inp, str) else "".join(x.get("content", "") for x in inp or [] if isinstance(x.get("content"), str))
    elif proto == "messages":
        text = (body.get("messages") or [{}])[-1].get("content", "")
    else:
        text = (body.get("messages") or [{}])[-1].get("content", "")
    st = re.search(r"<!-- STUDIO: (\S+) -->", text)
    out = studio_answer(st.group(1), text, model) if st else "OK"
    time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
    pin, pout = len(text) // 2, len(out) // 2  # 費用は返さない（OpenCode と同じ）
    if proto == "responses":
        return self._json(200, {"id": "resp_mock", "object": "response", "model": model, "status": "completed",
                                "output": [{"type": "reasoning", "summary": []},
                                           {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": out}]}],
                                "usage": {"input_tokens": pin, "output_tokens": pout}})
    if proto == "messages":
        return self._json(200, {"id": "msg_mock", "type": "message", "role": "assistant", "model": model, "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": out}], "usage": {"input_tokens": pin, "output_tokens": pout}})
    usage = {"prompt_tokens": pin, "completion_tokens": pout}
    if body.get("stream"):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(out), 400):
            self.wfile.write(f"data: {json.dumps({'model': model, 'choices': [{'delta': {'content': out[i:i + 400]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(f"data: {json.dumps({'model': model, 'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': usage})}\n\ndata: [DONE]\n\n".encode())
        return
    return self._json(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}], "usage": usage})


H._opencode = _opencode_handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    print(f"mock OpenRouter on http://127.0.0.1:{a.port}/api/v1  （OpenCode Go の真似は /zen/go/v1）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()

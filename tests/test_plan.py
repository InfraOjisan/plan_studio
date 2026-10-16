"""plan_studio の回帰テスト（標準ライブラリの unittest だけ。API キー・費用は不要）。

偽モデル（studio/devtools/mock_openrouter.py）を自分で立てて、テーマ → 提案 → 分類 → 選ぶ → 原案 → たたき → 書き出し を端から端まで通す。
  python3 -m unittest discover -s tests
"""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import xml.dom.minidom
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(REPO / "studio"))

THEME = {"genre": "ITサービス", "customer": "中小製造業の経理", "theme": "月末の突き合わせを減らす", "team": "5名", "period": "6か月",
         "must": "既存の会計ソフトと連携", "must_not": "", "research": "競合SaaS、海外との差"}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_mock(port, **env):
    e = {**os.environ, **env}
    p = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(port)], env=e,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/models", headers={"User-Agent": "t"}), timeout=1)
            return p
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("mock did not start")


def fresh_core(base, works):
    """環境変数を入れてから core を読み直す（WORKS と窓口は import 時に決まるため）。"""
    os.environ.update({"PLAN_LLM_BASE_URL": base, "PLAN_PROVIDER_APIKEY": "mock", "PLAN_WORKS_DIR": works})
    for k in ("NOVEL_LLM_BASE_URL", "NOVEL_PROVIDER_APIKEY", "NOVEL_WORKS_DIR"):
        os.environ.pop(k, None)
    for m in ("core", "llm", "outputs", "similarity", "llm_wire"):
        sys.modules.pop(m, None)
    import core
    return core


class Similarity(unittest.TestCase):
    def test_convergent_and_outlier(self):
        import similarity
        a = {"customer": "中小製造業の経理", "pain": "突き合わせの手間", "solution": "SaaS", "revenue": "月額", "edge": "価格", "market": "既存の置き換え"}
        b = {"customer": "自治体の窓口", "pain": "問い合わせの多さ", "solution": "代行・BPO", "revenue": "従量", "edge": "規制対応", "market": "制度・規制起点"}
        props = [{"id": f"P0{i}", "title": "経理SaaS", "summary": "経理の突き合わせを自動化する", "market": "国内"} for i in range(1, 5)]
        props.append({"id": "P05", "title": "窓口代行", "summary": "自治体の問い合わせを受ける", "market": "自治体"})
        sk = {p["id"]: a for p in props[:4]}
        sk["P05"] = b
        r = similarity.classify(props, sk, 0.5)
        self.assertEqual(r["labels"]["P01"]["label"], "収束")
        self.assertEqual(r["labels"]["P05"]["label"], "外れ値")


class Lints(unittest.TestCase):
    def setUp(self):
        self.core = fresh_core("http://127.0.0.1:9/api/v1", tempfile.mkdtemp())
        import outputs
        self.outputs = outputs

    def test_limits(self):
        c = self.core
        self.assertEqual(c.over_limits({"title": "あ" * 50, "summary": "い" * 1100}), [])  # 15% までは許す
        self.assertTrue(c.over_limits({"title": "あ" * 70}))

    def test_draft_lint(self):
        c = self.core
        d = {"title": "x", "checks": ["a"]}
        for k, _, t in c.DRAFT_SECTIONS:
            d[k] = "あ" * t
        self.assertTrue(c.draft_lint(d)["ok"])
        d["design"] = ""
        r = c.draft_lint(d)
        self.assertFalse(r["ok"])
        self.assertTrue(any("概要設計" in e for e in r["errors"]))
        d["design"], d["sales"], d["checks"] = "あ" * 3000, "あ" * 100, []
        r = c.draft_lint(d)
        self.assertTrue(r["ok"])
        self.assertTrue(any("販売方法" in w and "短い" in w for w in r["warnings"]))
        self.assertTrue(any("要確認" in w for w in r["warnings"]))

    def test_deck_lint_new_numbers(self):
        draft = {"market": "国内に推定12,000社、月額5万円。成長率3.5%。"}
        deck = {"slides": [{"title": "市場", "message": "12000社が対象", "bullets": ["月額5万円", "成長率3.5%", "3年で48億円"]}]}
        r = self.outputs.deck_lint(deck, draft, 1)
        self.assertEqual(r["new_numbers"], ["48"])  # 12,000 と 12000 は同じ数字とみなす。1桁の数は見ない
        self.assertTrue(r["ok"])

    def test_docx_is_valid(self):
        deck = {"title": "題 & <名>", "slides": [{"title": "表紙", "message": "結論", "bullets": ["a", "b"], "visual": "棒グラフ", "notes": "話す"}]}
        data = self.outputs.deck_docx(deck, {"audience": "経営会議", "minutes": 10})
        z = zipfile.ZipFile(io.BytesIO(data))
        for n in ("[Content_Types].xml", "word/document.xml", "word/styles.xml", "_rels/.rels", "docProps/core.xml"):
            xml.dom.minidom.parseString(z.read(n))  # 壊れた XML なら例外
        doc = z.read("word/document.xml").decode()
        self.assertIn("題 &amp; &lt;名&gt;", doc)
        self.assertIn('w:val="Heading1"', doc)

    def test_tool_prompts(self):
        deck = {"title": "t", "slides": [{"title": "a", "message": "b"}]}
        o = {"audience": "x", "minutes": 5, "purpose": "approve"}
        self.assertIn("見出し1", self.outputs.tool_prompt("copilot", deck, o))
        self.assertIn(".pptx", self.outputs.tool_prompt("claude", deck, o))
        self.assertIn("Google スライド", self.outputs.tool_prompt("gemini", deck, o))
        self.assertIn("marp: true", self.outputs.deck_marp(deck))


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.mock = start_mock(cls.port, MOCK_LONG="1", MOCK_NEW_NUMBER="1")
        cls.works = tempfile.mkdtemp()
        cls.core = fresh_core(f"http://127.0.0.1:{cls.port}/api/v1", cls.works)

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        cls.mock.wait()

    def usage(self, slug):
        return [json.loads(x) for x in (Path(self.works) / slug / "studio" / "usage.jsonl").read_text().splitlines()]

    def test_full_run(self):
        c = self.core
        c.create_work("acc", THEME)
        r = c.propose("acc", ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna-pro", "deepseek/deepseek-v4.1-flash"], n=2)
        self.assertEqual(len(r["proposals"]), 6, r)
        # 字数の上限を超えた（MOCK_LONG）→ 一度だけ詰め直しを頼んだので、提案の呼び出しは 3モデル×2回
        self.assertEqual(sum(1 for u in self.usage("acc") if u["stage"] == "propose"), 6)
        st = c.load("acc")
        self.assertTrue(all(not p["over"] for p in st["proposals"].values()))
        self.assertTrue(all(len(p["summary"]) <= 1150 for p in st["proposals"].values()))
        cl = c.classify("acc")
        self.assertIn("収束", {v["label"] for v in cl["labels"].values()})
        self.assertTrue(c.avoid_list(c.load("acc")))
        with self.assertRaises(c.StudioError):
            c.make_draft("acc")  # 選ぶ前は作れない
        c.select("acc", "P01")
        v = c.make_draft("acc", borrow=["P02", "P99"])
        d = c.get_draft(c.load("acc"), v)
        self.assertTrue(d["lint"]["ok"], d["lint"])
        self.assertEqual(d["borrow"], ["P02"])  # ない案は捨てる
        v2 = c.make_draft("acc", instruction="リスクを厚く", base_version=v)
        self.assertIn("指示を反映", c.get_draft(c.load("acc"), v2)["draft"]["catch"])
        v3 = c.edit_draft("acc", v2, {"catch": "人が直した", "checks": "一つ目\n二つ目\n"})
        d3 = c.get_draft(c.load("acc"), v3)
        self.assertEqual((d3["origin"], d3["draft"]["checks"]), ("human", ["一つ目", "二つ目"]))
        k = c.make_deck("acc", v3, {"slides": 8, "audience": "経営会議", "purpose": "approve", "minutes": 10})
        deck = next(x for x in c.load("acc")["decks"] if x["v"] == k)
        self.assertEqual(len(deck["deck"]["slides"]), 8)
        self.assertIn("48", deck["lint"]["new_numbers"])  # 偽モデルがわざと足した、原案にない数字
        k2 = c.make_deck("acc", None, base_version=k, instruction="短く")
        self.assertEqual(next(x for x in c.load("acc")["decks"] if x["v"] == k2)["draft_v"], v3)
        for kind in ("draft_md", "deck_md", "deck_docx", "deck_marp"):
            name, data, ctype = c.export("acc", kind, v3 if kind == "draft_md" else k)
            self.assertTrue(data, kind)
        name, data, _ = c.export("acc", "draft_md", v3)
        self.assertIn("## 要確認事項", data.decode())
        self.assertIn("## 概要設計", data.decode())
        self.assertEqual(c.stage_of(c.load("acc")), "deck")
        snap = c.snapshot("acc")
        self.assertGreater(snap["costs"]["total_usd"], 0)
        self.assertEqual(set(c.tool_prompts("acc", k)), {"copilot", "claude", "chatgpt", "gemini"})

    def test_rolling_and_revise(self):
        c = self.core
        c.create_work("roll", THEME)
        c.propose("roll", ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna-pro"], n=3)
        c.classify("roll")
        r = c.propose("roll", ["anthropic/claude-haiku-5.5"], n=2, mode="rolling")
        self.assertEqual(c.load("roll")["rounds"][-1]["mode"], "rolling")
        new = c.revise_proposal("roll", r["proposals"][0], "openai/gpt-6-luna-pro", "顧客を絞る")
        p = c.load("roll")["proposals"][new]
        self.assertEqual((p["origin"], p["parent"]), ("revise", r["proposals"][0]))
        e = c.edit_proposal("roll", new, {"title": "人の題", "verdict": "国内ブルー"})
        self.assertEqual(c.load("roll")["proposals"][e]["title"], "人の題")

    def test_derive(self):
        c = self.core
        c.create_work("moon", THEME)
        before = (Path(self.works) / "moon" / "studio.json").read_bytes()
        st = c.derive_work("moon", "moon-2", {**THEME, "customer": "自治体の窓口"})
        self.assertEqual(st["derived_from"]["changed"], ["顧客層"])
        self.assertEqual((Path(self.works) / "moon" / "studio.json").read_bytes(), before)
        with self.assertRaises(c.StudioError):
            c.derive_work("moon", "moon-2", THEME)

    def test_server_and_cli(self):
        port = free_port()
        env = {**os.environ}
        srv = subprocess.Popen([PY, str(REPO / "studio" / "server.py"), "--port", str(port)], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/works", timeout=1)
                    break
                except OSError:
                    time.sleep(0.1)
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/works", data=json.dumps({"slug": "web", "theme": THEME}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            self.assertEqual(json.loads(urllib.request.urlopen(req).read())["slug"], "web")
            snap = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/works/web").read())
            self.assertEqual(snap["stage"], "theme")
            self.assertEqual(len(snap["sections"]), 6)
            html = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
            self.assertIn("plan", html)
        finally:
            srv.terminate()
            srv.wait()
        out = subprocess.run([PY, str(REPO / "studio" / "cli.py"), "show", "web", "--brief"], capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(out.stdout)["slug"], "web")


class ChunkedClassify(unittest.TestCase):
    """8案を分析しても上流で落ちないこと：骨格は小分けに頼み、落ちたら1案ずつに分け、取れた分から保存する。選んだ案だけを分析する。"""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        # 2案以上の依頼は「上流が諦める」。P05 はいつも失敗する
        cls.mock = start_mock(cls.port, MOCK_SKELETON_MAX="1", MOCK_FAIL_SKELETON="P05")
        cls.works = tempfile.mkdtemp()
        cls.core = fresh_core(f"http://127.0.0.1:{cls.port}/api/v1", cls.works)
        c = cls.core
        c.create_work("many", THEME)
        c.propose("many", ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna-pro", "deepseek/deepseek-v4.1-flash", "qwen/qwen3.8-flash"], n=2)
        assert len(c.load("many")["proposals"]) == 8

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        cls.mock.wait()

    def skeleton_calls(self):
        f = Path(self.works) / "many" / "studio" / "usage.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines() if json.loads(x)["stage"] == "skeleton"]

    def test_1_split_and_keep(self):
        c = self.core
        seen = []
        r = c.classify("many", progress=lambda d, t, w="": seen.append((d, t)))
        self.assertEqual(r["missing"], ["P05"])
        self.assertEqual(len(r["ids"]), 7)
        self.assertNotIn("P05", r["labels"])
        self.assertTrue(r["errors"]["P05"])
        calls = self.skeleton_calls()
        self.assertEqual(len(calls), 7)  # 3案ずつの依頼はすべて落ち、1案ずつで7案取れた（費用は成功した呼び出しだけ）
        self.assertTrue(all("," not in u["note"] for u in calls))
        self.assertEqual(seen[-1], (7, 8))
        self.assertEqual(sorted(c.load("many")["skeletons"]), ["P01", "P02", "P03", "P04", "P06", "P07", "P08"])

    def test_2_selected_only_and_cached(self):
        c = self.core
        before = len(self.skeleton_calls())
        r = c.classify("many", ids=["P01", "P02", "P07"])
        self.assertEqual(sorted(r["ids"]), ["P01", "P02", "P07"])
        self.assertEqual(set(r["labels"]), {"P01", "P02", "P07"})
        self.assertEqual(len(self.skeleton_calls()), before)  # 抜き出し済みの骨格は使い回す（モデルを呼ばない）
        r2 = c.reclassify("many", 0.3)
        self.assertEqual(set(r2["labels"]), {"P01", "P02", "P07"})  # しきい値を変えても、分類した案の組はそのまま
        with self.assertRaises(c.StudioError):
            c.classify("many", ids=["P01"])
        with self.assertRaises(c.StudioError):
            c.classify("many", ids=["P05", "P99"])

    def test_3_ai_reads_skeletons_only(self):
        c = self.core
        st = c.load("many")
        props = [st["proposals"][i] for i in ("P01", "P02")]
        block = c._skeleton_block(props, c.skeleton_store(st))
        self.assertNotIn(props[0]["summary"][:30], block)  # 本文は渡さない
        self.assertIn("one_line", block)
        r = c.classify("many", ids=["P01", "P02", "P03", "P04"], method="both")
        self.assertEqual(set(r["ai"]["labels"]), {"P01", "P02", "P03", "P04"})

    def test_4_export(self):
        c = self.core
        name, data, ctype = c.export_proposals("many", ["P01", "P03", "P05"], "csv")
        self.assertTrue(name.endswith("3案.csv"))
        text = data.decode("utf-8")
        self.assertTrue(text.startswith("\ufeff案,"))
        import csv
        rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        self.assertEqual(len(rows), 4)
        self.assertEqual([r[0] for r in rows[1:]], ["P01", "P03", "P05"])
        name, data, _ = c.export_proposals("many", ["P01", "P03"], "md")
        md = data.decode()
        self.assertIn("| P01 |", md)
        self.assertIn("### 企画の概要", md)
        obj = json.loads(c.export_proposals("many", [], "json")[1])
        self.assertEqual(len(obj["proposals"]), 8)  # 何も選ばなければ、外していない案すべて
        with self.assertRaises(c.StudioError):
            c.export_proposals("many", ["P01"], "xlsx")


class OpenCodeEndToEnd(unittest.TestCase):
    """偽の OpenCode Go（/zen/go/v1）でも、窓口を使い分けて提案から原案まで通ること。"""

    def test_go(self):
        port = free_port()
        mock = start_mock(port)
        try:
            c = fresh_core(f"http://127.0.0.1:{port}/zen/go/v1", tempfile.mkdtemp())
            c.create_work("go", THEME)
            r = c.propose("go", ["claude-haiku-5-5", "gpt-6-luna", "deepseek-v4.1-flash"], n=1)
            self.assertEqual(len(r["proposals"]), 3, r)
            protos = {u["model"]: u["protocol"] for u in (Path(c.WORKS) / "go" / "studio" / "usage.jsonl").read_text().splitlines()
                      for u in [json.loads(u)]}
            self.assertEqual(protos.get("claude-haiku-5-5"), "messages")
            self.assertEqual(protos.get("gpt-6-luna"), "responses")
            c.select("go", r["proposals"][0])
            v = c.make_draft("go", model="claude-opus-5-5")
            self.assertTrue(c.get_draft(c.load("go"), v)["lint"]["ok"])
        finally:
            mock.terminate()
            mock.wait()


if __name__ == "__main__":
    unittest.main()

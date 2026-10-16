#!/usr/bin/env python3
"""studio/cli.py — 画面と同じ工程を、ハーネス（HermesAgent / agy / omp など）やスクリプトから使うための CLI。

すべて JSON を返す（標準出力に1つ）。終了コード 0=成功 / 2=人の判断が要る・拒否 / 1=エラー。
画面（server.py）と同じ core.py を呼ぶので、画面とハーネスを混ぜて使ってよい（同じ works/<slug>/studio.json を読む）。

  python3 studio/cli.py works
  python3 studio/cli.py new <slug> --theme theme.json
  python3 studio/cli.py show <slug> [--brief]                  状態の要約（--brief で素案の本文を省く）
  python3 studio/cli.py theme <slug> --theme theme.json         テーマを変えて最初から（記録は studio/history/ に退避）
  python3 studio/cli.py derive <slug> <new-slug> [--theme theme.json]  テーマを直して、新しい企画フォルダで提案し直す
  python3 studio/cli.py propose <slug> --models m1,m2 [--n 2] [--mode new|resubmit|rolling] [--instruction "..."] [--temperature 0.8] [--web]
  python3 studio/cli.py edit <slug> <pid> --fields fields.json   人（または進行役）が素案を直す。新しい案として足される
  python3 studio/cli.py revise <slug> <pid> --model m --instruction "..."
  python3 studio/cli.py classify <slug> [--ids P01,P03,...] [--method tool|ai|both] [--model m] [--threshold 0.5] [--refresh]
        骨格は小分け（settings の skeleton_batch 案ずつ、skeleton_parallel 本まで同時）に抜き出し、取れた分から保存する
  python3 studio/cli.py export-proposals <slug> [--ids P01,P03,...] [--fmt md|csv|json] [--out path]   素案をまとめて書き出す（見比べる用）
  python3 studio/cli.py reclassify <slug> --threshold 0.45      骨格は使い回し、しきい値だけ変える（モデルを呼ばない）
  python3 studio/cli.py select <slug> <pid>
  python3 studio/cli.py draft <slug> [--model m] [--instruction "..."] [--base V] [--borrow P03,P07]
  python3 studio/cli.py draft-put <slug> <base-V> --fields fields.json   節ごとに直した原案を新しい版として置く
  python3 studio/cli.py deck <slug> --draft V [--audience "..."] [--purpose approve|customer|share] [--slides 12] [--minutes 15]
                                     [--model m] [--instruction "..."] [--base V]
  python3 studio/cli.py deck-put <slug> <base-V> --file deck.json
  python3 studio/cli.py export <slug> draft_md|deck_md|deck_docx|deck_marp <V> [--tool copilot|claude|chatgpt|gemini] [--out path]
  python3 studio/cli.py costs <slug>
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402


def out(obj, rc=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return rc


def summary(st, short=False):
    props = {}
    for pid, p in st["proposals"].items():
        props[pid] = {k: p.get(k) for k in ("model", "kind", "genre", "title", "verdict", "usd", "origin", "parent", "hidden", "over")}
        if not short:
            props[pid]["summary"] = p.get("summary")
        lab = (st.get("classify") or {}).get("labels", {}).get(pid)
        if lab:
            props[pid]["label"] = lab["label"]
    c = st.get("classify") or {}
    return {"slug": st["slug"], "stage": core.stage_of(st), "theme": st["theme"], "theme_version": st["theme_version"], "derived_from": st.get("derived_from"),
            "rounds": [{k: r.get(k) for k in ("id", "mode", "models", "n", "web", "proposals", "errors")} for r in st["rounds"]],
            "proposals": props, "selected": st.get("selected"),
            "classify": {"clusters": [{k: cl.get(k) for k in ("id", "label", "type", "members", "cohesion")} for cl in c.get("clusters", [])],
                         "mean_similarity": c.get("mean_similarity"), "threshold": c.get("threshold")} if c else None,
            "avoid": core.avoid_list(st),
            "drafts": [{"v": d["v"], "from": d["from"], "borrow": d.get("borrow"), "model": d["model"], "origin": d.get("origin"), "lint": d["lint"], "usd": d["usd"]}
                       for d in st["drafts"]],
            "decks": [{"v": k["v"], "draft_v": k["draft_v"], "opts": k["opts"], "model": k["model"], "origin": k.get("origin"), "lint": k["lint"], "usd": k["usd"]}
                      for k in st["decks"]],
            "costs": core.costs(st["slug"])}


def rd_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--theme")
    ap.add_argument("--models")
    ap.add_argument("--model")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--mode", default="new")
    ap.add_argument("--instruction", default="")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--web", action="store_true")
    ap.add_argument("--fields")
    ap.add_argument("--method", default="tool")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--base", type=int)
    ap.add_argument("--borrow", default="")
    ap.add_argument("--draft", type=int)
    ap.add_argument("--audience")
    ap.add_argument("--purpose")
    ap.add_argument("--slides", type=int)
    ap.add_argument("--minutes", type=int)
    ap.add_argument("--file")
    ap.add_argument("--tool", default="claude")
    ap.add_argument("--out")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--ids", default="")
    ap.add_argument("--fmt", default="md")
    a = ap.parse_args(argv)
    A = a.args
    try:
        if a.cmd == "works":
            return out({"works": core.list_works()})
        if a.cmd == "new":
            core.create_work(A[0], rd_json(a.theme) if a.theme else {})
            return out(summary(core.load(A[0])))
        if a.cmd == "show":
            return out(summary(core.load(A[0]), short=a.brief))
        if a.cmd == "theme":
            core.change_theme(A[0], rd_json(a.theme))
            return out(summary(core.load(A[0])))
        if a.cmd == "derive":
            st = core.derive_work(A[0], A[1], rd_json(a.theme) if a.theme else core.load(A[0])["theme"])
            return out(summary(st))
        if a.cmd == "propose":
            models = [m.strip() for m in (a.models or ",".join(core.settings()["propose_models"])).split(",") if m.strip()]
            return out(core.propose(A[0], models, a.n, a.instruction, a.mode, a.temperature, web=a.web))
        if a.cmd == "edit":
            return out({"pid": core.edit_proposal(A[0], A[1], rd_json(a.fields))})
        if a.cmd == "revise":
            return out({"pid": core.revise_proposal(A[0], A[1], a.model or core.settings()["skeleton_model"], a.instruction)})
        if a.cmd == "classify":
            ids = [x for x in a.ids.split(",") if x] or None
            r = core.classify(A[0], a.model, a.method, a.threshold, refresh_skeletons=a.refresh, ids=ids,
                              progress=lambda d, t, w="": print(f"{w} {d}/{t}", file=sys.stderr, flush=True))
            return out({k: r.get(k) for k in ("ids", "labels", "clusters", "mean_similarity", "top_pairs", "ai", "missing", "errors")},
                       0 if not r.get("missing") else 2)
        if a.cmd == "export-proposals":
            name, data, _ = core.export_proposals(A[0], [x for x in a.ids.split(",") if x], a.fmt)
            p = Path(a.out or name)
            p.write_bytes(data)
            return out({"file": str(p), "bytes": len(data)})
        if a.cmd == "reclassify":
            r = core.reclassify(A[0], a.threshold)
            return out({k: r.get(k) for k in ("labels", "clusters", "mean_similarity")})
        if a.cmd == "select":
            core.select(A[0], A[1])
            return out({"selected": A[1]})
        if a.cmd == "draft":
            v = core.make_draft(A[0], a.model, a.instruction, a.base, [x for x in a.borrow.split(",") if x])
            d = core.get_draft(core.load(A[0]), v)
            return out({"v": v, "lint": d["lint"]}, 0 if d["lint"]["ok"] else 2)
        if a.cmd == "draft-put":
            v = core.edit_draft(A[0], int(A[1]), rd_json(a.fields))
            return out({"v": v, "lint": core.get_draft(core.load(A[0]), v)["lint"]})
        if a.cmd == "deck":
            if not a.draft and not a.base:
                return out({"error": "--draft（原案の版）が必要です"}, 1)
            opts = {k: getattr(a, k) for k in ("audience", "purpose", "slides", "minutes") if getattr(a, k) is not None}
            v = core.make_deck(A[0], a.draft, opts, a.model, a.instruction, a.base)
            k = next(x for x in core.load(A[0])["decks"] if x["v"] == v)
            return out({"v": v, "lint": k["lint"]}, 0 if k["lint"]["ok"] else 2)
        if a.cmd == "deck-put":
            v = core.edit_deck(A[0], int(A[1]), rd_json(a.file))
            return out({"v": v})
        if a.cmd == "export":
            name, data, _ = core.export(A[0], A[1], A[2], a.tool)
            p = Path(a.out or name)
            p.write_bytes(data)
            return out({"file": str(p), "bytes": len(data)})
        if a.cmd == "costs":
            return out(core.costs(A[0]))
        return out({"error": f"不明なコマンド {a.cmd}", "usage": __doc__}, 1)
    except core.StudioError as e:
        return out({"error": str(e)}, 2)
    except (IndexError, FileNotFoundError, ValueError) as e:
        return out({"error": f"引数が足りないか不正です：{e}", "usage": __doc__.strip().splitlines()[0]}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

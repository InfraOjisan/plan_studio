# plan_studio

生成 AI に商品・サービスの企画を出させ、企画書のたたきまで進める道具です。テーマを入れると、複数のモデルが企画の素案を出し、似た案と外れた案を見分け、選んだ案から企画原案（企画書のベースになる文書）を作り、PowerPoint の Copilot・Claude・ChatGPT・Gemini に渡せば企画書になる「たたき」まで、一つの画面で進みます。

[novel_studio](https://github.com/InfraOjisan/novel_studio)（小説を書く道具）の企画コンペの部分を、事業企画用に分けたものです（novel_studio の Issue #3「汎用の企画提案」）。

- Python 3.9 以上の標準ライブラリだけで動きます（pip install は不要）。
- モデルは OpenRouter か OpenCode（Go・Zen）。社内のモデルルーターなど、OpenAI 互換の窓口なら差し替え可。
- 自分の Mac（127.0.0.1）で使う前提です。novel_studio（8780）と並べて動かせるよう、既定のポートは 8790 です。

```
git clone https://github.com/InfraOjisan/plan_studio && cd plan_studio
echo "OPENROUTER_API_KEY=sk-or-..." > .env
python3 studio/server.py          # → http://127.0.0.1:8790
```

社内のモデルルーター（OpenAI 互換）を使うときは `.env` を次のようにします。novel_studio と同じ `NOVEL_*` の名前もそのまま読みます。

```
PLAN_LLM_BASE_URL=https://router.example.local/v1
PLAN_PROVIDER_APIKEY=<鍵>
```

## 流れ

| 工程 | 何をするか | 既定のモデル |
|---|---|---|
| 一 テーマ | ジャンル（ITサービス／人系サービス／物販／統合ソリューション…）・顧客層・テーマ（コンセプト・題材）・開発規模（チーム人数・リリース期間）・必ず入れたいこと・入れたくないこと・調査事項（競合、海外と国内の市場差など）。任意で自社の前提・目標の規模感。あとから**テーマを直して、新しい企画フォルダで頼み直せる** | 人 |
| 二 提案 | 選んだモデルに n 案ずつ並列で出させる。素案は 企画タイトル（50字）・判断の一言（30字）・概要（1000字）・必要なリソース（500字）・マーケット（500字）・判断（500字、数年後レッド／国内はブルーなど）・出典。**上限を超えたら一度だけ詰め直しを頼む**。OpenRouter なら Web 検索（`:online`）で競合・市場を調べさせられる | Haiku 5.5 / GPT-6 Luna Pro / DeepSeek V4.1 Flash ほか |
| 三 分類・選ぶ | 事業の骨格（誰の・何の課題を・どう解き・どこで儲け・何で勝つか・どんな市場か）をモデルに抜き出させ、**似ているかは道具が数える** →「収束」「バリエーション」「外れ値」。人が原案に進める案を選ぶ | Luna Pro（抽出だけ） |
| 四 企画原案 | 選んだ案から、サービスの概要（2000字）・マーケットの状況とインパクト（1000字）・当社の必要リソースと目標売上・目標利益（1000字）・リスクと対策（1000字）・概要設計（3000字）・販売方法・アプローチ（1000字）・**要確認事項**。ほかの案（外れ値など）の要素を取り込ませることもできる。字数を機械で検査。指示つき改訂・節ごとの直接編集 | Opus 5.5 |
| 五 企画書たたき | 聞き手・目的・枚数・時間を決めて、1枚＝1メッセージのスライド構成案にする。**原案にない数字が出たら警告**。PowerPoint の Copilot 用の Word（.docx）、Claude・ChatGPT・Gemini 用の依頼文つき Markdown、AI を通さずに見る Marp を書き出す | Luna Pro |

画面の上に、工程ごとの費用が出ます。

## 中身

```
studio/     Web アプリ（server.py）、同じ工程の CLI（cli.py）、分類（similarity.py）、書き出し（outputs.py）、偽モデル（devtools/）
tests/      回帰テスト（偽モデルで端から端まで）
works/      作った企画（git には入れない。PLAN_WORKS_DIR で別の場所にもできる）
```

ハーネス（HermesAgent・agy・omp など）から使うときは `studio/cli.py`（すべて JSON で返す）。画面と同じ `works/<slug>/studio.json` を読むので、混ぜて使えます。

## 考え方（詳しくは docs/DESIGN.md）

- **合議は外れ値を生まない。** 分類は良し悪しではなく「型への寄り方」を見せる。外れ値を拾うのは人。
- **重複の判定は LLM に任せない。** モデルには骨格の抽出だけを頼み、近さは文字 2-gram と骨格の欄で数える。
- **数字は出どころを残す。** 素案と原案には「出典」か「推定（筋道つき）」を書かせ、確かめていない点は原案の「要確認事項」に残す。たたきでは原案にない数字を機械で見つける。
- **中身を作る工程だけ上位モデル。** 原案は上位モデル、たたき（並べ替えと要約）は中位モデルで足りる。
- **スライドそのものは作らない。** 体裁は PowerPoint の Copilot などの得意分野。こちらは「何枚目で何を言うか」と、渡すための材料と依頼文までを作る。

## 費用をかけずに試す

```
python3 studio/devtools/mock_openrouter.py &
PLAN_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 PLAN_PROVIDER_APIKEY=mock python3 studio/server.py
python3 -m unittest discover -s tests      # 回帰テスト（偽モデルで端から端まで）
```

## ライセンス

MIT

/* plan_studio — 画面。サーバーの /api を呼ぶだけで、状態はすべてサーバー（works/<slug>/）にある。 */
"use strict";
const STAGES = ["theme", "propose", "classify", "draft", "deck"];
const FIELDS_JA = { kind: "種類", genre: "ジャンル・実現手段", title: "企画タイトル", verdict: "判断の一言", summary: "企画の概要", fit: "要件への適合",
  resources: "企画実行に必要なリソース", market: "マーケットの状況・規模", judgment: "判断", sources: "出典" };
const SK_JA = { customer: "誰の", pain: "何の課題", solution: "解き方", revenue: "儲け方", edge: "勝ち筋", market: "市場の型", one_line: "一行" };
const TOOL_HOW = {
  copilot: "Word ファイルを OneDrive か SharePoint に置き、PowerPoint の Copilot で「ファイルからプレゼンテーションを作成」→ このファイルを選び、依頼文を貼ります。",
  claude: "依頼文つきの Markdown をダウンロードして添付するか、そのまま貼ります。",
  chatgpt: "依頼文つきの Markdown をダウンロードして添付するか、そのまま貼ります。",
  gemini: "依頼文つきの Markdown をダウンロードして添付するか、そのまま貼ります。",
};
const S = { slug: "", snap: null, models: [], settings: {}, view: null, draftV: null, draftTab: "read", deckV: null, deckTab: "slides", tool: "copilot", jobs: {}, pick: new Set() };

const $ = (q, el = document) => el.querySelector(q);
const h = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const usd = (x) => "$" + (Number(x) || 0).toFixed(4);
const short = (m) => String(m || "").split("/").pop();
const len = (s) => [...String(s || "")].length;

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), err ? 7000 : 3000);
}

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}

async function waitJob(jid, label) {
  S.jobs[jid] = label;
  render();
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const j = await api("GET", `/api/jobs/${jid}`);
      if (j.progress) { S.jobs[jid] = `${label}（${j.progress.what} ${j.progress.done}/${j.progress.total}）`; updBusy(); }
      if (j.state === "done") return j.result;
      if (j.state === "error") throw new Error(j.error);
    }
  } finally {
    delete S.jobs[jid];
    await refresh();
  }
}

async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("コピーしました"); }
  catch (e) {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); toast("コピーしました"); } catch (e2) { toast("コピーできませんでした。手で選んでください", true); }
    ta.remove();
  }
}

/* ---------------------------------------------------------------- 読み込み */
async function loadWorks(select) {
  const d = await api("GET", "/api/works");
  const sel = $("#workSel");
  sel.innerHTML = `<option value="">— 選ぶ —</option>` + d.works.map((w) => `<option value="${h(w.slug)}">${h(w.slug)}　${h(w.title)}</option>`).join("");
  if (select) sel.value = select;
  const ks = $("#keyState");
  ks.textContent = d.has_key ? (d.base_url.includes("openrouter.ai") ? "キー：あり" : "窓口：" + d.base_url) : "APIキーがありません（.env）";
  ks.className = "keystate" + (d.has_key ? "" : " bad");
}

async function loadModels() {
  const d = await api("GET", "/api/models");
  S.models = d.models;
  S.settings = d.settings;
}

async function refresh() {
  if (!S.slug) { S.snap = null; render(); return; }
  try {
    S.snap = await api("GET", `/api/works/${S.slug}`);
  } catch (e) { toast(e.message, true); return; }
  if (!S.view) S.view = S.snap.stage;
  render();
}

function busy() {
  return Object.keys(S.jobs).length > 0 || (S.snap && S.snap.jobs && S.snap.jobs.length > 0);
}
setInterval(() => { if (S.slug && busy()) refresh(); }, 4000);

/* ---------------------------------------------------------------- 描画 */
function reached() {
  if (!S.snap) return 0;
  let i = STAGES.indexOf(S.snap.stage);
  if (S.snap.selected) i = Math.max(i, STAGES.indexOf("draft"));       // 案を選んだら原案へ
  if ((S.snap.drafts || []).some((d) => d.lint.ok)) i = Math.max(i, STAGES.indexOf("deck")); // 検査を通った原案があればたたきへ
  return i;
}

function render() {
  const snap = S.snap;
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = true; if (p.id !== "empty") p.innerHTML = ""; });
  renderMeter();
  if (!snap) {
    if (S.view === "theme") { $("#panel-theme").hidden = false; renderTheme(); }
    else $("#empty").hidden = false;
    renderRail();
    return;
  }
  renderRail();
  const v = S.view || snap.stage;
  $(`#panel-${v}`).hidden = false;
  ({ theme: renderTheme, propose: renderPropose, classify: renderClassify, draft: renderDraft, deck: renderDeck })[v]();
}

function renderRail() {
  const r = reached();
  document.querySelectorAll("#rail li").forEach((li) => {
    const i = STAGES.indexOf(li.dataset.stage);
    li.className = "";
    if (i < r) li.classList.add("done");
    if (i === r) li.classList.add("now");
    if (!S.snap ? i > 0 : i > r) li.classList.add("locked");
    if (li.dataset.stage === (S.view || (S.snap && S.snap.stage))) li.classList.add("view");
  });
}

function renderMeter() {
  const c = S.snap && S.snap.costs;
  $("#meterTotal").textContent = (c && c.estimated ? "≈" : "") + usd(c ? c.total_usd : 0) + (c && c.unpriced ? "+?" : "");
  if (!c) { $("#meterTip").textContent = "企画を選ぶと、工程ごとの費用が出ます。"; return; }
  const lines = ["工程ごと"];
  for (const [k, v] of Object.entries(c.studio)) lines.push(`  ${k.padEnd(14)} ${usd(v.usd)}  ${v.calls}回`);
  lines.push(`  合計 ${usd(c.studio_usd)}`);
  if (c.estimated) lines.push("", `うち ${c.estimated}回は公開価格からの見積もり（窓口が費用を返さない）`);
  if (c.unpriced) lines.push(`価格が分からず $0 で数えた呼び出し ${c.unpriced}回（PLAN_LLM_PRICES で価格を足せます）`);
  $("#meterTip").textContent = lines.join("\n");
}

function busyText(kinds) {
  const mine = Object.values(S.jobs).filter((l) => !kinds || kinds.some((k) => l.startsWith(k)));
  const srv = (S.snap && S.snap.jobs || []).filter((j) => !kinds || kinds.includes(j.kind));
  if (!mine.length && !srv.length) return "";
  const txt = mine.length ? mine.join("、") : srv.map((j) => `${j.kind}（${j.since}秒${j.progress ? `・${j.progress.what} ${j.progress.done}/${j.progress.total}` : ""}）`).join("、");
  return `${txt}を実行中です。このページを離れても止まりません。`;
}

function busyLine(kinds) {
  const t = busyText(kinds);
  return t ? `<p class="busy" data-kinds="${h((kinds || []).join(","))}">${h(t)}</p>` : "";
}

function updBusy() {  // 画面を描き直さずに、進み具合の文だけを書き換える（入力中の欄を消さないため）
  document.querySelectorAll(".busy[data-kinds]").forEach((el) => {
    const t = busyText(el.dataset.kinds ? el.dataset.kinds.split(",") : null);
    if (t) el.textContent = t;
  });
}

function modelOptions(selected, filter) {
  const list = S.models.length ? S.models : [{ id: selected }];
  return list.filter((m) => !filter || m.id === selected || m.id.includes(filter))
    .map((m) => `<option value="${h(m.id)}" ${m.id === selected ? "selected" : ""}>${h(m.id)}${m.in_per_m != null ? `　入${m.in_per_m}/出${m.out_per_m}` : ""}</option>`).join("");
}

function modelSelect(id, selected) {
  return `<select id="${id}">${modelOptions(selected)}</select>`;
}

function dialog(inner, okLabel, wide) {
  return new Promise((resolve) => {
    const dlg = $("#dlg"), form = $("#dlgForm");
    dlg.classList.toggle("wide", !!wide);
    form.innerHTML = inner + `<div class="row"><button class="act" value="ok" type="submit">${h(okLabel)}</button><button class="ghost" value="cancel" type="submit">やめる</button></div>`;
    form.querySelectorAll("[data-max]").forEach((ta) => counter(ta));
    dlg.onclose = () => {
      if (dlg.returnValue !== "ok") return resolve(null);
      const out = {};
      form.querySelectorAll("[name]").forEach((el) => (out[el.name] = el.value));
      resolve(out);
    };
    dlg.showModal();
  });
}

/* 字数の数え（上限か目安つき） */
function counter(ta) {
  const max = Number(ta.dataset.max), soft = ta.dataset.soft === "1";
  const c = document.createElement("span");
  c.className = "count";
  ta.after(c);
  const up = () => {
    const n = len(ta.value);
    c.textContent = `${n.toLocaleString()} 字／${soft ? "目安" : "上限"} ${max.toLocaleString()}`;
    c.classList.toggle("over", soft ? (n > max * 1.4 || n < max * 0.6) : n > max);
  };
  ta.addEventListener("input", up); up();
}

/* ---------------------------------------------------------------- 一 テーマ（#7） */
function themeForm(t = {}) {
  const v = (k, d = "") => h(t[k] ?? d);
  const rfiN = len(t.rfi || "");
  return `<div class="theme-form">
    <fieldset class="tf"><legend>What：要件（必ず守る。①→②の順に優先）</legend>
      <div class="form">
        <div class="f wide rfi">
          <span>① 添付の要件書（RFI・RFP・仕様書など。あれば最優先）</span>
          <div class="row"><input type="file" id="rfiFile" accept=".pdf,.docx,.txt,.md"><span class="muted" id="rfiInfo">${t.rfi_name ? `「${h(t.rfi_name)}」${rfiN.toLocaleString()} 字（${h(t.rfi_method || "")}）` : "PDF・Word（.docx）・テキストから文字を取り出して下に入れます。読み取りを確かめて、必要なら直してください。"}</span></div>
          <textarea name="rfi" id="rfiText" rows="${rfiN ? 10 : 3}" placeholder="要件書がなければ空のまま。本文を貼り付けてもかまいません">${v("rfi")}</textarea>
          <input type="hidden" name="rfi_name" id="rfiName" value="${v("rfi_name")}"><input type="hidden" name="rfi_method" id="rfiMethod" value="${v("rfi_method")}">
        </div>
        <label class="f wide">② テーマ・コンセプト・題材<textarea name="theme" placeholder="そのままサービスのイメージ。例：コストダウン、監査、セキュリティ">${v("theme")}</textarea></label>
      </div></fieldset>
    <fieldset class="tf"><legend>How：実現手段</legend>
      <div class="form">
        <label class="f">ジャンル（要件を満たす手段。要件そのものではない）<input type="text" name="genre" list="genres" value="${v("genre")}" placeholder="ITサービス／人系サービス／物販／統合ソリューション…"></label>
        <datalist id="genres"><option>ITサービス</option><option>人系サービス</option><option>物販</option><option>統合ソリューション</option><option>BPO・代行</option><option>ハード＋保守</option></datalist>
      </div></fieldset>
    <fieldset class="tf"><legend>誰に・誰が・いつ</legend>
      <div class="form">
        <label class="f">誰に（顧客層）<input type="text" name="customer" value="${v("customer")}" placeholder="例：中小製造業の経理、自治体の窓口"></label>
        <label class="f">誰が（チーム人数）<input type="text" name="team" value="${v("team")}" placeholder="例：5名（PM1・開発3・営業1）"></label>
        <label class="f">いつ（リリース時期）<input type="text" name="period" value="${v("period")}" placeholder="例：6か月で最初の版"></label>
      </div></fieldset>
    <fieldset class="tf"><legend>特色と割り切り</legend>
      <div class="form">
        <label class="f wide">必ず入れたいこと（要件以外で特色を出すポイント）<textarea name="must" placeholder="例：職員が自分でワークフローを直せる、監査ログを標準で残す">${v("must")}</textarea></label>
        <label class="f wide">入れたくないこと（競っても無駄・考える必要がないこと）<textarea name="must_not" placeholder="例：大手と同じ汎用チャットの機能競争、自前の大規模言語モデル">${v("must_not")}</textarea></label>
      </div></fieldset>
    <fieldset class="tf"><legend>そのほか（任意）</legend>
      <div class="form">
        <label class="f wide">調べてほしいこと（競合・海外と国内の市場差など）<textarea name="research" placeholder="例：競合があれば調査、海外と国内の市場差">${v("research")}</textarea></label>
        <label class="f wide">自社の前提<textarea name="company" placeholder="強み・既存顧客・使える技術・避けたい領域。原案の『当社の必要リソース』と『なぜ自社か』に効きます">${v("company")}</textarea></label>
        <label class="f">目標の規模感<input type="text" name="goal" value="${v("goal")}" placeholder="例：3年で年商5億、粗利40%"></label>
      </div></fieldset>
  </div>`;
}

function bindThemeForm(root) {  // 要件書のファイルを読み、文字を取り出して欄に入れる（サーバーでは保存しない）
  const f = $("#rfiFile", root);
  if (!f) return;
  const ta = $("#rfiText", root), info = $("#rfiInfo", root);
  const show = () => { if (!$("#rfiName", root).value && ta.value.trim()) info.textContent = `貼り付けた本文 ${len(ta.value).toLocaleString()} 字`; };
  ta.addEventListener("input", show);
  f.onchange = () => guard(async () => {
    const file = f.files[0];
    if (!file) return;
    info.textContent = `「${file.name}」を読んでいます…`;
    const data = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1] || ""); r.onerror = rej; r.readAsDataURL(file); });
    let d;
    try { d = await api("POST", "/api/extract", { name: file.name, data }); }
    catch (e) { info.textContent = e.message; throw e; }
    ta.value = d.text; ta.rows = 12;
    $("#rfiName", root).value = file.name; $("#rfiMethod", root).value = d.method;
    info.textContent = `「${file.name}」${d.chars.toLocaleString()} 字（${d.method === "pdftotext" ? "pdftotext" : d.method === "builtin" ? "内蔵の読み取り" : d.method}${d.pages ? `・${d.pages}ページ` : ""}）。読み取りを確かめて、必要なら直してください。` + (d.warning ? `　⚠ ${d.warning}` : "");
    info.classList.toggle("warn", !!d.warning);
  });
}

function readThemeForm(root) {
  const t = {};
  root.querySelectorAll("[name]").forEach((el) => { if (el.name !== "slug") t[el.name] = el.value.trim(); });
  return t;
}

function renderTheme() {
  const el = $("#panel-theme");
  if (!S.snap) {
    el.innerHTML = `<header><h1>新しい企画</h1></header>
      <label class="f" style="max-width:320px">企画フォルダの名前（英数字・-・_）<input type="text" name="slug" id="slugIn" placeholder="audit-saas"></label>
      <h2>テーマ</h2>${themeForm()}
      <div class="row"><button class="act" id="createBtn" type="button">企画を作って、提案へ進む</button></div>`;
    bindThemeForm(el);
    $("#createBtn").onclick = () => guard(async () => {
      const slug = $("#slugIn").value.trim();
      const d = await api("POST", "/api/works", { slug, theme: readThemeForm(el) });
      S.slug = d.slug; S.view = "propose"; S.pick = new Set(); localStore("slug", S.slug);
      await loadWorks(S.slug); await refresh();
    });
    return;
  }
  const df = S.snap.derived_from;
  const kids = S.snap.derived || [];
  el.innerHTML = `<header><h1>テーマ</h1><span class="muted">版 ${S.snap.theme_version}</span></header>
    ${df ? `<p class="muted">《<a href="#" data-goto="${h(df.slug)}">${h(df.slug)}</a>》のテーマ（版${df.theme_version}）から派生。変えた欄：${h((df.changed || []).join("・") || "なし")}</p>` : ""}
    ${themeForm(S.snap.theme)}
    <section class="derive">
      <h2>テーマを直して、提案を頼み直す</h2>
      <p class="muted">上のテーマを直してから押してください。<b>新しい企画フォルダ</b>で提案から始めます。いまの企画（${h(S.slug)}）の素案・分類・原案・たたきは一切変わりません。</p>
      <div class="row"><label class="f" style="max-width:320px">新しい企画フォルダの名前（必須・英数字・-・_）<input type="text" id="newSlug" value="${h(S.snap.suggest_slug || "")}" autocomplete="off"></label></div>
      <div class="row"><button class="act" id="deriveBtn" type="button">直したテーマで、新しい企画として提案へ</button></div>
      ${kids.length ? `<p class="muted">この企画から派生した企画：${kids.map((k) => `<a href="#" data-goto="${h(k.slug)}">${h(k.slug)}</a>`).join("、")}</p>` : ""}
    </section>
    <details class="retheme"><summary>この企画フォルダのまま、テーマを変えて最初からやり直す</summary>
      <div class="row"><button class="ghost" id="retheme" type="button">テーマを変えて、最初からやり直す</button>
      <span class="muted">いまの素案・分類・原案・たたきは企画フォルダの studio/history/ に退避されます。</span></div>
    </details>`;
  el.querySelectorAll("[data-goto]").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); gotoWork(a.dataset.goto); });
  bindThemeForm(el);
  $("#deriveBtn").onclick = () => guard(async () => {
    const ns = $("#newSlug").value.trim();
    if (!ns) { toast("新しい企画フォルダの名前を入れてください"); $("#newSlug").focus(); return; }
    if (ns === S.slug) { toast("いまの企画とは別の名前にしてください"); $("#newSlug").focus(); return; }
    const d = await api("POST", `/api/works/${S.slug}/derive`, { new_slug: ns, theme: readThemeForm(el.querySelector(".theme-form")) });
    S.slug = d.slug; S.view = "propose"; S.draftV = null; S.deckV = null; S.pick = new Set(); localStore("slug", S.slug);
    await loadWorks(S.slug); await refresh(); toast(`新しい企画 ${d.slug} を作りました。提案を頼んでください。`);
  });
  $("#retheme").onclick = () => guard(async () => {
    if (!confirm("テーマを変えると、この企画は提案からやり直しになります。よろしいですか。")) return;
    await api("POST", `/api/works/${S.slug}/theme`, { theme: readThemeForm(el.querySelector(".theme-form")) });
    S.view = "propose"; await refresh(); toast("テーマを変えました。提案からやり直します。");
  });
}

async function gotoWork(slug) {
  S.slug = slug; loadPick(); S.view = null; S.draftV = null; S.deckV = null; localStore("slug", slug);
  $("#workSel").value = slug; await refresh();
}

/* ---------------------------------------------------------------- 二 提案（#8） */
function sheet(p, opts = {}) {
  const c = S.snap.classify && S.snap.classify.labels && S.snap.classify.labels[p.id];
  const sel = S.snap.selected === p.id;
  const origin = p.origin === "human" ? `<span class="tag human">人が編集（${h(p.parent)}から）</span>` :
    p.origin === "revise" ? `<span class="tag human">改稿（${h(p.parent)}から）</span>` : "";
  const over = (p.over || []).length ? `<span class="tag over" title="${h(p.over.join("、"))}">字数超過</span>` : "";
  const dl = ["resources", "market", "judgment", "sources"].map((f) => p[f] ? `<dt>${FIELDS_JA[f]}<span class="n">${len(p[f])}字</span></dt><dd>${h(p[f])}</dd>` : "").join("") +
    (p.changes ? `<dt>改稿で変えたこと</dt><dd>${h(p.changes)}</dd>` : "") + (p.instruction ? `<dt>改稿の指示</dt><dd>${h(p.instruction)}</dd>` : "");
  return `<article class="sheet${sel ? " selected" : ""}${p.hidden ? " hidden-p" : ""}" id="sheet-${p.id}" data-pid="${p.id}">
    ${c ? `<div class="stamp k-${h(c.label)}" title="${c.nearest ? `いちばん近い案 ${h(c.nearest)}（${c.sim}）` : ""}">${h(c.label).replace("バリエーション", "バリエー<br>ション")}</div>` : ""}
    <div class="meta">${opts.noActs ? "" : `<label class="pick" title="分析・ファイル出力の対象にする"><input type="checkbox" data-pick="${p.id}" ${S.pick.has(p.id) ? "checked" : ""}></label>`}<b>${p.id}</b><span>${h(short(p.model))}</span><span>${usd(p.usd)}</span><span>${h(p.kind)}</span><span>${h(p.genre)}</span>${origin}${over}${sel ? "<b>選択中</b>" : ""}</div>
    <h3>${h(p.title)}</h3>
    ${p.verdict ? `<p class="verdict">${h(p.verdict)}</p>` : ""}
    <p class="syn">${h(p.summary)}</p>
    ${p.fit ? `<p class="fit"><b>要件への適合</b>${h(p.fit)}</p>` : ""}
    <dl>${dl}</dl>
    ${opts.noActs ? "" : `<div class="acts">
      <button class="ghost small" data-a="open" type="button">全文</button>
      <button class="ghost small" data-a="edit" type="button">自分で直す</button>
      <button class="ghost small" data-a="revise" type="button">指示して改稿</button>
      <button class="act small" data-a="select" type="button">この案で原案へ</button>
      <button class="ghost small" data-a="hide" type="button">${p.hidden ? "戻す" : "外す"}</button>
    </div>`}
  </article>`;
}

function bindSheets(root) {
  root.querySelectorAll(".sheet .acts button").forEach((b) => {
    const pid = b.closest(".sheet").dataset.pid;
    b.onclick = () => guard(() => sheetAction(b.dataset.a, pid, b));
  });
  root.querySelectorAll("input[data-pick]").forEach((cb) => (cb.onchange = () => {
    if (cb.checked) S.pick.add(cb.dataset.pick); else S.pick.delete(cb.dataset.pick);
    syncPick();
  }));
}

/* ---------------------------------------------------------------- 選んだ案：分析する・ファイル出力する（画面の最下部） */
function savePick() { localStore("pick." + S.slug, JSON.stringify([...S.pick])); }

function loadPick() {
  let ids = [];
  try { ids = JSON.parse(localStore("pick." + S.slug) || "[]"); } catch (e) { ids = []; }
  S.pick = new Set(ids);
}

function syncPick() {  // 同じ案が二か所に描かれていても、印とバーの数をそろえる
  for (const id of [...S.pick]) if (!S.snap || !S.snap.proposals[id]) S.pick.delete(id);
  document.querySelectorAll("input[data-pick]").forEach((cb) => (cb.checked = S.pick.has(cb.dataset.pick)));
  document.querySelectorAll(".sheet").forEach((el) => el.classList.toggle("picked", S.pick.has(el.dataset.pid)));
  savePick();
  const n = S.pick.size, bar = $("#actionBar");
  if (!bar) return;
  $("#abN").textContent = n;
  const batch = (S.settings.skeleton_batch || 3);
  const c = S.snap.classify, sk = Object.assign({}, S.snap.skeletons || {}, (c && c.skeletons) || {});
  const need = [...S.pick].filter((id) => !sk[id]).length;
  $("#abAnalyze").disabled = n < 2;
  $("#abExport").disabled = n < 1;
  $("#abHint").textContent = n < 2 ? "分析は2案から、ファイル出力は1案から選べます。" :
    need ? `骨格の抜き出し：${need}案を ${batch}案ずつ ${Math.ceil(need / batch)} 回に分けて頼みます（取れた分から保存）。` : "骨格はすべて抜き出し済みです（モデルを呼ばずに分類します）。";
}

function actionBar() {
  const c = S.snap.classify;
  return `<div class="actionbar" id="actionBar">
    <div class="ab-sel"><span><b id="abN">0</b> 案を選択中</span>
      <button class="ghost small" data-sel="all" type="button">全部</button>
      <button class="ghost small" data-sel="last" type="button">最新ラウンド</button>
      ${c ? `<button class="ghost small" data-sel="外れ値" type="button">外れ値</button><button class="ghost small" data-sel="バリエーション" type="button">バリエーション</button>` : ""}
      <button class="ghost small" data-sel="none" type="button">外す</button></div>
    <div class="ab-acts">
      <button class="act" id="abAnalyze" type="button">分析する</button>
      <span class="ab-out"><select id="abFmt" aria-label="ファイルの形式"><option value="md">Markdown（読む）</option><option value="csv">CSV（Excel で並べる）</option><option value="json">JSON</option></select>
      <button class="act" id="abExport" type="button">ファイル出力する</button></span>
    </div>
    <p class="muted" id="abHint"></p>
    ${busyLine(["分析", "classify"])}
  </div>`;
}

function bindActionBar() {
  const snap = S.snap;
  document.querySelectorAll("#actionBar [data-sel]").forEach((b) => (b.onclick = () => {
    const k = b.dataset.sel, labels = (snap.classify && snap.classify.labels) || {};
    const visible = Object.values(snap.proposals).filter((p) => !p.hidden).map((p) => p.id);
    if (k === "none") S.pick.clear();
    else if (k === "all") visible.forEach((id) => S.pick.add(id));
    else if (k === "last") { S.pick.clear(); const r = snap.rounds[snap.rounds.length - 1]; (r ? r.proposals : []).forEach((id) => S.pick.add(id)); }
    else { S.pick.clear(); visible.filter((id) => labels[id] && labels[id].label === k).forEach((id) => S.pick.add(id)); }
    syncPick();
  }));
  $("#abExport").onclick = () => {
    const a = document.createElement("a");
    a.href = `/api/works/${S.slug}/proposals_export?fmt=${$("#abFmt").value}&ids=${encodeURIComponent([...S.pick].join(","))}`;
    a.download = ""; document.body.appendChild(a); a.click(); a.remove();
  };
  $("#abAnalyze").onclick = () => guard(async () => {
    const ids = [...S.pick];
    const body = { ids, method: $("#cM") ? $("#cM").value : "tool", model: $("#cModel") ? $("#cModel").value : undefined,
      refresh: $("#cRefresh") ? $("#cRefresh").checked : false };
    const d = await api("POST", `/api/works/${S.slug}/classify`, body);
    S.view = "classify";
    const r = await waitJob(d.job, `分析（${ids.length}案）`);
    toast(`分析しました（${r.ids.length}案）` + (r.missing && r.missing.length ? `。骨格が取れなかった案：${r.missing.join("・")}` : ""), !!(r.missing && r.missing.length));
  });
  syncPick();
}

async function sheetAction(a, pid, btn) {
  const p = S.snap.proposals[pid];
  const lim = S.snap.limits || {};
  if (a === "open") { btn.closest(".sheet").classList.toggle("open"); return; }
  if (a === "select") { await api("POST", `/api/works/${S.slug}/select`, { pid }); S.view = "draft"; await refresh(); return; }
  if (a === "hide") { await api("POST", `/api/works/${S.slug}/hide`, { pid, hidden: !p.hidden }); await refresh(); return; }
  if (a === "edit") {
    const f = await dialog(`<h2>${pid} を自分で直す</h2><p class="muted">元の案は残り、直した版が新しい案として加わります。</p>
      ${Object.keys(FIELDS_JA).map((k) => {
        const mx = lim[k] ? `data-max="${lim[k]}"` : "";
        return ["kind", "genre", "title", "verdict"].includes(k)
          ? `<label class="f">${FIELDS_JA[k]}<input type="text" name="${k}" value="${h(p[k])}" ${mx}></label>`
          : `<label class="f">${FIELDS_JA[k]}<textarea name="${k}" rows="${k === "summary" ? 9 : 4}" ${mx}>${h(p[k])}</textarea></label>`;
      }).join("")}`, "新しい案として保存", true);
    if (!f) return;
    const d = await api("POST", `/api/works/${S.slug}/edit`, { pid, fields: f });
    toast(`${d.pid} として保存しました`); await refresh(); return;
  }
  if (a === "revise") {
    const f = await dialog(`<h2>${pid} を改稿してもらう</h2>
      <label class="f">モデル<select name="model">${modelOptions(p.model.replace(/:online$/, ""))}</select></label>
      <label class="f">指示<textarea name="instruction" rows="5" placeholder="例：顧客を従業員50名以下に絞る。価格は月額1万円台で成り立つか検討して"></textarea></label>`, "改稿を頼む");
    if (!f || !f.instruction.trim()) return;
    const d = await api("POST", `/api/works/${S.slug}/revise`, { pid, model: f.model, instruction: f.instruction });
    const r = await waitJob(d.job, `${pid} の改稿`);
    toast(`${r} として届きました`);
  }
}

function renderPropose() {
  const snap = S.snap, el = $("#panel-propose");
  const def = S.settings.propose_models || [];
  const lastModels = snap.rounds.length ? snap.rounds[snap.rounds.length - 1].models : def;
  const rounds = [...snap.rounds].reverse();
  const roundCost = (r) => r.proposals.reduce((a, pid) => a + (snap.proposals[pid] ? snap.proposals[pid].usd : 0), 0);
  const extra = Object.values(snap.proposals).filter((p) => p.origin !== "model");
  el.innerHTML = `<header><h1>提案</h1><span class="muted">素案 ${Object.keys(snap.proposals).length} 件　ラウンド ${snap.rounds.length}</span></header>
    <p class="muted">返ってくるのは箇条書きの企画の素案です：企画タイトル（50字）・判断の一言（30字）・概要（1000字）・必要なリソース（500字）・マーケット（500字）・判断（500字）・出典。上限を超えたら一度だけ詰め直しを頼みます。</p>
    <div class="cols">
      <div>
        <label class="f">モデルを探す<input type="text" id="mFilter" placeholder="haiku, luna, deepseek…"></label>
        <div class="models" id="mList"></div>
        <p class="muted">単価は 100万トークンあたりのドル（入力／出力）。</p>
      </div>
      <div>
        <div class="form">
          <label class="f">1モデルあたりの案の数<select id="pN"><option>1</option><option selected>2</option><option>3</option><option>4</option></select></label>
          <label class="f">発散の度合い（temperature）<input type="number" id="pT" min="0" max="1.5" step="0.1" value="0.8"></label>
          <label class="f wide check"><span><input type="checkbox" id="pW" ${snap.web_ok ? "" : "disabled"}> Web 検索で競合・市場を調べさせる</span>
            <span class="muted">${snap.web_ok ? "OpenRouter の :online を使います（1回あたり数セント上乗せ）。出典の URL が sources に入ります。" : "いまの窓口（OpenRouter 以外）では使えません。数字は「推定」と筋道つきで書かせます。"}</span></label>
          <label class="f wide">追加の指示（任意。再提出のときの注文もここに）<textarea id="pI" placeholder="例：初期費用ゼロで始められる案を必ず1つ／海外の先行事例を持ち込む案"></textarea></label>
        </div>
        <div class="row">
          <button class="act" id="pGo" type="button">${snap.rounds.length ? "もう一度出してもらう" : "提案を出してもらう"}</button>
          <button class="ghost" id="pRoll" type="button" ${snap.avoid.length ? "" : "disabled"} title="分類で「収束」になった骨格を渡し、その外の案だけを求めます">収束した骨格を避けて出してもらう</button>
          <button class="ghost" id="pTheme" type="button">テーマを変える</button>
        </div>
        ${snap.avoid.length ? `<details><summary class="muted">避ける骨格（${snap.avoid.length}）</summary><ul>${snap.avoid.map((x) => `<li>${h(x)}</li>`).join("")}</ul></details>` : ""}
        ${busyLine(["提案", "改稿", "propose", "revise"])}
      </div>
    </div>
    ${rounds.map((r) => `<h2>${r.id}　${r.mode === "rolling" ? "収束を避けて" : r.mode === "resubmit" ? "再提出" : "最初の提案"}<span class="muted">　${r.models.length}モデル・${usd(roundCost(r))}${r.web ? "・Web検索" : ""}${r.instruction ? `・指示「${h(r.instruction)}」` : ""}</span></h2>
      ${Object.keys(r.errors || {}).length ? `<p class="notice">返ってこなかったモデル：${Object.entries(r.errors).map(([m, e]) => `${h(short(m))}（${h(e).slice(0, 160)}）`).join("、")}</p>` : ""}
      <div class="sheets">${r.proposals.map((pid) => snap.proposals[pid]).filter(Boolean).map((p) => sheet(p)).join("")}</div>`).join("")}
    ${extra.length ? `<h2>直した案・改稿した案</h2><div class="sheets">${extra.map((p) => sheet(p)).join("")}</div>` : ""}
    ${snap.rounds.length ? `<div class="row"><button class="act" id="toClassify" type="button">分類の画面へ</button></div>` : ""}
    ${Object.keys(snap.proposals).length ? actionBar() : ""}`;
  bindSheets(el);
  if ($("#toClassify")) $("#toClassify").onclick = () => { S.view = "classify"; render(); };
  if ($("#actionBar")) bindActionBar();
  const drawModels = () => {
    const f = $("#mFilter").value.trim();
    const checked = new Set([...el.querySelectorAll("#mList input:checked")].map((x) => x.value));
    const base = checked.size ? checked : new Set(lastModels);
    const list = (S.models.length ? S.models : def.map((id) => ({ id }))).filter((m) => base.has(m.id) || !f || m.id.includes(f)).slice(0, 120);
    $("#mList").innerHTML = list.map((m) => `<label><input type="checkbox" value="${h(m.id)}" ${base.has(m.id) ? "checked" : ""}><span>${h(m.id)}</span><span class="price">${m.in_per_m != null ? `${m.in_per_m}／${m.out_per_m}` : ""}</span></label>`).join("");
  };
  drawModels();
  $("#mFilter").oninput = drawModels;
  const go = (mode) => guard(async () => {
    const models = [...el.querySelectorAll("#mList input:checked")].map((x) => x.value);
    if (!models.length) throw new Error("モデルを一つ以上選んでください");
    const d = await api("POST", `/api/works/${S.slug}/propose`, { models, n: Number($("#pN").value), temperature: Number($("#pT").value),
      instruction: $("#pI").value, mode, web: $("#pW").checked });
    const r = await waitJob(d.job, `提案（${models.length}モデル）`);
    toast(`${r.proposals.length} 案が届きました` + (Object.keys(r.errors).length ? `（${Object.keys(r.errors).length} モデルは失敗）` : ""));
  });
  $("#pGo").onclick = () => go(snap.rounds.length ? "resubmit" : "new");
  $("#pRoll").onclick = () => go("rolling");
  $("#pTheme").onclick = () => { S.view = "theme"; render(); };
}

/* ---------------------------------------------------------------- 三 分類・選ぶ */
function renderClassify() {
  const snap = S.snap, el = $("#panel-classify"), c = snap.classify;
  const props = snap.proposals;
  const ai = c && c.ai;
  el.innerHTML = `<header><h1>分類・選ぶ</h1><span class="muted">収束・バリエーション・外れ値 → 原案に進める案を人が選ぶ</span></header>
    <p>モデルに各案の<b>事業の骨格</b>（誰の・何の課題を・どう解き・どこで儲け・何で勝つか・どんな市場か）だけを抜き出させ、似ているかどうかは道具が数えます。分類は良し悪しではなく「型への寄り方」です。外れ値を拾うかどうかは人が決めます。</p>
    <div class="form">
      <label class="f">やり方<select id="cM">${[["tool", "骨格を数える（既定）"], ["both", "骨格を数える＋AIにも骨格を読ませる"], ["ai", "AIに骨格を読ませるだけ"]].map(([k, v]) => `<option value="${k}" ${k === (S.cMethod || "tool") ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="f">骨格を抜き出すモデル${modelSelect("cModel", S.cModel || (c && c.model) || S.settings.skeleton_model)}</label>
      <label class="f check"><span><input type="checkbox" id="cRefresh"> 選んだ案の骨格を抜き出し直す</span></label>
    </div>
    <p class="muted">案を選んで、画面のいちばん下の「分析する」を押してください。骨格は ${S.settings.skeleton_batch || 3} 案ずつに分けて頼み、取れた分から保存します（途中で落ちても、もう一度押せば残りだけを頼みます）。AI に読ませるときも、本文ではなく骨格の一覧だけを渡します。</p>
    ${c ? `<p class="muted">いまの分類：${(c.ids || []).length}案（${h((c.ids || []).join("・"))}）　${h(c.ts || "")}</p>` : ""}
    ${c && c.missing && c.missing.length ? `<div class="notice">骨格を抜き出せなかった案：${h(c.missing.join("・"))}（分類から外しました）。もう一度「分析する」を押すと、その案だけを頼み直します。
      ${Object.entries(c.errors || {}).map(([k, v]) => `<br><span class="muted">${h(k)}：${h(v).slice(0, 200)}</span>`).join("")}</div>` : ""}
    ${c && c.ai_error ? `<div class="notice">AI の読みは返ってきませんでした（骨格による分類は済んでいます）：${h(c.ai_error).slice(0, 240)}</div>` : ""}
    ${c && c.clusters && c.clusters.length ? `
      <div class="slider"><span class="muted">似ているとみなす境目</span><input type="range" id="cTh" min="0.2" max="0.9" step="0.05" value="${c.threshold}"><b id="cThV">${c.threshold}</b>
      <span class="muted">案どうしの近さの平均 ${c.mean_similarity ?? "—"}</span></div>
      <h2>まとまり</h2>
      <div class="clusters">${c.clusters.map((cl) => `<div class="cluster k-${h(cl.label)}">
        <div><b>${h(cl.label)}</b>　<span class="muted">${cl.members.length}案${cl.cohesion != null ? `・まとまりの強さ ${cl.cohesion}` : ""}</span></div>
        ${cl.type ? `<div>型：${h(cl.type)}</div>` : ""}
        <div class="members">${cl.members.map((m) => `<span class="chip" data-pid="${m}" title="${h(props[m] ? props[m].title : "")}">${m} ${h(props[m] ? props[m].title : "").slice(0, 16)}</span>`).join("")}</div>
      </div>`).join("")}</div>
      <h2>いちばん近い組</h2>
      <table><tr><th>案</th><th>案</th><th class="num">近さ</th></tr>${(c.top_pairs || []).map((t) => `<tr><td>${t.a} ${h(props[t.a] ? props[t.a].title : "")}</td><td>${t.b} ${h(props[t.b] ? props[t.b].title : "")}</td><td class="num">${t.sim}</td></tr>`).join("")}</table>` : ""}
    ${ai ? `<h2>AI の読み</h2>${(ai.types || []).length ? `<p>目についた型：${ai.types.map(h).join("／")}</p>` : ""}
      <table><tr><th>案</th><th>道具の分類</th><th>AI の分類</th><th>AI の理由</th></tr>
      ${Object.entries(ai.labels).map(([pid, v]) => `<tr><td>${pid} ${h(props[pid] ? props[pid].title : "")}</td><td>${h(c.labels && c.labels[pid] ? c.labels[pid].label : "—")}</td><td>${h(v.label)}</td><td>${h(v.reason)}</td></tr>`).join("")}</table>` : ""}
    ${c && c.skeletons && Object.keys(c.skeletons).length ? `<details><summary><h2 style="display:inline">骨格の一覧</h2></summary>
      <table><tr><th>案</th>${Object.values(SK_JA).map((x) => `<th>${x}</th>`).join("")}</tr>
      ${Object.entries(c.skeletons).map(([pid, s]) => `<tr><td>${pid}</td>${Object.keys(SK_JA).map((k) => `<td>${h(s[k])}</td>`).join("")}</tr>`).join("")}</table></details>` : ""}
    <h2>${c ? "分類つきの案" : "案"}</h2><p class="muted">「この案で原案へ」で原案に進める案を選びます。選び直しはいつでもできます（作った原案は、どの案から作ったかの記録つきで残ります）。左上の印は、分析・ファイル出力の対象です。</p>
      <div class="sheets">${Object.values(props).filter((p) => !p.hidden).map((p) => sheet(p)).join("")}</div>
    ${c ? `<div class="row"><button class="ghost" id="cRoll" type="button" ${snap.avoid.length ? "" : "disabled"}>収束した骨格を避けて、もう一度出してもらう</button></div>` : ""}
    ${Object.keys(props).length ? actionBar() : ""}`;
  bindSheets(el);
  el.querySelectorAll(".chip").forEach((ch) => (ch.onclick = () => { const s = $(`#sheet-${ch.dataset.pid}`, el); if (s) { s.classList.add("open"); s.scrollIntoView({ behavior: "smooth", block: "center" }); } }));
  $("#cM").onchange = () => (S.cMethod = $("#cM").value);
  $("#cModel").onchange = () => (S.cModel = $("#cModel").value);
  if ($("#actionBar")) bindActionBar();
  if ($("#cTh")) {
    $("#cTh").oninput = () => ($("#cThV").textContent = $("#cTh").value);
    $("#cTh").onchange = () => guard(async () => { await api("POST", `/api/works/${S.slug}/reclassify`, { threshold: Number($("#cTh").value) }); await refresh(); });
  }
  if ($("#cRoll")) $("#cRoll").onclick = () => { S.view = "propose"; render(); setTimeout(() => $("#pRoll") && $("#pRoll").focus(), 50); };
}

/* ---------------------------------------------------------------- 読みもの（Markdown の小さな描き手） */
function md(src) {
  src = String(src || "").replace(/<!--[\s\S]*?-->/g, "");
  const inline = (s) => h(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const out = [], lines = src.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*$/.test(l)) continue;
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if (/^---+\s*$/.test(l)) { out.push("<hr>"); continue; }
    if (l.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) { rows.push(lines[i]); i++; }
      i--;
      const cells = (r) => r.replace(/^\||\|\s*$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\|[\s|:-]+\|?\s*$/.test(r));
      out.push("<table>" + body.map((r, k) => "<tr>" + cells(r).map((c) => (k === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`)).join("") + "</tr>").join("") + "</table>");
      continue;
    }
    const LI = /^\s*([-*]\s|・|\d+[.)]\s|\d+[．）])/;  // 「3.5億円」は箇条にしない
    if (LI.test(l)) {
      const items = [];
      while (i < lines.length && LI.test(lines[i])) { items.push(lines[i].replace(LI, "")); i++; }
      i--;
      out.push("<ul>" + items.map((x) => `<li>${inline(x)}</li>`).join("") + "</ul>");
      continue;
    }
    if (l.startsWith(">")) { out.push(`<blockquote>${inline(l.replace(/^>\s?/, ""))}</blockquote>`); continue; }
    out.push(`<p>${inline(l)}</p>`);
  }
  return out.join("\n");
}

function lintBox(lint, okText) {
  return (lint.errors.length ? `<div class="alarm"><h3>機械検査の指摘</h3><ul>${lint.errors.map((e) => `<li>${h(e)}</li>`).join("")}</ul><p class="muted">指示欄を空のまま「作り直す」と、指摘を直すよう頼めます。</p></div>` :
    `<div class="ok-box lint-ok">${h(okText)}</div>`) + (lint.warnings.length ? `<div class="notice">${lint.warnings.map(h).join("<br>")}</div>` : "");
}

function versionList(items, cur, label) {
  return `<ul class="versions">${items.slice().reverse().map((b) => `<li class="${b.v === cur ? "cur" : ""}"><button type="button" data-v="${b.v}">
    v${b.v}　${b.origin === "human" ? "人が編集" : h(short(b.model))}${b.base ? `（v${b.base}から）` : ""}${label ? label(b) : ""}<br>
    <span class="muted">${usd(b.usd)}　${b.lint.ok ? `<span class="lint-ok">検査OK</span>` : `<span class="lint-ng">指摘${b.lint.errors.length}</span>`}${b.lint.warnings.length ? `・注意${b.lint.warnings.length}` : ""}</span></button></li>`).join("")}</ul>`;
}

/* ---------------------------------------------------------------- 四 企画原案（#9） */
function draftMd(d) {
  const secs = S.snap.sections;
  let s = `# ${d.title || "（無題）"}\n` + (d.catch ? `\n> ${d.catch}\n` : "");
  for (const x of secs) s += `\n## ${x.label}\n\n${d[x.key] || ""}\n`;
  if ((d.reqmap || []).length) s += `\n## 要件への対応（要件①の求める事項ごと）\n\n` + d.reqmap.map((c) => `- ${c}`).join("\n") + "\n";
  if ((d.checks || []).length) s += `\n## 要確認事項（推定・仮定・未調査）\n\n` + d.checks.map((c) => `- ${c}`).join("\n") + "\n";
  return s;
}

function renderDraft() {
  const snap = S.snap, el = $("#panel-draft");
  const sel = snap.selected && snap.proposals[snap.selected];
  const drafts = snap.drafts || [];
  if (!S.draftV || !drafts.find((b) => b.v === S.draftV)) S.draftV = drafts.length ? drafts[drafts.length - 1].v : null;
  const cur = drafts.find((b) => b.v === S.draftV);
  const labels = (snap.classify && snap.classify.labels) || {};
  const others = Object.values(snap.proposals).filter((p) => !p.hidden && sel && p.id !== sel.id);
  el.innerHTML = `<header><h1>企画原案</h1><span class="muted">企画書のベースになる文書。${snap.sections.map((x) => `${h(x.label)}（${x.target}字）`).join("・")}・要確認事項</span></header>
    ${!sel ? `<p class="notice">提案か分類の画面で「この案で原案へ」を押してください。</p>` : `
    <h2>選んだ案</h2><div class="sheets" style="grid-template-columns:1fr">${sheet(sel, { noActs: true })}</div>
    <div class="form">
      <label class="f">原案を書くモデル${modelSelect("dModel", S.settings.draft_model)}</label>
      <label class="f wide">ほかの案から取り込む（任意）<span class="muted">外れ値やバリエーションの良いところだけを、選んだ案の芯を崩さずに取り込ませます。</span>
        <div class="borrow">${others.map((p) => `<label class="chip${labels[p.id] && labels[p.id].label === "外れ値" ? " out" : ""}"><input type="checkbox" value="${p.id}"> ${p.id} ${h(p.title).slice(0, 20)}${labels[p.id] ? `（${h(labels[p.id].label)}）` : ""}</label>`).join("") || `<span class="muted">ほかの案はありません</span>`}</div></label>
      <label class="f wide">追加の指示（任意）<textarea id="dI" placeholder="例：初年度は既存顧客30社への提供に絞る／価格は月額3万円を上限に"></textarea></label>
    </div>
    <div class="row"><button class="act" id="dGo" type="button">この案から原案を作る</button></div>
    ${busyLine(["原案", "draft"])}`}
    ${drafts.length ? `<div class="cols" style="margin-top:20px">
      <div><h3>版</h3>${versionList(drafts, S.draftV, (b) => `<br><span class="muted">案 ${h(b.from)}${(b.borrow || []).length ? "＋" + b.borrow.join("・") : ""}</span>`)}</div>
      <div id="dView"></div></div>` : ""}`;
  if ($("#dGo")) $("#dGo").onclick = () => guard(async () => {
    const borrow = [...el.querySelectorAll(".borrow input:checked")].map((x) => x.value);
    const d = await api("POST", `/api/works/${S.slug}/draft`, { model: $("#dModel").value, instruction: $("#dI").value, borrow });
    S.draftTab = "read";
    S.draftV = await waitJob(d.job, "原案の作成"); toast(`原案 v${S.draftV} ができました`); render();
  });
  el.querySelectorAll(".versions button").forEach((b) => (b.onclick = () => { S.draftV = Number(b.dataset.v); S.draftTab = "read"; renderDraft(); }));
  if (!cur) return;
  const view = $("#dView"), d = cur.draft, ch = cur.lint.chars || {};
  view.innerHTML = `<div class="row tabs"><button class="ghost small${S.draftTab === "read" ? " on" : ""}" data-t="read" type="button">読む</button>
      <button class="ghost small${S.draftTab === "edit" ? " on" : ""}" data-t="edit" type="button">自分で直す</button>
      <a class="ghost small btn" download href="/api/works/${S.slug}/export?kind=draft_md&v=${cur.v}">Markdown をダウンロード</a>
      <span class="muted">v${cur.v}　${h(cur.ts)}　案 ${h(cur.from)}${cur.instruction ? `　指示「${h(cur.instruction)}」` : ""}${cur.changes ? `　変更：${h(cur.changes)}` : ""}</span></div>
    ${lintBox(cur.lint, "機械検査OK（6つの節がそろっています）")}
    <p class="muted">${snap.sections.map((x) => `${h(x.label)} ${(ch[x.key] || 0).toLocaleString()}／${x.target}字`).join("　")}</p>
    <div id="dBody"></div>
    <div class="form" style="margin-top:14px"><label class="f wide">この版に指示を足して作り直す<textarea id="dR" placeholder="例：リスクに個人情報の扱いを足す／概要設計の MVP を3か月で出せる範囲に"></textarea></label></div>
    <div class="row"><button class="ghost" id="dRe" type="button">指示を足して作り直す</button>${cur.lint.ok ? `<button class="act" id="toDeck" type="button">この版で企画書たたきへ</button>` : ""}</div>`;
  view.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { S.draftTab = b.dataset.t; renderDraft(); }));
  $("#dRe").onclick = () => guard(async () => {
    const r = await api("POST", `/api/works/${S.slug}/draft`, { model: $("#dModel") ? $("#dModel").value : undefined, instruction: $("#dR").value, base: cur.v });
    S.draftTab = "read";
    S.draftV = await waitJob(r.job, "原案の作り直し"); toast(`原案 v${S.draftV} ができました`); render();
  });
  if ($("#toDeck")) $("#toDeck").onclick = () => { S.deckFrom = cur.v; S.view = "deck"; render(); };
  if (S.draftTab === "edit") {
    $("#dBody").innerHTML = `<div class="form one">
      <label class="f">企画名<input type="text" name="title" value="${h(d.title)}"></label>
      <label class="f">キャッチ<input type="text" name="catch" value="${h(d.catch)}"></label>
      ${snap.sections.map((x) => `<label class="f">${h(x.label)}<textarea name="${x.key}" rows="${Math.max(6, Math.round(x.target / 120))}" data-max="${x.target}" data-soft="1">${h(d[x.key])}</textarea></label>`).join("")}
      ${snap.theme && snap.theme.rfi ? `<label class="f">要件への対応（要件①の求める事項ごとに1行。どの節で・どう応えるか）<textarea name="reqmap" rows="6">${h((d.reqmap || []).join("\n"))}</textarea></label>` : ""}
      <label class="f">要確認事項（1行に1つ）<textarea name="checks" rows="5">${h((d.checks || []).join("\n"))}</textarea></label></div>
      <div class="row"><button class="act" id="dSave" type="button">新しい版として保存</button><span class="muted">保存すると機械検査が走ります。</span></div>`;
    $("#dBody").querySelectorAll("textarea[data-max]").forEach((ta) => counter(ta));
    $("#dSave").onclick = () => guard(async () => {
      const fields = {};
      $("#dBody").querySelectorAll("[name]").forEach((x) => (fields[x.name] = x.value));
      const r = await api("POST", `/api/works/${S.slug}/draft_edit`, { v: cur.v, fields });
      S.draftV = r.v; S.draftTab = "read"; toast(`v${r.v} として保存しました`); await refresh();
    });
    return;
  }
  $("#dBody").innerHTML = `<div class="doc">${md(draftMd(d))}</div>`;
}

/* ---------------------------------------------------------------- 五 企画書たたき（#10） */
function renderDeck() {
  const snap = S.snap, el = $("#panel-deck");
  const okDrafts = (snap.drafts || []).filter((d) => d.lint.ok);
  const decks = snap.decks || [];
  if (!S.deckV || !decks.find((b) => b.v === S.deckV)) S.deckV = decks.length ? decks[decks.length - 1].v : null;
  const cur = decks.find((b) => b.v === S.deckV);
  const fromV = S.deckFrom || (okDrafts.length ? okDrafts[okDrafts.length - 1].v : null);
  const today = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10).replace(/-/g, ".");
  const last = Object.assign({ audience: "社内の経営会議", purpose: "approve", slides: 12, minutes: 15, company: "", date: today, presenter: "" }, cur ? cur.opts : {});
  el.innerHTML = `<header><h1>企画書たたき</h1><span class="muted">原案をスライドの構成案にして、PowerPoint の Copilot・Claude・ChatGPT・Gemini に渡す材料を作ります</span></header>
    ${!okDrafts.length ? `<p class="notice">機械検査を通った原案がまだありません。</p>` : `
    <div class="form">
      <label class="f">元にする原案<select id="kD">${okDrafts.slice().reverse().map((d) => `<option value="${d.v}" ${d.v === fromV ? "selected" : ""}>原案 v${d.v}（${h(d.origin === "human" ? "人が編集" : short(d.model))}）</option>`).join("")}</select></label>
      <label class="f">聞き手<input type="text" id="kA" value="${h(last.audience)}" placeholder="例：社内の経営会議／製造業の情報システム部長"></label>
      <label class="f">目的<select id="kP">${Object.entries(snap.purposes).map(([k, v]) => `<option value="${k}" ${k === last.purpose ? "selected" : ""}>${h(v)}</option>`).join("")}</select></label>
      <label class="f">枚数（表紙を含む）<input type="number" id="kS" min="4" max="30" value="${last.slides}"></label>
      <label class="f">発表時間（分）<input type="number" id="kM" min="3" max="120" value="${last.minutes}"></label>
      <label class="f">社名（表紙とフッター。空なら入れない）<input type="text" id="kC" value="${h(last.company)}" placeholder="例：株式会社サンプル／イノベーション推進室"></label>
      <label class="f">発表日（空なら入れない）<input type="text" id="kT" value="${h(last.date)}" placeholder="例：2026.10.30"></label>
      <label class="f">発表者（空なら入れない）<input type="text" id="kW" value="${h(last.presenter)}" placeholder="例：くろだ"></label>
      <label class="f">構成を組むモデル${modelSelect("kModel", S.settings.deck_model)}</label>
      <label class="f wide">追加の指示（任意）<textarea id="kI" placeholder="例：競合比較は表で1枚に／お願いは『PoC 予算 300万円の承認』"></textarea></label>
    </div>
    <div class="row"><button class="act" id="kGo" type="button">たたきを作る</button>
      <span class="muted">中身は原案で決まっているので、並べ替えと要約だけです。原案にない数字が出たら警告します。</span></div>
    ${busyLine(["たたき", "deck"])}`}
    ${decks.length ? `<div class="cols" style="margin-top:20px">
      <div><h3>版</h3>${versionList(decks, S.deckV, (b) => `<br><span class="muted">原案 v${b.draft_v}・${b.deck.slides.length}枚</span>`)}</div>
      <div id="kView"></div></div>` : ""}`;
  if ($("#kGo")) $("#kGo").onclick = () => guard(async () => {
    const opts = { audience: $("#kA").value, purpose: $("#kP").value, slides: Number($("#kS").value), minutes: Number($("#kM").value),
      company: $("#kC").value, date: $("#kT").value, presenter: $("#kW").value };
    const d = await api("POST", `/api/works/${S.slug}/deck`, { draft_v: Number($("#kD").value), opts, model: $("#kModel").value, instruction: $("#kI").value });
    S.deckTab = "slides"; S.deckFrom = null;
    S.deckV = await waitJob(d.job, "たたきの作成"); toast(`たたき v${S.deckV} ができました`); render();
  });
  el.querySelectorAll(".versions button").forEach((b) => (b.onclick = () => { S.deckV = Number(b.dataset.v); renderDeck(); }));
  if (!cur) return;
  const view = $("#kView");
  view.innerHTML = `<div class="row tabs">
      ${[["slides", "スライド"], ["hand", "ツールに渡す"], ["edit", "自分で直す（JSON）"]].map(([k, l]) => `<button class="ghost small${S.deckTab === k ? " on" : ""}" data-t="${k}" type="button">${l}</button>`).join("")}
      <span class="muted">v${cur.v}　原案 v${cur.draft_v}　${h(cur.opts.audience)}・${cur.deck.slides.length}枚・${cur.opts.minutes}分${[cur.opts.company, cur.opts.date, cur.opts.presenter].filter(Boolean).length ? "　" + h([cur.opts.company, cur.opts.date, cur.opts.presenter].filter(Boolean).join("・")) : ""}${cur.changes ? `　変更：${h(cur.changes)}` : ""}</span></div>
    ${lintBox(cur.lint, (cur.lint.new_numbers || []).length || cur.lint.warnings.length ? "機械検査OK（各スライドに結論があります。下の注意を確かめてください）" : "機械検査OK（各スライドに結論があり、原案にない数字も仮の社名・日付もありません）")}
    <div id="kBody"></div>
    <div class="form" style="margin-top:14px"><label class="f wide">この版に指示を足して作り直す<textarea id="kR" placeholder="例：3枚目と4枚目をまとめる／リスクを2枚に分ける"></textarea></label></div>
    <div class="row"><button class="ghost" id="kRe" type="button">指示を足して作り直す</button></div>`;
  view.querySelectorAll("[data-t]").forEach((b) => (b.onclick = () => { S.deckTab = b.dataset.t; renderDeck(); }));
  $("#kRe").onclick = () => guard(async () => {
    const r = await api("POST", `/api/works/${S.slug}/deck`, { model: $("#kModel") ? $("#kModel").value : undefined, instruction: $("#kR").value, base: cur.v });
    S.deckV = await waitJob(r.job, "たたきの作り直し"); toast(`たたき v${S.deckV} ができました`); render();
  });
  const body = $("#kBody");
  if (S.deckTab === "edit") {
    body.innerHTML = `<textarea class="code" id="kJson">${h(JSON.stringify(cur.deck, null, 1))}</textarea>
      <div class="row"><button class="act" id="kSave" type="button">新しい版として保存</button><span class="muted">保存すると機械検査が走ります。</span></div>`;
    $("#kSave").onclick = () => guard(async () => {
      let obj;
      try { obj = JSON.parse($("#kJson").value); } catch (e) { throw new Error("JSON として読めません：" + e.message); }
      const r = await api("POST", `/api/works/${S.slug}/deck_edit`, { v: cur.v, deck: obj });
      S.deckV = r.v; S.deckTab = "slides"; toast(`v${r.v} として保存しました`); await refresh();
    });
    return;
  }
  if (S.deckTab === "hand") return renderHand(body, cur);
  const nn = new Set(cur.lint.new_numbers || []);
  const mark = (s) => h(s).replace(/\d[\d,，.]*/g, (m) => nn.has(m.replace(/[,，]/g, "").replace(/\.$/, "")) ? `<mark title="原案にない数字">${m}</mark>` : m);
  body.innerHTML = `<div class="deckhead"><h2>${h(cur.deck.title)}</h2>${cur.deck.subtitle ? `<p class="muted">${h(cur.deck.subtitle)}</p>` : ""}</div>
    <ol class="slides">${cur.deck.slides.map((s, i) => `<li class="slide">
      <div class="sn">${i + 1}</div>
      <div><h3>${mark(s.title)}</h3><p class="msg">${mark(s.message)}</p>
      ${(s.bullets || []).length ? `<ul>${s.bullets.map((b) => `<li>${mark(b)}</li>`).join("")}</ul>` : ""}
      ${s.visual ? `<p class="vis">図・表：${mark(s.visual)}</p>` : ""}
      ${s.notes ? `<details><summary class="muted">話すこと</summary><p class="notes">${h(s.notes)}</p></details>` : ""}
      ${s.source ? `<span class="tag">原案：${h(s.source)}</span>` : ""}</div></li>`).join("")}</ol>`;
}

async function renderHand(body, cur) {
  body.innerHTML = `<p class="busy">依頼文を用意しています</p>`;
  const d = await api("GET", `/api/works/${S.slug}/prompts?v=${cur.v}`).catch((e) => ({ error: e.message }));
  if (!body.isConnected) return;  // 待っている間に画面が描き直された
  if (d.error) { body.innerHTML = `<p class="notice">${h(d.error)}</p>`; return; }
  const t = S.tool in d.tools ? S.tool : "copilot";
  const base = `/api/works/${S.slug}/export?v=${cur.v}`;
  body.innerHTML = `<div class="seg" role="tablist">${Object.entries(d.tools).map(([k, v]) => `<label><input type="radio" name="tool" value="${k}" ${k === t ? "checked" : ""}><span>${h(v)}</span></label>`).join("")}</div>
    <p class="muted" style="margin-top:10px">${h(TOOL_HOW[t])}</p>
    <div class="row">
      ${t === "copilot" ? `<a class="act btn" download href="${base}&kind=deck_docx">Word（.docx）をダウンロード</a>` : `<a class="act btn" download href="${base}&kind=deck_md&tool=${t}">依頼文つき Markdown をダウンロード</a>`}
      <button class="ghost" id="hCopy" type="button">依頼文をコピー</button>
      ${t !== "copilot" ? `<button class="ghost" id="hCopyAll" type="button">依頼文＋構成案をまとめてコピー</button>` : ""}
    </div>
    <p class="muted">表紙とフッター：${h([["社名", cur.opts.company], ["発表日", cur.opts.date], ["発表者", cur.opts.presenter]].map(([k, v]) => `${k} ${v || "入れない"}`).join("／"))}。仕上がったスライドでは、表紙とフッターの日付・社名と、原案にない数字だけは目で確かめてください（渡した先の AI が足すことがあります）。</p>
    <h3>依頼文</h3><pre class="log prompt">${h(d.prompts[t])}</pre>
    <details><summary class="muted">ほかの形式</summary><div class="row">
      <a class="ghost small btn" download href="${base}&kind=deck_docx">Word（.docx）</a>
      <a class="ghost small btn" download href="${base}&kind=deck_md&tool=${t}">Markdown（依頼文つき）</a>
      <a class="ghost small btn" download href="${base}&kind=deck_marp">Marp（AI を通さずにスライドで見る）</a></div></details>`;
  body.querySelectorAll("input[name=tool]").forEach((r) => (r.onchange = () => { S.tool = r.value; renderHand(body, cur); }));
  $("#hCopy", body).onclick = () => copy(d.prompts[t]);
  if ($("#hCopyAll", body)) $("#hCopyAll", body).onclick = () => guard(async () => {
    const r = await fetch(`${base}&kind=deck_md&tool=${t}&inline=1`);
    await copy(await r.text());
  });
}

/* ---------------------------------------------------------------- 起動 */
document.querySelectorAll("#rail button").forEach((b) => (b.onclick = () => {
  const li = b.closest("li");
  if (li.classList.contains("locked")) return;
  S.view = li.dataset.stage; render();
}));
$("#workSel").onchange = async () => { S.slug = $("#workSel").value; loadPick(); S.view = null; S.draftV = null; S.deckV = null; localStore("slug", S.slug); await refresh(); };
$("#newWorkBtn").onclick = () => { S.slug = ""; S.snap = null; S.view = "theme"; $("#workSel").value = ""; render(); };

function localStore(k, v) {
  try { if (v === undefined) return localStorage.getItem("plan." + k); localStorage.setItem("plan." + k, v); } catch (e) { return null; }
}

(async () => {
  await guard(loadModels);
  await guard(() => loadWorks());
  const last = localStore("slug");
  if (last && [...$("#workSel").options].some((o) => o.value === last)) { S.slug = last; $("#workSel").value = last; loadPick(); }
  await refresh();
})();

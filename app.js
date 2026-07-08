/*
 * 今日の美瑛 — 青い池スコアと霧・雲海チャンス
 * Open-Meteo の予報値のみで完結（サーバー不要・無料）
 */

const LAT = 43.489; // 白金青い池 付近
const LON = 142.621;

const API =
  "https://api.open-meteo.com/v1/forecast" +
  `?latitude=${LAT}&longitude=${LON}` +
  "&daily=sunrise,sunset,temperature_2m_max,temperature_2m_min" +
  "&hourly=cloud_cover,precipitation,wind_speed_10m,relative_humidity_2m,temperature_2m,dew_point_2m" +
  "&past_days=2&forecast_days=2&timezone=Asia%2FTokyo";

// Asia/Tokyo の YYYY-MM-DD（閲覧者のタイムゾーンに依存させない）
function tokyoDateStr(offsetDays = 0) {
  const d = new Date(Date.now() + offsetDays * 86400000);
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" }).format(d);
}
function tokyoHour() {
  return Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Tokyo",
      hour: "2-digit",
      hour12: false,
    }).format(new Date())
  );
}

function shiftDateStr(dateStr, days) {
  const d = new Date(`${dateStr}T00:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// hourly 配列から「日付 + 時刻範囲」の値を取り出す
function pick(hourly, key, dateStr, hFrom, hTo) {
  const out = [];
  for (let i = 0; i < hourly.time.length; i++) {
    const t = hourly.time[i]; // "2026-07-06T05:00"
    if (!t.startsWith(dateStr)) continue;
    const h = Number(t.slice(11, 13));
    if (h >= hFrom && h <= hTo) out.push(hourly[key][i]);
  }
  return out;
}
const avg = (a) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : null);
const sum = (a) => a.reduce((s, v) => s + v, 0);
const clamp = (v) => Math.max(0, Math.min(100, Math.round(v)));

// ── 青い池スコア ────────────────────────────────────────
// day: 評価対象日(今日または明日)。直近雨は対象日前の48時間で評価する。
function pondScore(hourly, day) {
  let score = 55;
  const reasons = [];

  // 対象日直前48時間の降水（濁りの最大要因）
  const winStart = shiftDateStr(day, -2) + "T00:00";
  const winEnd = day + "T00:00";
  const rainHours = [];
  for (let i = 0; i < hourly.time.length; i++) {
    const t = hourly.time[i];
    if (t >= winStart && t < winEnd && hourly.precipitation[i] !== null) {
      rainHours.push(hourly.precipitation[i]);
    }
  }
  const rain48 = sum(rainHours);
  if (rain48 >= 30) { score -= 35; reasons.push(["minus", `直近2日の雨 ${rain48.toFixed(0)}mm — 濁りやすい`]); }
  else if (rain48 >= 15) { score -= 20; reasons.push(["minus", `直近2日の雨 ${rain48.toFixed(0)}mm`]); }
  else if (rain48 >= 5) { score -= 8; reasons.push(["", `直近2日の雨 ${rain48.toFixed(0)}mm`]); }
  else { score += 5; reasons.push(["plus", "直近2日はほぼ雨なし"]); }

  // 日中(9〜15時)の雲量 — 光が入るほど青が映える
  const cloud = avg(pick(hourly, "cloud_cover", day, 9, 15));
  if (cloud !== null) {
    if (cloud <= 30) { score += 25; reasons.push(["plus", `日中の雲量 ${cloud.toFixed(0)}% — 光たっぷり`]); }
    else if (cloud <= 60) { score += 10; reasons.push(["plus", `日中の雲量 ${cloud.toFixed(0)}%`]); }
    else if (cloud <= 85) { reasons.push(["", `日中の雲量 ${cloud.toFixed(0)}% — 曇りがち`]); }
    else { score -= 10; reasons.push(["minus", `日中の雲量 ${cloud.toFixed(0)}% — 厚い雲`]); }
  }

  // 日中の風 — 水面が波立つと色がにごって見える
  const wind = avg(pick(hourly, "wind_speed_10m", day, 9, 15)); // km/h
  if (wind !== null) {
    if (wind >= 25) { score -= 12; reasons.push(["minus", `風 ${(wind / 3.6).toFixed(1)}m/s — 水面が波立つ`]); }
    else if (wind >= 15) { score -= 6; reasons.push(["", `風 ${(wind / 3.6).toFixed(1)}m/s`]); }
    else { score += 5; reasons.push(["plus", `風 ${(wind / 3.6).toFixed(1)}m/s — 水面おだやか`]); }
  }

  // 対象日の降水予報
  const rainDay = sum(pick(hourly, "precipitation", day, 6, 18));
  if (rainDay >= 3) { score -= 10; reasons.push(["minus", `日中に雨予報 ${rainDay.toFixed(0)}mm`]); }

  return { score: clamp(score), reasons };
}

function pondVerdict(s) {
  if (s >= 80) return ["コバルトブルー期待", "光の条件が揃っています。昼過ぎ(13〜14時)が狙い目。"];
  if (s >= 60) return ["青が見えそう", "晴れ間のタイミングを狙えば十分楽しめそうです。"];
  if (s >= 40) return ["五分五分", "時間帯しだい。現地のSNS最新投稿も合わせて確認を。"];
  if (s >= 20) return ["白濁ぎみかも", "雨後の濁りや厚い雲の影響が出ていそうです。"];
  return ["期待薄", "無理せず、丘の風景や美術館プランもおすすめ。"];
}

// ── 霧・雲海チャンス（明朝・放射冷却型） ────────────────
function fogScore(hourly, today, tomorrow) {
  let score = 30;
  const reasons = [];

  // 夜間(今日21時〜明日3時)の雲量 — 放射冷却には晴れた夜が必要
  const nightCloud = avg(
    pick(hourly, "cloud_cover", today, 21, 23).concat(pick(hourly, "cloud_cover", tomorrow, 0, 3))
  );
  if (nightCloud !== null) {
    if (nightCloud <= 30) { score += 30; reasons.push(["plus", `夜の雲量 ${nightCloud.toFixed(0)}% — 放射冷却が効く`]); }
    else if (nightCloud <= 60) { score += 10; reasons.push(["", `夜の雲量 ${nightCloud.toFixed(0)}%`]); }
    else { score -= 20; reasons.push(["minus", `夜の雲量 ${nightCloud.toFixed(0)}% — 冷え込みにくい`]); }
  }

  // 明け方(3〜6時)の風 — 弱いほど霧が滞留する
  const dawnWind = avg(pick(hourly, "wind_speed_10m", tomorrow, 3, 6));
  if (dawnWind !== null) {
    if (dawnWind <= 7) { score += 20; reasons.push(["plus", `明け方の風 ${(dawnWind / 3.6).toFixed(1)}m/s — ほぼ無風`]); }
    else if (dawnWind <= 15) { score += 5; reasons.push(["", `明け方の風 ${(dawnWind / 3.6).toFixed(1)}m/s`]); }
    else { score -= 15; reasons.push(["minus", `明け方の風 ${(dawnWind / 3.6).toFixed(1)}m/s — 霧が飛ばされやすい`]); }
  }

  // 明け方の湿度と露点差
  const dawnHum = avg(pick(hourly, "relative_humidity_2m", tomorrow, 3, 6));
  const dawnTemp = avg(pick(hourly, "temperature_2m", tomorrow, 3, 6));
  const dawnDew = avg(pick(hourly, "dew_point_2m", tomorrow, 3, 6));
  if (dawnHum !== null) {
    if (dawnHum >= 95) { score += 25; reasons.push(["plus", `明け方の湿度 ${dawnHum.toFixed(0)}% — 飽和寸前`]); }
    else if (dawnHum >= 88) { score += 15; reasons.push(["plus", `明け方の湿度 ${dawnHum.toFixed(0)}%`]); }
    else if (dawnHum >= 80) { score += 5; reasons.push(["", `明け方の湿度 ${dawnHum.toFixed(0)}%`]); }
    else { score -= 10; reasons.push(["minus", `明け方の湿度 ${dawnHum.toFixed(0)}% — 乾燥ぎみ`]); }
  }
  if (dawnTemp !== null && dawnDew !== null && dawnTemp - dawnDew <= 1.5) {
    score += 10;
    reasons.push(["plus", "気温と露点が接近 — 霧が発生しやすい"]);
  }

  return { score: clamp(score), reasons };
}

function fogVerdict(s) {
  if (s >= 75) return ["雲海チャンス大", "日の出前に丘の高台へ。防寒と長靴を忘れずに。"];
  if (s >= 55) return ["霧の可能性あり", "早起きの価値あり。日の出30〜60分前が勝負。"];
  if (s >= 35) return ["微妙なライン", "出るとしても薄め。ダメ元で狙うなら高い丘から。"];
  return ["期待薄", "朝はゆっくりで良さそうです。"];
}

// ── 描画 ────────────────────────────────────────────────
function renderScore(prefix, score, verdictFn, reasons) {
  const ring = document.getElementById(`${prefix}-ring`);
  ring.style.setProperty("--pct", score);
  document.getElementById(`${prefix}-score`).textContent = score;
  const [title, advice] = verdictFn(score);
  document.getElementById(`${prefix}-verdict`).textContent = title;
  document.getElementById(`${prefix}-advice`).textContent = advice;
  const ul = document.getElementById(`${prefix}-reasons`);
  ul.innerHTML = "";
  for (const [cls, text] of reasons) {
    const li = document.createElement("li");
    if (cls) li.className = cls;
    li.textContent = text;
    ul.appendChild(li);
  }
}

function hhmm(iso) {
  return iso ? iso.slice(11, 16) : "--:--";
}
function minus30(iso) {
  if (!iso) return "--:--";
  const [h, m] = iso.slice(11, 16).split(":").map(Number);
  const t = h * 60 + m - 30;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}

// ── 丘の色ごよみ(平年の目安・日付連動) ──────────────────
const KOYOMI = [
  { from: "01-01", to: "02-29", title: "白の季節 — 雪原と青い影",
    colors: ["#f5f8fb", "#dce7f2", "#9db8d2"],
    desc: "丘は一面の雪原。晴れた朝はダイヤモンドダストや樹氷、夕方は雪面がほんのり桃色に染まります。青い池は凍結し、夜はライトアップされます。" },
  { from: "03-01", to: "04-14", title: "雪解けのはじまり",
    colors: ["#e8edf2", "#b9a98e", "#7a6a52"],
    desc: "雪が緩み、丘に土の色が戻り始める季節。雪と土のまだら模様は今だけの風景です。融雪期は足元と道路状況に注意。" },
  { from: "04-15", to: "05-14", title: "目覚めの丘 — 耕起と遅い桜",
    colors: ["#6b4f2f", "#a2b56b", "#e8b7c8"],
    desc: "畑起こしが始まり、黒い土のうねが現れます。連休の頃に桜が咲き、カラマツが芽吹く。パッチワークの下絵ができていく時期です。" },
  { from: "05-15", to: "06-20", title: "緑のグラデーション",
    colors: ["#a8c98a", "#7fb069", "#4a7c3f"],
    desc: "麦・ビート・じゃがいもの若葉で、丘が何段階もの緑に塗り分けられます。朝霧の発生も増え、撮影の好機が続きます。" },
  { from: "06-21", to: "07-15", title: "じゃがいもの花と色づく麦",
    colors: ["#f2f0e4", "#c9cf8e", "#8a9a5b"],
    desc: "じゃがいも畑に白や薄紫の花が咲き、小麦は緑から金色へ変わり始めます。日の出が早く、朝4時台の光を狙える贅沢な時期。" },
  { from: "07-16", to: "08-05", title: "麦秋 — 丘が金色になる",
    colors: ["#d4a937", "#e6c96a", "#7fb069"],
    desc: "小麦が実り、丘が金色に波打つ美瑛のハイライト。ラベンダーやひまわりも重なり、一年で最も色数の多い季節です。収穫が始まると麦稈ロールが転がります。" },
  { from: "08-06", to: "08-31", title: "そばの白と収穫の丘",
    colors: ["#f4f2ec", "#c8b98a", "#9a8a6a"],
    desc: "刈り取られた麦畑の金茶色に、そば畑の白い花が重なります。夏のピークが過ぎ、丘は少しずつ静けさを取り戻していきます。" },
  { from: "09-01", to: "09-30", title: "収穫の秋 — ロールの転がる丘",
    colors: ["#c8a45a", "#8a6f3f", "#6f8f5f"],
    desc: "じゃがいも・豆・ビートの収穫が進み、牧草ロールがあちこちに現れます。空気が澄み、朝霧・雲海の当たり日が増える撮影の季節。" },
  { from: "10-01", to: "11-10", title: "カラマツの黄葉と初雪の便り",
    colors: ["#d9a336", "#8f5f2f", "#e8edf2"],
    desc: "カラマツ林が黄金色に染まり、十勝岳には初冠雪。緑・金・白が同居する贅沢な時期です。青い池の紅葉も見どころ。" },
  { from: "11-11", to: "12-31", title: "雪化粧 — 白の季節へ",
    colors: ["#eef2f6", "#c9d6e2", "#8fa8bf"],
    desc: "丘が白く覆われ、パッチワークは雪原に変わります。青い池のライトアップが始まり、空気が澄んで星空も美しい季節です。" },
];

function renderKoyomi() {
  const mmdd = tokyoDateStr().slice(5); // "MM-DD"
  let idx = KOYOMI.findIndex((k) => k.from <= mmdd && mmdd <= k.to);
  if (idx < 0) idx = 0;
  const cur = KOYOMI[idx];
  const next = KOYOMI[(idx + 1) % KOYOMI.length];

  const [fm, fd] = cur.from.split("-").map(Number);
  const [tm, td] = cur.to.split("-").map(Number);
  document.getElementById("koyomi-period").textContent = `いまの時期(${fm}/${fd}〜${tm}/${td} 頃)`;
  document.getElementById("koyomi-title").textContent = cur.title;
  document.getElementById("koyomi-desc").textContent = cur.desc;

  const sw = document.getElementById("koyomi-swatches");
  sw.innerHTML = "";
  for (const c of cur.colors) {
    const d = document.createElement("div");
    d.className = "swatch";
    d.style.background = c;
    sw.appendChild(d);
  }

  const [nm, nd] = next.from.split("-").map(Number);
  document.getElementById("koyomi-next").textContent = `このあとの丘: ${next.title}(${nm}/${nd}頃〜)`;
}

// ── 麦秋メーター(平年比方式・積算温度) ──────────────────
// 美瑛の日平均気温を4/1から積算(基準0℃)し、過去10年の平年ペースと比較して
// 「今年の麦秋(小麦が金色になる)ピーク」を相対推定する。
const BAKUSHU_PEAK_MMDD = "07-20"; // 平年の麦秋ピークの目安
const GDD_START_MMDD = "04-01"; // 積算開始(融雪・起生期の目安)
const ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive";

function cacheGet(key, maxAgeMs) {
  try {
    const c = JSON.parse(localStorage.getItem(key));
    if (c && Date.now() - c.ts < maxAgeMs) return c.data;
  } catch (e) { /* 破損時は無視 */ }
  return null;
}
function cacheSet(key, data) {
  try { localStorage.setItem(key, JSON.stringify({ ts: Date.now(), data })); } catch (e) { /* 容量超過等は無視 */ }
}

async function fetchDailyMeans(start, end) {
  const url =
    `${ARCHIVE_API}?latitude=${LAT}&longitude=${LON}` +
    `&start_date=${start}&end_date=${end}` +
    "&daily=temperature_2m_mean&timezone=Asia%2FTokyo";
  const res = await fetch(url);
  if (!res.ok) throw new Error(res.status);
  const d = await res.json();
  return { time: d.daily.time, temp: d.daily.temperature_2m_mean };
}

// year年の 4/1〜8/31 の累積積算温度(基準0℃)。データ未着の末尾はnull。
function cumulativeGdd(times, temps, year) {
  const out = [];
  let sum = 0;
  for (let i = 0; i < times.length; i++) {
    const t = times[i];
    if (!t.startsWith(String(year))) continue;
    const mmdd = t.slice(5);
    if (mmdd < GDD_START_MMDD || mmdd > "08-31") continue;
    const v = temps[i];
    if (v === null || v === undefined) { out.push(null); continue; }
    sum += Math.max(0, v);
    out.push(sum);
  }
  return out;
}

async function getNormalCurve(year) {
  const key = `bakushu-normal-${year}`;
  const hit = cacheGet(key, 30 * 86400000);
  if (hit) return hit;
  const d = await fetchDailyMeans(`${year - 10}-04-01`, `${year - 1}-08-31`);
  const curves = [];
  for (let y = year - 10; y <= year - 1; y++) {
    const c = cumulativeGdd(d.time, d.temp, y);
    if (c.filter((v) => v !== null).length > 100) curves.push(c);
  }
  const len = Math.min(...curves.map((c) => c.length));
  const avg = [];
  for (let i = 0; i < len; i++) {
    let s = 0, n = 0;
    for (const c of curves) if (c[i] !== null) { s += c[i]; n++; }
    avg.push(n ? s / n : null);
  }
  cacheSet(key, avg);
  return avg;
}

function mmddOfIndex(year, idx) {
  const d = new Date(`${year}-${GDD_START_MMDD}T00:00:00`);
  d.setDate(d.getDate() + idx);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function bakushuStage(progressPct) {
  if (progressPct < 25) return "生育初期 — 丘はまだ深い緑";
  if (progressPct < 60) return "茎立ち〜出穂 — 麦の穂が出そろう頃";
  if (progressPct < 95) return "登熟期 — 緑から金色へ色づいていく";
  if (progressPct < 108) return "麦秋ピーク圏 — 丘が金色に輝く";
  return "収穫期 — 麦稈ロールが転がり出す";
}

async function loadBakushu() {
  const status = document.getElementById("bakushu-status");
  const pred = document.getElementById("bakushu-pred");
  const stage = document.getElementById("bakushu-stage");
  const fill = document.getElementById("bakushu-fill");
  const barWrap = document.getElementById("bakushu-bar-wrap");
  try {
    const today = tokyoDateStr();
    const year = Number(today.slice(0, 4));
    const mmdd = today.slice(5);

    // シーズンオフ(9/1〜3/31): 次の積算開始までのカウントダウン
    if (mmdd < GDD_START_MMDD || mmdd > "08-31") {
      const nextYear = mmdd > "08-31" ? year + 1 : year;
      const days = Math.ceil(
        (new Date(`${nextYear}-${GDD_START_MMDD}T00:00:00`) - new Date(`${today}T00:00:00`)) / 86400000
      );
      status.textContent = "シーズンオフ — 小麦は雪の下で春を待っています。";
      pred.textContent = `次の積算スタート(4/1)まで あと${days}日`;
      stage.textContent = `平年の麦秋ピークは 7/20 頃。来季もここでカウントします。`;
      barWrap.classList.add("hidden");
      return;
    }

    const [normal, cur] = await Promise.all([
      getNormalCurve(year),
      (async () => {
        const key = `bakushu-cur-${today}`;
        const hit = cacheGet(key, 6 * 3600000);
        if (hit) return hit;
        // アーカイブAPIは直近約5日が未収録のため、末尾は予報APIの実績値で補う
        const cut = new Date(`${today}T00:00:00`);
        cut.setDate(cut.getDate() - 7);
        const cutStr = cut.toISOString().slice(0, 10);
        const arch = await fetchDailyMeans(`${year}-04-01`, cutStr);
        const res = await fetch(
          `https://api.open-meteo.com/v1/forecast?latitude=${LAT}&longitude=${LON}` +
            "&daily=temperature_2m_mean&past_days=7&forecast_days=1&timezone=Asia%2FTokyo"
        );
        if (!res.ok) throw new Error(res.status);
        const rec = (await res.json()).daily;
        const time = [...arch.time];
        const temp = [...arch.temp];
        for (let i = 0; i < rec.time.length; i++) {
          if (rec.time[i] > cutStr && rec.time[i] <= today) {
            time.push(rec.time[i]);
            temp.push(rec.temperature_2m_mean[i]);
          }
        }
        const c = cumulativeGdd(time, temp, year);
        cacheSet(key, c);
        return c;
      })(),
    ]);

    // 今年の最新値(データ未着の末尾nullを除く)
    let j = cur.length - 1;
    while (j >= 0 && cur[j] === null) j--;
    if (j < 0) throw new Error("no data");
    const v = cur[j];

    // 平年カーブ上で同じ積算値に達する日を探す
    let i = normal.findIndex((n) => n !== null && n >= v);
    if (i < 0) i = normal.length - 1;
    const earlyDays = i - j; // 正 = 今年は平年より早い

    // 平年ピーク日の平年積算値に対する進捗
    const peakIdx = Math.round(
      (new Date(`${year}-${BAKUSHU_PEAK_MMDD}T00:00:00`) - new Date(`${year}-${GDD_START_MMDD}T00:00:00`)) / 86400000
    );
    const peakGdd = normal[Math.min(peakIdx, normal.length - 1)];
    const progress = Math.max(0, Math.min(115, (v / peakGdd) * 100));

    // 予測ピーク日 = 平年ピーク − 早い日数
    const predDate = new Date(`${year}-${BAKUSHU_PEAK_MMDD}T00:00:00`);
    predDate.setDate(predDate.getDate() - earlyDays);
    const predStr = `${predDate.getMonth() + 1}/${predDate.getDate()}`;

    const paceStr =
      earlyDays > 1 ? `平年より${earlyDays}日早いペース`
      : earlyDays < -1 ? `平年より${-earlyDays}日遅いペース`
      : "ほぼ平年並みのペース";

    status.textContent = `積算温度 ${Math.round(v)}℃・日(${mmddOfIndex(year, j)}時点) — ${paceStr}`;
    fill.style.width = `${Math.min(100, progress)}%`;
    pred.textContent =
      progress >= 105
        ? `今年の麦秋ピークは過ぎました(推定 ${predStr} 頃)。丘は収穫の季節へ。`
        : `金色ピーク予想: ${predStr} 頃(平年 7/20 頃)`;
    stage.textContent = `いまの段階: ${bakushuStage(progress)}`;
  } catch (e) {
    status.textContent = "積算データの取得に失敗しました。時間をおいて再読み込みしてください。";
    barWrap.classList.add("hidden");
    pred.textContent = "";
    stage.textContent = "";
  }
}

// ── 十勝岳 火山情報(気象庁・噴火警報/予報) ──────────────
const VOLCANO_URL = "https://www.jma.go.jp/bosai/volcano/data/warning/108.json";

async function loadVolcano() {
  try {
    const res = await fetch(VOLCANO_URL);
    if (!res.ok) return;
    const data = await res.json();
    const item = data.volcanoInfos?.[0]?.items?.[0];
    if (!item || !item.name) return;

    const date = (data.reportDatetime || "").slice(0, 10).replaceAll("-", "/");
    const cond = item.condition && item.condition !== "発表" ? `・${item.condition}` : "";
    document.getElementById("volcano-text").textContent =
      `現在 ${item.name} — ${date} 気象庁発表${cond}`;

    const box = document.getElementById("volcano-box");
    // レベル2以上は注意色、3以上は警告色で表示する(気象庁の表記は全角数字)
    const levelMatch = item.name.match(/レベル\s*([0-9０-９])/);
    const level = levelMatch
      ? Number(levelMatch[1].replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)))
      : 0;
    box.classList.remove("info", "warning");
    box.classList.add(level >= 3 ? "danger" : level >= 2 ? "warning" : "info");
    box.classList.remove("hidden");
  } catch (e) {
    /* 取得失敗時は何も表示しない */
  }
}

async function main() {
  renderKoyomi();
  loadVolcano();
  loadBakushu();
  const today = tokyoDateStr();
  const tomorrow = tokyoDateStr(1);
  document.getElementById("today-date").textContent = `（${today}）`;

  let data;
  try {
    const res = await fetch(API);
    if (!res.ok) throw new Error(res.status);
    data = await res.json();
  } catch (e) {
    document.getElementById("error-box").classList.remove("hidden");
    return;
  }

  const hourly = data.hourly;

  // 現在の状況（現在時刻に最も近い hourly 値）
  const nowIdx = hourly.time.indexOf(`${today}T${String(tokyoHour()).padStart(2, "0")}:00`);
  if (nowIdx >= 0) {
    document.getElementById("now-temp").textContent = `${hourly.temperature_2m[nowIdx].toFixed(1)}℃`;
    document.getElementById("now-cloud").textContent = `雲量 ${hourly.cloud_cover[nowIdx]}%`;
    document.getElementById("now-wind").textContent = `風 ${(hourly.wind_speed_10m[nowIdx] / 3.6).toFixed(1)}m/s`;
  }

  // 17時以降は「明日の見込み」に切り替える(旅行前夜のユーザー向け)
  const eveningMode = tokyoHour() >= 17;
  const pondDay = eveningMode ? tomorrow : today;
  if (eveningMode) {
    document.getElementById("pond-title").textContent =
      `明日(${Number(tomorrow.slice(5, 7))}/${Number(tomorrow.slice(8, 10))})の青い池スコア`;
    document.getElementById("pond-mode").textContent = "🌙 今夜の時点での明日の見込みです";
    document.getElementById("pond-mode").classList.remove("hidden");
  }
  const pond = pondScore(hourly, pondDay);
  renderScore("pond", pond.score, pondVerdict, pond.reasons);

  const fog = fogScore(hourly, today, tomorrow);
  renderScore("fog", fog.score, fogVerdict, fog.reasons);

  // 日の出・日の入り（daily.time は past_days ぶん前から始まる）
  const dTomorrow = data.daily.time.indexOf(tomorrow);
  const dToday = data.daily.time.indexOf(today);
  const sunrise = dTomorrow >= 0 ? data.daily.sunrise[dTomorrow] : null;
  const sunset = dToday >= 0 ? data.daily.sunset[dToday] : null;
  document.getElementById("sunrise").textContent = hhmm(sunrise);
  document.getElementById("sunset").textContent = hhmm(sunset);
  document.getElementById("golden-am").textContent = minus30(sunrise);
  document.getElementById("golden-pm").textContent = minus30(sunset);
}

main();

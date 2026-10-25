// Telegram botu (Cloudflare Workers, ücretsiz katman): kanal menüsü, istatistikler ve "yayında" bildirimleri.
// Gerekli gizli değerler: TELEGRAM_BOT_TOKEN, CHAT_ID, WEBHOOK_SECRET, REGISTER_SECRET,
//                          YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN (youtube.readonly yetkili token)
// Değişken: CHANNELS (JSON: [{id, name, emoji, yt}]). KV bağlaması: KV.

const TR_OFFSET_MS = 3 * 3600 * 1000;          // TSİ = UTC+3
const STATS_CACHE_MS = 120 * 1000;             // YouTube kotasını korumak için istatistik önbelleği
const MIN_FORCED_REFRESH_MS = 30 * 1000;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (request.method === "POST" && url.pathname === "/telegram") return await onTelegram(request, env, ctx);
      if (request.method === "POST" && url.pathname === "/register") return await onRegister(request, env);
    } catch (err) {
      console.error("fetch hatası", (err && err.stack) || err);
    }
    return new Response("ok");
  },

  async scheduled(event, env, ctx) {
    if (event.cron === "0 21 * * *") ctx.waitUntil(dailyRollover(env).catch((e) => console.error("rollover", e)));
    ctx.waitUntil(flushPending(env).catch((e) => console.error("flush", e)));
  },
};

// ---------------------------------------------------------------- yardımcılar
const channels = (env) => JSON.parse(env.CHANNELS);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const fmt = (n) => Number(n).toLocaleString("tr-TR");
const trDate = (ms = Date.now()) => new Date(ms + TR_OFFSET_MS).toISOString().slice(0, 10);
const trClock = (ms = Date.now()) => new Date(ms + TR_OFFSET_MS).toISOString().slice(11, 16) + " TSİ";

async function tg(env, method, body) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}
const send = (env, text, extra = {}) =>
  tg(env, "sendMessage", { chat_id: env.CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true, ...extra });

// ---------------------------------------------------------------- Telegram webhook
async function onTelegram(request, env, ctx) {
  if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
    return new Response("forbidden", { status: 403 });
  }
  const update = await request.json();
  ctx.waitUntil(handleUpdate(update, env).catch((e) => console.error("update", (e && e.stack) || e)));
  return new Response("ok");
}

function menuKeyboard(env) {
  const buttons = channels(env).map((c) => ({ text: `${c.emoji} ${c.name}`, callback_data: `ch:${c.id}` }));
  const rows = [];
  for (let i = 0; i < buttons.length; i += 2) rows.push(buttons.slice(i, i + 2));
  rows.push([{ text: "📊 Tüm kanallar", callback_data: "all" }]);
  return { inline_keyboard: rows };
}

const MENU_TEXT = "📺 <b>Kanal menüsü</b>\nİstatistiklerini görmek istediğiniz kanalı seçin 👇";

async function handleUpdate(update, env) {
  const message = update.message;
  const callback = update.callback_query;
  const chatId = String((message && message.chat.id) || (callback && callback.message && callback.message.chat.id) || "");
  if (chatId !== String(env.CHAT_ID)) return;   // yalnızca bot sahibi kullanabilir

  if (message) {
    await send(env, MENU_TEXT, { reply_markup: menuKeyboard(env) });
    return;
  }
  const data = callback.data || "";
  const target = { chat_id: env.CHAT_ID, message_id: callback.message.message_id, parse_mode: "HTML", disable_web_page_preview: true };
  await tg(env, "answerCallbackQuery", { callback_query_id: callback.id });

  if (data === "menu") {
    await tg(env, "editMessageText", { ...target, text: MENU_TEXT, reply_markup: menuKeyboard(env) });
    return;
  }
  const refresh = data.endsWith(":r");
  const key = refresh ? data.slice(0, -2) : data;
  let stats;
  try {
    stats = await getStats(env, refresh);
  } catch (err) {
    console.error("istatistik hatası", (err && err.stack) || err);
    await tg(env, "editMessageText", {
      ...target,
      text: "⚠️ İstatistikler şu an alınamadı, biraz sonra tekrar deneyin.",
      reply_markup: { inline_keyboard: [[{ text: "⬅️ Menü", callback_data: "menu" }]] },
    });
    return;
  }
  const snap = await getSnapshot(env, stats);
  let text;
  let again;
  if (key === "all") {
    text = allText(env, stats, snap);
    again = "all:r";
  } else if (key.startsWith("ch:")) {
    const channel = channels(env).find((c) => c.id === key.slice(3));
    if (!channel) return;
    text = channelText(channel, stats, snap);
    again = `${key}:r`;
  } else {
    return;
  }
  const result = await tg(env, "editMessageText", {
    ...target,
    text,
    reply_markup: { inline_keyboard: [[{ text: "🔄 Yenile", callback_data: again }, { text: "⬅️ Menü", callback_data: "menu" }]] },
  });
  if (!result.ok && !/not modified/i.test(result.description || "")) console.error("editMessageText", result);
}

// ---------------------------------------------------------------- YouTube istatistikleri
async function ytToken(env) {
  const cached = await env.KV.get("yt_token", "json");
  if (cached && cached.exp > Date.now() + 60000) return cached.token;
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    body: new URLSearchParams({
      client_id: env.YT_CLIENT_ID,
      client_secret: env.YT_CLIENT_SECRET,
      refresh_token: env.YT_REFRESH_TOKEN,
      grant_type: "refresh_token",
    }),
  });
  const json = await res.json();
  if (!json.access_token) throw new Error("YouTube token yenilenemedi: " + JSON.stringify(json).slice(0, 150));
  await env.KV.put("yt_token", JSON.stringify({ token: json.access_token, exp: Date.now() + (json.expires_in - 120) * 1000 }), { expirationTtl: 3000 });
  return json.access_token;
}

async function getStats(env, force = false) {
  const cached = await env.KV.get("stats", "json");
  const age = cached ? Date.now() - cached.t : Infinity;
  if (cached && (force ? age < MIN_FORCED_REFRESH_MS : age < STATS_CACHE_MS)) return cached;

  const ids = channels(env).map((c) => c.yt).join(",");
  const res = await fetch(`https://www.googleapis.com/youtube/v3/channels?part=statistics&id=${ids}&maxResults=50`, {
    headers: { Authorization: `Bearer ${await ytToken(env)}` },
  });
  const json = await res.json();
  if (!json.items) {
    if (cached) return cached;   // kota/ağ sorununda son bilinen değer
    throw new Error("YouTube istatistikleri alınamadı: " + JSON.stringify(json).slice(0, 150));
  }
  const data = { t: Date.now(), channels: {} };
  for (const item of json.items) {
    const s = item.statistics;
    data.channels[item.id] = {
      subs: Number(s.subscriberCount || 0),
      hidden: Boolean(s.hiddenSubscriberCount),
      views: Number(s.viewCount || 0),
      videos: Number(s.videoCount || 0),
    };
  }
  await env.KV.put("stats", JSON.stringify(data));
  return data;
}

const viewsMap = (stats) => Object.fromEntries(Object.entries(stats.channels).map(([id, s]) => [id, s.views]));

async function getSnapshot(env, stats) {
  let snap = await env.KV.get("snap", "json");
  if (!snap) {   // ilk kurulum: gün başı verisi yok, kurulumdan itibaren sayılır
    snap = { date: trDate(), views: viewsMap(stats), partial: true, from: trClock() };
    await env.KV.put("snap", JSON.stringify(snap));
  }
  return snap;
}

function todayViews(channel, stats, snap) {
  const now = stats.channels[channel.yt];
  const base = snap.views[channel.yt];
  if (!now || base === undefined) return null;
  return Math.max(now.views - base, 0);
}

function todayLabel(snap) {
  return snap.partial ? `Bugün (${snap.from}'den beri)` : "Bugün";
}

function channelText(channel, stats, snap) {
  const s = stats.channels[channel.yt];
  if (!s) return `${channel.emoji} <b>${esc(channel.name)}</b>\n\nVeri alınamadı.`;
  const today = todayViews(channel, stats, snap);
  return [
    `${channel.emoji} <b>${esc(channel.name)}</b>`,
    "",
    `👥 Abone: <b>${s.hidden ? "gizli" : fmt(s.subs)}</b>`,
    `👁 Toplam izlenme: <b>${fmt(s.views)}</b>`,
    `🎬 Paylaşılan video: <b>${fmt(s.videos)}</b>`,
    `📈 ${todayLabel(snap)}: <b>${today === null ? "-" : "+" + fmt(today)}</b> izlenme`,
    "",
    `🕒 ${trClock(stats.t)}`,
  ].join("\n");
}

function allText(env, stats, snap) {
  let subs = 0, views = 0, videos = 0, today = 0;
  const lines = [];
  for (const c of channels(env)) {
    const s = stats.channels[c.yt];
    if (!s) continue;
    const t = todayViews(c, stats, snap) || 0;
    subs += s.subs; views += s.views; videos += s.videos; today += t;
    lines.push(`${c.emoji} <b>${esc(c.name)}</b>: 👥 ${s.hidden ? "gizli" : fmt(s.subs)} · 👁 ${fmt(s.views)} · 🎬 ${s.videos} · 📈 +${fmt(t)}`);
  }
  return [
    "📊 <b>Tüm kanallar</b>",
    "",
    ...lines,
    "",
    `<b>Toplam</b>: 👥 ${fmt(subs)} · 👁 ${fmt(views)} · 🎬 ${videos}`,
    `${todayLabel(snap)}: <b>+${fmt(today)}</b> izlenme`,
    "",
    `🕒 ${trClock(stats.t)}`,
  ].join("\n");
}

// Her gün 00:00 TSİ: biten günün özetini gönderir ve yeni gün için başlangıç verisini kaydeder
async function dailyRollover(env) {
  const stats = await getStats(env, true);
  const snap = await env.KV.get("snap", "json");
  if (snap) {
    let total = 0;
    const lines = channels(env).map((c) => {
      const t = todayViews(c, stats, snap) || 0;
      total += t;
      return `${c.emoji} ${esc(c.name)}: +${fmt(t)}`;
    });
    await send(env, `📅 <b>Günlük özet (${snap.date})</b>\n\n${lines.join("\n")}\n\n<b>Toplam</b>: +${fmt(total)} izlenme`);
  }
  await env.KV.put("snap", JSON.stringify({ date: trDate(), views: viewsMap(stats), partial: false }));
}

// ---------------------------------------------------------------- "yayında" bildirimleri
async function onRegister(request, env) {
  if (request.headers.get("Authorization") !== `Bearer ${env.REGISTER_SECRET}`) {
    return new Response("forbidden", { status: 403 });
  }
  const item = await request.json();
  if (!item.video_id || !item.publish_at) return new Response("bad request", { status: 400 });
  const list = ((await env.KV.get("pending", "json")) || []).filter((i) => i.video_id !== item.video_id);
  list.push(item);
  await env.KV.put("pending", JSON.stringify(list));
  return new Response("ok");
}

async function flushPending(env) {
  const list = (await env.KV.get("pending", "json")) || [];
  if (!list.length) return;
  const now = Date.now();
  const due = list.filter((i) => Date.parse(i.publish_at) <= now);
  if (!due.length) return;
  await env.KV.put("pending", JSON.stringify(list.filter((i) => Date.parse(i.publish_at) > now)));
  for (const item of due) {
    await send(env, `🟢 <b>Video yayında</b>\n\n📺 <b>${esc(item.name)}</b>\n🎬 ${esc(item.title)}\n🔗 https://youtube.com/shorts/${item.video_id}`, {
      disable_web_page_preview: false,
    });
  }
}

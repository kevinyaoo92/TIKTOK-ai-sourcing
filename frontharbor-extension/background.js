importScripts("miaoshou.js");

﻿// POC-4: 完整闭环（搜索→卡片过滤→逐个详情→四档降级→最多3个）
// ========== 环境配置 ==========
// 生产环境（发布给真实用户时使用）：
// const BACKEND = "http://118.89.85.175";
// 本地调试时改用下面这行（注释掉上行）：
const BACKEND = "http://127.0.0.1:8123";
const POLL_MS = 2000;
const SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm?keywords=";
const DETAIL_URL = "https://detail.1688.com/offer/";
const WAIT_MS = 10000;
const MAX_DETAIL = 40;
const MAX_RESULTS = 3;

let busy = false;
let lastStatus = "idle";
let lastInfo = "";

async function saveState(extra) {
  try {
    await chrome.storage.local.set(Object.assign({
      lastStatus: lastStatus,
      lastInfo: lastInfo,
      updatedAt: new Date().toLocaleString()
    }, extra || {}));
  } catch (e) {}
}

function openAndWait(url) {
  return new Promise((resolve) => {
    chrome.tabs.create({ url: url, active: true }, (tab) => {
      if (!tab || !tab.id) { resolve(null); return; }
      const tabId = tab.id;
      const onUpdated = (tid, info) => {
        if (tid === tabId && info.status === "complete") {
          chrome.tabs.onUpdated.removeListener(onUpdated);
          setTimeout(() => resolve(tabId), 2500);
        }
      };
      chrome.tabs.onUpdated.addListener(onUpdated);
      setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(onUpdated);
        resolve(tabId);
      }, WAIT_MS + 5000);
    });
  });
}

async function readDetailMainWorld(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      world: "MAIN",
      func: () => {
        const get = (o, k) => (o && typeof o === "object") ? o[k] : undefined;
        const root = get(get(get(get(window, "context"), "result"), "data"), "Root");
        const dataJson = get(get(root, "fields"), "dataJson");
        const skuModel = dataJson && dataJson.skuModel;
        if (!skuModel) return { found: false, reason: "skuModel 不存在" };
        const props = skuModel.skuProps || [];
        const infoMap = skuModel.skuInfoMap || {};
        const dims = {};
        for (let i = 0; i < props.length; i++) {
          const p = props[i];
          const name = p.prop || ("dim" + i);
          const cnt = (p.value || []).length;
          if (cnt > 0) dims[name] = cnt;
        }
        const stocks = [];
        const keys = Object.keys(infoMap);
        for (let i = 0; i < keys.length; i++) {
          const v = infoMap[keys[i]];
          const n = v && v.canBookCount;
          if (typeof n === "number") stocks.push(n);
        }
        const minStock = stocks.length ? Math.min.apply(null, stocks) : null;
        return { found: true, dims: dims, total_combos: keys.length, stock_count: stocks.length, min_stock: minStock };
      }
    });
    return results && results[0] && results[0].result ? results[0].result : { found: false, reason: "no result" };
  } catch (e) {
    return { found: false, reason: String(e.message || e).slice(0, 200) };
  }
}

async function readDetailDomFallback(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: () => {
        const bodyText = (document.body && document.body.innerText) || "";
        const title = (document.title || "").replace(/\s*[-—]\s*阿里巴巴.*$/, "").trim();
        const m24 = bodyText.match(/24h揽收率\s*([\d.]+)\s*%/);
        const rate24 = m24 ? parseFloat(m24[1]) : null;
        const mRate = bodyText.match(/回头率\s*([\d.]+)\s*%/);
        const rate = mRate ? parseFloat(mRate[1]) : null;
        const UNIT = "[件箱个包袋套双条盒支张片瓶罐桶斤克升卷装提ml]";
        const TRIGGER = "(?:起批|混批|起订|起售|起发)";
        let moq = null;
        const lines = bodyText.split("\n");
        for (let i = 0; i < lines.length; i++) {
          const line = (lines[i] || "").trim();
          if (line.length > 80) continue;
          if (!/(起批|混批|起订|起售|起发)/.test(line)) continue;
          const re1 = new RegExp("([0-9]+)\\s*" + UNIT + ".{0,4}" + TRIGGER);
          const m1 = line.match(re1);
          const re2 = new RegExp(TRIGGER + "[^0-9]{0,6}([0-9]+)");
          const m2 = m1 ? m1 : line.match(re2);
          if (m2) {
            const n = parseInt(m2[1], 10);
            if (n >= 1 && n < 100000) { moq = n; break; }
          }
        }
        const stockEls = document.querySelectorAll('[i18n="sku-stock"]');
        const stockList = [];
        stockEls.forEach((el) => {
          const txt = (el.textContent || "").trim();
          if (/库存不足|无货|缺货|售罄/.test(txt)) { stockList.push(0); return; }
          const m = txt.match(/([0-9]+)/);
          stockList.push(m ? parseInt(m[1], 10) : 0);
        });
        const minStock = stockList.length ? Math.min.apply(null, stockList) : null;
        return {
          source: "dom", title: title, rate: rate, rate24: rate24, moq: moq,
          min_stock: minStock, stock_count: stockList.length, url: location.href
        };
      }
    });
    return results && results[0] && results[0].result ? results[0].result : { source: "dom", error: "no result" };
  } catch (e) {
    return { source: "dom", error: String(e.message || e).slice(0, 200) };
  }
}

async function readSearchCards(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId: tabId },
    func: () => {
      const els = document.querySelectorAll('[data-offer-grid-cell="true"]');
      const out = [];
      els.forEach((el) => {
        const offer_id = el.getAttribute("data-offer-expose-id") || "";
        const linkEl = el.querySelector("a");
        const link = linkEl ? linkEl.href : "";
        const mainImg = el.querySelector("img");
        const img = mainImg ? mainImg.src : "";
        const shopRow = el.querySelector('[class*="shopRow"]');
        const shopImgs = [];
        let shopName = "";
        if (shopRow) {
          shopRow.querySelectorAll("img").forEach((im) => {
            const r = im.getBoundingClientRect();
            if (r.width > 30 && r.width < 120 && r.height > 10 && r.height < 30) {
              shopImgs.push(im.src);
            }
          });
          shopName = (shopRow.innerText || "").replace(/\n+/g, " ").trim().slice(0, 60);
        }
        let price = "";
        const p1 = el.querySelector('[class*="price-item"]');
        if (p1) price = (p1.textContent || "").trim();
        if (!price) {
          const p2 = el.querySelector('[class*="price"]');
          if (p2) price = (p2.textContent || "").trim();
        }
        const text = (el.innerText || "").replace(/\n+/g, " | ");
        const mr = text.match(/回头率\s*(\d+(?:\.\d+)?)\s*%/);
        const rate = mr ? parseFloat(mr[1]) : null;
        let title = "";
        const t1 = el.querySelector('[class*="title"]');
        if (t1) title = (t1.innerText || "").trim();
        if (!title) {
          const t2 = el.querySelector("a[title]");
          if (t2) title = t2.getAttribute("title") || "";
        }
        if (!title && linkEl) title = (linkEl.innerText || "").trim().split("\n")[0];
        out.push({
          offer_id: offer_id, link: link, img: img, price: price,
          shop_name: shopName, shop_imgs: shopImgs,
          rate: rate, title: title.slice(0, 200), text: text.slice(0, 500)
        });
      });
      return out;
    }
  });
  return (results && results[0] && results[0].result) || [];
}

async function backendCardFilter(cards, keyword) {
  try {
    const r = await fetch(BACKEND + "/api/poc_card_filter", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({cards: cards, keyword: keyword})
    });
    const d = await r.json();
    if (d && d.status === "ok") return { cards: d.cards || [], stats: d.stats || {} };
  } catch (e) {}
  return { cards: cards, stats: { error: "filter_failed" } };
}

async function backendSearchUrl(keyword) {
  try {
    const r = await fetch(BACKEND + "/api/poc_build_search_url", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({keyword: keyword})
    });
    const d = await r.json();
    if (d && d.status === "ok" && d.url) return d.url;
  } catch (e) {}
  return null;
}

async function backendJudge(payload, keyword) {
  try {
    const r = await fetch(BACKEND + "/api/poc_judge", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({payload: payload, keyword: keyword})
    });
    const d = await r.json();
    if (d && d.status === "ok") return d.judge;
  } catch (e) {}
  return { result: "reject", tier: null, reason: "judge_failed" };
}

async function runFullTask(task) {
  const keyword = task.keyword;
  const diag = { keyword: keyword, cards_raw: 0, filter_stats: {}, details_checked: 0,
                 rejects: {}, final: [], tier_used: null, audit: [] };
  const searchUrl = await backendSearchUrl(keyword);
  if (!searchUrl) { diag.error = "build_search_url_failed"; return { diag: diag, results: [] }; }
  let pageTab = await openAndWait(searchUrl);
  if (!pageTab) { diag.error = "open_search_failed"; return { diag: diag, results: [] }; }
  const rawCards = await readSearchCards(pageTab);
  diag.cards_raw = rawCards.length;
  try { chrome.tabs.remove(pageTab); } catch (e) {}
  if (!rawCards.length) { diag.error = "no_cards"; return { diag: diag, results: [] }; }

  const fr = await backendCardFilter(rawCards, keyword);
  diag.filter_stats = fr.stats;
  const cards = fr.cards;
  if (!cards.length) { diag.error = "no_cards_after_filter"; return { diag: diag, results: [] }; }

  const TIER_ORDER = ["严格", "中等", "宽松", "最宽"];
  const buckets = { "严格": [], "中等": [], "宽松": [], "最宽": [] };
  const rejectReasons = {};
  let detailCount = 0;

  for (let i = 0; i < cards.length; i++) {
    if (detailCount >= MAX_DETAIL) break;
    const c = cards[i];
    if (!c.offer_id) continue;
    const dt = await openAndWait(DETAIL_URL + c.offer_id + ".html");
    if (!dt) { rejectReasons["open_detail_failed"] = (rejectReasons["open_detail_failed"] || 0) + 1; continue; }
    const mainR = await readDetailMainWorld(dt);
    const domR = await readDetailDomFallback(dt);
    try { chrome.tabs.remove(dt); } catch (e) {}
    detailCount++;
    const payload = { main: mainR, dom: domR, offer_id: c.offer_id, url: DETAIL_URL + c.offer_id + ".html" };
    const jr = await backendJudge(payload, keyword);
    diag.audit.push({
      seq: i + 1,
      offer_id: c.offer_id,
      title: (domR.title || c.title || "").slice(0, 80),
      rate: domR.rate,
      rate24: domR.rate24,
      min_stock: (mainR.min_stock != null ? mainR.min_stock : domR.min_stock),
      moq: domR.moq,
      sku_dims: mainR.dims || {},
      judge_result: jr.result,
      judge_tier: jr.tier,
      judge_reason: jr.reason || "",
      attempts: jr.attempts || []
    });
    if (jr.result === "pass") {
      buckets[jr.tier].push({
        offer_id: c.offer_id,
        title: domR.title || c.title,
        link: DETAIL_URL + c.offer_id + ".html",
        img: c.img || "",
        price: c.price,
        shop_name: c.shop_name,
        rate: domR.rate,
        rate24: domR.rate24,
        sku_dimensions: (mainR.dims || {}),
        min_stock: mainR.min_stock != null ? mainR.min_stock : domR.min_stock,
        moq: domR.moq,
        tier: jr.tier
      });
    } else {
      const key = jr.reason || "unknown";
      rejectReasons[key] = (rejectReasons[key] || 0) + 1;
    }
  }
  diag.details_checked = detailCount;
  diag.rejects = rejectReasons;

  let results = [];
  for (let i = 0; i < TIER_ORDER.length; i++) {
    const t = TIER_ORDER[i];
    if (buckets[t].length > 0) {
      results = buckets[t].slice(0, MAX_RESULTS);
      diag.tier_used = t;
      break;
    }
  }
  diag.final = results.length;
  return { diag: diag, results: results };
}

async function fetchPending() {
  if (busy) return;
  try {
    const r = await fetch(BACKEND + "/api/poc_task/pending");
    const d = await r.json();
    if (!d || d.status !== "ok" || !d.task) return;
    busy = true;
    const t = d.task;
    lastStatus = "running";
    lastInfo = "task=" + t.task_id + " type=" + t.task_type;
    await saveState({ lastTaskId: t.task_id });
    console.log("[POC-4] picked", t.task_id, t.task_type, t.keyword);

    let payload = null;
    let success = false;
    let errorMsg = "";
    try {
      if (t.task_type === "miaoshou_probe") {
        const r = await runMiaoshouProbe();
        payload = r;
        success = !r.error;
        if (r.error) errorMsg = r.error;
      } else if (t.task_type === "find_full" && t.keyword) {
        const r = await runFullTask(t);
        payload = r.diag;
        payload.results = r.results;
        success = r.results.length > 0;
        if (!success) errorMsg = "no results after all tiers";
      } else if (t.task_type === "miaoshou_collect" && t.offer_id) {
        const r = await runMiaoshouTask(t);
        payload = r.diag;
        payload.outcome = r.outcome;
        payload.message = r.message;
        success = (r.outcome === "success");
        if (!success) errorMsg = r.outcome;
      } else if (t.task_type === "detail" && t.offer_id) {
        const dt = await openAndWait(DETAIL_URL + t.offer_id + ".html");
        if (dt) {
          const mainR = await readDetailMainWorld(dt);
          const domR = await readDetailDomFallback(dt);
          try { chrome.tabs.remove(dt); } catch (e) {}
          payload = { main: mainR, dom: domR, offer_id: t.offer_id };
          success = true;
        } else {
          errorMsg = "open_detail_failed";
        }
      } else {
        errorMsg = "unknown task_type: " + t.task_type;
      }
    } catch (e) {
      errorMsg = String(e && e.message || e).slice(0, 200);
    }

    await fetch(BACKEND + "/api/poc_task/complete", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({task_id: t.task_id, success: success, payload: payload, error: errorMsg})
    });

    lastStatus = success ? "success" : "failed";
    lastInfo = "task=" + t.task_id + " tier=" + (payload && payload.tier_used || "-") + " final=" + (payload && payload.final || 0);
    await saveState();
    console.log("[POC-4] done", lastStatus, lastInfo);
  } catch (e) {
    console.warn("[POC-4] poll error", e && e.message);
  } finally {
    busy = false;
  }
}

setInterval(fetchPending, POLL_MS);
chrome.alarms.create('pollTask', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(function(){ fetchPending(); });
fetchPending();
saveState();

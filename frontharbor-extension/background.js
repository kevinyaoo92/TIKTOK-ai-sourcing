// POC-3A: 打开 1688 详情页，读 window.context + DOM 兜底
const BACKEND = "http://127.0.0.1:8123";
const POLL_MS = 2000;
const DETAIL_URL = "https://detail.1688.com/offer/";
const WAIT_MS = 10000;

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
    chrome.tabs.create({ url: url, active: false }, (tab) => {
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
        if (!skuModel) {
          return { found: false, reason: "skuModel 不存在" };
        }
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
        return {
          found: true,
          dims: dims,
          total_combos: keys.length,
          stock_count: stocks.length,
          min_stock: minStock
        };
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
          source: "dom",
          title: title,
          rate: rate,
          rate24: rate24,
          moq: moq,
          min_stock: minStock,
          stock_count: stockList.length,
          url: location.href
        };
      }
    });
    return results && results[0] && results[0].result ? results[0].result : { source: "dom", error: "no result" };
  } catch (e) {
    return { source: "dom", error: String(e.message || e).slice(0, 200) };
  }
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
    console.log("[POC-3A] picked", t.task_id, t.task_type, t.offer_id);

    let payload = null;
    let success = false;
    let errorMsg = "";
    let tabId = null;
    try {
      if (t.task_type === "detail" && t.offer_id) {
        const url = DETAIL_URL + t.offer_id + ".html";
        tabId = await openAndWait(url);
        // 先试 MAIN world
        const mainResult = await readDetailMainWorld(tabId);
        // 再做 DOM 兜底
        const domResult = await readDetailDomFallback(tabId);
        payload = { main: mainResult, dom: domResult, offer_id: t.offer_id, url: url };
        success = true;
      } else {
        errorMsg = "非 detail 任务，POC-3A 只处理 detail";
      }
    } catch (e) {
      errorMsg = String(e && e.message || e).slice(0, 200);
    }

    if (tabId) {
      try { chrome.tabs.remove(tabId); } catch (e) {}
    }

    await fetch(BACKEND + "/api/poc_task/complete", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        task_id: t.task_id,
        success: success,
        payload: payload,
        error: errorMsg
      })
    });

    lastStatus = success ? "success" : "failed";
    if (payload && payload.main && payload.main.found) {
      lastInfo = "MAIN world OK, combos=" + payload.main.total_combos + " min=" + payload.main.min_stock;
    } else if (payload) {
      lastInfo = "MAIN world FAIL: " + (payload.main && payload.main.reason);
    }
    await saveState();
    console.log("[POC-3A] done", lastStatus, lastInfo);
  } catch (e) {
    console.warn("[POC-3A] poll error", e && e.message);
  } finally {
    busy = false;
  }
}

setInterval(fetchPending, POLL_MS);
chrome.alarms.create('pollTask', { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(function(){ fetchPending(); });
fetchPending();
saveState();

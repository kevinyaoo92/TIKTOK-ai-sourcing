// POC-2: 拿任务 → 打开 1688 搜索页 → content script 读卡片 → 回传
const BACKEND = "http://127.0.0.1:8123";
const POLL_MS = 2000;
const SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm?keywords=";
const WAIT_MS = 8000;

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

// 打开 tab 并等待加载完成
function openAndWait(url) {
  return new Promise((resolve) => {
    chrome.tabs.create({ url: url, active: false }, (tab) => {
      const tabId = tab.id;
      const onUpdated = (tid, info) => {
        if (tid === tabId && info.status === "complete") {
          chrome.tabs.onUpdated.removeListener(onUpdated);
          setTimeout(() => resolve(tabId), 1500);
        }
      };
      chrome.tabs.onUpdated.addListener(onUpdated);
      // 兜底超时
      setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(onUpdated);
        resolve(tabId);
      }, WAIT_MS + 5000);
    });
  });
}

// 向 tab 注入脚本读取卡片
async function readCards(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId: tabId },
    func: () => {
      const els = document.querySelectorAll('[data-offer-grid-cell="true"]');
      const out = [];
      els.forEach((el, i) => {
        if (i >= 5) return;
        const linkEl = el.querySelector("a");
        const link = linkEl ? linkEl.href : "";
        const text = (el.innerText || "").replace(/\n+/g, " | ");
        // 回头率
        const m = text.match(/回头率\s*(\d+(?:\.\d+)?)\s*%/);
        const rate = m ? parseFloat(m[1]) : null;
        // 标题: 尝试多个选择器
        let title = "";
        const t1 = el.querySelector('[class*="title"]');
        if (t1) title = (t1.innerText || "").trim();
        if (!title) {
          const t2 = el.querySelector('a[title]');
          if (t2) title = t2.getAttribute("title") || "";
        }
        if (!title && linkEl) {
          title = (linkEl.innerText || "").trim().split("\n")[0];
        }
        out.push({
          offer_id: el.getAttribute("data-offer-expose-id") || "",
          title: title.slice(0, 200),
          link: link,
          rate: rate
        });
      });
      // 额外诊断：旧选择器是否命中
      const oldSel = document.querySelectorAll('[data-offer-grid-cell="true"]').length;
      return { total: oldSel, items: out, url: location.href };
    }
  });
  return results && results[0] && results[0].result ? results[0].result : { total: 0, items: [], url: "" };
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
    lastInfo = "task=" + t.task_id + " kw=" + t.keyword;
    await saveState({ lastTaskId: t.task_id });
    console.log("[POC-2] picked task", t.task_id, "keyword", t.keyword);

    let payload = null;
    let success = false;
    let errorMsg = "";
    let tabId = null;
    try {
      const url = SEARCH_URL + encodeURIComponent(t.keyword);
      tabId = await openAndWait(url);
      const cardData = await readCards(tabId);
      payload = cardData;
      success = cardData.total > 0 && cardData.items.length > 0;
      if (!success) errorMsg = "卡片数量=" + cardData.total + " items=" + cardData.items.length;
    } catch (e) {
      errorMsg = String(e && e.message || e).slice(0, 200);
    }

    // 关闭 tab
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
    lastInfo = "task=" + t.task_id + " cards=" + (payload ? payload.total : 0) + " items=" + (payload ? payload.items.length : 0);
    await saveState();
    console.log("[POC-2] done", lastStatus, lastInfo);
  } catch (e) {
    console.warn("[POC-2] poll error", e && e.message);
  } finally {
    busy = false;
  }
}

setInterval(fetchPending, POLL_MS);
fetchPending();
saveState();

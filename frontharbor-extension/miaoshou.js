// 妙手采集模块（独立执行层）
const MIAOSHOU_COLLECT_URL = "https://erp.91miaoshou.com/common_collect_box/index?fetchType=linkCopy";
const MIAOSHOU_LIST_URL = "https://erp.91miaoshou.com/common_collect_box/items?tabPaneName=all";

async function runMiaoshouProbe() {
  const tabId = await openAndWait(MIAOSHOU_COLLECT_URL);
  if (!tabId) return { error: "open_failed" };
  await new Promise(function(r) { setTimeout(r, 5000); });
  const res = await chrome.scripting.executeScript({
    target: { tabId: tabId },
    func: function() {
      var tas = [];
      document.querySelectorAll("textarea").forEach(function(t) {
        tas.push({ cls: t.className, ph: (t.placeholder || "").slice(0, 60) });
      });
      var btns = [];
      document.querySelectorAll("button").forEach(function(b) {
        var txt = (b.innerText || "").trim();
        if (txt) btns.push(txt.slice(0, 20));
      });
      return { url: location.href, title: document.title,
        textarea_count: tas.length, textareas: tas,
        button_count: btns.length, buttons: btns.slice(0, 30) };
    }
  });
  try { chrome.tabs.remove(tabId); } catch (e) {}
  return (res && res[0] && res[0].result) || { error: "no_result" };
}

async function runMiaoshouTask(task) {
  const offerId = task.offer_id;
  const productUrl = "https://detail.1688.com/offer/" + offerId + ".html";
  const collectUrl = MIAOSHOU_COLLECT_URL;
  const btnTextMatch = "采集并自动认领";
  const popupKeywords = ["已提交采集任务", "采集任务已提交", "采集任务，完成采集后"];
  const diag = { offer_id: offerId, product_url: productUrl, step: "opening" };

  // ===== 1. 打开采集页 =====
  const tabId = await openAndWait(collectUrl);
  if (!tabId) {
    diag.step = "open_failed";
    return { diag: diag, outcome: "failed", message: "打开妙手采集页失败" };
  }
  await new Promise(function(r) { setTimeout(r, 4000); });

  // ===== 2. 填 URL =====
  const fillRes = await chrome.scripting.executeScript({
    target: { tabId: tabId },
    func: function(url) {
      // 先看 URL 是否是采集页
      if (location.href.indexOf("common_collect_box/index") < 0) {
        return { ok: false, reason: "page_changed", current_url: location.href.slice(0, 150) };
      }
      const ta = document.querySelector("textarea.jx-textarea__inner");
      if (!ta) {
        const body = (document.body && document.body.innerText) || "";
        const hasLogin = !!document.querySelector('input[type="password"]')
          || body.indexOf("立即登录") >= 0
          || body.indexOf("忘记密码") >= 0;
        if (hasLogin) return { ok: false, reason: "need_login" };
        return { ok: false, reason: "page_changed", current_url: location.href.slice(0, 150) };
      }
      ta.focus();
      // 受控组件：用 prototype setter 触发框架响应
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(ta, url);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      ta.dispatchEvent(new Event("change", { bubbles: true }));
      ta.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true }));
      // 验证值已写入
      if (ta.value !== url) return { ok: false, reason: "value_not_set", got: ta.value };
      return { ok: true, value_len: ta.value.length };
    },
    args: [productUrl]
  });
  const r1 = fillRes && fillRes[0] && fillRes[0].result;
  if (!r1 || !r1.ok) {
    diag.step = "fill_failed";
    try { chrome.tabs.remove(tabId); } catch (e) {}
    if (r1 && r1.reason === "need_login") {
      return { diag: diag, outcome: "need_login", message: "请先登录妙手 ERP" };
    }
    return { diag: diag, outcome: "failed", message: "填入 URL 失败" };
  }

  // ===== 3. 点击"采集并自动认领" =====
  let clicked = false;
  for (let i = 0; i < 15; i++) {
    await new Promise(function(r) { setTimeout(r, 500); });
    const clickRes = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: function(btnText) {
        for (const b of document.querySelectorAll("button")) {
          const t = (b.innerText || "").trim();
          if (t.indexOf(btnText) >= 0) {
            if (!b.disabled && b.getAttribute("aria-disabled") !== "true") {
              b.click();
              return { clicked: true };
            }
            return { clicked: false, disabled: true };
          }
        }
        return { clicked: false, not_found: true };
      },
      args: [btnTextMatch]
    });
    const r2 = clickRes && clickRes[0] && clickRes[0].result;
    if (r2 && r2.clicked) { clicked = true; break; }
  }
  if (!clicked) {
    diag.step = "button_not_ready";
    try { chrome.tabs.remove(tabId); } catch (e) {}
    return { diag: diag, outcome: "failed", message: "采集按钮未启用" };
  }
  // ===== 4. 等弹窗出现（仅代表受理）=====
  diag.step = "waiting_confirm";
  var popupSeen = false;
  for (var w = 0; w < 15; w++) {
    await new Promise(function(r) { setTimeout(r, 1000); });
    const bodyRes = await chrome.scripting.executeScript({
      target: { tabId: tabId },
      func: function() { return (document.body && document.body.innerText) || ""; }
    });
    const txt = (bodyRes && bodyRes[0] && bodyRes[0].result) || "";
    var matched = false;
    for (var kk = 0; kk < popupKeywords.length; kk++) { if (txt.indexOf(popupKeywords[kk]) >= 0) { matched = true; break; } }
    if (matched) {
      popupSeen = true; break;
    }
    if (txt.indexOf("格式错误") >= 0 || txt.indexOf("链接无效") >= 0) {
      try { chrome.tabs.remove(tabId); } catch (e) {}
      diag.step = "page_failed";
      return { diag: diag, outcome: "failed", message: "妙手反馈链接错误" };
    }
  }
  try { chrome.tabs.remove(tabId); } catch (e) {}
  if (!popupSeen) {
    diag.step = "no_popup";
    return { diag: diag, outcome: "failed", message: "未捕获妙手确认弹窗" };
  }
  diag.popup_seen = true;

  // ===== 5. 一轮采集箱列表检查（仅作证据，不决定结果）=====
  diag.step = "checking_inbox_once";
  await new Promise(function(r) { setTimeout(r, 10000); });

  var listTabId = await openAndWait(MIAOSHOU_LIST_URL);
  if (listTabId) {
    await new Promise(function(r) { setTimeout(r, 8000); });
    const checkRes = await chrome.scripting.executeScript({
      target: { tabId: listTabId },
      func: function(oid) {
        var bodyTxt = (document.body && document.body.innerText) || "";
        var idx = bodyTxt.indexOf(oid);
        if (idx >= 0) return { found: true, preview: bodyTxt.slice(Math.max(0, idx - 40), idx + 40) };
        return { found: false, body_len: bodyTxt.length };
      },
      args: [offerId]
    });
    try { chrome.tabs.remove(listTabId); } catch (e) {}
    diag.inbox_check_once = checkRes && checkRes[0] && checkRes[0].result;
  } else {
    diag.inbox_check_once = { error: "list_open_failed" };
  }

  // ===== 6. 最终状态：提交成功（未验证入箱）=====
  diag.step = "submitted";
  return {
    diag: diag,
    outcome: "submitted",
    message: "妙手已受理采集请求（提交成功，入箱状态未验证）"
  };
}

/* ==========================================================================
   泰国 TikTok 市场机会雷达 · 第一界面
   流程：国家 -> 一级类目(单选) -> 二级类目(最多多选2个) -> AI 选品分析
   类目数据唯一来源：GET /api/categories（服务端从官方类目 TXT 读取，不硬编码）
   ========================================================================== */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var MAX_L2 = 2;                // 单次 AI 选品分析最多选择 2 个二级类目
  var LIMIT_HINT = "单次最多选择 2 个二级类目";

  var elCountry = $("country-select");
  var elLevel1 = $("level1-select");
  var elLevel2Box = $("level2-box");
  var elL2Empty = $("l2-empty");
  var elL2Hint = $("l2-count-hint");
  var elBtn = $("btn-analyze");
  var elHint = $("btn-hint");
  var elToast = $("toast");

  var CATALOG = null;            // /api/categories 返回的官方类目
  var selectedL2 = [];           // 已选官方 L2 名称（按官方顺序）
  var toastTimer = null;

  /* ---------------------------------------------------------- 工具 */
  function showToast(msg) {
    elToast.textContent = msg;
    elToast.hidden = false;
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { elToast.hidden = true; }, 2600);
  }

  function updateL2Hint() {
    if (selectedL2.length === 0) {
      elL2Hint.textContent = "";
    } else if (selectedL2.length >= MAX_L2) {
      elL2Hint.textContent = "已选择 " + selectedL2.length + " 个二级类目（" + LIMIT_HINT + "）";
    } else {
      elL2Hint.textContent = "已选择 " + selectedL2.length + " 个二级类目";
    }
  }

  function updateBtnState() {
    var ready = selectedL2.length > 0;
    elBtn.disabled = !ready;
    if (elLevel1.value === "") {
      elHint.textContent = "请先选择一级类目";
    } else if (!ready) {
      elHint.textContent = "请选择至少一个二级类目";
    } else {
      elHint.textContent = "";
    }
  }

  /* ---------------------------------------------------------- 级联数据 */
  function fillLevel1() {
    if (!CATALOG) return;
    var frag = document.createDocumentFragment();
    var opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "请选择一级类目";
    frag.appendChild(opt);
    CATALOG.level1.forEach(function (item) {
      var o = document.createElement("option");
      o.value = item.name;
      o.textContent = item.name;
      frag.appendChild(o);
    });
    elLevel1.innerHTML = "";
    elLevel1.appendChild(frag);
    elLevel1.disabled = false;
  }

  function currentL1() {
    if (!CATALOG) return null;
    for (var i = 0; i < CATALOG.level1.length; i++) {
      if (CATALOG.level1[i].name === elLevel1.value) return CATALOG.level1[i];
    }
    return null;
  }

  /* ---------------------------------------------------------- L2 多选 */
  function renderL2() {
    selectedL2 = [];
    var l1 = currentL1();
    elL2Hint.textContent = "";
    elL2Empty.style.display = "none";

    if (!l1) {
      elL2Empty.textContent = "请先选择一级类目";
      elL2Empty.style.display = "block";
      elLevel2Box.innerHTML = "";
      updateBtnState();
      return;
    }

    var frag = document.createDocumentFragment();
    l1.level2.forEach(function (name) {
      var label = document.createElement("label");
      label.className = "l2-item";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = name;
      var span = document.createElement("span");
      span.className = "l2-name";
      span.textContent = name;
      label.appendChild(cb);
      label.appendChild(span);
      label.addEventListener("click", function (ev) {
        // disabled 行不可点击：不翻转、不改变选中态
        if (cb.disabled) {
          ev.preventDefault();
          return;
        }
        if (ev.target !== cb) {
          // 点击行任意位置 = 点击 checkbox
          cb.checked = !cb.checked;
        }
        toggleL2(name, cb.checked);
        if (ev.target !== cb) ev.preventDefault();
      });
      frag.appendChild(label);
    });

    elLevel2Box.innerHTML = "";
    elLevel2Box.appendChild(frag);
    refreshL2Disabled();
    updateBtnState();
  }

  /* 依据当前选中数量刷新未选 L2 的 disabled 状态：
     达到上限时，未选中的 L2 全部置灰不可选；取消后自动恢复。 */
  function refreshL2Disabled() {
    var atLimit = selectedL2.length >= MAX_L2;
    var items = elLevel2Box.querySelectorAll(".l2-item");
    for (var i = 0; i < items.length; i++) {
      var cb = items[i].querySelector("input[type=checkbox]");
      var isSelected = selectedL2.indexOf(cb.value) !== -1;
      var disabled = atLimit && !isSelected;
      cb.disabled = disabled;
      items[i].classList.toggle("disabled", disabled);
      // 已选中的行保持浅蓝高亮
      items[i].classList.toggle("selected", isSelected);
    }
  }

  function toggleL2(name, checked) {
    // 上限防御：已选满 2 个时拒绝第 3 个（disabled 之外的兜底）
    if (checked && selectedL2.length >= MAX_L2 && selectedL2.indexOf(name) === -1) {
      var items = elLevel2Box.querySelectorAll(".l2-item");
      for (var i = 0; i < items.length; i++) {
        var c = items[i].querySelector("input[type=checkbox]");
        if (c.value === name) { c.checked = false; break; }
      }
      showToast(LIMIT_HINT);
      return;
    }
    var idx = selectedL2.indexOf(name);
    if (checked && idx === -1) selectedL2.push(name);
    if (!checked && idx !== -1) selectedL2.splice(idx, 1);
    // 同步行的选中态
    var items = elLevel2Box.querySelectorAll(".l2-item");
    for (var i = 0; i < items.length; i++) {
      var c = items[i].querySelector("input[type=checkbox]");
      if (c.value === name) {
        items[i].classList.toggle("selected", checked);
      }
    }
    refreshL2Disabled();
    updateL2Hint();
    updateBtnState();
  }

  /* ---------------------------------------------------------- 事件 */
  elCountry.addEventListener("change", function () {
    // 国家目前仅泰国；选择后允许选择一级类目
    elLevel1.disabled = false;
  });

  elLevel1.addEventListener("change", function () {
    renderL2();
  });

  elBtn.addEventListener("click", function () {
    if (selectedL2.length === 0) {
      showToast("请选择至少一个二级类目");
      return;
    }
    var selection = {
      country: elCountry.value,
      level1: elLevel1.value,
      level2: selectedL2.slice()
    };
    try {
      window.localStorage.setItem("analysis-selection", JSON.stringify(selection));
    } catch (e) { /* 隐私模式等场景下忽略 */ }
    // 跳转第二界面（机会分析结果页）：URL 参数 + localStorage 双通道传递
    var params = new URLSearchParams();
    params.set("country", selection.country);
    params.set("level1", selection.level1);
    params.set("level2", selection.level2.join(","));
    window.location.href = "/static/opportunity.html?" + params.toString();
  });

  /* ---------------------------------------------------------- 占位页 */
  function showAnalysisPage() {
    var selection = { country: "—", level1: "—", level2: [] };
    try {
      var raw = window.localStorage.getItem("analysis-selection");
      if (raw) selection = JSON.parse(raw);
    } catch (e) { /* ignore */ }

    $("view-selection").hidden = true;
    $("view-analysis").hidden = false;
    $("sum-country").textContent = selection.country || "—";
    $("sum-level1").textContent = selection.level1 || "—";
    var l2 = (selection.level2 || []);
    $("sum-level2").textContent = l2.length ? l2.join("、") : "—";
    window.scrollTo(0, 0);
  }

  function showSelectionPage() {
    $("view-selection").hidden = false;
    $("view-analysis").hidden = true;
    window.scrollTo(0, 0);
  }

  function router() {
    var hash = window.location.hash || "#/";
    if (hash.indexOf("#/analysis") === 0) {
      showAnalysisPage();
    } else {
      showSelectionPage();
    }
  }

  /* ---------------------------------------------------------- 初始化 */
  function init() {
    window.addEventListener("hashchange", router);
    router();

    fetch("/api/categories", { cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then(function (data) {
        CATALOG = data;
        if (!CATALOG || !CATALOG.level1 || CATALOG.level1.length === 0) {
          throw new Error("类目数据为空");
        }
        fillLevel1();
        updateBtnState();
      })
      .catch(function (err) {
        showToast("类目数据加载失败：" + err.message);
        elLevel1.disabled = true;
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

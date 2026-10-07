function render() {
  chrome.storage.local.get(["lastStatus", "lastInfo", "updatedAt"], function(d) {
    document.getElementById("v-status").textContent = d.lastStatus || "idle";
    document.getElementById("v-info").textContent = d.lastInfo || "—";
    document.getElementById("v-updated").textContent = d.updatedAt || "—";
  });
}
render();
setInterval(render, 1000);

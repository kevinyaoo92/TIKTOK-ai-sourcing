function render() {
  chrome.storage.local.get(["lastTaskId", "lastStatus", "updatedAt"], function(d) {
    document.getElementById("v-status").textContent = d.lastStatus || "idle";
    document.getElementById("v-task-id").textContent = d.lastTaskId || "—";
    document.getElementById("v-updated").textContent = d.updatedAt || "—";
  });
}
render();
setInterval(render, 1000);

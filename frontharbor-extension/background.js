// POC-1A: 每 2 秒轮询后端，取 pending 任务，立即回报成功。
const BACKEND = "http://127.0.0.1:8123";
const POLL_MS = 2000;

let lastTaskId = null;
let lastStatus = "idle";

async function saveState() {
  try {
    await chrome.storage.local.set({
      lastTaskId: lastTaskId,
      lastStatus: lastStatus,
      updatedAt: new Date().toLocaleString()
    });
  } catch (e) {}
}

async function fetchPending() {
  try {
    const r = await fetch(BACKEND + "/api/poc_task/pending");
    const d = await r.json();
    if (!d || d.status !== "ok" || !d.task) return;
    const t = d.task;
    if (t.task_id === lastTaskId) return;
    lastTaskId = t.task_id;
    lastStatus = "running";
    await saveState();
    await fetch(BACKEND + "/api/poc_task/complete", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({task_id: t.task_id, success: true})
    });
    lastStatus = "success";
    await saveState();
    console.log("[POC-1A] task done:", t.task_id);
  } catch (e) {
    console.warn("[POC-1A] poll error:", e.message);
  }
}

setInterval(fetchPending, POLL_MS);
fetchPending();
saveState();

# -*- coding: utf-8 -*-
"""产品前端 Web 服务（零第三方依赖：标准库 http.server + sqlite3）。

- 静态页面: frontend/static/*
- 数据 API : /api/*（每次请求独立连接）
- 类目来源 : app.category_loader.load_catalog()（官方类目 TXT，30 L1 / 215 L2 原样返回）
- 旧业务表 : opportunity.db 只读（保留兼容旧接口 /api/opportunities）
- 分析链路 : POST /api/analyze → app.analysis.analyze_service（读 tiktok_market.db
             真实采集数据 → Stage0-5 确定性评分 → DeepSeek 语义 → 写 opportunity_analysis/ai_analysis_log）

流程: 国家 -> 一级官方类目(单选) -> 二级官方类目(多选) -> 「AI 选品分析」-> /api/analyze -> 第二界面

启动: python frontend/server.py [--port 8123]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent          # frontend/
STATIC = ROOT / "static"
DB = (ROOT.parent / "database" / "opportunity.db").resolve()
MARKET_DB = (ROOT.parent / "data" / "tiktok_market.db").resolve()

sys.stdout.reconfigure(encoding="utf-8")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/javascript", ".js")

# 类目唯一来源：官方类目 TXT -> app.category_loader.load_catalog()
PROJECT_ROOT = ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from app.category_loader import load_catalog  # noqa: E402
from app.analysis import analyze_service  # noqa: E402
from app.analysis import supplier_finder  # noqa: E402
import threading  # noqa: E402
import uuid  # noqa: E402
import time  # noqa: E402

# ========== 找货任务管理 ==========
_FINDER_TASKS = {}       # task_id -> dict
_FINDER_LOCK = threading.Lock()
_FINDER_TTL = 1800       # 30 分钟后清理


def _run_finder_task(task_id, keyword):
    def on_progress(pct, phase, extra):
        with _FINDER_LOCK:
            if task_id in _FINDER_TASKS:
                _FINDER_TASKS[task_id]["progress"] = pct
                _FINDER_TASKS[task_id]["phase"] = phase
                _FINDER_TASKS[task_id].update(extra or {})
    try:
        result = supplier_finder.find_suppliers(keyword, on_progress=on_progress)
        with _FINDER_LOCK:
            if task_id in _FINDER_TASKS:
                _FINDER_TASKS[task_id].update({
                    "status": "done",
                    "progress": 100,
                    "phase": "匹配完成",
                    "result": result,
                    "finished_at": time.time(),
                })
    except Exception as e:
        with _FINDER_LOCK:
            if task_id in _FINDER_TASKS:
                _FINDER_TASKS[task_id].update({
                    "status": "error",
                    "progress": 100,
                    "phase": "出错",
                    "error": str(e)[:200],
                    "finished_at": time.time(),
                })


def api_find_suppliers(body: dict) -> dict:
    keyword = (body.get("keyword") or "").strip()
    if not keyword:
        return {"status": "error", "error": "missing_keyword", "message": "缺少 keyword"}
    task_id = str(uuid.uuid4())
    with _FINDER_LOCK:
        _FINDER_TASKS[task_id] = {
            "status": "running",
            "progress": 0,
            "phase": "任务已创建",
            "keyword": keyword,
            "created_at": time.time(),
        }
    t = threading.Thread(target=_run_finder_task, args=(task_id, keyword), daemon=True)
    t.start()
    return {"status": "ok", "task_id": task_id}


def api_find_suppliers_status(task_id: str) -> dict:
    with _FINDER_LOCK:
        task = _FINDER_TASKS.get(task_id)
    if not task:
        return {"status": "error", "error": "not_found", "message": "任务不存在或已过期"}
    resp = {
        "status": task["status"],
        "progress": task["progress"],
        "phase": task["phase"],
        "keyword": task.get("keyword", ""),
    }
    if task["status"] == "done":
        resp["result"] = task.get("result", {})
    elif task["status"] == "error":
        resp["error"] = task.get("error", "")
    return resp


def _cleanup_finder_tasks():
    now = time.time()
    with _FINDER_LOCK:
        expired = [tid for tid, t in _FINDER_TASKS.items()
                   if now - t.get("created_at", now) > _FINDER_TTL]
        for tid in expired:
            del _FINDER_TASKS[tid]



# direction_id -> 官方二级类目（时尚配件，基于 category_mapping_audit 确认映射）
DIRECTION_TO_LEVEL2 = {
    "hair": "发饰",
    "glasses": "眼镜",
    "necklace": "平价饰品",
    "earring": "平价饰品",
    "hand": "平价饰品",
    "brooch": "平价饰品",
    "watch": "手表与配件",
    "belt": "服饰配件",
    "headwear": "服饰配件",
    "headwrap": "服饰配件",
    "keychain": "服饰配件",
}


def ro_conn() -> sqlite3.Connection:
    conn = sqlite3.connect("file:" + str(DB).replace("\\", "/") + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_opps(conn, keys: list | None = None) -> dict:
    """v2 池行(仅非 EXCLUDE) join analysis，按 canonical_product_key 索引。"""
    sql = ("SELECT v.*, a.evidence_state, a.reason_tags, a.risk_tags, "
           "a.product_type, a.attributes, a.style, a.target_group, a.model, "
           "a.cluster_id, a.direction_id AS analysis_direction "
           "FROM product_opportunities_v2 v "
           "LEFT JOIN opportunity_analysis a "
           "  ON a.canonical_product_key = v.canonical_product_key "
           "WHERE v.category_status != 'EXCLUDE'")
    args: list = []
    if keys:
        ph = ",".join("?" * len(keys))
        sql += f" AND v.canonical_product_key IN ({ph})"
        args = list(keys)
    rows = conn.execute(sql + " ORDER BY v.score DESC", args).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["source_keywords"] = json.loads(d["source_keywords"] or "[]")
        d["weekly_evidence"] = json.loads(d["weekly_evidence"] or "[]")
        d["monthly_evidence"] = json.loads(d["monthly_evidence"] or "[]")
        for col in ("reason_tags", "risk_tags"):
            d[col] = json.loads(d[col] or "[]")
        for col in ("attributes", "style", "target_group"):
            d[col] = json.loads(d[col] or "[]")
        # 官方二级类目：仅使用 DB 中的真实 level_2_category。
        # 一级行(db_l2=NULL)不应用 DIRECTION_TO_LEVEL2 映射，保持 None，
        # 避免一级专属机会混入二级类目结果。
        db_l2 = d.get("level_2_category")
        d["level_2_category"] = db_l2
        # 同一 canonical_product_key 同时存在一级(db_l2=NULL)和二级(db_l2!=NULL)时，
        # 优先保留二级；一级仅在没有对应二级记录时保留。
        key = d["canonical_product_key"]
        existing = out.get(key)
        if existing is None:
            out[key] = d
        elif existing.get("level_2_category") is None and db_l2:
            out[key] = d  # 真二级覆盖一级
    return out


def _meta(conn) -> dict:
    pool_run = conn.execute("SELECT MAX(run_id) r FROM product_opportunities_v2").fetchone()["r"]
    ana_run = conn.execute("SELECT MAX(run_id) r FROM opportunity_directions").fetchone()["r"]
    r = conn.execute("SELECT MIN(first_seen) f, MAX(last_seen) l FROM product_opportunities_v2").fetchone()
    return {
        "market": "泰国 TikTok（TH）",
        "keyword_rows": conn.execute("SELECT COUNT(*) c FROM keyword_data").fetchone()["c"],
        "pool_rows": conn.execute("SELECT COUNT(*) c FROM product_opportunities_v2").fetchone()["c"],
        "visible_opportunities": conn.execute(
            "SELECT COUNT(*) c FROM product_opportunities_v2 WHERE category_status != 'EXCLUDE'").fetchone()["c"],
        "period": f"{r['f']} ~ {r['l']}" if r["f"] else "—",
        "pool_run": pool_run, "analysis_run": ana_run,
    }


# ---------------------------------------------------------------- API ----
def api_categories(market: str = "TH") -> dict:
    """第一界面类目接口：只读官方类目（app.category_loader.load_catalog()）。

    返回 {market, country, source, level1:[{name, level2:[...]}]}。
    官方 L1/L2 名称原样返回，供 国家 -> L1 -> L2 级联选择使用；不读库、不查机会数据。
    """
    cat = load_catalog()
    return {
        "market": market,
        "country": "泰国",
        "source": str(cat.source),
        "level1": [{"name": e.level1, "level2": list(e.level2)} for e in cat.entries],
    }


def api_opportunities(market: str, level_1: str, level_2_list: list[str]) -> dict:
    """按二级类目分组返回机会（各二级独立排序，不混合）。"""
    conn = ro_conn()
    try:
        opps = _fetch_opps(conn)
        groups: dict[str, list] = {}
        for l2 in level_2_list:
            groups[l2] = []
        for o in opps.values():
            if (o.get("level_1_category") or "") != level_1:
                continue
            l2 = o.get("level_2_category")
            if l2 in groups:
                groups[l2].append(o)
        result_groups = []
        for l2 in level_2_list:
            items = sorted(groups[l2], key=lambda x: x.get("score") or 0, reverse=True)
            result_groups.append({
                "level_2": l2,
                "opportunity_count": len(items),
                "opportunities": [{
                    "key": o["canonical_product_key"],
                    "name": o["canonical_product_name"],
                    "score": o["score"],
                    "category_status": o["category_status"],
                    "evidence_state": o["evidence_state"],
                    "demand_score": o["demand_score"],
                    "purchase_intent_score": o["purchase_intent_score"],
                    "opportunity_gap": o["opportunity_gap"],
                    "source_keywords": o["source_keywords"],
                } for o in items],
            })
        return {"market": market, "level_1": level_1, "level_2": level_2_list,
                "groups": result_groups, "meta": _meta(conn)}
    finally:
        conn.close()


def api_opportunity(key: str) -> dict:
    conn = ro_conn()
    try:
        row = conn.execute(
            "SELECT canonical_product_key FROM product_opportunities_v2 WHERE canonical_product_key=?",
            (key,)).fetchone()
        if not row:
            return {"error": "unknown opportunity"}, 404
        opps = _fetch_opps(conn, [key])
        if key not in opps:
            return {"error": "not available"}, 404
        o = opps[key]
        return ({"opportunity": o,
                "level_1_category": o.get("level_1_category"),
                "level_2_category": o.get("level_2_category"),
                "meta": _meta(conn)}, 200)
    finally:
        conn.close()


def api_analyze(body: dict) -> dict:
    """POST /api/analyze：真实数据分析链路（tiktok_market.db + analyze_service）。

    请求体: {country, level1, level2, period, top_n?}
    返回  : analyze_service.analyze() 完整结果（机会含 AI 字段）。
    若 tiktok_market.db 不存在或该 L2 无数据，返回明确错误（不用 mock）。
    """
    country = str(body.get("country") or "泰国").strip()
    level1 = str(body.get("level1") or "").strip()
    level2 = str(body.get("level2") or "").strip()
    period = str(body.get("period") or "").strip()
    if not level1 or not level2:
        return {"status": "error", "error": "missing_params",
                "message": "缺少 level1 / level2 / period 参数"}
    try:
        top_n = int(body.get("top_n") or 20)
    except (TypeError, ValueError):
        top_n = 20
    if top_n <= 0 or top_n > 50:
        top_n = 20
    if not MARKET_DB.exists():
        return {"status": "error", "error": "no_db",
                "message": f"数据库不存在: {MARKET_DB.name}，请先完成采集入库。"}
    print(f"[API/ANALYZE] country={country!r} level1={level1!r} level2={level2!r} period={period!r}", flush=True)
    _r = analyze_service.analyze(country, level1, level2, period,
                                   top_n=top_n, db_path=MARKET_DB)
    _cached = _r.get("summary", {}).get("cached") if isinstance(_r, dict) else None
    _n = len(_r.get("opportunities", [])) if isinstance(_r, dict) else 0
    print(f"[API/ANALYZE] -> status={_r.get('status') if isinstance(_r, dict) else '?'} cached={_cached} n={_n}", flush=True)
    return _r


# ---------------------------------------------------------------- HTTP ----
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[web] %s\n" % (fmt % args))

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _static(self, rel: str):
        p = (STATIC / rel.lstrip("/")).resolve()
        if not str(p).startswith(str(STATIC.resolve())) or not p.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        self._send(200, p.read_bytes(), ctype)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        try:
            if path == "/":
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/categories":
                market = qs.get("market", ["TH"])[0]
                return self._json(api_categories(market))
            if path == "/api/opportunities":
                market = qs.get("market", ["TH"])[0]
                level_1 = qs.get("level_1", [""])[0]
                level_2_raw = qs.get("level_2", [""])[0]
                level_2_list = [x for x in level_2_raw.split(",") if x]
                if not level_1 or not level_2_list:
                    return self._json({"error": "missing level_1 or level_2"}, 400)
                return self._json(api_opportunities(market, level_1, level_2_list))
            if path.startswith("/api/opportunity/"):
                obj, code = api_opportunity(path.rsplit("/", 1)[1])
                return self._json(obj, code)
            if path == "/api/find_suppliers/status":
                task_id = qs.get("task_id", [""])[0]
                return self._json(api_find_suppliers_status(task_id))
            if path == "/api/active_period":
                con = sqlite3.connect(str(MARKET_DB))
                cur = con.cursor()
                row = cur.execute("SELECT value FROM app_config WHERE key='active_period'").fetchone()
                con.close()
                return self._json({"period": row[0] if row else ""})
            if path == "/api/miaoshou_check_login":
                from app.analysis import miaoshou_collector
                return self._json(miaoshou_collector.check_login())
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            try:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            except Exception:
                pass

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/api/analyze":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    return self._json({"status": "error", "error": "bad_json",
                                       "message": "请求体不是合法 JSON"}, 400)
                return self._json(api_analyze(body))
            if path == "/api/find_suppliers":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    return self._json({"status": "error", "error": "bad_json"}, 400)
                _cleanup_finder_tasks()
                return self._json(api_find_suppliers(body))
            if path == "/api/miaoshou_collect":
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    return self._json({"status": "error", "error": "bad_json"}, 400)
                offer_id = (body.get("offer_id") or "").strip()
                if not offer_id:
                    return self._json({"status": "failed", "message": "missing offer_id"}, 400)
                from app.analysis import miaoshou_collector
                return self._json(miaoshou_collector.collect_to_miaoshou(offer_id))
            if path == "/api/miaoshou_check_login":
                from app.analysis import miaoshou_collector
                return self._json(miaoshou_collector.check_login())
            if path == "/api/miaoshou_open_login":
                from app.analysis import miaoshou_collector
                return self._json(miaoshou_collector.open_login_page())
            return self._send(404, b"not found", "text/plain; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            try:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            except Exception:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="产品前端只读服务")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if not DB.exists():
        print(f"数据库不存在: {DB}")
        return 1
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"产品前端已启动: http://{args.host}:{args.port}  (DB: {DB.name}, 只读)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

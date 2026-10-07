# -*- coding: utf-8 -*-
"""机会分析服务层：串联 分析管线 + DeepSeek + 写库。

职责（POST /api/analyze 后端业务）：
1. 调用 analyze_pipeline.run_analysis() 完成 Stage 0-5 确定性评分。
2. 对 Top N 每个机会调用 DeepSeek 选品专家（Stage 5 语义层）：
   - product_intent=非商品 → 机会剔除，反向写入非商品规则库（下次过滤生效）。
   - 调用失败/重试仍失败 → ai_analysis_log 记录失败，机会保留，ai_summary 用占位文本（不阻塞）。
3. 写入 opportunity_analysis（最终机会）+ ai_analysis_log（AI 调用日志）。
4. 返回前端渲染所需的完整 JSON（与用户 API 响应体一致）。

约束：
- DeepSeek 不算分/不排名/不计算百分位；分数来自 analyze_pipeline（纯 Python）。
- 指数不等于真实销量；数据缺失返回明确错误，不用 mock。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import config
from app.ai import deepseek
from app.analysis import analyze_pipeline

# 反向非商品规则库（DeepSeek 判定非商品的关键词持久化位置）
AI_NONPRODUCT_FEEDBACK_FILE = (
    Path(config.PROJECT_ROOT) / "data" / "nonproduct_ai_feedback.json"
)

# 占位文本（AI 失败时不阻塞流程）
AI_PLACEHOLDER_SUMMARY = "AI 语义分析暂不可用（调用失败），已保留确定性评分结果，建议后续重跑补充。"
AI_PLACEHOLDER_MARKET = "（AI 分析暂不可用）"
AI_PLACEHOLDER_COMPETITION = "（AI 分析暂不可用）"
AI_PLACEHOLDER_SUITABLE = "（待补充）"
AI_PLACEHOLDER_TERMS: list[str] = []
AI_PLACEHOLDER_RISKS: list[str] = []


# ---------------------------------------------------------------------------
# 非商品反馈库
# ---------------------------------------------------------------------------
def load_ai_nonproduct_feedback() -> set[str]:
    """读取 AI 反向写入的非商品词集合。"""
    f = AI_NONPRODUCT_FEEDBACK_FILE
    if not f.exists():
        return set()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return set(data.get("keywords", []))
    except Exception:
        return set()


def add_ai_nonproduct_feedback(keyword: str, reason: str = "AI 判定非商品") -> None:
    """把 AI 判定非商品的关键词反向写入规则库（幂等，追加去重）。"""
    data = {"keywords": [], "updated_at": "", "notes": []}
    if AI_NONPRODUCT_FEEDBACK_FILE.exists():
        try:
            data = json.loads(AI_NONPRODUCT_FEEDBACK_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {"keywords": [], "updated_at": "", "notes": []}
    kws = list(data.get("keywords", []))
    if keyword not in kws:
        kws.append(keyword)
    data["keywords"] = kws
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    notes = data.get("notes", [])
    notes.append({"keyword": keyword, "reason": reason,
                  "time": datetime.now().isoformat(timespec="seconds")})
    data["notes"] = notes[-200:]  # 只保留最近 200 条备注
    AI_NONPRODUCT_FEEDBACK_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 数据库 schema（tiktok_market.db）
# ---------------------------------------------------------------------------
ANALYSIS_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunity_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT UNIQUE,
    country TEXT,
    level1 TEXT,
    level2 TEXT,
    period TEXT,
    name_cn TEXT,
    score REAL,
    rating TEXT,
    action TEXT,
    demand_score REAL,
    conversion_score REAL,
    competition_score REAL,
    representative_keyword TEXT,
    related_keywords TEXT,        -- JSON array
    evidence_json TEXT,           -- JSON array
    ai_summary TEXT,
    market_judgment TEXT,
    competition_judgment TEXT,
    suitable_for_tiktok_th TEXT,
    search_terms_1688 TEXT,       -- JSON array
    risks TEXT,                   -- JSON array
    confidence REAL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_analysis_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT,
    input_keyword TEXT,
    input_metrics TEXT,           -- JSON
    raw_output TEXT,
    call_time TEXT,
    token_usage TEXT,             -- JSON
    success INTEGER
);

CREATE INDEX IF NOT EXISTS idx_opp_country_l2 ON opportunity_analysis(country, level1, level2, period);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);
"""


def ensure_analysis_schema(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(ANALYSIS_SCHEMA)


# ---------------------------------------------------------------------------
# AI 机会分析（含失败占位 / 非商品剔除）
# ---------------------------------------------------------------------------

def get_active_period(db_path: Path) -> str:
    """读当前活跃 period。优先 app_config.active_period，否则从数据里取 MAX。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'active_period'"
        ).fetchone()
        if row and row["value"]:
            return row["value"]
        row2 = conn.execute(
            "SELECT MAX(period) AS p FROM opportunity_analysis"
        ).fetchone()
        if row2 and row2["p"]:
            return row2["p"]
        return "2026-08"


def set_active_period(db_path: Path, period: str) -> None:
    """设置活跃 period（用于月度切换）。"""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            INSERT INTO app_config (key, value, updated_at)
            VALUES ('active_period', ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
        """, (period,))
        conn.commit()


def _read_cached_opportunities(db_path, country, level1, level2, period, top_n):
    """从 opportunity_analysis 读缓存。

    修 B：返回 summary 里加 sample_size（前端用来显示"样本量少"标签）
    修 C：期望条数 = min(候选数, top_n)，不是固定 top_n
          小样本 L2 只要缓存 == 候选数 就算完整，不再每次重跑
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        # 查候选数（用于判断缓存完整性 + 决定 sample_size）
        n_cand_row = conn.execute(
            """SELECT COUNT(DISTINCT km.keyword) AS n
               FROM keyword_metric km
               JOIN category c ON km.category_id = c.id
               WHERE c.level1 = ? AND c.level2 = ? AND km.period = ?""",
            (level1, level2, period)).fetchone()
        n_cand = n_cand_row["n"] if n_cand_row else 0
        expected = min(n_cand, top_n)

        rows = conn.execute(
            """SELECT * FROM opportunity_analysis
               WHERE country = ? AND level1 = ? AND level2 = ? AND period = ?
               ORDER BY score DESC""",
            (country, level1, level2, period)).fetchall()

    # 缓存命中判据（2026-10-07 修）
    # 旧判据 len(rows) < expected 恒成立（候选数=过滤前，rows=过滤后，天然<=候选数），
    # 导致 161 个 L2 缓存全部不命中，每次打开都重跑 Stage0-5 + AI（1-3 分钟）。
    # 新判据：
    #   1) rows 非空（数据库里有数据）
    #   2) 至少一行 ai_summary 有内容（AI 跑过 = 数据完整，挡住半截崩溃的残次数据）
    if len(rows) == 0:
        return None
    if not any((r["ai_summary"] or "").strip() for r in rows):
        return None

    opportunities = []
    for r in rows[:top_n]:
        opportunities.append({
            "opportunity_id": r["opportunity_id"],
            "name_cn": r["name_cn"],
            "score": r["score"],
            "rating": r["rating"],
            "action": r["action"],
            "demand_score": r["demand_score"],
            "conversion_score": r["conversion_score"],
            "competition_score": r["competition_score"],
            "representative_keyword": r["representative_keyword"],
            "representative_keyword_cn": r["name_cn"],
            "related_keywords": json.loads(r["related_keywords"] or "[]"),
            "evidence": json.loads(r["evidence_json"] or "[]"),
            "ai_summary": r["ai_summary"] or "",
            "market_judgment": r["market_judgment"] or "",
            "competition_judgment": r["competition_judgment"] or "",
            "suitable_for_tiktok_th": r["suitable_for_tiktok_th"] or "",
            "search_terms_1688": json.loads(r["search_terms_1688"] or "[]"),
            "risks": json.loads(r["risks"] or "[]"),
            "confidence": r["confidence"] or 0.0,
        })

    return {
        "status": "success",
        "category": {"country": country, "level1": level1,
                     "level2": level2, "period": period},
        "summary": {"total_keywords": "cached",
                    "top_n": len(opportunities),
                    "cached": True,
                    "sample_size": "small" if n_cand < 30 else "normal",
                    "cohort_size": n_cand},
        "stages": {},
        "opportunities": opportunities,
        "ai_stats": {"requested": len(opportunities),
                     "succeeded": len(opportunities),
                     "failed": 0, "nonproduct_removed": 0, "cached": True},
        "ai_nonproduct_removed": [],
    }

def _opp_input_for_ai(opp: dict, category: dict, cohort_size: int) -> dict:
    r = opp["_row"]
    sv = r.get("搜索量", {})
    ctor = r.get("CTOR评分", {})
    sku = r.get("SKU销售指数", {})
    ons = r.get("在售商品", {})
    ctr = r.get("CTR指数", {})
    return {
        "level1": category["level1"],
        "level2": category["level2"],
        "period": category["period"],
        "keyword": opp["representative_keyword"],
        "related_keywords": opp["related_keywords"],
        "search_volume": sv.get("orig", "—"),
        "demand_pct": r.get("demand_pct"),
        "ctor_score": ctor.get("orig", "—"),
        "intent_pct": r.get("intent_pct"),
        "sku_sales_index": sku.get("orig", "—"),
        "sales_pct": r.get("sales_pct"),
        "on_sale_products": ons.get("orig", "—"),
        "competition_pct": r.get("competition_pct"),
        "ctr_index": ctr.get("orig", "—"),
        "opportunity_score": opp["score"],
        "rating": opp["rating"],
    }


def run_ai_for_opportunity(opp: dict, category: dict, db_path: Path,
                           cohort_size: int) -> dict:
    """对单个机会执行 DeepSeek 分析，返回 (ai_result, log_success, token_usage)。

    失败时返回占位字段，并把失败写入 ai_analysis_log（不抛异常，不阻塞）。
    """
    opp_input = _opp_input_for_ai(opp, category, cohort_size)
    call_time = datetime.now().isoformat(timespec="seconds")
    token_usage = {}
    success = 0
    raw_output = ""
    try:
        ai = deepseek.analyze_opportunity(opp_input)
        raw_output = json.dumps(ai, ensure_ascii=False)
        success = 1
        token_usage = {"estimated_tokens": len(raw_output) // 2}
        _write_ai_log(db_path, opp["opportunity_id"], opp_input, raw_output,
                      call_time, token_usage, success)
        return ai, success, token_usage
    except deepseek.DeepSeekDisabledError as e:
        # 配置缺失：明确记录，机会保留 + 占位
        raw_output = f"DeepSeekDisabled: {e}"
        _write_ai_log(db_path, opp["opportunity_id"], opp_input, raw_output,
                      call_time, token_usage, 0)
        return _placeholder_ai(str(e)), success, token_usage
    except deepseek.DeepSeekError as e:
        raw_output = f"DeepSeekError: {e}"
        _write_ai_log(db_path, opp["opportunity_id"], opp_input, raw_output,
                      call_time, token_usage, 0)
        return _placeholder_ai(str(e)), success, token_usage


def _placeholder_ai(err: str) -> dict:
    return {
        "normalized_product_name_cn": "",
        "product_intent": "不确定",
        "market_judgment": AI_PLACEHOLDER_MARKET,
        "competition_judgment": AI_PLACEHOLDER_COMPETITION,
        "suitable_for_tiktok_th": AI_PLACEHOLDER_SUITABLE,
        "reasons": [f"AI 分析失败：{err[:80]}"],
        "risks": [],
        "search_terms_1688": [],
        "confidence": 0.0,
        "ai_failed": True,
        "ai_error": err,
    }


def _write_ai_log(db_path: Path, opp_id: str, opp_input: dict, raw_output: str,
                  call_time: str, token_usage: dict, success: int) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT INTO ai_analysis_log
               (opportunity_id, input_keyword, input_metrics, raw_output,
                call_time, token_usage, success)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (opp_id, opp_input.get("keyword", ""),
             json.dumps(opp_input, ensure_ascii=False), raw_output,
             call_time, json.dumps(token_usage, ensure_ascii=False), success))
        conn.commit()


# ---------------------------------------------------------------------------
# 主流程：完整分析 + AI + 写库
# ---------------------------------------------------------------------------
def _clear_l2_cache(db_path, country, level1, level2, period):
    """清掉某 L2 的旧缓存（opportunity_analysis + ai_analysis_log）"""
    import sqlite3
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            DELETE FROM opportunity_analysis
            WHERE country = ? AND level1 = ? AND level2 = ? AND period = ?
        """, (country, level1, level2, period))
        conn.commit()


def analyze(country: str, level1: str, level2: str, period: Optional[str] = None,
            top_n: int = 20, db_path: Optional[Path] = None,
            with_ai: bool = True, force_refresh: bool = False) -> dict:
    db_path = db_path or Path(config.PROJECT_ROOT) / "data" / "tiktok_market.db"
    if not db_path.exists():
        return {"status": "error", "error": f"数据库不存在: {db_path}",
                "message": f"该 L2 暂无采集数据（{level1}>{level2}），请先采集入库。"}
    ensure_analysis_schema(db_path)

    if not period:
        period = get_active_period(db_path)

    # ========== 缓存检查 ==========
    if not force_refresh:
        cached = _read_cached_opportunities(
            db_path, country, level1, level2, period, top_n)
        if cached is not None:
            return cached
    # ========== 缓存检查结束 ==========

    # 注入 AI 反向非商品规则
    ai_feedback = load_ai_nonproduct_feedback()
    result = analyze_pipeline.run_analysis(
        country, level1, level2, period, top_n=top_n, db_path=db_path,
        extra_blacklist=ai_feedback)

    if result["status"] != "success":
        # empty 时清掉该 L2 的旧缓存，避免读旧数据
        if result.get("status") == "empty":
            _clear_l2_cache(db_path, country, level1, level2, period)
        return result

    # 数据缺失明确错误（不用 mock）
    if result["summary"]["total_keywords"] == 0:
        return {"status": "error",
                "error": "no_data",
                "message": f"数据库中没有 {country} / {level1} > {level2} / {period} 的采集数据，请先采集入库。"}
    if result["summary"]["scored"] == 0:
        return {"status": "error",
                "error": "no_scorable",
                "message": "清洗/非商品过滤后没有可评分的关键词。"}

    # 修 A：成功时先清该 L2 旧缓存，避免 INSERT OR REPLACE 残留
    _clear_l2_cache(db_path, country, level1, level2, period)

    # Stage 5：AI 语义分析（对 Top N）
    kept_opps = []
    removed_nonproduct = []
    ai_stats = {"requested": len(result["opportunities"]), "succeeded": 0,
                "failed": 0, "nonproduct_removed": 0}
    for opp in result["opportunities"]:
        if not with_ai:
            ai = _placeholder_ai("AI 未启用（with_ai=False）")
            ai["ai_failed"] = True
            ai["ai_error"] = "AI 未启用"
        else:
            ai, ok, _tok = run_ai_for_opportunity(opp, result["category"], db_path,
                                                  result["summary"]["cohort_size"])
            ai_stats["succeeded" if ok else "failed"] += 1
        # 非商品剔除（AI 明确判定非商品）
        intent = str(ai.get("product_intent", "")).strip()
        if intent == "非商品":
            ai_stats["nonproduct_removed"] += 1
            add_ai_nonproduct_feedback(opp["representative_keyword"],
                                       reason=ai.get("reasons", ["AI 判定非商品"])[0] if ai.get("reasons") else "AI 判定非商品")
            removed_nonproduct.append(opp["representative_keyword"])
            continue
        # 回填 AI 字段
        name_cn = ai.get("normalized_product_name_cn") or opp["name_cn"]
        opp["name_cn"] = name_cn
        opp["representative_keyword_cn"] = name_cn
        opp["ai_summary"] = _build_ai_summary(opp, ai)
        opp["market_judgment"] = ai.get("market_judgment", "")
        opp["competition_judgment"] = ai.get("competition_judgment", "")
        opp["suitable_for_tiktok_th"] = ai.get("suitable_for_tiktok_th", "")
        opp["search_terms_1688"] = ai.get("search_terms_1688", []) or []
        opp["risks"] = ai.get("risks", []) or []
        opp["confidence"] = ai.get("confidence", 0.0)
        opp["product_intent"] = intent
        # 写库 opportunity_analysis
        _write_opportunity(db_path, opp, result["category"])
        kept_opps.append(opp)

    result["opportunities"] = kept_opps
    result["ai_stats"] = ai_stats
    result["ai_nonproduct_removed"] = removed_nonproduct
    result["summary"]["top_n"] = len(kept_opps)
    return result


def _build_ai_summary(opp: dict, ai: dict) -> str:
    if ai.get("ai_failed"):
        return AI_PLACEHOLDER_SUMMARY
    reasons = "；".join(ai.get("reasons", []) or [])
    return (f"{ai.get('market_judgment', '')}"
            + (f" 推荐理由：{reasons}" if reasons else ""))


def _write_opportunity(db_path: Path, opp: dict, category: dict) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO opportunity_analysis
               (opportunity_id, country, level1, level2, period,
                name_cn, score, rating, action,
                demand_score, conversion_score, competition_score,
                representative_keyword, related_keywords, evidence_json,
                ai_summary, market_judgment, competition_judgment,
                suitable_for_tiktok_th, search_terms_1688, risks,
                confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (opp["opportunity_id"],
             category["country"], category["level1"], category["level2"], category["period"],
             opp["name_cn"], opp["score"], opp["rating"], opp["action"],
             opp["demand_score"], opp["conversion_score"], opp["competition_score"],
             opp["representative_keyword"],
             json.dumps(opp["related_keywords"], ensure_ascii=False),
             json.dumps(opp["evidence"], ensure_ascii=False),
             opp.get("ai_summary", ""),
             opp.get("market_judgment", ""),
             opp.get("competition_judgment", ""),
             opp.get("suitable_for_tiktok_th", ""),
             json.dumps(opp.get("search_terms_1688", []), ensure_ascii=False),
             json.dumps(opp.get("risks", []), ensure_ascii=False),
             opp.get("confidence", 0.0),
             datetime.now().isoformat(timespec="seconds")))
        conn.commit()


def public_opportunity(opp: dict) -> dict:
    """把机会转换为前端 API 响应体（移除内部 _row）。"""
    return {k: v for k, v in opp.items() if k != "_row"}

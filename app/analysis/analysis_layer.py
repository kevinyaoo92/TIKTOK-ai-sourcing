# -*- coding: utf-8 -*-
"""V2.1 机会分析层（Opportunity Analysis Layer）——独立于机会池的归纳/解释层。

铁律:
  - 归纳 ≠ 删除；聚类 ≠ 合并；总结 ≠ 覆盖原始。V2 pool / product_opportunities 一律只读。
  - 数值(score/demand/intent/gap/证据计数/优先级) 全部由确定性 Python 计算；AI 禁止改数字。
  - DeepSeek 默认 calls=0（rule-based）。保留受限 AI 注入点 ai_naming_fn（一次性批量命名/
    语义判断，输出严格 JSON；异常 JSON 只记录失败、绝不写库污染）。

输出三张独立表（run_id = 来源 V2 pool run_id，幂等）:
  opportunity_directions  高层方向(用户视角少量方向)
  opportunity_clusters    方向内按商品类型聚合的机会簇(成员机会保持独立)
  opportunity_analysis    每个机会的类型/属性/风格/人群/证据状态/标签

透明优先级公式: direction/cluster priority = round(mean(成员机会.score), 4)
evidence_state 规则(基于 weekly/monthly_evidence 事实, 不凭感觉):
  category=REVIEW                        → REVIEW
  有周+有月证据                          → STABLE
  仅周证据 且 evidence_count≥2           → RISING
  仅周证据 且 evidence_count=1           → RECENT
  仅月证据(单粒度、无周对照)             → WEAK_EVIDENCE
  其它                                 → WEAK_EVIDENCE / UNKNOWN
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from app import config
from app.database import AnalysisRepo, now_str
from app.models import (
    OpportunityAnalysisRecord,
    OpportunityClusterRecord,
    OpportunityDirectionRecord,
)

RULE_MODEL = "rule-based-v2.1"

# ---------------------------------------------------------------- 词表(业务) --
# 商品类型词(用于从 canonical name 提取 product_type，长词优先)
_TYPE_TERMS = [
    "蝴蝶结发饰", "女士包头巾", "包头巾", "一次性口罩", "防蓝光眼镜", "远视眼镜",
    "太阳镜", "防晒面罩", "碎发整理棒", "手指保护套", "假发片", "防晒帽",
    "棒球帽", "腰带扣", "钥匙扣", "手表", "假刘海", "发网",
    "项链", "手链", "手镯", "耳环", "耳钉", "胸针", "发夹", "发箍",
    "发圈", "发带", "发簪", "头巾", "披肩", "眼镜", "腰带", "帽子", "假发",
]
_TARGET_WORDS = ("女士", "男士", "女性", "男性", "女", "男", "孕妇", "儿童", "宝宝",
                 "学生", "老人", "男生", "女生", "女人", "男人")
_STYLE_WORDS = ("y2k", "Y2K", "韩版", "日系", "复古", "法式", "ins", "网红", "卡哇伊",
                "可爱", "卡通", "泰银", "佛牌", "欧美女", "泰式", "原创", "简约",
                "甜美", "朋克", "运动", "休闲", "奢侈", "高档")
_DIRECTION_RULES = [
    # (key 匹配子串, direction_id, 名称, 业务层)
    ("hair", "hair", "发饰", "CORE"),
    ("headband", "hair", "发饰", "CORE"),   # 发带归发饰(防御)
    ("necklace", "necklace", "项链", "CORE"),
    ("earring", "earring", "耳饰", "CORE"),
    ("brooch", "brooch", "胸针", "CORE"),
    ("bangle", "hand", "手部饰品", "CORE"),
    ("bracelet", "hand", "手部饰品", "CORE"),
    ("belt", "belt", "腰带", "ADJACENT"),
    ("keychain", "keychain", "钥匙扣", "ADJACENT"),
    ("glasses", "glasses", "眼镜", "ADJACENT"),
    ("hat", "headwear", "帽饰", "ADJACENT"),
    ("cap", "headwear", "帽饰", "ADJACENT"),
    ("head_wrap", "headwrap", "头巾围巾", "ADJACENT"),
    ("shawl", "headwrap", "头巾围巾", "ADJACENT"),
    ("watch", "watch", "腕表", "ADJACENT"),
]
_DIRECTION_META = {
    "hair": ("发饰", "头戴类时尚发饰(发夹/发箍/发圈/发带/发簪/蝴蝶结等)"),
    "necklace": ("项链", "颈部饰品(不同属性/人群/风格的项链机会)"),
    "hand": ("手部饰品", "手链/手镯等腕部饰品"),
    "earring": ("耳饰", "耳环/耳钉等耳部饰品"),
    "brooch": ("胸针", "胸针/徽章类饰品"),
    "belt": ("腰带", "腰带及腰带配件(扩展品类)"),
    "headwear": ("帽饰", "帽子(防晒帽/棒球帽等扩展品类)"),
    "glasses": ("眼镜", "眼镜(太阳镜/功能眼镜；含边界待复核项)"),
    "headwrap": ("头巾围巾", "头巾/披肩类(扩展品类)"),
    "keychain": ("钥匙扣", "钥匙扣/包挂(扩展品类)"),
    "watch": ("腕表", "手表(扩展品类)"),
    "other_core": ("其他饰品", "暂未归入典型形态的其他饰品机会"),
    "other_adj": ("其他配件", "其他时尚配件(扩展)"),
}


def _assign_direction(key: str, status: str) -> tuple[str, str]:
    k = key.lower()
    for sub, dk, _name, _biz in _DIRECTION_RULES:
        if sub in k:
            name, _desc = _DIRECTION_META[dk]
            return dk, name
    if status == "CORE":
        return "other_core", _DIRECTION_META["other_core"][0]
    if status == "ADJACENT":
        return "other_adj", _DIRECTION_META["other_adj"][0]
    return "other_core", _DIRECTION_META["other_core"][0]


def parse_identity(name: str, level_3: str = "") -> dict:
    """从 canonical name 拆分 商品类型/属性/风格/人群（宁空不编造）。

    规则: 取 name 中最长命中的类型词 → type；剩余前缀按词表拆 target/style/attributes。
    未命中类型词 → 回退 level_3(语义三级类目)；仍无 → type='' (KEYWORD_AMBIGUITY)。
    """
    name = (name or "").strip()
    hit = ""
    for t in sorted(_TYPE_TERMS, key=len, reverse=True):
        if t in name:
            hit = t
            break
    attrs, styles, targets = [], [], []
    if hit:
        prefix = name.replace(hit, "", 1)
        for tok in [x for x in prefix.replace(" ", "").split("、") if x]:
            if any(w in tok for w in _TARGET_WORDS):
                targets.append(tok)
            elif any(w in tok for w in _STYLE_WORDS):
                styles.append(tok)
            else:
                attrs.append(tok)
    product_type = hit or (level_3 or "")
    return {"product_type": product_type, "attributes": attrs,
            "style": styles, "target_group": targets}


def evidence_state(weekly_n: int, monthly_n: int, evidence_count: int,
                   category_status: str) -> str:
    """证据状态（基于事实的确定性规则，见模块 docstring）。"""
    if category_status == "REVIEW":
        return "REVIEW"
    if weekly_n >= 1 and monthly_n >= 1:
        return "STABLE"
    if weekly_n >= 1 and monthly_n == 0:
        return "RISING" if evidence_count >= 2 else "RECENT"
    if monthly_n >= 1 and weekly_n == 0:
        return "WEAK_EVIDENCE"        # 单粒度月证据：无周对照，无法判稳定/动量
    return "UNKNOWN"


def _reason_tags(o) -> list[str]:
    tags = []
    if (o["demand"] or 0) >= 0.75:
        tags.append("HIGH_DEMAND")
    if (o["intent"] or 0) >= 0.75:
        tags.append("HIGH_PURCHASE_INTENT")
    if (o["gap"] or -9) >= 0.10:
        tags.append("SUPPLY_GAP")
    if o["evidence_count"] >= 3:
        tags.append("MULTI_KEYWORD")
    if o["weekly_n"] >= 1:
        tags.append("WEEKLY_SUPPORTED")
    if o["monthly_n"] >= 1:
        tags.append("MONTHLY_SUPPORTED")
    if o["weekly_n"] >= 1 and o["monthly_n"] == 0:
        tags.append("RECENT_APPEARANCE")
    if o["status"] == "CORE":
        tags.append("CORE_CATEGORY")
    return tags


def _risk_tags(o, identity) -> list[str]:
    tags = []
    total_periods = o["weekly_n"] + o["monthly_n"]
    if total_periods < 2:
        tags.append("WEAK_HISTORY")
    if o["status"] == "ADJACENT":
        tags.append("ADJACENT_CATEGORY")
    if o["status"] == "REVIEW":
        tags.append("BUSINESS_SCOPE_REVIEW")
    if o["evidence_count"] == 1:
        tags.append("LOW_EVIDENCE")
    if (o["supply"] or 0) >= 0.75:
        tags.append("HIGH_SUPPLY")
    if not identity["product_type"]:
        tags.append("KEYWORD_AMBIGUITY")
    return tags


def _analysis_text(name: str, identity: dict, state: str, tags: list[str]) -> str:
    parts = [f"商品形态：{identity['product_type'] or '未定'}"]
    if identity["attributes"]:
        parts.append(f"属性：{'/'.join(identity['attributes'])}")
    if identity["style"]:
        parts.append(f"风格：{'/'.join(identity['style'])}")
    if identity["target_group"]:
        parts.append(f"人群：{'/'.join(identity['target_group'])}")
    parts.append(f"证据状态：{state}")
    parts.append(f"关注理由：{'、'.join(tags) or '待补充数据后判断'}")
    return "；".join(parts) + "。"


def _load_pool(conn, market: str):
    run = conn.execute(
        "SELECT MAX(run_id) AS r FROM product_opportunities_v2 WHERE market=?",
        (market,)).fetchone()["r"]
    if not run:
        return None, [], []
    rows = conn.execute(
        "SELECT * FROM product_opportunities_v2 WHERE market=? AND run_id=?",
        (market, run)).fetchall()
    incl = [r for r in rows if r["category_status"] != "EXCLUDE"]
    excl = [r for r in rows if r["category_status"] == "EXCLUDE"]
    return run, incl, excl


def build_analysis(
    conn,
    *,
    market: str = "TH",
    store: bool = True,
    ai_naming_fn: Optional[Callable] = None,   # 受限 AI 注入点（默认 None → calls=0）
    ai_model: str = "deepseek-chat",
) -> dict:
    """基于最新 V2 池构建分析层。返回汇总 dict。"""
    pool_run, incl, excl = _load_pool(conn, market)
    if pool_run is None:
        return {"run_id": "", "stats": {"input": 0, "analyzed": 0, "excluded": 0,
                                        "directions": 0, "clusters": 0,
                                        "deepseek_calls": 0, "ai_failures": 0}}
    run_id = pool_run                 # 分析 run 与来源池批次绑定 → 重复分析幂等
    oid = 0

    def to_obj(r) -> dict:
        return {
            "key": r["canonical_product_key"], "name": r["canonical_product_name"],
            "opp_id": r["opportunity_id"], "score": r["score"],
            "demand": r["demand_score"], "intent": r["purchase_intent_score"],
            "supply": r["supply_score"], "gap": r["opportunity_gap"],
            "l3": r["level_3_category"], "status": r["category_status"],
            "evidence_count": r["evidence_count"],
            "weekly": json.loads(r["weekly_evidence"] or "[]"),
            "monthly": json.loads(r["monthly_evidence"] or "[]"),
            "weekly_n": len(json.loads(r["weekly_evidence"] or "[]")),
            "monthly_n": len(json.loads(r["monthly_evidence"] or "[]")),
            "first": r["first_seen"], "last": r["last_seen"],
        }

    objs = [to_obj(r) for r in incl]
    ai_calls = 0
    ai_failures = 0

    # ---------- 逐机会分析 ----------
    analysis_rows: list[OpportunityAnalysisRecord] = []
    clusters: dict[tuple, dict] = {}       # (direction_id, type_or_UNKNOWN) -> cluster
    for i, o in enumerate(objs, start=1):
        ident = parse_identity(o["name"], o["l3"])
        dir_id, dir_name = _assign_direction(o["key"], o["status"])
        o["direction_id"], o["direction_name"] = dir_id, dir_name
        o["identity"] = ident
        o["state"] = evidence_state(len(o["weekly"]), len(o["monthly"]),
                                    o["evidence_count"], o["status"])
        o["reason"] = _reason_tags(o)
        o["risk"] = _risk_tags(o, ident)
        ctype = ident["product_type"] or "未分类"
        ck = (dir_id, ctype)
        cl = clusters.setdefault(ck, {"direction_id": dir_id, "type": ctype,
                                      "members": [], "statuses": set()})
        cl["members"].append(o)
        cl["statuses"].add(o["status"])
        oid += 1
        analysis_rows.append(OpportunityAnalysisRecord(
            run_id=run_id, analysis_id=f"AN-{oid:04d}",
            opportunity_id=o["opp_id"], market=market,
            canonical_product_key=o["key"], cluster_id="", direction_id=dir_id,
            product_type=ident["product_type"],
            attributes=json.dumps(ident["attributes"], ensure_ascii=False),
            style=json.dumps(ident["style"], ensure_ascii=False),
            target_group=json.dumps(ident["target_group"], ensure_ascii=False),
            evidence_state=o["state"],
            reason_tags=json.dumps(o["reason"], ensure_ascii=False),
            risk_tags=json.dumps(o["risk"], ensure_ascii=False),
            analysis_text=_analysis_text(o["name"], ident, o["state"], o["reason"]),
            model=RULE_MODEL, created_at=now_str(), updated_at=now_str()))

    # ---------- 受限 AI 命名注入（默认关闭；异常不写库） ----------
    if ai_naming_fn is not None and clusters:
        ai_calls += 1
        candidates = [{"cluster_id": "_".join(k), "direction_id": v["direction_id"],
                       "member_keys": [m["key"] for m in v["members"]]}
                      for k, v in clusters.items()]
        try:
            payload = ai_naming_fn(candidates)
            # 只接受严格 JSON 结构；失败仅计数，不污染任何表
            if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
                ai_failures += 1
        except Exception:  # noqa: BLE001
            ai_failures += 1

    # ---------- 簇统计 ----------
    cluster_records = []
    for ci, ((dir_id, ctype), cl) in enumerate(sorted(clusters.items()), start=1):
        ms = cl["members"]
        scores = [m["score"] for m in ms]
        cid = f"CL-{ci:03d}"
        for m in ms:
            pass  # analysis.cluster_id 稍后回填
        st = ms[0]["status"]
        if st == "REVIEW" or "REVIEW" in cl["statuses"]:
            ctype2 = "REVIEW"
        elif any(s != "CORE" for s in cl["statuses"]):
            ctype2 = "ADJACENT"
        else:
            ctype2 = "PRODUCT"
        names = " / ".join(dict.fromkeys(m["name"] for m in ms))
        cluster_records.append(OpportunityClusterRecord(
            run_id=run_id, cluster_id=cid, direction_id=dir_id, market=market,
            cluster_name=_DIRECTION_META[dir_id][0] + "·" + (ctype or "未分类"),
            cluster_type=ctype2,
            description=f"同一商品形态({ctype})的 {len(ms)} 个机会：{names}。成员机会保持独立。",
            opportunity_count=len(ms),
            core_count=sum(1 for m in ms if m["status"] == "CORE"),
            adjacent_count=sum(1 for m in ms if m["status"] == "ADJACENT"),
            weekly_supported_count=sum(1 for m in ms if m["weekly"]),
            monthly_supported_count=sum(1 for m in ms if m["monthly"]),
            priority=round(sum(scores) / len(scores), 4),
            evidence_summary=json.dumps(
                {"weekly": sum(1 for m in ms if m["weekly"]),
                 "monthly": sum(1 for m in ms if m["monthly"]),
                 "mean_score": round(sum(scores) / len(scores), 4)}, ensure_ascii=False),
            risk_summary=json.dumps(
                {"review_keys": [m["key"] for m in ms if m["status"] == "REVIEW"],
                 "note": "无周数据时证据状态为 WEAK_EVIDENCE"}, ensure_ascii=False),
            member_keys=json.dumps([m["key"] for m in ms], ensure_ascii=False),
            created_at=now_str(), updated_at=now_str()))
        # 回填 analysis.cluster_id
        for a in analysis_rows:
            for m in ms:
                if a.canonical_product_key == m["key"]:
                    a.cluster_id = cid

    # ---------- 方向统计 ----------
    dir_records = []
    by_dir: dict[str, list] = {}
    for a, o in zip(analysis_rows, objs):
        by_dir.setdefault(o["direction_id"], []).append(o)
    for di, (dir_id, members) in enumerate(sorted(by_dir.items()), start=1):
        scores = [m["score"] for m in members]
        keys = [m["key"] for m in members]
        name, desc = _DIRECTION_META.get(dir_id, (dir_id, ""))
        dir_records.append(OpportunityDirectionRecord(
            run_id=run_id, direction_id=dir_id, market=market,
            direction_name=name, description=desc,
            opportunity_count=len(members),
            core_count=sum(1 for m in members if m["status"] == "CORE"),
            adjacent_count=sum(1 for m in members if m["status"] == "ADJACENT"),
            review_count=sum(1 for m in members if m["status"] == "REVIEW"),
            weekly_supported_count=sum(1 for m in members if m["weekly"]),
            monthly_supported_count=sum(1 for m in members if m["monthly"]),
            priority=round(sum(scores) / len(scores), 4),
            evidence_summary=json.dumps(
                {"weekly": sum(1 for m in members if m["weekly"]),
                 "monthly": sum(1 for m in members if m["monthly"]),
                 "mean_score": round(sum(scores) / len(scores), 4)}, ensure_ascii=False),
            risk_summary=json.dumps(
                {"review_keys": [m["key"] for m in members if m["status"] == "REVIEW"],
                 "note": ""}, ensure_ascii=False),
            member_keys=json.dumps(keys, ensure_ascii=False),
            created_at=now_str(), updated_at=now_str()))

    stored = 0
    if store:
        repo = AnalysisRepo(conn)
        repo.replace_run(run_id)          # 幂等：同池批次重复分析不翻倍
        stored += repo.insert_directions(dir_records)
        stored += repo.insert_clusters(cluster_records)
        stored += repo.insert_analysis(analysis_rows)
        conn.commit()

    return {
        "run_id": run_id,
        "model": RULE_MODEL,
        "directions": dir_records,
        "clusters": cluster_records,
        "analysis": analysis_rows,
        "stats": {
            "input": len(incl) + len(excl),
            "analyzed": len(incl),
            "excluded_keys": [r["canonical_product_key"] for r in excl],
            "directions": len(dir_records),
            "clusters": len(cluster_records),
            "deepseek_calls": ai_calls,
            "ai_failures": ai_failures,
            "stored": stored,
            "evidence_states": {s: 0 for s in ()} or _state_dist(analysis_rows),
        },
    }


def _state_dist(rows) -> dict:
    from collections import Counter
    return dict(Counter(r.evidence_state for r in rows))

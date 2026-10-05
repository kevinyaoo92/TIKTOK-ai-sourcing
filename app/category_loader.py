# -*- coding: utf-8 -*-
"""官方类目文件加载器：泰国 TikTok 官方 L1/L2 类目清单。

唯一类目来源：用户确认的官方类目 TXT（默认桌面「泰国TK官方类目.txt」）。
原则（严格遵守）：
- 不修改、不猜测、不翻译、不增删、不合并、不重新分类官方类目。
- L1 以 "-" 前缀行表示；其后的无前缀非空行属于该 L1 的 L2；两者均原样保存。
- 文件首行视为标题行（无前缀且出现在任何 L1 之前），不当作 L2。
- 不把具体商品类型当 L2：本模块只按文件结构归属，不自行判断。
- 来源路径可被环境变量 TIKTOK_CATEGORY_FILE 覆盖；TXT 是唯一来源，不生成第二份人工修改版。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover - 极端环境兜底
        pass

DEFAULT_CATEGORY_FILE_NAME = "泰国TK官方类目.txt"


def default_category_file() -> Path:
    """默认类目文件路径：项目内 app/data/泰国TK官方类目.txt。

    顺序：
    1. 环境变量 TIKTOK_CATEGORY_FILE（显式指定唯一来源）；
    2. 项目内 app/data/泰国TK官方类目.txt；
    3. 兜底：当前用户桌面（兼容本地开发）。
    """
    env = os.environ.get("TIKTOK_CATEGORY_FILE", "").strip()
    if env:
        return Path(env)
    project_file = Path(__file__).resolve().parent / "data" / DEFAULT_CATEGORY_FILE_NAME
    if project_file.exists():
        return project_file
    return Path.home() / "Desktop" / DEFAULT_CATEGORY_FILE_NAME


@dataclass
class CategoryEntry:
    """一个 L1 及其官方 L2 清单（原样保存）。"""
    level1: str
    level2: list[str] = field(default_factory=list)


@dataclass
class CategoryCatalog:
    """官方类目目录：解析结果 + 校验告警。"""
    title: str
    source: Path
    entries: list[CategoryEntry]
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    @property
    def l1_count(self) -> int:
        return len(self.entries)

    @property
    def l2_total(self) -> int:
        return sum(len(e.level2) for e in self.entries)

    def l2_count_of(self, level1: str) -> int:
        for e in self.entries:
            if e.level1 == level1:
                return len(e.level2)
        return 0

    def to_tasks(self, market: str = "TH", period: str = "month",
                 tab: str = "热门搜索关键词") -> list[dict]:
        """生成采集任务清单（不启动采集）。

        任务字段：market / level1 / level2 / period / tab。
        period 与 tab 沿用当前已验证的采集配置，本次不扩大采集范围。
        """
        tasks: list[dict] = []
        for e in self.entries:
            for l2 in e.level2:
                tasks.append({
                    "market": market,
                    "level1": e.level1,
                    "level2": l2,
                    "period": period,
                    "tab": tab,
                })
        return tasks

    def check_duplicate_tasks(self) -> list[str]:
        """校验任务级重复（同 market+level1+level2）。"""
        seen: set[tuple] = set()
        dups: list[str] = []
        for t in self.to_tasks():
            key = (t["market"], t["level1"], t["level2"])
            if key in seen:
                dups.append(f"重复任务: {key[0]} / {key[1]} / {key[2]}")
            seen.add(key)
        return dups


def parse_category_file(path: Path | str) -> CategoryCatalog:
    """解析官方类目 TXT。

    格式（以实际文件为准，不猜测）：
      - 首行：标题（如「TikTok 泰国后台官方类目」），跳过，不当作 L2。
      - "-" 开头：L1。
      - 无前缀非空行：当前 L1 下的 L2。
      - 空行：跳过。
    任何不符合上述结构的情况记录到 warnings，不中断解析。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"官方类目文件不存在: {p}")

    raw_lines: list[str] = []
    try:
        with open(p, "r", encoding="utf-8-sig") as fh:
            raw_lines = [ln.rstrip("\r\n") for ln in fh]
    except UnicodeDecodeError:
        # 兜底：部分编辑器可能保存为 GBK/GB18030；仅作容错，不猜测格式语义
        with open(p, "r", encoding="gb18030") as fh:
            raw_lines = [ln.rstrip("\r\n") for ln in fh]

    warnings: list[str] = []
    title = ""
    entries: list[CategoryEntry] = []
    current: CategoryEntry | None = None
    seen_l1: set[str] = set()

    for idx, line in enumerate(raw_lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue  # 空行跳过
        if stripped.startswith("-"):
            name = stripped[1:].strip()
            if not name:
                warnings.append(f"第{idx}行: L1 名称为空（'-'后无内容）")
                continue
            if name in seen_l1:
                warnings.append(f"第{idx}行: 重复 L1「{name}」（首次出现于前面行）")
            seen_l1.add(name)
            current = CategoryEntry(level1=name)
            entries.append(current)
        else:
            # 无前缀行
            if current is None:
                if not title and entries == []:
                    # 任何 L1 之前的第一条无前缀行视为标题
                    title = stripped
                else:
                    warnings.append(f"第{idx}行: 无法解析（无前缀且当前无 L1 归属）: {stripped}")
                continue
            if stripped in current.level2:
                warnings.append(f"第{idx}行: L1「{current.level1}」内重复 L2「{stripped}」")
            current.level2.append(stripped)

    if not entries:
        warnings.append("文件中未解析到任何 L1（可能不是预期格式）")

    return CategoryCatalog(title=title, source=p.resolve(), entries=entries, warnings=warnings)


def load_catalog(path: Path | str | None = None) -> CategoryCatalog:
    """便捷入口：path 缺省时使用默认官方类目文件。"""
    p = Path(path) if path else default_category_file()
    return parse_category_file(p)

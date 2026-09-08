from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class TicketLot:
    sector: str
    price_kopecks: int
    fee_kopecks: int
    count: int

    @property
    def price_rub(self) -> int:
        return self.price_kopecks // 100

    @property
    def fee_rub(self) -> int:
        return self.fee_kopecks // 100

    @property
    def total_rub(self) -> int:
        return (self.price_kopecks + self.fee_kopecks) // 100

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.sector, self.price_kopecks, self.fee_kopecks)


@dataclass
class TicketSnapshot:
    lots: list[TicketLot] = field(default_factory=list)
    sale_status: str = "unknown"
    total_count: int = 0

    def as_map(self) -> dict[tuple[str, int, int], int]:
        result: dict[tuple[str, int, int], int] = {}
        for lot in self.lots:
            result[lot.key] = result.get(lot.key, 0) + lot.count
        return result


def aggregate_hallplan(hallplan: dict, display_type: str) -> TicketSnapshot:
    counters: dict[tuple[str, int, int], int] = defaultdict(int)

    if display_type == "category-counter":
        for level in hallplan.get("levels", []):
            sector = level.get("name") or "Без названия"
            for counter in level.get("categoryCounters", []) or []:
                price_info = counter.get("priceInfo") or {}
                price = (price_info.get("price") or {}).get("value", 0)
                fee = (price_info.get("fee") or {}).get("value", 0)
                count = int(counter.get("availableCount") or counter.get("count") or 0)
                if count > 0:
                    counters[(sector, price, fee)] += count
    elif display_type == "admission":
        for level in hallplan.get("levels", []):
            sector = level.get("name") or "Входной билет"
            admission = level.get("admission") or {}
            for category in admission.get("categories", []) or []:
                price_info = category.get("priceInfo") or {}
                price = (price_info.get("price") or {}).get("value", 0)
                fee = (price_info.get("fee") or {}).get("value", 0)
                count = int(category.get("availableCount") or category.get("count") or 0)
                if count > 0:
                    counters[(sector, price, fee)] += count
    else:
        for level in hallplan.get("levels", []):
            sector = level.get("name") or "Без названия"
            for seat in level.get("seats", []) or []:
                price_info = seat.get("priceInfo") or {}
                price = (price_info.get("price") or {}).get("value", 0)
                fee = (price_info.get("fee") or {}).get("value", 0)
                counters[(sector, price, fee)] += 1

    lots = [
        TicketLot(sector=sector, price_kopecks=price, fee_kopecks=fee, count=count)
        for (sector, price, fee), count in sorted(counters.items())
    ]
    total = sum(lot.count for lot in lots)
    return TicketSnapshot(lots=lots, total_count=total)


def diff_snapshots(
    previous: TicketSnapshot | None, current: TicketSnapshot
) -> list[tuple[TicketLot, int]]:
    prev_map = previous.as_map() if previous else {}
    curr_map = current.as_map()
    changes: list[tuple[TicketLot, int]] = []

    for key, new_count in curr_map.items():
        old_count = prev_map.get(key, 0)
        if new_count != old_count:
            sector, price, fee = key
            delta = new_count - old_count
            changes.append(
                (
                    TicketLot(
                        sector=sector,
                        price_kopecks=price,
                        fee_kopecks=fee,
                        count=new_count,
                    ),
                    delta,
                )
            )

    for key, old_count in prev_map.items():
        if key not in curr_map and old_count > 0:
            sector, price, fee = key
            changes.append(
                (
                    TicketLot(
                        sector=sector,
                        price_kopecks=price,
                        fee_kopecks=fee,
                        count=0,
                    ),
                    -old_count,
                )
            )

    return changes


def format_price_line(lot: TicketLot) -> str:
    if lot.fee_rub:
        return (
            f"{lot.sector} - {lot.count} {_ticket_word(lot.count)} "
            f"по {lot.price_rub:,} ₽ (+{lot.fee_rub:,} ₽ сбор)".replace(",", " ")
        )
    return (
        f"{lot.sector} - {lot.count} {_ticket_word(lot.count)} "
        f"по {lot.price_rub:,} ₽".replace(",", " ")
    )


def format_change_line(lot: TicketLot, delta: int) -> str:
    sign = f"+{delta}" if delta > 0 else str(delta)
    if lot.fee_rub:
        return (
            f"  {lot.sector} по {lot.price_rub:,} ₽ (+{lot.fee_rub:,} ₽ сбор): "
            f"{sign} (стало {lot.count})".replace(",", " ")
        )
    return (
        f"  {lot.sector} по {lot.price_rub:,} ₽: {sign} (стало {lot.count})".replace(",", " ")
    )


def _ticket_word(count: int) -> str:
    n = abs(count) % 100
    n1 = n % 10
    if 11 <= n <= 19:
        return "билетов"
    if n1 == 1:
        return "билет"
    if 2 <= n1 <= 4:
        return "билета"
    return "билетов"

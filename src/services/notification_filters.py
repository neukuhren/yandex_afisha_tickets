from __future__ import annotations

from src.parser.aggregator import TicketLot, TicketSnapshot, diff_snapshots


def lot_matches_filters(
    lot: TicketLot,
    *,
    price_min_rub: int | None,
    price_max_rub: int | None,
    excluded_sectors: set[str],
) -> bool:
    if lot.sector in excluded_sectors:
        return False
    total = lot.total_rub
    if price_min_rub is not None and total < price_min_rub:
        return False
    if price_max_rub is not None and total > price_max_rub:
        return False
    return True


def filter_snapshot(
    snapshot: TicketSnapshot,
    *,
    price_min_rub: int | None,
    price_max_rub: int | None,
    excluded_sectors: set[str],
) -> TicketSnapshot:
    lots = [
        lot
        for lot in snapshot.lots
        if lot_matches_filters(
            lot,
            price_min_rub=price_min_rub,
            price_max_rub=price_max_rub,
            excluded_sectors=excluded_sectors,
        )
    ]
    total = sum(lot.count for lot in lots)
    return TicketSnapshot(
        lots=lots,
        sale_status=snapshot.sale_status,
        total_count=total,
    )


def filter_event_snapshot(event, snapshot: TicketSnapshot) -> TicketSnapshot:
    excluded = set(event.notify_excluded_sectors or [])
    return filter_snapshot(
        snapshot,
        price_min_rub=event.notify_price_min_rub,
        price_max_rub=event.notify_price_max_rub,
        excluded_sectors=excluded,
    )


def filtered_diff(
    previous: TicketSnapshot | None,
    current: TicketSnapshot,
    *,
    price_min_rub: int | None,
    price_max_rub: int | None,
    excluded_sectors: set[str],
) -> list[tuple[TicketLot, int]]:
    prev_f = (
        filter_snapshot(
            previous,
            price_min_rub=price_min_rub,
            price_max_rub=price_max_rub,
            excluded_sectors=excluded_sectors,
        )
        if previous
        else None
    )
    curr_f = filter_snapshot(
        current,
        price_min_rub=price_min_rub,
        price_max_rub=price_max_rub,
        excluded_sectors=excluded_sectors,
    )
    return diff_snapshots(prev_f, curr_f)

"""Stateful level-two projection for snapshot/bootstrap plus incremental updates."""
from __future__ import annotations

from api.websocket.schemas import BookDeltaMessage, BookLevel, BookSnapshotMessage
from engine.core.order_book import BookSnapshot


class BookDeltaProjector:
    """
    Convert full internal snapshots into bandwidth-efficient client deltas.

    The engine continues to publish immutable point-in-time depth. Only this
    dissemination layer retains prior state, keeping recovery and C++/FPGA
    replacement boundaries independent from dashboard transport policy.
    """

    def __init__(self) -> None:
        self._prior: dict[str, BookSnapshot] = {}

    @staticmethod
    def snapshot_message(snapshot: BookSnapshot) -> BookSnapshotMessage:
        return BookSnapshotMessage(
            symbol=snapshot.symbol,
            sequence=snapshot.sequence,
            bids=[BookLevel(price=p, quantity=q, order_count=c) for p, q, c in snapshot.bids],
            asks=[BookLevel(price=p, quantity=q, order_count=c) for p, q, c in snapshot.asks],
            spread=snapshot.spread,
            mid_price=snapshot.mid_price,
            timestamp_ns=snapshot.timestamp_ns,
        )

    def project(self, snapshot: BookSnapshot) -> BookSnapshotMessage | BookDeltaMessage:
        prior = self._prior.get(snapshot.symbol)
        self._prior[snapshot.symbol] = snapshot
        if prior is None:
            return self.snapshot_message(snapshot)
        return BookDeltaMessage(
            symbol=snapshot.symbol,
            previous_sequence=prior.sequence,
            sequence=snapshot.sequence,
            bids=self._changes(prior.bids, snapshot.bids),
            asks=self._changes(prior.asks, snapshot.asks),
            spread=snapshot.spread,
            mid_price=snapshot.mid_price,
            timestamp_ns=snapshot.timestamp_ns,
        )

    @staticmethod
    def _changes(
        previous: list[tuple[float, int, int]],
        current: list[tuple[float, int, int]],
    ) -> list[BookLevel]:
        prior_levels = {price: (quantity, count) for price, quantity, count in previous}
        current_levels = {price: (quantity, count) for price, quantity, count in current}
        changed = [
            BookLevel(price=price, quantity=quantity, order_count=count)
            for price, (quantity, count) in current_levels.items()
            if prior_levels.get(price) != (quantity, count)
        ]
        # A zero-quantity delta deletes a level from a dashboard's local book.
        changed.extend(
            BookLevel(price=price, quantity=0, order_count=0)
            for price in prior_levels.keys() - current_levels.keys()
        )
        return changed

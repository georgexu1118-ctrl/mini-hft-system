"""
FPGAMatchingHAL — Hardware Abstraction Layer backed by an FPGA engine.

IMPLEMENTATION STATUS: Scaffold only. Requires:
  - Alveo U50/U250 or AWS F1 FPGA with the HFT matching bitstream loaded
  - XRT (Xilinx Runtime) or OPAE (Intel) driver
  - hugepage-backed DMA ring buffers (see docs/fpga_acceleration_design.md)

To activate when hardware is available:
    app.state.engine = FPGAMatchingHAL(device_idx=0)

No other code changes needed — the HAL contract is identical to
SoftwareMatchingHAL and CppMatchingHAL.

Architecture overview
─────────────────────
                    CPU                         FPGA
  ┌──────────────────────────┐   PCIe BAR   ┌─────────────────────────┐
  │  FPGAMatchingHAL         │◄────────────►│  XRT Shell              │
  │                          │              │                         │
  │  submit_order_bytes()    │  DMA write   │  Order Ingress          │
  │    → encode 64-byte frame├────────────►│    ITCH/Binary parser   │
  │    → write to TX ring    │              │    Deserialise frame    │
  │                          │  DMA read    │                         │
  │  _poll_completion_ring() │◄────────────│  Matching Engine (RTL)  │
  │    → read TRADE_FRAMEs   │              │    Price-time FIFO      │
  │    → hydrate Trade objs  │              │    SortedPriceLevels    │
  │                          │              │    in BRAM              │
  └──────────────────────────┘              └─────────────────────────┘

Latency budget (target with Alveo U50):
  Order serialise (CPU)     :   ~200 ns
  PCIe DMA write            :   ~500 ns
  FPGA matching pipeline    :   ~100 ns   (10-stage FIFO pipeline @ 300 MHz)
  PCIe DMA read completion  :   ~500 ns
  Trade deserialise (CPU)   :   ~200 ns
  ─────────────────────────────────────
  Total                     : < 1.5 µs   (vs ~900 µs Python, ~5 µs C++)

Key invariants (from AGENTS.md):
  - No Python objects allocated inside submit_order_bytes() / cancel_order_bytes()
  - TX ring is hugepage-backed; head/tail are cache-line-padded (false sharing)
  - Completion ring uses busy-poll (no interrupts on critical path)
  - Graceful fallback: if FPGA not available, raise FPGANotAvailableError at init
"""
from __future__ import annotations

from typing import Optional

from engine.core.order import Order
from engine.core.order_book import BookSnapshot, MatchResult
from engine.core.ring_buffer import SPSCRingBuffer
from engine.core.types import OrderId, Symbol
from engine.hal.abstract import EventCallback, MatchingHAL


class FPGANotAvailableError(RuntimeError):
    """Raised when the FPGA device is not present or bitstream not loaded."""


class FPGAMatchingHAL(MatchingHAL):
    """
    HAL backed by an FPGA matching engine over PCIe DMA.

    This is a scaffold — see module docstring for build requirements.
    The class structure and API are complete; DMA implementation pending.
    """

    @property
    def backend_name(self) -> str:
        return "fpga"

    def __init__(self, device_idx: int = 0) -> None:
        # ── Device detection ──────────────────────────────────────────────────
        self._device = self._open_device(device_idx)

        # ── DMA rings (hugepage-backed in production) ─────────────────────────
        # TX ring: CPU → FPGA  (outbound orders)
        # RX ring: FPGA → CPU  (inbound completions / trade prints)
        self._tx_ring = SPSCRingBuffer(capacity=4096, item_size=64)
        self._rx_ring = SPSCRingBuffer(capacity=4096, item_size=64)

        self._event_callback: Optional[EventCallback] = None
        self._symbols: list[Symbol] = []

    # ── Device lifecycle ──────────────────────────────────────────────────────

    @staticmethod
    def _open_device(idx: int):
        """
        Open PCIe device via XRT or OPAE driver.

        Production implementation:
            import pyxrt  # Xilinx Runtime
            device = pyxrt.device(idx)
            xclbin = pyxrt.xclbin("hft_matching.xclbin")
            device.load_xclbin(xclbin)
            return device

        Raises FPGANotAvailableError if device not found.
        """
        raise FPGANotAvailableError(
            f"FPGA device {idx} not available. "
            "Ensure XRT driver is installed and bitstream is loaded. "
            "See docs/fpga_acceleration_design.md for setup instructions."
        )

    # ── MatchingHAL implementation ────────────────────────────────────────────

    def register_symbol(self, symbol: Symbol) -> None:
        """
        Register symbol with FPGA price level allocator.

        Production: write a REGISTER_SYMBOL frame to TX ring.
        The FPGA allocates a BRAM price level array for this symbol.
        """
        if symbol not in self._symbols:
            self._symbols.append(symbol)

    def submit_order(self, order: Order) -> MatchResult:
        """Encode to 64-byte frame and DMA to FPGA, then poll completion ring."""
        frame = order.to_bytes()
        return self._submit_frame(frame, order)

    def cancel_order(self, order_id: OrderId) -> Optional[Order]:
        frame = self._encode_cancel(order_id)
        result_frame = self._roundtrip(frame)
        if result_frame is None:
            return None
        return Order.from_bytes(result_frame)

    def get_snapshot(self, symbol: Symbol, depth: int = 10) -> Optional[BookSnapshot]:
        """
        Read current order book depth from FPGA.

        Production: write SNAPSHOT_REQUEST frame → poll SNAPSHOT_RESPONSE.
        The FPGA snapshot controller reads BRAM price levels and DMA-writes
        depth BookLevel frames to the completion ring.
        """
        raise NotImplementedError("FPGA snapshot: pending hardware")

    def get_order(self, order_id: OrderId) -> Optional[Order]:
        raise NotImplementedError("FPGA order lookup: pending hardware")

    def set_event_callback(self, cb: EventCallback) -> None:
        self._event_callback = cb

    @property
    def registered_symbols(self) -> list[Symbol]:
        return list(self._symbols)

    # ── Binary fast path ──────────────────────────────────────────────────────

    def submit_order_bytes(self, frame: bytes) -> bytes:
        """
        Zero-copy FPGA submit path.

        In production:
            1. Write frame to hugepage TX ring slot (memcpy, ~50 ns)
            2. Advance TX head (store-release, ~5 ns)
            3. Busy-poll RX ring head for completion (FPGA DMA write, ~500 ns)
            4. Read completion slot (copy, ~50 ns)
            5. Return raw completion bytes
        """
        raise NotImplementedError("FPGA DMA: pending hardware")

    def cancel_order_bytes(self, order_id_bytes: bytes) -> bytes:
        raise NotImplementedError("FPGA DMA cancel: pending hardware")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _submit_frame(self, frame: bytes, order: Order) -> MatchResult:
        raise NotImplementedError("FPGA DMA: pending hardware")

    def _roundtrip(self, frame: bytes) -> Optional[bytes]:
        raise NotImplementedError("FPGA DMA: pending hardware")

    @staticmethod
    def _encode_cancel(order_id: str) -> bytes:
        """Encode a cancel request as a 64-byte frame."""
        import uuid
        frame = bytearray(64)
        frame[0] = 0x02  # CANCEL frame type
        order_bytes = uuid.UUID(order_id).bytes
        frame[1:17] = order_bytes
        return bytes(frame)

    @property
    def stats(self):
        """FPGA hardware counters (DMA register read in production)."""
        from engine.core.matching_engine import EngineStats
        return EngineStats()

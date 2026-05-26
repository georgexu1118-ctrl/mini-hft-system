/**
 * engine/cpp/include/order.hpp
 *
 * Fixed-size, cache-line-aligned order representation for the C++ engine.
 *
 * Layout mirrors the Python binary_protocol.py ORDER_FRAME exactly so that
 * orders can be passed from Python → C++ as raw bytes without re-encoding.
 *
 * Alignment:
 *   __attribute__((packed))  — no internal padding (we control it manually)
 *   alignas(64)              — one struct per cache line; prevents false sharing
 *                              when orders sit in a pre-allocated pool array.
 *
 * Memory pool usage:
 *   Orders are allocated from a pre-allocated pool (see order_pool.hpp).
 *   New/delete are never called on the hot path.
 *
 * FPGA note (M8):
 *   This struct maps directly to the 64-byte frame sent over PCIe BAR.
 *   The FPGA ITCH parser writes ORDER_FRAME frames into a hugepage-backed
 *   DMA ring buffer; the CPU polling loop casts the slot pointer to Order*.
 */
#pragma once

#include <array>
#include <cstdint>
#include <cstring>

namespace hft {

// ── Enumerations (byte-width for wire compat) ─────────────────────────────────

enum class Side    : uint8_t { BUY = 0, SELL = 1 };
enum class OType   : uint8_t { LIMIT = 0, MARKET = 1 };
enum class OStatus : uint8_t {
    PENDING          = 0,
    OPEN             = 1,
    PARTIALLY_FILLED = 2,
    FILLED           = 3,
    CANCELLED        = 4,
    REJECTED         = 5,
};
enum class TIF : uint8_t { GTC = 0, IOC = 1, FOK = 2, GTD = 3 };

// ── Order (64 bytes, 1 cache line) ────────────────────────────────────────────

struct alignas(64) Order {
    // 16 bytes: UUID stored as raw bytes (matches Python uuid.UUID.bytes)
    uint8_t  order_id[16];

    // 8 bytes
    double   price;         // 0.0 for MARKET orders

    // 4 + 4 bytes
    uint32_t quantity;
    uint32_t filled_quantity;

    // 8 + 8 bytes
    uint64_t created_at_ns;
    uint64_t processed_at_ns;

    // 6 bytes: symbol padded with NUL (e.g. "AAPL\0\0")
    char     symbol[6];

    // 1 + 1 + 1 + 1 bytes
    Side     side;
    OType    order_type;
    OStatus  status;
    TIF      time_in_force;

    // 6 bytes padding to reach 64 bytes
    uint8_t  _pad[6];

    // ── Helpers ──────────────────────────────────────────────────────────────

    uint32_t remaining_quantity() const noexcept {
        return quantity - filled_quantity;
    }

    bool is_buy()  const noexcept { return side == Side::BUY;  }
    bool is_sell() const noexcept { return side == Side::SELL; }
    bool is_market() const noexcept { return order_type == OType::MARKET; }
    bool is_limit()  const noexcept { return order_type == OType::LIMIT;  }
    bool is_active() const noexcept {
        return status == OStatus::OPEN || status == OStatus::PARTIALLY_FILLED;
    }

    void set_symbol(const char* sym, size_t len) noexcept {
        std::memset(symbol, 0, sizeof(symbol));
        std::memcpy(symbol, sym, std::min(len, sizeof(symbol)));
    }
};

static_assert(sizeof(Order) == 64, "Order must be exactly 64 bytes (1 cache line)");
static_assert(alignof(Order) == 64, "Order must be aligned to a cache line");

// ── Trade (64 bytes) ──────────────────────────────────────────────────────────

struct alignas(64) Trade {
    uint8_t  trade_id[16];      // UUID
    double   price;
    uint32_t quantity;
    Side     aggressor_side;
    uint8_t  _pad1[3];
    uint64_t timestamp_ns;
    uint8_t  maker_order_id[8]; // first 8 bytes of UUID (sufficient for dedup)
    uint8_t  taker_order_id[8];
};

static_assert(sizeof(Trade) == 64, "Trade must be exactly 64 bytes");

}  // namespace hft

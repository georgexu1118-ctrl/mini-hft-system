/**
 * engine/cpp/include/order.hpp
 *
 * Fixed-size, cache-line-aligned order representation for the C++ engine.
 *
 * This is the native host representation, not a reinterpret-cast view of the
 * wire frame. Python's ORDER_FRAME is explicitly big-endian while commodity
 * C++ hosts are normally little-endian. wire_codec.hpp performs the fixed,
 * bounded conversion at the HAL boundary.
 *
 * Alignment:
 *   __attribute__((packed))  — no internal padding (we control it manually)
 *   alignas(64)              — one struct per cache line; prevents false sharing
 *                              when orders sit in a pre-allocated pool array.
 *
 * Allocation policy:
 *   OrderBook accepts caller-owned nodes so a later arena/slab allocator can
 *   replace allocation without altering matching semantics. The M7 facade
 *   retains stable nodes with one allocation per admitted order until the
 *   benchmark demonstrates whether pooling is worth the additional machinery.
 *
 * FPGA note (M8):
 *   ORDER_FRAME remains the DMA ABI. Hardware and host must consume it using
 *   the documented byte order; native Order is deliberately an internal type.
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
    TIF      time_in_force;
    OStatus  status;

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

#ifdef _MSC_VER
#pragma warning(push)
// The final bytes are intentional reserved cache-line padding in the wire-oriented type.
#pragma warning(disable : 4324)
#endif
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
#ifdef _MSC_VER
#pragma warning(pop)
#endif

}  // namespace hft

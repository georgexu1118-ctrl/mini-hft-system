/**
 * engine/cpp/include/order_book.hpp
 *
 * Single-instrument Central Limit Order Book — C++ implementation.
 *
 * Data structures:
 *   Bids: std::map<Price, PriceLevel, std::greater<>> — descending (best bid first)
 *   Asks: std::map<Price, PriceLevel>                 — ascending  (best ask first)
 *   cancel_map: std::unordered_map<UUID, OrderNode*>  — O(1) cancel lookup
 *
 * Complexity:
 *   submit_order  O(log P + F)   P = distinct price levels, F = fills generated
 *   cancel_order  O(log P)       vs O(log P + N) in Python (deque scan)
 *   get_snapshot  O(D)           D = depth requested
 *
 * Memory:
 *   OrderNode objects come from a caller-supplied pool (avoid heap on hot path).
 *   PriceLevels are created on first order at that price; destroyed when empty.
 *   The std::map node overhead (~48 bytes) is acceptable — far fewer price
 *   levels than orders. FPGA path bypasses this with BRAM direct-map.
 *
 * Thread safety:
 *   Not thread-safe. Single-threaded matching engine (same as Python version).
 *   If you need parallelism: shard by symbol, one engine per core.
 */
#pragma once

#include "order.hpp"
#include "price_level.hpp"

#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <unordered_map>
#include <vector>

namespace hft {

// ── Trade record returned from matching ───────────────────────────────────────

struct MatchTrade {
    double   price;
    uint32_t quantity;
    Side     aggressor_side;
    // Pointers valid for lifetime of the engine's pool
    const OrderNode* maker;
    const OrderNode* taker;
};

// ── Match result ──────────────────────────────────────────────────────────────

struct MatchResult {
    OrderNode*              order    = nullptr;
    std::vector<MatchTrade> trades;
    bool                    resting  = false;   // residual placed in book

    uint32_t total_filled() const noexcept {
        uint32_t total = 0;
        for (const auto& t : trades) total += t.quantity;
        return total;
    }
};

// ── Book snapshot (depth-limited) ─────────────────────────────────────────────

struct LevelSnapshot {
    double   price;
    uint32_t quantity;
    uint32_t order_count;
};

struct BookSnapshot {
    char                      symbol[6];
    uint64_t                  sequence;
    std::vector<LevelSnapshot> bids;
    std::vector<LevelSnapshot> asks;
    uint64_t                  timestamp_ns;

    double best_bid() const noexcept { return bids.empty() ? 0.0 : bids[0].price; }
    double best_ask() const noexcept { return asks.empty() ? 0.0 : asks[0].price; }
    double spread()   const noexcept {
        return (bids.empty() || asks.empty()) ? 0.0 : asks[0].price - bids[0].price;
    }
    double mid_price() const noexcept {
        return (bids.empty() || asks.empty()) ? 0.0 : (bids[0].price + asks[0].price) / 2.0;
    }
};

// ── CancelMapEntry ────────────────────────────────────────────────────────────

struct CancelEntry {
    Side       side;
    double     price;
    OrderNode* node;
};

// ── OrderBook ─────────────────────────────────────────────────────────────────

class OrderBook {
public:
    explicit OrderBook(const char* symbol) noexcept;

    /**
     * Submit an order to the book.
     *
     * The caller must supply a pre-allocated OrderNode from its pool.
     * The engine does NOT allocate memory on this path.
     *
     * @param node  Pre-allocated OrderNode (fields filled by caller).
     * @return      MatchResult with trade list and final order status.
     */
    MatchResult submit(OrderNode* node) noexcept;

    /**
     * Cancel a resting order. Returns the cancelled node or nullptr.
     * O(log P) — map lookup + intrusive list removal.
     */
    OrderNode* cancel(const uint8_t order_id[16]) noexcept;

    /**
     * Depth-limited snapshot. O(depth).
     */
    BookSnapshot snapshot(int depth = 10) const noexcept;

    uint64_t sequence() const noexcept { return sequence_; }

    double best_bid() const noexcept;
    double best_ask() const noexcept;

private:
    bool crosses(Side incoming_side, double incoming_price, double level_price) const noexcept;
    void rest_order(OrderNode* node) noexcept;

    char     symbol_[6];
    uint64_t sequence_ = 0;

    // Bids: descending order (highest first)
    std::map<double, PriceLevel, std::greater<double>> bids_;
    // Asks: ascending order (lowest first)
    std::map<double, PriceLevel>                       asks_;

    // UUID (16 bytes) → cancel map entry
    struct UUIDHash {
        size_t operator()(const std::array<uint8_t, 16>& id) const noexcept;
    };
    std::unordered_map<std::array<uint8_t, 16>, CancelEntry, UUIDHash> cancel_map_;
};

}  // namespace hft

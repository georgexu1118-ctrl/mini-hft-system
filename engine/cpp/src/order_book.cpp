/**
 * engine/cpp/src/order_book.cpp
 *
 * OrderBook implementation.
 *
 * Build:
 *   g++ -std=c++20 -O3 -march=native -o libhft_engine.so \
 *       -shared -fPIC src/order_book.cpp
 *
 * For Python binding (M7 pybind11):
 *   See engine/cpp/src/bindings.cpp
 *
 * For FPGA DMA path (M8):
 *   Replace submit() with submit_from_dma_ring() in engine_hal.cpp.
 *   The matching loop itself is identical — only the input path changes.
 */
#include "../include/order_book.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <numeric>

namespace hft {

// ── UUIDHash ──────────────────────────────────────────────────────────────────

size_t OrderBook::UUIDHash::operator()(const std::array<uint8_t, 16>& id) const noexcept {
    // FNV-1a over 16 bytes — fast, good distribution for random UUIDs
    size_t hash = 14695981039346656037ULL;
    for (auto b : id) {
        hash ^= b;
        hash *= 1099511628211ULL;
    }
    return hash;
}

// ── Construction ──────────────────────────────────────────────────────────────

OrderBook::OrderBook(const char* symbol) noexcept : sequence_(0) {
    std::memset(symbol_, 0, sizeof(symbol_));
    std::strncpy(symbol_, symbol, sizeof(symbol_) - 1);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

bool OrderBook::crosses(Side incoming_side, double incoming_price, double level_price) const noexcept {
    if (incoming_side == Side::BUY)  return incoming_price >= level_price;
    return incoming_price <= level_price;
}

void OrderBook::rest_order(OrderNode* node) noexcept {
    auto& book = (node->side == Side::BUY) ? bids_ : asks_;
    auto  it   = book.find(node->price);
    if (it == book.end()) {
        PriceLevel level;
        level.price = node->price;
        auto [ins, ok] = book.emplace(node->price, std::move(level));
        it = ins;
    }
    it->second.push_back(node);

    std::array<uint8_t, 16> key;
    std::memcpy(key.data(), node->order_id, 16);
    cancel_map_[key] = CancelEntry{ node->side, node->price, node };

    if (node->filled_quantity == 0) node->status = OStatus::OPEN;
    // else: preserve PARTIALLY_FILLED
}

// ── Submit ────────────────────────────────────────────────────────────────────

MatchResult OrderBook::submit(OrderNode* node) noexcept {
    ++sequence_;
    MatchResult result;
    result.order = node;

    auto& opposite = (node->side == Side::BUY) ? asks_ : bids_;

    // ── Matching loop ─────────────────────────────────────────────────────────
    while (node->remaining_quantity() > 0 && !opposite.empty()) {
        auto it = opposite.begin();          // best price (ascending asks or descending bids)
        const double best_price = it->first;

        // Check if incoming price crosses the best level
        const double cross_price = node->is_market() ? best_price : node->price;
        if (!crosses(node->side, cross_price, best_price)) break;

        PriceLevel& level = it->second;

        while (node->remaining_quantity() > 0 && !level.empty()) {
            OrderNode* resting = level.front();
            const uint32_t fill_qty = std::min(node->remaining_quantity(), resting->remaining_quantity());

            // Maker price governs the trade
            node->filled_quantity    += fill_qty;
            resting->filled_quantity += fill_qty;
            level.total_quantity     -= fill_qty;

            result.trades.push_back(MatchTrade{
                resting->price,
                fill_qty,
                node->side,
                resting,
                node,
            });

            if (resting->remaining_quantity() == 0) {
                resting->status = OStatus::FILLED;
                level.remove(resting);
                level.order_count--;   // already decremented in remove but let's be consistent
                std::array<uint8_t, 16> key;
                std::memcpy(key.data(), resting->order_id, 16);
                cancel_map_.erase(key);
            } else {
                resting->status = OStatus::PARTIALLY_FILLED;
            }
        }

        if (level.empty()) {
            opposite.erase(it);
        }
    }

    // ── Update status ─────────────────────────────────────────────────────────
    const auto now_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count()
    );
    node->processed_at_ns = now_ns;

    if (node->remaining_quantity() == 0) {
        node->status = OStatus::FILLED;
    } else if (node->filled_quantity > 0) {
        node->status = OStatus::PARTIALLY_FILLED;
    }

    // ── TIF policy ────────────────────────────────────────────────────────────
    if (node->remaining_quantity() > 0 && node->is_limit()) {
        switch (node->time_in_force) {
            case TIF::GTC:
            case TIF::GTD:
                rest_order(node);
                result.resting = true;
                break;
            case TIF::IOC:
            case TIF::FOK:
                node->status = OStatus::CANCELLED;
                break;
        }
    }

    return result;
}

// ── Cancel ────────────────────────────────────────────────────────────────────

OrderNode* OrderBook::cancel(const uint8_t order_id[16]) noexcept {
    std::array<uint8_t, 16> key;
    std::memcpy(key.data(), order_id, 16);

    auto map_it = cancel_map_.find(key);
    if (map_it == cancel_map_.end()) return nullptr;

    CancelEntry entry = map_it->second;
    cancel_map_.erase(map_it);

    auto& book = (entry.side == Side::BUY) ? bids_ : asks_;
    auto  it   = book.find(entry.price);
    if (it == book.end()) return nullptr;

    it->second.remove(entry.node);
    if (it->second.empty()) book.erase(it);

    entry.node->status = OStatus::CANCELLED;
    return entry.node;
}

// ── Snapshot ──────────────────────────────────────────────────────────────────

BookSnapshot OrderBook::snapshot(int depth) const noexcept {
    BookSnapshot snap;
    std::memcpy(snap.symbol, symbol_, 6);
    snap.sequence    = sequence_;
    snap.timestamp_ns = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count()
    );

    int i = 0;
    for (const auto& [price, level] : bids_) {
        if (i++ >= depth) break;
        snap.bids.push_back({ price, level.total_quantity, level.order_count });
    }

    i = 0;
    for (const auto& [price, level] : asks_) {
        if (i++ >= depth) break;
        snap.asks.push_back({ price, level.total_quantity, level.order_count });
    }

    return snap;
}

// ── BBO ───────────────────────────────────────────────────────────────────────

double OrderBook::best_bid() const noexcept {
    return bids_.empty() ? 0.0 : bids_.begin()->first;
}

double OrderBook::best_ask() const noexcept {
    return asks_.empty() ? 0.0 : asks_.begin()->first;
}

}  // namespace hft

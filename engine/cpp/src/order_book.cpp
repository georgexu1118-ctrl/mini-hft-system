/**
 * Price-time-priority CLOB implementation for the native C++ backend.
 *
 * This file has no Python awareness. Wire decoding and object ownership sit in
 * MatchingEngineFacade so that the matching loop remains synchronous and I/O-free.
 */
#include "../include/order_book.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>

namespace hft {

namespace {
uint64_t now_ns() noexcept {
    return static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch()
        ).count()
    );
}
}  // namespace

size_t OrderBook::UUIDHash::operator()(const std::array<uint8_t, 16>& id) const noexcept {
    size_t hash = 14695981039346656037ULL;
    for (const auto byte : id) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

OrderBook::OrderBook(const char* symbol) noexcept {
    std::memset(symbol_, 0, sizeof(symbol_));
    // Six-character instruments occupy the complete fixed-width wire field;
    // NUL termination is neither promised nor needed inside the snapshot ABI.
    std::memcpy(symbol_, symbol, std::min(std::strlen(symbol), sizeof(symbol_)));
}

bool OrderBook::crosses(
    Side incoming_side,
    double incoming_price,
    double level_price
) const noexcept {
    return incoming_side == Side::BUY
        ? incoming_price >= level_price
        : incoming_price <= level_price;
}

void OrderBook::rest_order(OrderNode* node) {
    // The comparators make bids_ and asks_ different C++ types. One side branch
    // here is preferable to type erasure or indirection in every match step.
    if (node->side == Side::BUY) {
        auto [it, inserted] = bids_.try_emplace(node->price);
        if (inserted) it->second.price = node->price;
        it->second.push_back(node);
    } else {
        auto [it, inserted] = asks_.try_emplace(node->price);
        if (inserted) it->second.price = node->price;
        it->second.push_back(node);
    }

    std::array<uint8_t, 16> key{};
    std::memcpy(key.data(), node->order_id, key.size());
    cancel_map_[key] = CancelEntry{node->side, node->price, node};

    if (node->filled_quantity == 0) {
        node->status = OStatus::OPEN;
    }
}

template <typename OppositeBook>
void OrderBook::match_against(
    OrderNode* node,
    OppositeBook& opposite,
    MatchResult& result
) {
    // O(F log P): consume FIFO orders and erase each emptied level once.
    while (node->remaining_quantity() > 0 && !opposite.empty()) {
        auto level_it = opposite.begin();
        const double best_price = level_it->first;
        const double crossing_price = node->is_market() ? best_price : node->price;
        if (!crosses(node->side, crossing_price, best_price)) {
            break;
        }

        PriceLevel& level = level_it->second;
        while (node->remaining_quantity() > 0 && !level.empty()) {
            OrderNode* resting = level.front();
            const uint32_t fill_quantity = std::min(
                node->remaining_quantity(),
                resting->remaining_quantity()
            );
            node->filled_quantity += fill_quantity;
            resting->filled_quantity += fill_quantity;
            level.total_quantity -= fill_quantity;
            result.trades.push_back(MatchTrade{
                ++trade_sequence_,
                now_ns(),
                resting->price,
                fill_quantity,
                node->side,
                resting,
                node,
            });

            if (resting->remaining_quantity() == 0) {
                resting->status = OStatus::FILLED;
                // Remaining quantity is zero, so remove changes list metadata
                // without double-subtracting the quantity already consumed.
                level.remove(resting);
                std::array<uint8_t, 16> key{};
                std::memcpy(key.data(), resting->order_id, key.size());
                cancel_map_.erase(key);
            } else {
                resting->status = OStatus::PARTIALLY_FILLED;
            }
        }
        if (level.empty()) {
            opposite.erase(level_it);
        }
    }
}

MatchResult OrderBook::submit(OrderNode* node) {
    // O(log P + F): map work plus generated executions; no I/O or locking.
    ++sequence_;
    MatchResult result;
    result.order = node;
    result.trades.reserve(4);  // Most orders fill at only a handful of levels.

    if (node->side == Side::BUY) {
        match_against(node, asks_, result);
    } else {
        match_against(node, bids_, result);
    }

    node->processed_at_ns = now_ns();
    if (node->remaining_quantity() == 0) {
        node->status = OStatus::FILLED;
    } else if (node->filled_quantity > 0) {
        node->status = OStatus::PARTIALLY_FILLED;
    }

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

OrderNode* OrderBook::cancel(const uint8_t order_id[16]) noexcept {
    // O(log P): cancel_map resolves the intrusive node without a FIFO scan.
    std::array<uint8_t, 16> key{};
    std::memcpy(key.data(), order_id, key.size());
    const auto entry_it = cancel_map_.find(key);
    if (entry_it == cancel_map_.end()) {
        return nullptr;
    }
    const CancelEntry entry = entry_it->second;
    cancel_map_.erase(entry_it);

    if (entry.side == Side::BUY) {
        const auto it = bids_.find(entry.price);
        if (it == bids_.end()) return nullptr;
        it->second.remove(entry.node);
        if (it->second.empty()) bids_.erase(it);
    } else {
        const auto it = asks_.find(entry.price);
        if (it == asks_.end()) return nullptr;
        it->second.remove(entry.node);
        if (it->second.empty()) asks_.erase(it);
    }
    entry.node->status = OStatus::CANCELLED;
    return entry.node;
}

BookSnapshot OrderBook::snapshot(int depth) const {
    BookSnapshot snapshot;
    std::memcpy(snapshot.symbol, symbol_, sizeof(symbol_));
    snapshot.sequence = sequence_;
    snapshot.timestamp_ns = now_ns();
    snapshot.bids.reserve(static_cast<size_t>(depth));
    snapshot.asks.reserve(static_cast<size_t>(depth));

    int included = 0;
    for (const auto& [price, level] : bids_) {
        if (included++ >= depth) break;
        snapshot.bids.push_back({price, level.total_quantity, level.order_count});
    }
    included = 0;
    for (const auto& [price, level] : asks_) {
        if (included++ >= depth) break;
        snapshot.asks.push_back({price, level.total_quantity, level.order_count});
    }
    return snapshot;
}

double OrderBook::best_bid() const noexcept {
    return bids_.empty() ? 0.0 : bids_.begin()->first;
}

double OrderBook::best_ask() const noexcept {
    return asks_.empty() ? 0.0 : asks_.begin()->first;
}

}  // namespace hft

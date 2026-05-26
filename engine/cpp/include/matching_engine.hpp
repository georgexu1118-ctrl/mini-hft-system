/**
 * Multi-symbol ownership/validation facade above the single-symbol OrderBook.
 *
 * The facade owns stable OrderNode addresses for the duration of engine life.
 * This first measurable C++ implementation allocates one node per accepted
 * order. A slab pool is a later, benchmark-driven optimization; correctness and
 * stable cancellation pointers take precedence for this milestone.
 */
#pragma once

#include "order_book.hpp"

#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace hft {

enum class RejectCode : uint8_t {
    NONE = 0,
    SYMBOL_NOT_FOUND = 1,
    DUPLICATE_ORDER_ID = 2,
    INVALID_QUANTITY = 3,
    INVALID_PRICE = 4,
};

struct Submission {
    MatchResult result;
    BookSnapshot snapshot;
    bool has_snapshot = false;
    RejectCode rejection = RejectCode::NONE;
};

struct NativeStats {
    uint64_t orders_received = 0;
    uint64_t orders_matched = 0;
    uint64_t orders_resting = 0;
    uint64_t orders_cancelled = 0;
    uint64_t orders_rejected = 0;
    uint64_t trades_executed = 0;
    uint64_t total_volume = 0;
};

class MatchingEngineFacade {
public:
    void register_symbol(const std::string& symbol);
    Submission submit(const Order& order);
    OrderNode* cancel(const uint8_t order_id[16]);
    const OrderNode* get_order(const uint8_t order_id[16]) const;
    BookSnapshot* snapshot(const std::string& symbol, int depth);
    std::vector<std::string> registered_symbols() const;
    const NativeStats& stats() const noexcept { return stats_; }

private:
    static std::string id_key(const uint8_t order_id[16]);
    static std::string symbol_key(const char symbol[6]);

    std::unordered_map<std::string, std::unique_ptr<OrderBook>> books_;
    std::unordered_map<std::string, OrderNode*> orders_;
    std::vector<std::unique_ptr<OrderNode>> owned_orders_;
    BookSnapshot scratch_snapshot_;
    NativeStats stats_;
};

}  // namespace hft

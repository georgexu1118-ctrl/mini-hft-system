#include "../include/matching_engine.hpp"

#include <cstring>
#include <stdexcept>

namespace hft {

std::string MatchingEngineFacade::id_key(const uint8_t order_id[16]) {
    return std::string(reinterpret_cast<const char*>(order_id), 16);
}

std::string MatchingEngineFacade::symbol_key(const char symbol[6]) {
    size_t length = 0;
    while (length < 6 && symbol[length] != '\0') ++length;
    return std::string(symbol, length);
}

void MatchingEngineFacade::register_symbol(const std::string& symbol) {
    if (symbol.empty() || symbol.size() > 6) {
        throw std::invalid_argument("wire ABI symbols must contain 1 to 6 ASCII bytes");
    }
    if (!books_.contains(symbol)) {
        books_[symbol] = std::make_unique<OrderBook>(symbol.c_str());
    }
}

Submission MatchingEngineFacade::submit(const Order& order) {
    ++stats_.orders_received;
    Submission submission;
    auto node = std::make_unique<OrderNode>();
    static_cast<Order&>(*node) = order;
    submission.result.order = node.get();

    const std::string symbol = symbol_key(node->symbol);
    const std::string id = id_key(node->order_id);
    const auto book_it = books_.find(symbol);
    if (book_it == books_.end()) {
        node->status = OStatus::REJECTED;
        submission.rejection = RejectCode::SYMBOL_NOT_FOUND;
    } else if (orders_.contains(id)) {
        node->status = OStatus::REJECTED;
        submission.rejection = RejectCode::DUPLICATE_ORDER_ID;
    } else if (node->quantity == 0) {
        node->status = OStatus::REJECTED;
        submission.rejection = RejectCode::INVALID_QUANTITY;
    } else if (node->is_limit() && node->price <= 0.0) {
        node->status = OStatus::REJECTED;
        submission.rejection = RejectCode::INVALID_PRICE;
    }

    if (submission.rejection != RejectCode::NONE) {
        ++stats_.orders_rejected;
        owned_orders_.push_back(std::move(node));
        return submission;
    }

    submission.result = book_it->second->submit(node.get());
    submission.snapshot = book_it->second->snapshot();
    submission.has_snapshot = true;
    orders_[id] = node.get();
    owned_orders_.push_back(std::move(node));
    if (submission.result.resting) ++stats_.orders_resting;
    if (!submission.result.trades.empty()) {
        ++stats_.orders_matched;
        stats_.trades_executed += submission.result.trades.size();
        stats_.total_volume += submission.result.total_filled();
    }
    return submission;
}

OrderNode* MatchingEngineFacade::cancel(const uint8_t order_id[16]) {
    const auto order_it = orders_.find(id_key(order_id));
    if (order_it == orders_.end()) return nullptr;
    OrderNode* order = order_it->second;
    const auto book_it = books_.find(symbol_key(order->symbol));
    if (book_it == books_.end()) return nullptr;
    OrderNode* cancelled = book_it->second->cancel(order_id);
    if (cancelled != nullptr) {
        ++stats_.orders_cancelled;
        if (stats_.orders_resting > 0) --stats_.orders_resting;
    }
    return cancelled;
}

const OrderNode* MatchingEngineFacade::get_order(const uint8_t order_id[16]) const {
    const auto order_it = orders_.find(id_key(order_id));
    return order_it == orders_.end() ? nullptr : order_it->second;
}

BookSnapshot* MatchingEngineFacade::snapshot(const std::string& symbol, int depth) {
    const auto book_it = books_.find(symbol);
    if (book_it == books_.end()) return nullptr;
    scratch_snapshot_ = book_it->second->snapshot(depth);
    return &scratch_snapshot_;
}

std::vector<std::string> MatchingEngineFacade::registered_symbols() const {
    std::vector<std::string> symbols;
    symbols.reserve(books_.size());
    for (const auto& [symbol, ignored] : books_) {
        (void)ignored;
        symbols.push_back(symbol);
    }
    return symbols;
}

}  // namespace hft

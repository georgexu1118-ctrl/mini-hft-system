/**
 * engine/cpp/include/price_level.hpp
 *
 * A single price level in the CLOB — an intrusive doubly-linked list of
 * resting orders at the same price, maintained FIFO.
 *
 * Intrusive vs. external:
 *   Python deque stores pointers (or references) to Order objects.
 *   This C++ version stores prev/next pointers INSIDE the Order (see
 *   OrderNode below). This means:
 *     - Cancel is O(1) given a pointer to the node (no scan needed)
 *     - Memory layout is contiguous — cache-friendly during matching
 *     - No heap allocation for the list nodes themselves
 *
 * The cancel_map stores (side, price, Order*) — the pointer gives us
 * O(1) list removal via the intrusive links.
 *
 * FPGA note (M8):
 *   On FPGA, price levels are direct-mapped arrays in BRAM indexed by
 *   price tick. The "linked list" is replaced by head/tail BRAM indices.
 *   Fill logic simply reads from BRAM[head], decrements quantity, and
 *   advances head — no dynamic memory at all.
 */
#pragma once

#include "order.hpp"
#include <cstdint>

namespace hft {

/**
 * OrderNode extends Order with intrusive list links.
 * Placed in the cancel_map's pool; cast to Order* for the HAL surface.
 */
struct OrderNode : Order {
    OrderNode* prev = nullptr;
    OrderNode* next = nullptr;
};

/**
 * PriceLevel: head/tail pointers + aggregate stats.
 *
 * Invariant: total_quantity == sum(n.remaining_quantity() for n in list).
 * Maintained incrementally — O(1) to read.
 */
struct PriceLevel {
    double      price          = 0.0;
    uint32_t    total_quantity = 0;
    uint32_t    order_count    = 0;
    OrderNode*  head           = nullptr;  // oldest (front of FIFO queue)
    OrderNode*  tail           = nullptr;  // newest

    void push_back(OrderNode* node) noexcept;
    void remove(OrderNode* node) noexcept;
    OrderNode* front() const noexcept { return head; }
    bool empty() const noexcept { return head == nullptr; }
};

inline void PriceLevel::push_back(OrderNode* node) noexcept {
    node->prev = tail;
    node->next = nullptr;
    if (tail) tail->next = node;
    else      head = node;
    tail = node;
    total_quantity += node->remaining_quantity();
    ++order_count;
}

inline void PriceLevel::remove(OrderNode* node) noexcept {
    if (node->prev) node->prev->next = node->next;
    else            head = node->next;
    if (node->next) node->next->prev = node->prev;
    else            tail = node->prev;
    total_quantity -= node->remaining_quantity();
    --order_count;
    node->prev = node->next = nullptr;
}

}  // namespace hft

/**
 * engine/cpp/tests/test_order_book.cpp
 *
 * Standalone C++ unit tests for OrderBook (no Python, no pybind11).
 * Compile with CMake: cmake -DCMAKE_BUILD_TYPE=Release .. && make test_engine
 *
 * Scenarios mirror the Python test_order_book.py to ensure identical
 * behaviour from both implementations (conformance property).
 */
#include "../include/order.hpp"
#include "../include/order_book.hpp"

#include <cassert>
#include <cstdio>
#include <cstring>

using namespace hft;

// ── Test helpers ──────────────────────────────────────────────────────────────

static int tests_run = 0, tests_pass = 0;

#define TEST(name)                                                       \
    static void name();                                                   \
    static struct name##_reg { name##_reg() { run_test(#name, name); } } \
        name##_instance;                                                  \
    static void name()

#define ASSERT(cond)                                                       \
    do {                                                                    \
        if (!(cond)) {                                                      \
            fprintf(stderr, "  FAIL  %s:%d  %s\n", __FILE__, __LINE__, #cond); \
            throw std::runtime_error(#cond);                               \
        }                                                                   \
    } while (0)

static void run_test(const char* name, void (*fn)()) {
    ++tests_run;
    try {
        fn();
        printf("  PASS  %s\n", name);
        ++tests_pass;
    } catch (const std::exception& e) {
        printf("  FAIL  %s: %s\n", name, e.what());
    }
}

// ── Node factory (replaces memory pool for tests) ─────────────────────────────

static OrderNode* make_order(
    Side side,
    double price,
    uint32_t qty = 100,
    OType otype  = OType::LIMIT,
    TIF   tif    = TIF::GTC
) {
    auto* node = new OrderNode{};
    // Random-ish UUID based on address
    const uintptr_t addr = reinterpret_cast<uintptr_t>(node);
    std::memcpy(node->order_id, &addr, sizeof(addr));
    node->price         = price;
    node->quantity      = qty;
    node->side          = side;
    node->order_type    = otype;
    node->time_in_force = tif;
    node->status        = OStatus::PENDING;
    return node;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

TEST(test_resting_buy_order) {
    OrderBook book("AAPL");
    auto* o = make_order(Side::BUY, 150.0);
    auto result = book.submit(o);
    ASSERT(result.trades.empty());
    ASSERT(result.resting);
    ASSERT(o->status == OStatus::OPEN);
    ASSERT(book.best_bid() == 150.0);
    ASSERT(book.best_ask() == 0.0);
    delete o;
}

TEST(test_matching_buy_sell) {
    OrderBook book("AAPL");
    auto* buy  = make_order(Side::BUY,  150.0, 100);
    auto* sell = make_order(Side::SELL, 150.0, 100);
    book.submit(buy);
    auto result = book.submit(sell);
    ASSERT(result.trades.size() == 1);
    ASSERT(result.trades[0].quantity  == 100);
    ASSERT(result.trades[0].price     == 150.0);
    ASSERT(buy->status  == OStatus::FILLED);
    ASSERT(sell->status == OStatus::FILLED);
    delete buy;
    delete sell;
}

TEST(test_partial_fill) {
    OrderBook book("AAPL");
    auto* buy  = make_order(Side::BUY,  150.0, 200);
    auto* sell = make_order(Side::SELL, 150.0, 100);
    book.submit(buy);
    auto result = book.submit(sell);
    ASSERT(result.trades.size() == 1);
    ASSERT(result.trades[0].quantity == 100);
    ASSERT(buy->filled_quantity  == 100);
    ASSERT(buy->status           == OStatus::PARTIALLY_FILLED);
    ASSERT(sell->status          == OStatus::FILLED);
    delete buy;
    delete sell;
}

TEST(test_cancel_resting_order) {
    OrderBook book("AAPL");
    auto* o = make_order(Side::BUY, 150.0);
    book.submit(o);
    auto* cancelled = book.cancel(o->order_id);
    ASSERT(cancelled != nullptr);
    ASSERT(cancelled->status == OStatus::CANCELLED);
    ASSERT(book.best_bid() == 0.0);
    delete o;
}

TEST(test_cancel_nonexistent_returns_nullptr) {
    OrderBook book("AAPL");
    uint8_t fake_id[16] = {0xFF, 0xFE};
    ASSERT(book.cancel(fake_id) == nullptr);
}

TEST(test_ioc_cancelled_when_no_match) {
    OrderBook book("AAPL");
    auto* o = make_order(Side::BUY, 150.0, 100, OType::LIMIT, TIF::IOC);
    auto result = book.submit(o);
    ASSERT(!result.resting);
    ASSERT(o->status == OStatus::CANCELLED);
    ASSERT(book.best_bid() == 0.0);
    delete o;
}

TEST(test_market_order_sweeps_book) {
    OrderBook book("AAPL");
    // Place 3 sells at different prices
    auto* s1 = make_order(Side::SELL, 100.0, 10);
    auto* s2 = make_order(Side::SELL, 101.0, 10);
    auto* s3 = make_order(Side::SELL, 102.0, 10);
    book.submit(s1);
    book.submit(s2);
    book.submit(s3);

    auto* buy = make_order(Side::BUY, 0.0, 30, OType::MARKET);
    auto result = book.submit(buy);
    ASSERT(result.trades.size() == 3);
    ASSERT(buy->filled_quantity == 30);
    ASSERT(buy->status == OStatus::FILLED);
    delete s1; delete s2; delete s3; delete buy;
}

TEST(test_snapshot_depth_limited) {
    OrderBook book("AAPL");
    for (int i = 0; i < 5; ++i) {
        auto* o = make_order(Side::BUY, 100.0 - i);
        book.submit(o);
        delete o;
    }
    auto snap = book.snapshot(3);
    ASSERT(snap.bids.size() == 3);
    ASSERT(snap.bids[0].price == 100.0);  // best bid first
    ASSERT(snap.bids[1].price == 99.0);
    ASSERT(snap.bids[2].price == 98.0);
}

TEST(test_price_time_priority) {
    OrderBook book("AAPL");
    // Two buys at same price — first one should match first
    auto* b1 = make_order(Side::BUY, 150.0, 50);
    auto* b2 = make_order(Side::BUY, 150.0, 50);
    book.submit(b1);
    book.submit(b2);

    // Sell 50 should take from b1 only
    auto* sell = make_order(Side::SELL, 150.0, 50);
    auto result = book.submit(sell);
    ASSERT(result.trades.size() == 1);
    ASSERT(b1->filled_quantity == 50);
    ASSERT(b2->filled_quantity == 0);
    delete b1; delete b2; delete sell;
}

// ── Main ──────────────────────────────────────────────────────────────────────

int main() {
    printf("\n=== C++ OrderBook Tests ===\n\n");
    printf("%d passed, %d failed out of %d tests\n\n",
        tests_pass, tests_run - tests_pass, tests_run);
    return (tests_pass == tests_run) ? 0 : 1;
}

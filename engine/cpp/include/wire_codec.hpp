/**
 * Explicit codec for the Python/FPGA big-endian wire ABI.
 *
 * Native C++ objects must never be memcpy'd directly from ORDER_FRAME: doubles
 * and integers in Python's `struct(">...")` format are network-byte-order.
 * Keeping this conversion isolated makes ABI assumptions reviewable and lets a
 * future DMA backend reuse the identical frames without leaking host layout.
 */
#pragma once

#include "order.hpp"
#include "order_book.hpp"

#include <bit>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>

namespace hft::wire {

constexpr size_t ORDER_FRAME_SIZE = 64;
constexpr size_t TRADE_FRAME_SIZE = 64;
constexpr size_t SNAPSHOT_HEADER_SIZE = 32;
constexpr size_t BOOK_LEVEL_SIZE = 16;
constexpr size_t RESULT_HEADER_SIZE = 8;

inline uint32_t read_u32(const uint8_t* source) noexcept {
    return (static_cast<uint32_t>(source[0]) << 24U)
        | (static_cast<uint32_t>(source[1]) << 16U)
        | (static_cast<uint32_t>(source[2]) << 8U)
        | static_cast<uint32_t>(source[3]);
}

inline uint64_t read_u64(const uint8_t* source) noexcept {
    uint64_t value = 0;
    for (size_t index = 0; index < 8; ++index) {
        value = (value << 8U) | source[index];
    }
    return value;
}

inline void write_u16(std::string& output, size_t offset, uint16_t value) noexcept {
    output[offset] = static_cast<char>((value >> 8U) & 0xffU);
    output[offset + 1] = static_cast<char>(value & 0xffU);
}

inline void write_u32(std::string& output, size_t offset, uint32_t value) noexcept {
    for (size_t index = 0; index < 4; ++index) {
        output[offset + index] = static_cast<char>((value >> (24U - index * 8U)) & 0xffU);
    }
}

inline void write_u64(std::string& output, size_t offset, uint64_t value) noexcept {
    for (size_t index = 0; index < 8; ++index) {
        output[offset + index] = static_cast<char>((value >> (56U - index * 8U)) & 0xffU);
    }
}

inline double read_double(const uint8_t* source) noexcept {
    return std::bit_cast<double>(read_u64(source));
}

inline void write_double(std::string& output, size_t offset, double value) noexcept {
    write_u64(output, offset, std::bit_cast<uint64_t>(value));
}

inline bool decode_order(std::string_view frame, Order& order) noexcept {
    if (frame.size() != ORDER_FRAME_SIZE) return false;
    const auto* source = reinterpret_cast<const uint8_t*>(frame.data());
    std::memcpy(order.order_id, source, 16);
    order.price = read_double(source + 16);
    order.quantity = read_u32(source + 24);
    order.filled_quantity = read_u32(source + 28);
    order.created_at_ns = read_u64(source + 32);
    order.processed_at_ns = read_u64(source + 40);
    std::memcpy(order.symbol, source + 48, 6);
    order.side = static_cast<Side>(source[54]);
    order.order_type = static_cast<OType>(source[55]);
    order.time_in_force = static_cast<TIF>(source[56]);
    order.status = static_cast<OStatus>(source[57]);
    return true;
}

inline std::string encode_order(const Order& order) {
    std::string output(ORDER_FRAME_SIZE, '\0');
    std::memcpy(output.data(), order.order_id, 16);
    write_double(output, 16, order.price);
    write_u32(output, 24, order.quantity);
    write_u32(output, 28, order.filled_quantity);
    write_u64(output, 32, order.created_at_ns);
    write_u64(output, 40, order.processed_at_ns);
    std::memcpy(output.data() + 48, order.symbol, 6);
    output[54] = static_cast<char>(order.side);
    output[55] = static_cast<char>(order.order_type);
    output[56] = static_cast<char>(order.time_in_force);
    output[57] = static_cast<char>(order.status);
    return output;
}

inline void trade_id(std::string& output, size_t offset, const MatchTrade& trade) noexcept {
    // Completion identifiers are deterministic and unique within one engine:
    // execution time plus monotonic trade sequence, each in network byte order.
    write_u64(output, offset, trade.timestamp_ns);
    write_u64(output, offset + 8, trade.sequence);
}

inline std::string encode_match_result(const MatchResult& result) {
    std::string output(
        RESULT_HEADER_SIZE + result.trades.size() * TRADE_FRAME_SIZE,
        '\0'
    );
    write_u16(output, 0, static_cast<uint16_t>(result.trades.size()));
    write_u32(output, 2, result.total_filled());
    output[6] = static_cast<char>(result.order->status);
    output[7] = result.resting ? 1 : 0;
    for (size_t index = 0; index < result.trades.size(); ++index) {
        const auto& trade = result.trades[index];
        const size_t offset = RESULT_HEADER_SIZE + index * TRADE_FRAME_SIZE;
        trade_id(output, offset, trade);
        write_double(output, offset + 16, trade.price);
        write_u32(output, offset + 24, trade.quantity);
        output[offset + 28] = static_cast<char>(trade.aggressor_side);
        write_u64(output, offset + 32, trade.timestamp_ns);
        write_u64(output, offset + 40, trade.sequence);
        std::memcpy(output.data() + offset + 48, trade.maker->order_id, 16);
    }
    return output;
}

inline std::string encode_snapshot(const BookSnapshot& snapshot) {
    std::string output(
        SNAPSHOT_HEADER_SIZE
            + (snapshot.bids.size() + snapshot.asks.size()) * BOOK_LEVEL_SIZE,
        '\0'
    );
    std::memcpy(output.data(), snapshot.symbol, 6);
    write_u16(output, 6, static_cast<uint16_t>(snapshot.bids.size()));
    write_u16(output, 8, static_cast<uint16_t>(snapshot.asks.size()));
    write_u32(output, 12, static_cast<uint32_t>(snapshot.sequence));
    write_u64(output, 16, snapshot.timestamp_ns);
    size_t offset = SNAPSHOT_HEADER_SIZE;
    const auto append = [&output, &offset](const LevelSnapshot& level) {
        write_double(output, offset, level.price);
        write_u32(output, offset + 8, level.quantity);
        write_u32(output, offset + 12, level.order_count);
        offset += BOOK_LEVEL_SIZE;
    };
    for (const auto& level : snapshot.bids) append(level);
    for (const auto& level : snapshot.asks) append(level);
    return output;
}

}  // namespace hft::wire

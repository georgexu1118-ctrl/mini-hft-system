/**
 * Thin pybind11 surface over MatchingEngineFacade.
 *
 * Domain reconstruction deliberately remains in CppMatchingHAL. The extension
 * accepts/returns fixed binary frames and scalar trade tuples, so it neither
 * imports Python domain classes nor couples native matching to FastAPI events.
 */
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "../include/matching_engine.hpp"
#include "../include/wire_codec.hpp"

#include <stdexcept>
#include <string>
#include <string_view>

namespace py = pybind11;
using namespace hft;

namespace {

std::string_view bytes_view(const py::bytes& value) {
    char* buffer = nullptr;
    Py_ssize_t size = 0;
    if (PyBytes_AsStringAndSize(value.ptr(), &buffer, &size) != 0) {
        throw py::error_already_set();
    }
    // The Python bytes argument owns this immutable memory for the complete
    // binding invocation; native decoding can therefore avoid an ingress copy.
    return std::string_view(buffer, static_cast<size_t>(size));
}

py::bytes identifier_bytes(const uint8_t identifier[16]) {
    return py::bytes(reinterpret_cast<const char*>(identifier), 16);
}

py::bytes execution_id(const MatchTrade& trade) {
    std::string identifier(16, '\0');
    wire::write_u64(identifier, 0, trade.timestamp_ns);
    wire::write_u64(identifier, 8, trade.sequence);
    return py::bytes(identifier);
}

class CppEngine {
public:
    void register_symbol(const std::string& symbol) {
        engine_.register_symbol(symbol);
    }

    py::dict submit_order_detail(const py::bytes& raw_frame) {
        Order order{};
        const std::string_view frame = bytes_view(raw_frame);
        if (!wire::decode_order(frame, order)) {
            throw std::invalid_argument("ORDER_FRAME must contain exactly 64 bytes");
        }

        Submission submission;
        {
            py::gil_scoped_release release;
            submission = engine_.submit(order);
        }
        py::dict output;
        output["order_frame"] = py::bytes(wire::encode_order(*submission.result.order));
        output["is_new_resting"] = submission.result.resting;
        output["rejection"] = static_cast<uint8_t>(submission.rejection);
        if (submission.has_snapshot) {
            output["snapshot_frame"] = py::bytes(wire::encode_snapshot(submission.snapshot));
        } else {
            output["snapshot_frame"] = py::none();
        }

        py::list trades;
        for (const auto& trade : submission.result.trades) {
            trades.append(py::make_tuple(
                execution_id(trade),
                trade.price,
                trade.quantity,
                static_cast<uint8_t>(trade.aggressor_side),
                identifier_bytes(trade.maker->order_id),
                identifier_bytes(trade.taker->order_id),
                trade.timestamp_ns
            ));
        }
        output["trades"] = std::move(trades);
        return output;
    }

    py::bytes submit_order_bytes(const py::bytes& raw_frame) {
        Order order{};
        const std::string_view frame = bytes_view(raw_frame);
        if (!wire::decode_order(frame, order)) {
            throw std::invalid_argument("ORDER_FRAME must contain exactly 64 bytes");
        }
        Submission submission;
        {
            py::gil_scoped_release release;
            submission = engine_.submit(order);
        }
        return py::bytes(wire::encode_match_result(submission.result));
    }

    py::object cancel_order_bytes(const py::bytes& raw_identifier) {
        const std::string_view identifier = bytes_view(raw_identifier);
        if (identifier.size() < 16) {
            throw std::invalid_argument("cancel identifier must contain 16 bytes");
        }
        OrderNode* cancelled;
        {
            py::gil_scoped_release release;
            cancelled = engine_.cancel(
                reinterpret_cast<const uint8_t*>(identifier.data())
            );
        }
        if (cancelled == nullptr) return py::none();
        return py::bytes(wire::encode_order(*cancelled));
    }

    py::object get_order_bytes(const py::bytes& raw_identifier) const {
        const std::string_view identifier = bytes_view(raw_identifier);
        if (identifier.size() < 16) return py::none();
        const OrderNode* order = engine_.get_order(
            reinterpret_cast<const uint8_t*>(identifier.data())
        );
        if (order == nullptr) return py::none();
        return py::bytes(wire::encode_order(*order));
    }

    py::object get_snapshot_bytes(const std::string& symbol, int depth) {
        BookSnapshot* snapshot = engine_.snapshot(symbol, depth);
        if (snapshot == nullptr) return py::none();
        return py::bytes(wire::encode_snapshot(*snapshot));
    }

    std::vector<std::string> registered_symbols() const {
        return engine_.registered_symbols();
    }

    py::dict stats() const {
        const auto& stats = engine_.stats();
        py::dict output;
        output["orders_received"] = stats.orders_received;
        output["orders_matched"] = stats.orders_matched;
        output["orders_resting"] = stats.orders_resting;
        output["orders_cancelled"] = stats.orders_cancelled;
        output["orders_rejected"] = stats.orders_rejected;
        output["trades_executed"] = stats.trades_executed;
        output["total_volume"] = stats.total_volume;
        return output;
    }

private:
    MatchingEngineFacade engine_;
};

}  // namespace

PYBIND11_MODULE(hft_engine_cpp, module) {
    module.doc() = "C++20 price-time-priority CLOB using explicit binary HAL frames";
    module.attr("order_frame_size") = wire::ORDER_FRAME_SIZE;
    module.attr("result_header_size") = wire::RESULT_HEADER_SIZE;

    py::class_<CppEngine>(module, "CppEngine")
        .def(py::init<>())
        .def("register_symbol", &CppEngine::register_symbol)
        .def("registered_symbols", &CppEngine::registered_symbols)
        .def("submit_order_detail", &CppEngine::submit_order_detail)
        .def("submit_order_bytes", &CppEngine::submit_order_bytes)
        .def("cancel_order_bytes", &CppEngine::cancel_order_bytes)
        .def("get_order_bytes", &CppEngine::get_order_bytes)
        .def("get_snapshot_bytes", &CppEngine::get_snapshot_bytes)
        .def("stats", &CppEngine::stats);
}

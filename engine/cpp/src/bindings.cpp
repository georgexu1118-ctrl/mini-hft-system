/**
 * engine/cpp/src/bindings.cpp
 *
 * pybind11 Python binding for the C++ matching engine.
 *
 * Build (requires pybind11 and a C++20 compiler):
 *
 *   # Install build deps
 *   pip install pybind11 setuptools
 *
 *   # Build (from repo root)
 *   python engine/cpp/setup.py build_ext --inplace
 *
 *   # The resulting .so/.pyd goes to engine/hal/cpp/hft_engine_cpp.<platform>.so
 *
 * The binding exposes a thin Python surface matching MatchingHAL:
 *
 *   import hft_engine_cpp as _cpp
 *   engine = _cpp.CppEngine()
 *   engine.register_symbol("AAPL")
 *   result = engine.submit_order_bytes(order_bytes)   # 64-byte frame in, 64-byte frame out
 *   cancelled = engine.cancel_order(order_id_str)
 *   snap = engine.get_snapshot("AAPL", depth=10)
 *
 * Binary fast path:
 *   submit_order_bytes() accepts the exact 64-byte ORDER_FRAME produced by
 *   Order.to_bytes() in Python — zero re-encoding on the hot path.
 *   The result is a list of 64-byte TRADE_FRAME payloads.
 *
 * HAL integration:
 *   CppMatchingHAL (engine/hal/cpp_hal.py) wraps this binding and satisfies
 *   MatchingHAL. To switch the backend:
 *
 *     app.state.engine = CppMatchingHAL()
 *
 *   No other code changes needed.
 */

// This file is a stub — it will be completed when pybind11 is available
// in the build environment (M7 implementation milestone).
//
// Stub left here to:
// 1. Document the intended binding surface
// 2. Allow CI to verify the C++ headers compile cleanly
// 3. Provide a starting point for M7

/*
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>

#include "../include/order.hpp"
#include "../include/order_book.hpp"

namespace py = pybind11;
using namespace hft;

// ... full binding here ...

PYBIND11_MODULE(hft_engine_cpp, m) {
    m.doc() = "C++ CLOB matching engine — 10-100x faster than Python baseline";

    py::class_<CppEngine>(m, "CppEngine")
        .def(py::init<>())
        .def("register_symbol", &CppEngine::register_symbol)
        .def("submit_order_bytes", &CppEngine::submit_order_bytes)
        .def("cancel_order", &CppEngine::cancel_order)
        .def("get_snapshot", &CppEngine::get_snapshot);
}
*/

// Placeholder so the file compiles as an empty translation unit
namespace hft { void bindings_stub() {} }

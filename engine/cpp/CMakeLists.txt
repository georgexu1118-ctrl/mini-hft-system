cmake_minimum_required(VERSION 3.20)
project(hft_engine_cpp CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ── Release optimisations ─────────────────────────────────────────────────────
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(
    -O3
    -march=native
    -Wall
    -Wextra
    -fno-exceptions   # disable C++ exceptions on hot path
)

# ── Standalone shared library (no pybind11 needed for unit tests) ─────────────
add_library(hft_engine SHARED
    src/order_book.cpp
)
target_include_directories(hft_engine PUBLIC include)

# ── Unit test binary ──────────────────────────────────────────────────────────
add_executable(test_engine
    tests/test_order_book.cpp
)
target_link_libraries(test_engine PRIVATE hft_engine)

# ── pybind11 extension (requires pybind11 installed) ─────────────────────────
find_package(Python COMPONENTS Interpreter Development)
find_package(pybind11 QUIET)

if(pybind11_FOUND)
    pybind11_add_module(hft_engine_cpp src/bindings.cpp src/order_book.cpp)
    target_include_directories(hft_engine_cpp PRIVATE include)
    install(TARGETS hft_engine_cpp DESTINATION "${CMAKE_CURRENT_SOURCE_DIR}/../hal/cpp")
else()
    message(STATUS "pybind11 not found — skipping Python extension target")
endif()

# ── Static analysis (optional) ───────────────────────────────────────────────
find_program(CLANG_TIDY clang-tidy)
if(CLANG_TIDY)
    set_target_properties(hft_engine PROPERTIES CXX_CLANG_TIDY "${CLANG_TIDY}")
endif()

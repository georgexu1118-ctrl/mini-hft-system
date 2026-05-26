cmake_minimum_required(VERSION 3.20)
project(hft_engine_cpp CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# pybind11 and the unit test harness require exceptions outside the matching
# loop. Critical matching operations remain noexcept where failure is excluded.
if(MSVC)
    add_compile_options(/O2 /W4 /EHsc)
else()
    add_compile_options(-O3 -march=native -Wall -Wextra)
endif()

add_library(hft_engine SHARED
    src/order_book.cpp
    src/matching_engine.cpp
)
target_include_directories(hft_engine PUBLIC include)
if(WIN32)
    set_target_properties(hft_engine PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
endif()

add_executable(test_engine tests/test_order_book.cpp)
target_link_libraries(test_engine PRIVATE hft_engine)

find_package(Python COMPONENTS Interpreter Development)
find_package(pybind11 QUIET)

if(pybind11_FOUND)
    pybind11_add_module(
        hft_engine_cpp
        src/bindings.cpp
        src/order_book.cpp
        src/matching_engine.cpp
    )
    target_include_directories(hft_engine_cpp PRIVATE include)
else()
    message(STATUS "pybind11 not found - skipping Python extension target")
endif()

find_program(CLANG_TIDY clang-tidy)
if(CLANG_TIDY)
    set_target_properties(hft_engine PROPERTIES CXX_CLANG_TIDY "${CLANG_TIDY}")
endif()

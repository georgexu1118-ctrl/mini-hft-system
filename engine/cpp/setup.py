"""
Build script for the C++ matching engine Python extension.

Usage:
    pip install pybind11 setuptools
    python engine/cpp/setup.py build_ext --inplace

The resulting in-place extension (hft_engine_cpp.so / .pyd) is placed at the
repository import root and imported by CppMatchingHAL.

Requirements:
    - C++20 compiler (GCC 10+, Clang 12+, MSVC 2019+)
    - pybind11 >= 2.11
    - Python 3.11+

For AWS EC2 (g4dn / F1 instances) targeting M8:
    - Install GCC 12 + libhugetlbfs for hugepage DMA ring support
    - Add -DHFT_HUGEPAGE_RING=1 to extra_compile_args
"""
import sys
from pathlib import Path

try:
    from pybind11 import get_include
    from setuptools import Extension, setup
except ImportError:
    print("ERROR: pybind11 and setuptools required. Run: pip install pybind11 setuptools", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).parent

ext = Extension(
    name="hft_engine_cpp",
    sources=[
        str(HERE / "src" / "bindings.cpp"),
        str(HERE / "src" / "matching_engine.cpp"),
        str(HERE / "src" / "order_book.cpp"),
    ],
    include_dirs=[
        str(HERE / "include"),
        get_include(),
    ],
    extra_compile_args=(
        ["/std:c++20", "/O2", "/EHsc", "/W4"]
        if sys.platform == "win32"
        else ["-std=c++20", "-O3", "-march=native", "-fvisibility=hidden", "-Wall", "-Wextra"]
    ),
    language="c++",
)

setup(
    name="hft_engine_cpp",
    version="0.1.0",
    author="mini-hft-system",
    description="C++ CLOB matching engine — Python extension module",
    ext_modules=[ext],
    zip_safe=False,
    python_requires=">=3.11",
)

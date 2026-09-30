# libstdc++ hides std::expected from compilers whose concepts support predates
# C++20's final form, which includes clang 18 and older (Ubuntu 24.04's default
# clang). Failing here gives one clear message instead of pages of errors.
include(CheckCXXSourceCompiles)
set(CMAKE_REQUIRED_FLAGS "-std=c++23")
check_cxx_source_compiles([=[
#include <expected>
int main() { std::expected<int, int> value{1}; return *value - 1; }
]=] WARDWATCH_HAVE_STD_EXPECTED)
unset(CMAKE_REQUIRED_FLAGS)
if(NOT WARDWATCH_HAVE_STD_EXPECTED)
  message(FATAL_ERROR
    "${CMAKE_CXX_COMPILER} (${CMAKE_CXX_COMPILER_ID} ${CMAKE_CXX_COMPILER_VERSION}) has no usable "
    "std::expected. Use clang 19 or later, or GCC 13 or later; on Ubuntu 24.04 install clang-19 "
    "and put /usr/lib/llvm-19/bin first on PATH.")
endif()

add_library(wardwatch_warnings INTERFACE)
target_compile_options(wardwatch_warnings INTERFACE
  -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion -Werror)

if(WARDWATCH_SANITIZERS)
  list(JOIN WARDWATCH_SANITIZERS "," sanitizer_list)
  add_compile_options(-fsanitize=${sanitizer_list} -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=${sanitizer_list})
endif()

if(WARDWATCH_COVERAGE)
  if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    message(FATAL_ERROR "The coverage preset uses clang source-based coverage")
  endif()
  add_compile_options(-fprofile-instr-generate -fcoverage-mapping)
  add_link_options(-fprofile-instr-generate)
endif()

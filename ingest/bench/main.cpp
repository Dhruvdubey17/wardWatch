#include <array>
#include <cstdio>
#include <fstream>
#include <string>

#include <benchmark/benchmark.h>

#ifdef __APPLE__
#include <sys/sysctl.h>
#endif

namespace {

// The CPU model goes into the results JSON so numbers are never quoted without
// the hardware that produced them.
std::string cpu_model() {
#ifdef __APPLE__
    std::array<char, 256> brand{};
    std::size_t size = brand.size();
    if (sysctlbyname("machdep.cpu.brand_string", brand.data(), &size, nullptr, 0) == 0) {
        return {brand.data()};
    }
#else
    std::ifstream cpuinfo("/proc/cpuinfo");
    for (std::string line; std::getline(cpuinfo, line);) {
        if (line.starts_with("model name")) {
            return line.substr(line.find(':') + 2);
        }
    }
#endif
    return "unknown";
}

}  // namespace

int main(int argc, char** argv) {
    benchmark::AddCustomContext("cpu_model", cpu_model());
    benchmark::AddCustomContext("wardwatch_build_type", WARDWATCH_BUILD_TYPE);
    benchmark::Initialize(&argc, argv);
    if (benchmark::ReportUnrecognizedArguments(argc, argv)) {
        return 1;
    }
    benchmark::RunSpecifiedBenchmarks();
    benchmark::Shutdown();
    return 0;
}

#include <csignal>
#include <exception>
#include <format>
#include <iostream>
#include <memory>
#include <pthread.h>
#include <string_view>
#include <vector>

#include "wardwatch/cli.hpp"
#include "wardwatch/server.hpp"
#include "wardwatch/sink.hpp"

namespace {

// Stop signals are blocked in every thread and collected with sigwait on the
// main thread, so no code runs in signal-handler context.
sigset_t stop_signals() {
    sigset_t signals{};
    sigemptyset(&signals);
    sigaddset(&signals, SIGTERM);
    sigaddset(&signals, SIGINT);
    return signals;
}

int run(const std::vector<std::string_view>& args) {
    auto options = wardwatch::parse_cli(args);
    if (!options) {
        std::cerr << "wardwatch-ingest: " << options.error() << "\n" << wardwatch::usage();
        return 2;
    }
    if (options->show_help) {
        std::cout << wardwatch::usage();
        return 0;
    }

    std::unique_ptr<wardwatch::Sink> sink;
    switch (options->sink) {
        case wardwatch::SinkKind::null:
            sink = std::make_unique<wardwatch::NullSink>();
            break;
        case wardwatch::SinkKind::file:
        case wardwatch::SinkKind::kafka:
            std::cerr << "wardwatch-ingest: this sink is not available yet\n";
            return 2;
    }

    // Writes to a closed socket return EPIPE instead of killing the process.
    if (std::signal(SIGPIPE, SIG_IGN) == SIG_ERR) {
        std::cerr << "wardwatch-ingest: cannot ignore SIGPIPE\n";
        return 1;
    }
    const sigset_t signals = stop_signals();
    if (pthread_sigmask(SIG_BLOCK, &signals, nullptr) != 0) {
        std::cerr << "wardwatch-ingest: cannot block stop signals\n";
        return 1;
    }

    wardwatch::Metrics metrics;
    wardwatch::Server server(options->server, *sink, metrics);
    if (auto started = server.start(); !started) {
        std::cerr << "wardwatch-ingest: " << started.error() << "\n";
        return 1;
    }
    std::cout << std::format(R"({{"event":"listening","port":{}}})", server.port()) << '\n'
              << std::flush;

    int received = 0;
    sigwait(&signals, &received);
    server.request_stop();
    server.wait();
    std::cout << std::format(R"({{"event":"stopped","signal":{},"accepted":{}}})", received,
                             metrics.messages_accepted.load())
              << '\n'
              << std::flush;
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return run(std::vector<std::string_view>(argv + 1, argv + argc));
    } catch (const std::exception& error) {
        std::cerr << "wardwatch-ingest: fatal: " << error.what() << "\n";
        return 1;
    }
}

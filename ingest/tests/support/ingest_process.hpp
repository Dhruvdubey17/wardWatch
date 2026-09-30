#pragma once

#include <array>
#include <chrono>
#include <cstdint>
#include <optional>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stdexcept>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

#include <nlohmann/json.hpp>

extern char** environ;

namespace wardwatch::testing {

// Runs the real wardwatch-ingest binary for integration tests. The binary
// prints one JSON line when it is listening, which carries the ports it chose.
class IngestProcess {
  public:
    explicit IngestProcess(std::vector<std::string> args) {
        std::array<int, 2> out{-1, -1};
        if (::pipe(out.data()) != 0) {
            throw std::runtime_error("pipe failed");
        }
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_adddup2(&actions, out[1], STDOUT_FILENO);
        posix_spawn_file_actions_addclose(&actions, out[0]);

        args.insert(args.begin(), WARDWATCH_INGEST_BINARY);
        std::vector<char*> argv;
        argv.reserve(args.size() + 1);
        for (auto& arg : args) {
            argv.push_back(arg.data());
        }
        argv.push_back(nullptr);
        const int spawned =
            posix_spawn(&pid_, WARDWATCH_INGEST_BINARY, &actions, nullptr, argv.data(), environ);
        posix_spawn_file_actions_destroy(&actions);
        ::close(out[1]);
        stdout_fd_ = out[0];
        if (spawned != 0) {
            throw std::runtime_error("posix_spawn failed");
        }
        const auto line = read_line(std::chrono::seconds(20));
        if (!line) {
            throw std::runtime_error("ingest did not report that it was listening");
        }
        const auto event = nlohmann::json::parse(*line);
        port_ = event.at("port").get<std::uint16_t>();
        metrics_port_ = event.at("metrics_port").get<std::uint16_t>();
    }

    IngestProcess(const IngestProcess&) = delete;
    IngestProcess& operator=(const IngestProcess&) = delete;
    IngestProcess(IngestProcess&&) = delete;
    IngestProcess& operator=(IngestProcess&&) = delete;

    ~IngestProcess() {
        if (pid_ > 0) {
            ::kill(pid_, SIGKILL);
            int status = 0;
            ::waitpid(pid_, &status, 0);
        }
        if (stdout_fd_ >= 0) {
            ::close(stdout_fd_);
        }
    }

    [[nodiscard]] std::uint16_t port() const noexcept { return port_; }
    [[nodiscard]] std::uint16_t metrics_port() const noexcept { return metrics_port_; }

    // Sends SIGTERM and returns the exit code once the process has drained.
    int terminate(std::chrono::seconds timeout = std::chrono::seconds(30)) {
        ::kill(pid_, SIGTERM);
        const auto deadline = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < deadline) {
            int status = 0;
            const pid_t done = ::waitpid(pid_, &status, WNOHANG);
            if (done == pid_) {
                pid_ = -1;
                return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        return -1;
    }

  private:
    std::optional<std::string> read_line(std::chrono::milliseconds timeout) {
        const auto deadline = std::chrono::steady_clock::now() + timeout;
        while (true) {
            const std::size_t newline = buffered_.find('\n');
            if (newline != std::string::npos) {
                std::string line = buffered_.substr(0, newline);
                buffered_.erase(0, newline + 1);
                return line;
            }
            const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
                deadline - std::chrono::steady_clock::now());
            if (remaining.count() <= 0) {
                return std::nullopt;
            }
            pollfd readable{.fd = stdout_fd_, .events = POLLIN, .revents = 0};
            if (::poll(&readable, 1, static_cast<int>(remaining.count())) <= 0) {
                continue;
            }
            std::array<char, 512> chunk{};
            const auto received = ::read(stdout_fd_, chunk.data(), chunk.size());
            if (received <= 0) {
                return std::nullopt;
            }
            buffered_.append(chunk.data(), static_cast<std::size_t>(received));
        }
    }

    pid_t pid_ = -1;
    int stdout_fd_ = -1;
    std::string buffered_;
    std::uint16_t port_ = 0;
    std::uint16_t metrics_port_ = 0;
};

}  // namespace wardwatch::testing

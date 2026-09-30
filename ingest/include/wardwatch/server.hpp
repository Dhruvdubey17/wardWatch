#pragma once

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <expected>
#include <functional>
#include <memory>
#include <string>
#include <thread>

#include "wardwatch/metrics.hpp"
#include "wardwatch/mllp.hpp"
#include "wardwatch/sink.hpp"
#include "wardwatch/spsc_ring.hpp"
#include "wardwatch/wakeup.hpp"

namespace wardwatch {

using Clock = std::function<std::chrono::system_clock::time_point()>;

struct ServerConfig {
    std::string bind_address = "127.0.0.1";
    // 0 asks the kernel for a free port; read it back with Server::port().
    std::uint16_t port = 2575;
    std::size_t max_frame_bytes = 1U << 20U;
    std::size_t oversize_keep_bytes = 4096;
    // Both rings; must be a power of two.
    std::size_t ring_capacity = 4096;
    std::size_t duplicate_window = 100'000;
    // How long shutdown waits for ACKs to reach senders after the sink flush.
    std::chrono::milliseconds drain_timeout{5000};
    Clock clock = [] { return std::chrono::system_clock::now(); };
};

struct InboundFrame {
    std::uint64_t connection_id = 0;
    Frame frame;
    std::chrono::system_clock::time_point received_at;
};

struct OutboundAck {
    std::uint64_t connection_id = 0;
    // CR-terminated ACK to frame and send. Empty when the sink could not take
    // the message: no ACK is sent, so the sender times out and retries.
    std::string ack;
};

// The MLLP listener. One I/O thread owns every socket and pushes framed
// messages into the inbound ring; one validation thread parses, validates,
// publishes to the sink and pushes ACKs back through the outbound ring.
class Server {
  public:
    Server(ServerConfig config, Sink& sink, Metrics& metrics);
    Server(const Server&) = delete;
    Server& operator=(const Server&) = delete;
    Server(Server&&) = delete;
    Server& operator=(Server&&) = delete;
    ~Server();

    // Binds, listens and starts both threads.
    [[nodiscard]] std::expected<void, std::string> start();

    // The bound port, valid after start().
    [[nodiscard]] std::uint16_t port() const noexcept { return bound_port_; }

    // Begins a graceful stop: no new connections or reads, every frame already
    // read is validated and published, the sink is flushed, and ACKs are
    // written before the sockets close. Safe to call from a signal handler.
    void request_stop() noexcept;

    // Blocks until both threads have finished.
    void wait();

    [[nodiscard]] RuntimeGauges gauges() const noexcept;

  private:
    class IoLoop;

    void run_validation();
    void wake_io() noexcept;

    ServerConfig config_;
    Sink& sink_;
    Metrics& metrics_;
    SpscRing<InboundFrame> inbound_;
    SpscRing<OutboundAck> outbound_;
    Wakeup inbound_wakeup_;
    std::unique_ptr<IoLoop> io_;
    std::uint16_t bound_port_ = 0;

    // Self-pipe that wakes the I/O thread for ACKs and for stop requests.
    int wake_read_fd_ = -1;
    int wake_write_fd_ = -1;
    std::atomic<bool> io_wake_pending_{false};
    std::atomic<bool> stop_requested_{false};
    // Set by the I/O thread once it will push no more frames.
    std::atomic<bool> inbound_closed_{false};
    // Set by the validation thread after the ring is drained and the sink flushed.
    std::atomic<bool> validation_done_{false};

    std::thread io_thread_;
    std::thread validation_thread_;
};

}  // namespace wardwatch

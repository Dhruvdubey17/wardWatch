#include "wardwatch/server.hpp"

#include <arpa/inet.h>
#include <array>
#include <cerrno>
#include <deque>
#include <fcntl.h>
#include <format>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <optional>
#include <sys/socket.h>
#include <system_error>
#include <unistd.h>
#include <unordered_map>

#include "wardwatch/ack.hpp"
#include "wardwatch/payload.hpp"
#include "wardwatch/poller.hpp"
#include "wardwatch/socket_io.hpp"
#include "wardwatch/validator.hpp"

namespace wardwatch {
namespace {

constexpr std::uint64_t kListenerToken = 0;
constexpr std::uint64_t kWakeToken = 1;
constexpr std::uint64_t kFirstConnectionToken = 2;
constexpr std::size_t kReadChunk = std::size_t{64} * 1024;
constexpr int kListenBacklog = 512;
constexpr std::chrono::milliseconds kIdlePoll{50};
constexpr std::chrono::milliseconds kBlockedPoll{1};

std::string errno_message(std::string_view what) {
    return std::format("{}: {}", what, std::error_code(errno, std::generic_category()).message());
}

// fcntl is variadic by POSIX definition; there is no typed alternative.
// NOLINTBEGIN(cppcoreguidelines-pro-type-vararg)
bool set_nonblocking(int fd) {
    const int flags = ::fcntl(fd, F_GETFL, 0);
    return flags >= 0 && ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}
// NOLINTEND(cppcoreguidelines-pro-type-vararg)

void configure_client_socket(int fd) {
    // ACKs are small and latency-sensitive; Nagle would hold them back.
    const int enabled = 1;
    ::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled));
    suppress_sigpipe(fd);
}

bool would_block() noexcept { return errno == EAGAIN || errno == EWOULDBLOCK; }

}  // namespace

class Server::IoLoop {
  public:
    IoLoop(Server& server, Poller poller, int listener_fd)
        : server_(server), poller_(std::move(poller)), listener_fd_(listener_fd) {}

    IoLoop(const IoLoop&) = delete;
    IoLoop& operator=(const IoLoop&) = delete;
    IoLoop(IoLoop&&) = delete;
    IoLoop& operator=(IoLoop&&) = delete;

    ~IoLoop() {
        for (auto& [id, connection] : connections_) {
            ::close(connection.fd);
        }
        if (listener_fd_ >= 0) {
            ::close(listener_fd_);
        }
    }

    void run() {
        std::array<PollEvent, 256> events{};
        while (true) {
            const auto timeout = blocked_connections_ > 0 ? kBlockedPoll : kIdlePoll;
            const auto ready = poller_.wait(events, timeout);
            const std::size_t count = ready ? *ready : 0;
            for (std::size_t i = 0; i < count; ++i) {
                dispatch(events.at(i));
            }
            drain_acks();
            retry_pending();
            if (server_.stop_requested_.load(std::memory_order_acquire) && !stopping_) {
                begin_stop();
            }
            if (stopping_ && finish_stop()) {
                break;
            }
            close_finished_connections();
        }
        close_all();
    }

  private:
    struct Connection {
        int fd = -1;
        std::uint64_t id = 0;
        MllpFramer framer;
        std::deque<InboundFrame> pending;
        std::string write_buffer;
        std::size_t write_offset = 0;
        // Frames handed to the validation thread whose ACK has not come back.
        std::size_t in_flight = 0;
        std::uint64_t outside_bytes_reported = 0;
        bool read_open = true;
        bool dead = false;
        bool read_interest = true;
        bool write_interest = false;
        bool blocked = false;
    };

    void dispatch(const PollEvent& event) {
        if (event.token == kListenerToken) {
            accept_all();
            return;
        }
        if (event.token == kWakeToken) {
            drain_wake_pipe();
            return;
        }
        const auto found = connections_.find(event.token);
        if (found == connections_.end()) {
            return;
        }
        Connection& connection = found->second;
        if (event.readable) {
            read_from(connection);
        }
        if (event.writable) {
            flush(connection);
        }
        if (event.hangup && !event.readable) {
            // Level triggered: unread data keeps reporting readable, so a
            // hangup without it means everything the peer sent has been read.
            connection.read_open = false;
            connection.dead = true;
            update_interest(connection);
        }
    }

    void accept_all() {
        while (!stopping_) {
            const int fd = ::accept(listener_fd_, nullptr, nullptr);
            if (fd < 0) {
                return;
            }
            if (!set_nonblocking(fd)) {
                ::close(fd);
                continue;
            }
            configure_client_socket(fd);
            const std::uint64_t id = next_token_++;
            if (!poller_.add(fd, id, true, false)) {
                ::close(fd);
                continue;
            }
            connections_.try_emplace(
                id, Connection{.fd = fd,
                               .id = id,
                               .framer = MllpFramer(server_.config_.max_frame_bytes,
                                                    server_.config_.oversize_keep_bytes),
                               .pending = {},
                               .write_buffer = {},
                               .write_offset = 0,
                               .in_flight = 0,
                               .outside_bytes_reported = 0,
                               .read_open = true,
                               .dead = false,
                               .read_interest = true,
                               .write_interest = false,
                               .blocked = false});
            server_.metrics_.connections_accepted.fetch_add(1, std::memory_order_relaxed);
            server_.metrics_.connections_open.fetch_add(1, std::memory_order_relaxed);
        }
    }

    void drain_wake_pipe() {
        // Clear the flag before draining so a wake requested during the drain
        // writes a fresh byte instead of being lost.
        server_.io_wake_pending_.store(false, std::memory_order_seq_cst);
        std::array<char, 256> sink{};
        while (::read(server_.wake_read_fd_, sink.data(), sink.size()) > 0) {
        }
    }

    void read_from(Connection& connection) {
        if (!connection.read_open || connection.blocked) {
            return;
        }
        std::array<char, kReadChunk> buffer{};
        const auto received = ::recv(connection.fd, buffer.data(), buffer.size(), 0);
        if (received > 0) {
            const auto size = static_cast<std::size_t>(received);
            server_.metrics_.bytes_read.fetch_add(size, std::memory_order_relaxed);
            const auto now = server_.config_.clock();
            connection.framer.feed(std::span<const char>(buffer.data(), size), [&](Frame&& frame) {
                server_.metrics_.messages_received.fetch_add(1, std::memory_order_relaxed);
                connection.pending.push_back(InboundFrame{
                    .connection_id = connection.id, .frame = std::move(frame), .received_at = now});
            });
            const std::uint64_t outside = connection.framer.stats().bytes_outside_frames;
            server_.metrics_.bytes_outside_frames.fetch_add(
                outside - connection.outside_bytes_reported, std::memory_order_relaxed);
            connection.outside_bytes_reported = outside;
            push_pending(connection);
            return;
        }
        if (received < 0 && would_block()) {
            return;
        }
        // Zero means the peer finished sending; an error means the socket is
        // gone. Either way frames already read still get validated.
        connection.read_open = false;
        if (received < 0) {
            connection.dead = true;
        }
        update_interest(connection);
    }

    void push_pending(Connection& connection) {
        bool pushed = false;
        while (!connection.pending.empty()) {
            if (!server_.inbound_.try_push(std::move(connection.pending.front()))) {
                break;
            }
            connection.pending.pop_front();
            ++connection.in_flight;
            pushed = true;
        }
        if (pushed) {
            server_.inbound_wakeup_.notify();
        }
        const bool blocked = !connection.pending.empty();
        if (blocked && !connection.blocked) {
            // Stop reading this socket until the ring has room, so the sender
            // feels the backpressure through TCP flow control.
            server_.metrics_.backpressure_events.fetch_add(1, std::memory_order_relaxed);
            ++blocked_connections_;
        } else if (!blocked && connection.blocked) {
            --blocked_connections_;
        }
        connection.blocked = blocked;
        update_interest(connection);
    }

    void retry_pending() {
        if (blocked_connections_ == 0) {
            return;
        }
        for (auto& [id, connection] : connections_) {
            if (connection.blocked) {
                push_pending(connection);
            }
        }
    }

    void drain_acks() {
        while (auto item = server_.outbound_.try_pop()) {
            const auto found = connections_.find(item->connection_id);
            if (found == connections_.end()) {
                continue;
            }
            Connection& connection = found->second;
            --connection.in_flight;
            if (connection.dead || item->ack.empty()) {
                continue;
            }
            const auto framed =
                mllp_wrap(std::span<const char>(item->ack.data(), item->ack.size()));
            connection.write_buffer.append(framed.begin(), framed.end());
            flush(connection);
        }
    }

    void flush(Connection& connection) {
        while (connection.write_offset < connection.write_buffer.size()) {
            const auto sent = send_no_signal(
                connection.fd, connection.write_buffer.data() + connection.write_offset,
                connection.write_buffer.size() - connection.write_offset);
            if (sent > 0) {
                connection.write_offset += static_cast<std::size_t>(sent);
                continue;
            }
            if (sent < 0 && would_block()) {
                break;
            }
            connection.dead = true;
            connection.write_buffer.clear();
            connection.write_offset = 0;
            break;
        }
        if (connection.write_offset == connection.write_buffer.size()) {
            connection.write_buffer.clear();
            connection.write_offset = 0;
        }
        update_interest(connection);
    }

    void update_interest(Connection& connection) {
        const bool read =
            connection.read_open && !connection.blocked && !stopping_ && !connection.dead;
        const bool write = !connection.write_buffer.empty() && !connection.dead;
        if (read == connection.read_interest && write == connection.write_interest) {
            return;
        }
        connection.read_interest = read;
        connection.write_interest = write;
        if (!poller_.modify(connection.fd, connection.id, read, write)) {
            connection.dead = true;
        }
    }

    [[nodiscard]] static bool finished(const Connection& connection) {
        if (!connection.pending.empty()) {
            return false;
        }
        if (connection.dead) {
            return true;
        }
        return !connection.read_open && connection.in_flight == 0 &&
               connection.write_buffer.empty();
    }

    void close_finished_connections() {
        std::erase_if(connections_, [&](auto& entry) {
            const Connection& connection = entry.second;
            if (!finished(connection)) {
                return false;
            }
            if (connection.blocked) {
                --blocked_connections_;
            }
            poller_.remove(connection.fd);
            ::close(connection.fd);
            server_.metrics_.connections_open.fetch_sub(1, std::memory_order_relaxed);
            return true;
        });
    }

    void begin_stop() {
        stopping_ = true;
        poller_.remove(listener_fd_);
        ::close(listener_fd_);
        listener_fd_ = -1;
        for (auto& [id, connection] : connections_) {
            // No more reads, so each connection closes as soon as its last
            // ACK is written.
            connection.read_open = false;
            update_interest(connection);
        }
    }

    void close_all() {
        for (auto& [id, connection] : connections_) {
            poller_.remove(connection.fd);
            ::close(connection.fd);
            server_.metrics_.connections_open.fetch_sub(1, std::memory_order_relaxed);
        }
        connections_.clear();
        blocked_connections_ = 0;
    }

    // Returns true once the stop has completed and the loop may exit.
    bool finish_stop() {
        const bool all_pushed = std::ranges::all_of(
            connections_, [](const auto& entry) { return entry.second.pending.empty(); });
        if (all_pushed && !server_.inbound_closed_.load(std::memory_order_relaxed)) {
            // Release makes every push above visible to the validation thread
            // once it acquires the flag.
            server_.inbound_closed_.store(true, std::memory_order_release);
            server_.inbound_wakeup_.notify();
        }
        if (!server_.validation_done_.load(std::memory_order_acquire)) {
            return false;
        }
        drain_acks();
        if (!drain_deadline_) {
            drain_deadline_ = std::chrono::steady_clock::now() + server_.config_.drain_timeout;
        }
        const bool all_written = std::ranges::all_of(connections_, [](const auto& entry) {
            return entry.second.dead || entry.second.write_buffer.empty();
        });
        return all_written || std::chrono::steady_clock::now() >= *drain_deadline_;
    }

    Server& server_;
    Poller poller_;
    int listener_fd_;
    std::unordered_map<std::uint64_t, Connection> connections_;
    std::uint64_t next_token_ = kFirstConnectionToken;
    std::size_t blocked_connections_ = 0;
    bool stopping_ = false;
    std::optional<std::chrono::steady_clock::time_point> drain_deadline_;
};

Server::Server(ServerConfig config, Sink& sink, Metrics& metrics)
    : config_(std::move(config)),
      sink_(sink),
      metrics_(metrics),
      inbound_(config_.ring_capacity),
      outbound_(config_.ring_capacity) {}

Server::~Server() {
    if (io_thread_.joinable() || validation_thread_.joinable()) {
        request_stop();
        wait();
    }
    io_.reset();
    if (wake_read_fd_ >= 0) {
        ::close(wake_read_fd_);
    }
    if (wake_write_fd_ >= 0) {
        ::close(wake_write_fd_);
    }
}

std::expected<void, std::string> Server::start() {
    auto poller = Poller::create();
    if (!poller) {
        return std::unexpected(poller.error());
    }

    std::array<int, 2> pipe_fds{-1, -1};
    if (::pipe(pipe_fds.data()) != 0) {
        return std::unexpected(errno_message("pipe"));
    }
    wake_read_fd_ = pipe_fds[0];
    wake_write_fd_ = pipe_fds[1];
    if (!set_nonblocking(wake_read_fd_) || !set_nonblocking(wake_write_fd_)) {
        return std::unexpected(errno_message("fcntl on wake pipe"));
    }

    const int listener = ::socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0) {
        return std::unexpected(errno_message("socket"));
    }
    const int enabled = 1;
    ::setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(config_.port);
    if (::inet_pton(AF_INET, config_.bind_address.c_str(), &address.sin_addr) != 1) {
        ::close(listener);
        return std::unexpected(std::format("bind address '{}' is not IPv4", config_.bind_address));
    }
    if (::bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 ||
        ::listen(listener, kListenBacklog) != 0 || !set_nonblocking(listener)) {
        auto message =
            errno_message(std::format("listen on {}:{}", config_.bind_address, config_.port));
        ::close(listener);
        return std::unexpected(std::move(message));
    }
    socklen_t length = sizeof(address);
    ::getsockname(listener, reinterpret_cast<sockaddr*>(&address), &length);
    bound_port_ = ntohs(address.sin_port);

    if (auto added = poller->add(listener, kListenerToken, true, false); !added) {
        ::close(listener);
        return std::unexpected(added.error());
    }
    if (auto added = poller->add(wake_read_fd_, kWakeToken, true, false); !added) {
        ::close(listener);
        return std::unexpected(added.error());
    }

    io_ = std::make_unique<IoLoop>(*this, std::move(*poller), listener);
    validation_thread_ = std::thread([this] { run_validation(); });
    io_thread_ = std::thread([this] { io_->run(); });
    return {};
}

void Server::request_stop() noexcept {
    stop_requested_.store(true, std::memory_order_release);
    wake_io();
}

void Server::wait() {
    if (io_thread_.joinable()) {
        io_thread_.join();
    }
    if (validation_thread_.joinable()) {
        validation_thread_.join();
    }
}

RuntimeGauges Server::gauges() const noexcept {
    return RuntimeGauges{.inbound_ring_size = inbound_.size_approx(),
                         .inbound_ring_capacity = inbound_.capacity(),
                         .outbound_ring_size = outbound_.size_approx(),
                         .outbound_ring_capacity = outbound_.capacity()};
}

void Server::wake_io() noexcept {
    if (wake_write_fd_ < 0) {
        return;
    }
    if (!io_wake_pending_.exchange(true, std::memory_order_seq_cst)) {
        const char byte = 1;
        // A full pipe already guarantees a pending wake, so a failed write is fine.
        [[maybe_unused]] const auto written = ::write(wake_write_fd_, &byte, 1);
    }
}

void Server::run_validation() {
    Validator validator(config_.duplicate_window);
    std::uint64_t ack_sequence = 0;

    const auto publish = [&](Topic topic, std::string_view key, std::string_view payload) {
        const bool accepted = sink_.publish(topic, key, payload);
        if (!accepted) {
            metrics_.sink_refused.fetch_add(1, std::memory_order_relaxed);
        }
        return accepted;
    };

    const auto handle = [&](InboundFrame item) {
        const auto started = std::chrono::steady_clock::now();
        std::optional<ParseError> error;
        MessageSummary summary;
        AckCode ack_code = AckCode::ar;
        bool delivered = true;

        if (item.frame.oversize) {
            summary =
                sniff_header(std::string_view(item.frame.bytes.data(), item.frame.bytes.size()));
            error = ParseError{ErrorCode::frame_oversize, config_.max_frame_bytes,
                               std::format("frame of {} bytes exceeds the {} byte limit",
                                           item.frame.total_size, config_.max_frame_bytes)};
            ack_code = ack_code_for(ErrorCode::frame_oversize);
            metrics_.parse_latency.observe(std::chrono::duration<double, std::micro>(
                                               std::chrono::steady_clock::now() - started)
                                               .count());
            delivered =
                publish(Topic::deadletter, summary.control_id,
                        build_deadletter_payload(DeadLetterStage::frame, *error, summary.control_id,
                                                 item.frame.bytes, true, item.received_at));
        } else {
            auto processed = validator.process(std::move(item.frame.bytes));
            auto& result = processed.result;
            std::optional<std::string> payload;
            if (result.disposition == Disposition::accepted) {
                auto built = build_validated_payload(*processed.message, result, item.received_at);
                if (built) {
                    payload = std::move(*built);
                } else {
                    result.disposition = Disposition::rejected;
                    result.ack_code = ack_code_for(built.error().code);
                    result.error = std::move(built.error());
                }
            }
            metrics_.parse_latency.observe(std::chrono::duration<double, std::micro>(
                                               std::chrono::steady_clock::now() - started)
                                               .count());
            summary = result.summary;
            ack_code = result.ack_code;
            error = result.error;
            const std::string& key = summary.mrn.empty() ? summary.control_id : summary.mrn;
            switch (result.disposition) {
                case Disposition::accepted:
                    metrics_.messages_accepted.fetch_add(1, std::memory_order_relaxed);
                    for (const auto& warning : result.warnings) {
                        metrics_.count_warning(warning.code);
                    }
                    delivered = publish(Topic::validated, key, *payload);
                    break;
                case Disposition::retransmission:
                    metrics_.messages_retransmitted.fetch_add(1, std::memory_order_relaxed);
                    break;
                case Disposition::rejected: {
                    const auto stage =
                        processed.message ? DeadLetterStage::validate : DeadLetterStage::parse;
                    const std::string_view raw = processed.raw();
                    delivered = publish(
                        Topic::deadletter, key,
                        build_deadletter_payload(stage, *error, summary.control_id,
                                                 std::span<const char>(raw.data(), raw.size()),
                                                 false, item.received_at));
                    break;
                }
            }
        }
        if (error) {
            metrics_.count_rejection(error->code);
        }

        OutboundAck outbound{.connection_id = item.connection_id, .ack = {}};
        if (delivered) {
            outbound.ack = build_ack(summary, ack_code, error,
                                     std::format("WWACK{}", ++ack_sequence), config_.clock());
        }
        // try_push leaves its argument intact when the ring is full.
        while (!outbound_.try_push(std::move(outbound))) {  // NOLINT(bugprone-use-after-move)
            // The I/O thread drains this ring on every loop, including during
            // shutdown, so a full ring clears quickly.
            wake_io();
            std::this_thread::yield();
        }
        wake_io();
    };

    while (true) {
        const std::uint32_t epoch = inbound_wakeup_.prepare();
        if (auto item = inbound_.try_pop()) {
            inbound_wakeup_.cancel();
            handle(std::move(*item));
            continue;
        }
        if (inbound_closed_.load(std::memory_order_acquire)) {
            inbound_wakeup_.cancel();
            // The flag is set only after the last push, so one more pop sees
            // anything pushed before it.
            if (auto item = inbound_.try_pop()) {
                handle(std::move(*item));
                continue;
            }
            break;
        }
        inbound_wakeup_.wait(epoch);
    }

    // Records still undelivered after the timeout are reported by the sink
    // through the delivery counters.
    [[maybe_unused]] const bool flushed = sink_.flush(config_.drain_timeout);
    validation_done_.store(true, std::memory_order_release);
    wake_io();
}

}  // namespace wardwatch

#include "wardwatch/socket_io.hpp"

#include <array>
#include <cerrno>
#include <csignal>
#include <sys/socket.h>
#include <unistd.h>

#include <gtest/gtest.h>

namespace wardwatch {
namespace {

// Writing to a socket whose peer has closed fails with EPIPE on the first
// send, and a plain ::send also raises SIGPIPE, which at its default action
// ends the process. SIGPIPE is left at the default here, so this test binary
// only survives if send_no_signal and suppress_sigpipe really stop the signal.
// On Linux the ingest integration tests died of exactly this before the test
// clients used these helpers.
TEST(SocketIo, SendToAClosedPeerFailsWithEpipeInsteadOfSigpipe) {
    const auto previous = std::signal(SIGPIPE, SIG_DFL);
    ASSERT_NE(previous, SIG_ERR);
    std::array<int, 2> pair{};
    ASSERT_EQ(::socketpair(AF_UNIX, SOCK_STREAM, 0, pair.data()), 0);
    suppress_sigpipe(pair[0]);
    ::close(pair[1]);

    const char byte = 'x';
    for (int attempt = 0; attempt < 3; ++attempt) {
        errno = 0;
        EXPECT_EQ(send_no_signal(pair[0], &byte, 1), -1);
        EXPECT_EQ(errno, EPIPE);
    }

    ::close(pair[0]);
    std::signal(SIGPIPE, previous);
}

TEST(SocketIo, SendToAnOpenPeerDeliversTheBytes) {
    std::array<int, 2> pair{};
    ASSERT_EQ(::socketpair(AF_UNIX, SOCK_STREAM, 0, pair.data()), 0);
    suppress_sigpipe(pair[0]);
    constexpr std::array<char, 3> message{'a', 'b', 'c'};
    EXPECT_EQ(send_no_signal(pair[0], message.data(), message.size()), 3);
    std::array<char, 3> received{};
    EXPECT_EQ(::recv(pair[1], received.data(), received.size(), 0), 3);
    EXPECT_EQ(received, message);
    ::close(pair[0]);
    ::close(pair[1]);
}

}  // namespace
}  // namespace wardwatch

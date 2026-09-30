#pragma once

#include <arpa/inet.h>
#include <array>
#include <cstdint>
#include <netinet/in.h>
#include <string>
#include <string_view>
#include <sys/socket.h>
#include <unistd.h>

#include "wardwatch/socket_io.hpp"

namespace wardwatch::testing {

inline std::string http_get(std::uint16_t port, std::string_view path) {
    const int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    ::inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
    if (::connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        ::close(fd);
        return {};
    }
    const std::string request = "GET " + std::string(path) + " HTTP/1.1\r\nHost: x\r\n\r\n";
    suppress_sigpipe(fd);
    send_no_signal(fd, request.data(), request.size());
    std::string response;
    std::array<char, 4096> buffer{};
    while (true) {
        const auto received = ::recv(fd, buffer.data(), buffer.size(), 0);
        if (received <= 0) {
            break;
        }
        response.append(buffer.data(), static_cast<std::size_t>(received));
    }
    ::close(fd);
    return response;
}

// Reads one un-labelled sample value from a Prometheus text page.
inline long long metric_value(std::string_view page, std::string_view name) {
    const std::string needle = "\n" + std::string(name) + " ";
    const std::size_t at = page.find(needle);
    if (at == std::string_view::npos) {
        return -1;
    }
    return std::stoll(std::string(page.substr(at + needle.size())));
}

}  // namespace wardwatch::testing

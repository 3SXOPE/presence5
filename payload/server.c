#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <poll.h>
#include <time.h>
#include <errno.h>
#include <sys/time.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <dirent.h>
#include <sys/stat.h>

#include "disc.h"

#define PRESENCE5_VERSION "1.0.0"
#include "eden.h"
#include <ps5/kernel.h>

typedef struct notify_request {
    char useless1[45];
    char message[3075];
} notify_request_t;

int sceSystemServiceGetAppIdOfRunningBigApp(void);
int sceSystemServiceGetAppTitleId(int app_id, char *title_id);
int sceKernelSendNotificationRequest(int, notify_request_t*, size_t, int);

int notify(const char* message)
{
    notify_request_t req;

    bzero(&req, sizeof req);
    strncpy(req.message, message, sizeof req.message);

    return sceKernelSendNotificationRequest(0, &req, sizeof req, 0);
}

static void send_disc_info(int client, bool debug) {
    static char out[8192];
    disc_info(out, sizeof(out), debug);
    send(client, out, strlen(out), MSG_NOSIGNAL);
}

// Another copy of this payload (an older one, say) may still hold the port.
// Ask it to quit, then try again.
static int bind_port(int fd, struct sockaddr_in* addr) {
    for (int attempt = 0; attempt < 6; attempt++) {
        if (bind(fd, (struct sockaddr*)addr, sizeof(*addr)) == 0) {
            return 0;
        }
        int old = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in local = *addr;
        local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (old >= 0 && connect(old, (struct sockaddr*)&local, sizeof(local)) == 0) {
            if (attempt == 0) {
                notify("Replacing the presence payload that was already running");
            }
            char line[64];
            recv(old, line, sizeof(line), 0);  // its state line
            send(old, "QUIT\n", 5, MSG_NOSIGNAL);
        }
        if (old >= 0) {
            close(old);
        }
        sleep(2);  // it answers once per poll, every two seconds at most
    }
    return -1;
}

// Opens the listening socket on port 8000, or returns -1. With replace, an
// older copy of the payload holding the port is asked to quit first.
static int open_server(bool replace) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        return -1;
    }
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(8000);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);

    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    int bound = replace ? bind_port(fd, &addr) : bind(fd, (struct sockaddr*)&addr, sizeof(addr));
    if (bound != 0 || listen(fd, 4) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

// When the network goes away (Wi-Fi drop, cable pulled, rest mode) the
// listening socket can go bad: accept() then fails at once, every time. The
// socket is closed and reopened instead, waiting longer between tries each
// time (5 s, 10 s, 20 s, then every 30 s), with one notification when the
// connection is lost and one when it is back. Nothing here ever loops
// without waiting.
#define RETRY_FIRST 5
#define RETRY_MAX 30
#define ACCEPT_FAILS_BEFORE_REOPEN 3

int main(void) {
    notify("Presence5 " PRESENCE5_VERSION ": payload sent");
    char line[64]; // Holds finished message to send

    int fd = open_server(true);
    if (fd < 0) {
        notify("Could not open port 8000");
        return 1;
    }
    notify("Server initialized");



    // Game detection part
    char current[32] = "";
    time_t start_sec = 0;
    struct timespec now;

    struct pollfd pfd = {0};
    pfd.events = POLLIN;
    int running = 1;
    int accept_fails = 0;
    int retry_wait = RETRY_FIRST;   // seconds until the next try at reopening
    bool lost_notified = false;
    bool title_fail_notified = false;
    while (running) {
        char seen[32] = "";
        int bigAppId = sceSystemServiceGetAppIdOfRunningBigApp();

        if (bigAppId >= 0) {
            if (sceSystemServiceGetAppTitleId(bigAppId, seen) != 0) {
                if (!title_fail_notified) {  // once, not every two seconds
                    notify("Failed to get title id");
                    title_fail_notified = true;
                }
                seen[0] = '\0';
            }
        }

        if (strcmp(seen, current) != 0) {
            strcpy(current, seen);
            clock_gettime(CLOCK_MONOTONIC, &now);
            start_sec = now.tv_sec;
        }

        // No listening socket: wait, then try to open one again.
        if (fd < 0) {
            sleep(retry_wait);
            fd = open_server(false);
            if (fd < 0) {
                retry_wait = retry_wait * 2 > RETRY_MAX ? RETRY_MAX : retry_wait * 2;
                continue;
            }
            retry_wait = RETRY_FIRST;
            accept_fails = 0;
            if (lost_notified) {
                notify("Presence: listening again");
                lost_notified = false;
            }
        }

        pfd.fd = fd;
        pfd.revents = 0;
        int ready = poll(&pfd, 1, 2000);
        if (ready == 0) {
            continue;
        }
        bool broken = ready < 0 || (pfd.revents & (POLLERR | POLLHUP | POLLNVAL));
        int client = -1;
        if (!broken) {
            if (!(pfd.revents & POLLIN)) {
                sleep(1);
                continue;
            }
            client = accept(fd, NULL, NULL);
            if (client < 0) {
                // A caller that gave up before we answered is not a fault.
                if (errno == ECONNABORTED || errno == EINTR || errno == EAGAIN) {
                    sleep(1);
                    continue;
                }
                if (++accept_fails < ACCEPT_FAILS_BEFORE_REOPEN) {
                    sleep(2);
                    continue;
                }
                broken = true;
            }
        }
        if (broken) {
            close(fd);
            fd = -1;
            if (!lost_notified) {
                notify("Presence: connection lost, waiting for the network");
                lost_notified = true;
            }
            continue;
        }
        accept_fails = 0;

        // A caller that stops reading must not hold the payload up.
        struct timeval limit = {3, 0};
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &limit, sizeof(limit));

        if (current[0] == '\0') {
            snprintf(line, sizeof(line), "IDLE\n");
        } else {
            clock_gettime(CLOCK_MONOTONIC, &now);
            long elapsed = (long)(now.tv_sec - start_sec);
            snprintf(line, sizeof(line), "GAME %s %ld\n", current, elapsed);
        }

        send(client, line, strlen(line), MSG_NOSIGNAL);

        struct pollfd cfd = {0};
        cfd.fd = client;
        cfd.events = POLLIN;

        if (poll(&cfd, 1, 200) > 0 && (cfd.revents & POLLIN)) {
            char cmd[16];
            ssize_t n = recv(client, cmd, sizeof(cmd) - 1, 0);
            if (n > 0) {
                cmd[n] = '\0';
                if (strncmp(cmd, "QUIT", 4) == 0) {
                    running = 0;

                } else if (strncmp(cmd, "PARAM", 5) == 0 && current[0] != '\0') {
                    char path[128];
                    char buf[1024];

                    snprintf(path, sizeof(path), "/user/appmeta/%s/param.json", current);
                    FILE* f = fopen(path, "rb");
                    size_t n_file;

                    if (f != NULL) {
                        while ((n_file = fread(buf, 1, sizeof(buf), f)) > 0) {
                            send(client, buf, n_file, MSG_NOSIGNAL);
                        }
                        fclose(f);
                    }

                } else if (strncmp(cmd, "FW", 2) == 0) {
                    // The firmware: 0x13420000 is 13.42.
                    uint32_t fw = kernel_get_fw_version();
                    char out[32];
                    snprintf(out, sizeof(out), "FW %x.%02x\n", (fw >> 24) & 0xff, (fw >> 16) & 0xff);
                    send(client, out, strlen(out), MSG_NOSIGNAL);
                } else if (strncmp(cmd, "EDEN", 4) == 0) {
                    static char out[1024];
                    eden_info(out, sizeof(out));
                    send(client, out, strlen(out), MSG_NOSIGNAL);
                } else if (strncmp(cmd, "DISCDEBUG", 9) == 0) {
                    send_disc_info(client, true);
                } else if (strncmp(cmd, "DISC", 4) == 0) {
                    send_disc_info(client, false);
                }
            }
        }

        close(client);
    }

    if (fd >= 0) {
        close(fd);
    }
    notify("Server closed");

    return 0;
}
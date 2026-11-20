// What ProsperoEden is doing, read from the files it writes itself.
//
// logs/stderr.log is rewritten from empty each time ProsperoEden starts and
// is not buffered. It gets "[ProsperoEden] loader: Game loaded; ..." when a
// game has started and "[ProsperoEden] shutdown: ..." when it is stopped, so
// the last of those says whether a game is running. Only the part of the log
// added since the last look is read.
//
// config/prosperoeden.json has "library": { "last_game": "<rom file>" },
// saved at the moment a game finishes loading.

#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>

#include "eden.h"

const char* eden_config_files[] = {
    "/data/prosperoeden/config/prosperoeden.json",
    "/user/download/PPSA99008/prosperoeden/prosperoeden.json",  // without filesystem access
    NULL,
};
const char* eden_log_files[] = {
    "/data/prosperoeden/logs/stderr.log",
    "/user/download/PPSA99008/eden-headless-g7/stderr.log",
    NULL,
};

static const char* started[] = {"loader: Game loaded", NULL};
static const char* stopped[] = {"shutdown: Stopping the game", "shutdown: Game stopped",
                                "session failed", "fatal startup failure", NULL};

static struct {
    const char* path;
    long offset;       // how much of the log has been read
    bool in_game;
    char carry[512];   // an unfinished last line
    size_t carry_len;
} seen;

static bool has_any(const char* line, const char** words) {
    for (int i = 0; words[i] != NULL; i++) {
        if (strstr(line, words[i]) != NULL) {
            return true;
        }
    }
    return false;
}

static void take_line(const char* line) {
    if (strstr(line, "[ProsperoEden]") == NULL) {
        return;
    }
    if (has_any(line, started)) {
        seen.in_game = true;
    } else if (has_any(line, stopped)) {
        seen.in_game = false;
    }
}

// Reads what was added to the log since last time. False when there is no log.
static bool follow_log(void) {
    struct stat st;
    const char* path = NULL;
    for (int i = 0; eden_log_files[i] != NULL; i++) {
        if (stat(eden_log_files[i], &st) == 0) {
            path = eden_log_files[i];
            break;
        }
    }
    if (path == NULL) {
        return false;
    }
    // A new session starts the log again from empty.
    if (path != seen.path || st.st_size < seen.offset) {
        memset(&seen, 0, sizeof(seen));
        seen.path = path;
    }
    if (st.st_size == seen.offset) {
        return true;
    }
    FILE* f = fopen(path, "rb");
    if (f == NULL) {
        return false;
    }
    fseek(f, seen.offset, SEEK_SET);
    static char buf[16384];
    size_t n;
    long budget = 8L * 1024 * 1024;  // per call; the rest is read next time
    while (budget > 0 && (n = fread(buf, 1, sizeof(buf), f)) > 0) {
        budget -= (long)n;
        seen.offset += (long)n;
        for (size_t i = 0; i < n; i++) {
            char c = buf[i];
            if (c == '\n') {
                seen.carry[seen.carry_len] = '\0';
                take_line(seen.carry);
                seen.carry_len = 0;
            } else if (seen.carry_len + 1 < sizeof(seen.carry)) {
                seen.carry[seen.carry_len++] = c;
            }
        }
    }
    fclose(f);
    return true;
}

// The string value of "last_game" in the settings file.
static bool last_game(char* out, size_t out_size, long* age) {
    for (int i = 0; eden_config_files[i] != NULL; i++) {
        struct stat st;
        FILE* f = fopen(eden_config_files[i], "rb");
        if (f == NULL) {
            continue;
        }
        static char text[65536];
        size_t n = fread(text, 1, sizeof(text) - 1, f);
        fclose(f);
        text[n] = '\0';
        const char* key = strstr(text, "\"last_game\"");
        const char* p = key ? strchr(key + 11, ':') : NULL;
        p = p ? strchr(p, '"') : NULL;
        if (p == NULL) {
            continue;
        }
        size_t o = 0;
        for (p++; *p != '\0' && *p != '"' && o + 1 < out_size; p++) {
            if (*p == '\\' && p[1] != '\0') {
                p++;  // \" and \\ ; other escapes don't occur in file names worth showing
            }
            if (*p != '\n' && *p != '\r') {
                out[o++] = *p;
            }
        }
        out[o] = '\0';
        *age = -1;
        if (stat(eden_config_files[i], &st) == 0) {
            time_t now = time(NULL);
            *age = now >= st.st_mtime ? (long)(now - st.st_mtime) : 0;
        }
        return o > 0;
    }
    return false;
}

void eden_info(char* out, size_t out_size) {
    size_t at = 0;
    out[0] = '\0';
    bool have_log = follow_log();
    at += (size_t)snprintf(out + at, out_size - at, "STATE %s\n",
                           !have_log ? "unknown" : seen.in_game ? "game" : "menu");
    char name[512];
    long age = -1;
    if (at < out_size && last_game(name, sizeof(name), &age)) {
        at += (size_t)snprintf(out + at, out_size - at, "LAST %s\n", name);
        if (age >= 0 && at < out_size) {
            snprintf(out + at, out_size - at, "AGE %ld\n", age);
        }
    }
}

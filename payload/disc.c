// Movie discs: what is in the drive and its title.
//
// A Blu-ray's title is in BDMV/META/DL/bdmt_*.xml (<di:name>), and
// BDMV/index.bdmv says whether it is a 4K Ultra HD disc. The files are read
// from wherever the disc is mounted; when it isn't mounted where this process
// can see it, they are read straight off the drive (/dev/cd0), with a small
// UDF reader (Blu-rays are UDF 2.50, with a metadata partition).

#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/param.h>
#include <sys/mount.h>

#include "disc.h"

#ifndef MNAMELEN
#define MNAMELEN 1024
#endif

#ifndef HOST_TEST
#include <ps5/kernel.h>
#endif

#define SECTOR 2048
#define MAX_FILE (256 * 1024)
#define MAX_DIR (2 * 1024 * 1024)

const char* disc_device = "/dev/cd0";

// ---------------------------------------------------------------------------
// Small helpers

static uint16_t le16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t le32(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint64_t le64(const uint8_t* p) { return (uint64_t)le32(p) | ((uint64_t)le32(p + 4) << 32); }

static void append(char* out, size_t size, const char* fmt, const char* a, long b) {
    size_t len = strlen(out);
    if (len < size) {
        snprintf(out + len, size - len, fmt, a, b);
    }
}

// Copies the text of the first <di:name> element into out.
static bool xml_name(const char* xml, char* out, size_t out_size) {
    const char* start = strstr(xml, "<di:name>");
    if (start == NULL) {
        return false;
    }
    start += strlen("<di:name>");
    const char* end = strstr(start, "</di:name>");
    if (end == NULL || end == start) {
        return false;
    }
    size_t len = (size_t)(end - start);
    if (len >= out_size) {
        len = out_size - 1;
    }
    for (size_t i = 0; i < len; i++) {
        char c = start[i];
        out[i] = (c == '\n' || c == '\r' || c == '\t') ? ' ' : c;
    }
    out[len] = '\0';
    int alnum = 0;
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)out[i];
        alnum += (c >= '0' && c <= '9') || ((c | 0x20) >= 'a' && (c | 0x20) <= 'z') || c >= 0x80;
    }
    return alnum >= 2;  // some discs carry "." or "-" instead of a title
}

static bool ends_with(const char* s, const char* suffix) {
    size_t a = strlen(s), b = strlen(suffix);
    return a >= b && strcasecmp(s + a - b, suffix) == 0;
}

// ---------------------------------------------------------------------------
// Reading files: from a mounted disc, or straight off the drive

typedef struct {
    uint32_t len;
    uint32_t lbn;
    uint16_t part;
} long_ad;

typedef struct {
    // Mounted disc
    const char* root;
    // Raw UDF
    int fd;
    uint32_t block;
    int nparts;
    struct {
        bool meta;
        uint16_t number;     // partition number (physical) it lives on
        uint32_t start;      // sector of that physical partition
        // metadata partition: the metadata file's extents, in the physical partition
        int next;
        uint32_t ext_len[64];
        uint32_t ext_pos[64];
    } parts[4];
    long_ad root_icb;
} disc_t;

typedef void (*entry_fn)(disc_t* d, const char* name, long_ad icb, bool is_dir, void* ctx);

static bool read_sector(disc_t* d, uint32_t sector, uint8_t* buf, size_t count) {
    off_t off = (off_t)sector * SECTOR;
    size_t want = count * SECTOR;
    ssize_t got = pread(d->fd, buf, want, off);
    return got == (ssize_t)want;
}

// Sector of a logical block in a partition (by its index in the partition maps).
static bool udf_sector(disc_t* d, uint16_t part, uint32_t lbn, uint32_t* sector) {
    if (part >= d->nparts) {
        return false;
    }
    if (!d->parts[part].meta) {
        *sector = d->parts[part].start + lbn;
        return true;
    }
    uint64_t want = (uint64_t)lbn * d->block, at = 0;
    for (int i = 0; i < d->parts[part].next; i++) {
        uint32_t len = d->parts[part].ext_len[i];
        if (want < at + len) {
            *sector = d->parts[part].start + d->parts[part].ext_pos[i] + (uint32_t)((want - at) / d->block);
            return true;
        }
        at += len;
    }
    return false;
}

static bool read_block(disc_t* d, uint16_t part, uint32_t lbn, uint8_t* buf) {
    uint32_t sector;
    return udf_sector(d, part, lbn, &sector) && read_sector(d, sector, buf, 1);
}

// Reads a file's data (up to max bytes), given its File Entry. Returns the size read, or -1.
static long udf_read(disc_t* d, long_ad icb, uint8_t* out, size_t max) {
    uint8_t fe[SECTOR];
    if (!read_block(d, icb.part, icb.lbn, fe)) {
        return -1;
    }
    uint16_t tag = le16(fe);
    size_t ad_off, l_ad;
    if (tag == 261) {         // File Entry
        ad_off = 176 + le32(fe + 168);
        l_ad = le32(fe + 172);
    } else if (tag == 266) {  // Extended File Entry
        ad_off = 216 + le32(fe + 208);
        l_ad = le32(fe + 212);
    } else {
        return -1;
    }
    if (ad_off + l_ad > SECTOR) {
        return -1;
    }
    uint64_t size = le64(fe + 56);
    if (size > max) {
        size = max;
    }
    int ad_type = le16(fe + 34) & 7;
    if (ad_type == 3) {  // data embedded in the entry
        size_t n = l_ad < size ? l_ad : (size_t)size;
        memcpy(out, fe + ad_off, n);
        return (long)n;
    }
    size_t step = ad_type == 0 ? 8 : 16;
    if (ad_type != 0 && ad_type != 1) {
        return -1;
    }
    uint64_t done = 0;
    uint8_t blk[SECTOR];
    for (size_t p = ad_off; p + step <= ad_off + l_ad && done < size; p += step) {
        uint32_t len = le32(fe + p) & 0x3FFFFFFF;
        uint32_t kind = le32(fe + p) >> 30;
        uint32_t lbn = le32(fe + p + 4);
        uint16_t part = ad_type == 0 ? icb.part : le16(fe + p + 8);
        if (len == 0) {
            break;
        }
        if (kind == 3) {
            break;  // continuation of the descriptors elsewhere: rare for these small files
        }
        for (uint32_t off = 0; off < len && done < size; off += d->block, lbn++) {
            size_t n = len - off < d->block ? len - off : d->block;
            if (n > size - done) {
                n = (size_t)(size - done);
            }
            if (kind == 0) {
                if (!read_block(d, part, lbn, blk)) {
                    return -1;
                }
                memcpy(out + done, blk, n);
            } else {
                memset(out + done, 0, n);  // allocated but not recorded
            }
            done += n;
        }
    }
    return (long)done;
}

// File identifier (d-string) to ASCII.
static void udf_name(const uint8_t* id, size_t len, char* out, size_t out_size) {
    size_t o = 0;
    if (len == 0) {
        out[0] = '\0';
        return;
    }
    if (id[0] == 16) {
        for (size_t i = 1; i + 1 < len && o + 1 < out_size; i += 2) {
            out[o++] = id[i] == 0 ? (char)id[i + 1] : '?';
        }
    } else {
        for (size_t i = 1; i < len && o + 1 < out_size; i++) {
            out[o++] = (char)id[i];
        }
    }
    out[o] = '\0';
}

static bool udf_list(disc_t* d, long_ad dir, entry_fn fn, void* ctx) {
    uint8_t* buf = malloc(MAX_DIR);
    if (buf == NULL) {
        return false;
    }
    long n = udf_read(d, dir, buf, MAX_DIR);
    for (long p = 0; n > 0 && p + 38 <= n;) {
        uint8_t* f = buf + p;
        if (le16(f) != 257) {
            break;
        }
        uint8_t chars = f[18];
        uint8_t l_fi = f[19];
        uint16_t l_iu = le16(f + 36);
        long total = (38 + l_iu + l_fi + 3) & ~3L;
        if (p + total > n) {
            break;
        }
        if (!(chars & 0x08) && !(chars & 0x04)) {  // not the parent entry, not deleted
            char name[256];
            udf_name(f + 38 + l_iu, l_fi, name, sizeof(name));
            long_ad icb = {le32(f + 20) & 0x3FFFFFFF, le32(f + 24), le16(f + 28)};
            fn(d, name, icb, (chars & 0x02) != 0, ctx);
        }
        p += total;
    }
    free(buf);
    return n >= 0;
}

typedef struct {
    const char* want;
    bool found;
    long_ad icb;
} find_ctx;

static void find_entry(disc_t* d, const char* name, long_ad icb, bool is_dir, void* ctx) {
    (void)d;
    (void)is_dir;
    find_ctx* f = ctx;
    if (!f->found && strcasecmp(name, f->want) == 0) {
        f->found = true;
        f->icb = icb;
    }
}

// Finds a path such as "BDMV/PLAYLIST" from the root directory.
static bool udf_find(disc_t* d, const char* path, long_ad* out) {
    long_ad at = d->root_icb;
    char part[64];
    while (*path) {
        size_t len = strcspn(path, "/");
        if (len >= sizeof(part)) {
            return false;
        }
        memcpy(part, path, len);
        part[len] = '\0';
        find_ctx f = {part, false, {0}};
        if (!udf_list(d, at, find_entry, &f) || !f.found) {
            return false;
        }
        at = f.icb;
        path += len + (path[len] == '/');
    }
    *out = at;
    return true;
}

static bool udf_open(disc_t* d, int fd) {
    memset(d, 0, sizeof(*d));
    d->fd = fd;
    d->block = SECTOR;
    uint8_t s[SECTOR];
    if (!read_sector(d, 256, s, 1) || le16(s) != 2) {  // Anchor Volume Descriptor Pointer
        return false;
    }
    uint32_t vds_len = le32(s + 16) / SECTOR, vds_loc = le32(s + 20);
    if (vds_len > 64) {
        vds_len = 64;
    }

    struct { uint16_t number; uint32_t start; } phys[4];
    int nphys = 0;
    uint8_t lvd[SECTOR];
    bool have_lvd = false;
    for (uint32_t i = 0; i < vds_len; i++) {
        if (!read_sector(d, vds_loc + i, s, 1)) {
            return false;
        }
        uint16_t tag = le16(s);
        if (tag == 5 && nphys < 4) {  // Partition Descriptor
            phys[nphys].number = le16(s + 22);
            phys[nphys].start = le32(s + 188);
            nphys++;
        } else if (tag == 6) {        // Logical Volume Descriptor
            memcpy(lvd, s, SECTOR);
            have_lvd = true;
        } else if (tag == 8) {        // Terminator
            break;
        }
    }
    if (!have_lvd || nphys == 0) {
        return false;
    }
    d->block = le32(lvd + 212);
    if (d->block != SECTOR) {
        return false;
    }
    uint32_t nmaps = le32(lvd + 268);
    const uint8_t* m = lvd + 440;
    for (uint32_t i = 0; i < nmaps && i < 4 && m + 2 <= lvd + SECTOR; i++) {
        uint8_t type = m[0], len = m[1];
        uint16_t number = 0;
        bool meta = false;
        uint32_t meta_file = 0;
        if (type == 1) {
            number = le16(m + 4);
        } else if (type == 2) {
            number = le16(m + 38);
            meta = memcmp(m + 5, "*UDF Metadata Partition", 23) == 0;
            meta_file = le32(m + 40);
        }
        d->parts[i].meta = meta;
        d->parts[i].number = number;
        for (int p = 0; p < nphys; p++) {
            if (phys[p].number == number) {
                d->parts[i].start = phys[p].start;
            }
        }
        d->nparts = (int)i + 1;
        if (meta) {
            // The metadata file lives in the physical partition: read its extents.
            uint8_t fe[SECTOR];
            if (!read_sector(d, d->parts[i].start + meta_file, fe, 1)) {
                return false;
            }
            size_t ad_off, l_ad;
            if (le16(fe) == 266) {
                ad_off = 216 + le32(fe + 208);
                l_ad = le32(fe + 212);
            } else if (le16(fe) == 261) {
                ad_off = 176 + le32(fe + 168);
                l_ad = le32(fe + 172);
            } else {
                return false;
            }
            int ad_type = le16(fe + 34) & 7;
            size_t step = ad_type == 0 ? 8 : 16;
            for (size_t p = ad_off; p + step <= ad_off + l_ad && p + step <= SECTOR &&
                                    d->parts[i].next < 64; p += step) {
                uint32_t elen = le32(fe + p) & 0x3FFFFFFF;
                if (elen == 0) {
                    break;
                }
                d->parts[i].ext_len[d->parts[i].next] = elen;
                d->parts[i].ext_pos[d->parts[i].next] = le32(fe + p + 4);
                d->parts[i].next++;
            }
        }
        m += len ? len : 6;
    }

    // File Set Descriptor -> root directory.
    long_ad fsd = {le32(lvd + 248), le32(lvd + 252), le16(lvd + 256)};
    if (!read_block(d, fsd.part, fsd.lbn, s) || le16(s) != 256) {
        return false;
    }
    d->root_icb = (long_ad){le32(s + 400), le32(s + 404), le16(s + 408)};
    return true;
}

// Volume name from the Logical Volume Descriptor (a 128-byte d-string at offset 84).
static bool udf_label(int fd, char* out, size_t out_size) {
    disc_t d = {.fd = fd};
    uint8_t s[SECTOR];
    if (!read_sector(&d, 256, s, 1) || le16(s) != 2) {
        return false;
    }
    uint32_t vds_len = le32(s + 16) / SECTOR, vds_loc = le32(s + 20);
    for (uint32_t i = 0; i < vds_len && i < 64; i++) {
        if (!read_sector(&d, vds_loc + i, s, 1)) {
            return false;
        }
        if (le16(s) == 6) {
            uint8_t len = s[84 + 127];
            if (len == 0 || len > 127) {
                return false;
            }
            udf_name(s + 84, len, out, out_size);
            return out[0] != '\0';
        }
        if (le16(s) == 8) {
            break;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// The same questions, asked of a mounted disc or of the raw UDF

static long read_file(disc_t* d, const char* path, uint8_t* buf, size_t max) {
    if (d->root) {
        char full[512];
        snprintf(full, sizeof(full), "%s/%s", d->root, path);
        FILE* f = fopen(full, "rb");
        if (f == NULL) {
            return -1;
        }
        size_t n = fread(buf, 1, max, f);
        fclose(f);
        return (long)n;
    }
    long_ad icb;
    return udf_find(d, path, &icb) ? udf_read(d, icb, buf, max) : -1;
}

typedef struct {
    const char* dir;
    const char* prefix;
    const char* suffix;
    void (*fn)(disc_t* d, const char* path, void* ctx);
    void* ctx;
} each_ctx;

static void each_udf(disc_t* d, const char* name, long_ad icb, bool is_dir, void* ctx) {
    (void)icb;
    each_ctx* e = ctx;
    if (!is_dir && strncasecmp(name, e->prefix, strlen(e->prefix)) == 0 && ends_with(name, e->suffix)) {
        char path[512];
        snprintf(path, sizeof(path), "%s/%s", e->dir, name);
        e->fn(d, path, e->ctx);
    }
}

// Calls fn for each file in dir whose name starts with prefix and ends with suffix.
static void each_file(disc_t* d, const char* dir, const char* prefix, const char* suffix,
                      void (*fn)(disc_t*, const char*, void*), void* ctx) {
    each_ctx e = {dir, prefix, suffix, fn, ctx};
    if (d->root) {
        char full[512];
        snprintf(full, sizeof(full), "%s/%s", d->root, dir);
        DIR* dh = opendir(full);
        if (dh == NULL) {
            return;
        }
        struct dirent* ent;
        while ((ent = readdir(dh)) != NULL) {
            each_udf(d, ent->d_name, (long_ad){0}, false, &e);
        }
        closedir(dh);
        return;
    }
    long_ad icb;
    if (udf_find(d, dir, &icb)) {
        udf_list(d, icb, each_udf, &e);
    }
}

typedef struct {
    uint8_t* buf;
    char* name;
    size_t name_size;
    bool found;
} scan_ctx;

static void try_bdmt(disc_t* d, const char* path, void* ctx) {
    scan_ctx* s = ctx;
    if (s->found) {
        return;
    }
    long n = read_file(d, path, s->buf, MAX_FILE - 1);
    if (n > 0) {
        s->buf[n] = '\0';
        s->found = xml_name((const char*)s->buf, s->name, s->name_size);
    }
}

static void describe_bd(disc_t* d, char* out, size_t out_size) {
    scan_ctx s = {malloc(MAX_FILE), NULL, 0, false};
    if (s.buf == NULL) {
        append(out, out_size, "TYPE BD%s\n", "", 0);
        return;
    }
    char name[256] = "";
    s.name = name;
    s.name_size = sizeof(name);
    try_bdmt(d, "BDMV/META/DL/bdmt_eng.xml", &s);
    if (!s.found) {
        each_file(d, "BDMV/META/DL", "bdmt_", ".xml", try_bdmt, &s);
    }
    // index.bdmv starts "INDX0300" on 4K Ultra HD discs, "INDX0200"/"INDX0100" otherwise.
    long n = read_file(d, "BDMV/index.bdmv", s.buf, 8);
    bool uhd = n == 8 && memcmp(s.buf, "INDX0300", 8) == 0;
    free(s.buf);

    append(out, out_size, "TYPE BD%s\n", "", 0);
    if (uhd) {
        append(out, out_size, "FORMAT UHD%s\n", "", 0);
    }
    if (s.found) {
        append(out, out_size, "NAME %s\n", name, 0);
    }
}

// ---------------------------------------------------------------------------
// Finding the disc

static bool is_dir(const char* path) {
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static bool has(const char* root, const char* sub) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", root, sub);
    return is_dir(path);
}

static int open_device(const char* path) {
    int fd = open(path, O_RDONLY);
#ifndef HOST_TEST
    if (fd < 0 && (errno == EPERM || errno == EACCES)) {
        // Borrow the shell's credentials for the open, as PS5SX2 does for the drive.
        pid_t pid = getpid();
        uint64_t authid = kernel_get_ucred_authid(pid);
        uint8_t caps[16], all[16];
        memset(all, 0xff, sizeof(all));
        kernel_get_ucred_caps(pid, caps);
        kernel_set_ucred_authid(pid, 0x4800000000010003ull);
        kernel_set_ucred_caps(pid, all);
        fd = open(path, O_RDONLY);
        int e = errno;
        kernel_set_ucred_authid(pid, authid);
        kernel_set_ucred_caps(pid, caps);
        errno = e;
    }
#endif
    return fd;
}

void disc_info(char* out, size_t out_size, bool debug) {
    out[0] = '\0';
    char roots[8][MNAMELEN];
    int nroots = 0;
    snprintf(roots[nroots++], MNAMELEN, "/mnt/disc");
    snprintf(roots[nroots++], MNAMELEN, "/mnt/sandbox/NPXS40140_000/disc");

#ifndef HOST_TEST
    // Anything mounted from the drive or with a disc file system, wherever it is.
    static struct statfs mounts[128];
    int n = getfsstat(mounts, sizeof(mounts), MNT_NOWAIT);
    for (int i = 0; i < n; i++) {
        const char* type = mounts[i].f_fstypename;
        const char* from = mounts[i].f_mntfromname;
        const char* on = mounts[i].f_mntonname;
        bool discy = strcmp(type, "udf") == 0 || strcmp(type, "udf2") == 0 ||
                     strcmp(type, "cd9660") == 0 || strstr(from, "cd0") || strstr(on, "disc") ||
                     strstr(from, "bd") || strstr(on, "bdj");
        if (!discy) {
            continue;
        }
        if (debug) {
            char line[MNAMELEN * 2 + 32];
            snprintf(line, sizeof(line), "%s %s %s", type, from, on);
            append(out, out_size, "MOUNT %s%.0ld\n", line, 0);
        }
        if (nroots < 8) {
            snprintf(roots[nroots++], MNAMELEN, "%s", on);
        }
    }
#endif

    for (int i = 0; i < nroots; i++) {
        if (has(roots[i], "BDMV")) {
            if (debug) {
                append(out, out_size, "ROOT %s%.0ld\n", roots[i], 0);
            }
            disc_t d = {.root = roots[i], .fd = -1};
            describe_bd(&d, out, out_size);
            // The volume label too, for discs whose title file is missing or a placeholder.
            int fd = open_device(disc_device);
            char label[128];
            if (fd >= 0) {
                if (udf_label(fd, label, sizeof(label))) {
                    append(out, out_size, "LABEL %s\n", label, 0);
                }
                close(fd);
            }
            return;
        }
        if (has(roots[i], "VIDEO_TS")) {
            append(out, out_size, "TYPE DVD%s\n", "", 0);
            return;
        }
    }

    // Not mounted where we can see it: read the drive itself.
    int fd = open_device(disc_device);
    if (fd < 0) {
        if (debug) {
            append(out, out_size, "DEVICE %s", disc_device, 0);
            append(out, out_size, " open errno %ld\n", "", errno);
        }
        append(out, out_size, "TYPE NONE%s\n", "", 0);
        return;
    }
    disc_t d;
    if (udf_open(&d, fd)) {
        long_ad bdmv;
        if (udf_find(&d, "BDMV", &bdmv)) {
            if (debug) {
                append(out, out_size, "ROOT %s (raw UDF)%.0ld\n", disc_device, 0);
            }
            describe_bd(&d, out, out_size);
            char label[128];
            if (udf_label(fd, label, sizeof(label))) {
                append(out, out_size, "LABEL %s\n", label, 0);
            }
            close(fd);
            return;
        }
        long_ad vts;
        if (udf_find(&d, "VIDEO_TS", &vts)) {
            append(out, out_size, "TYPE DVD%s\n", "", 0);
            close(fd);
            return;
        }
    }
    char label[128];
    if (udf_label(fd, label, sizeof(label))) {
        append(out, out_size, "TYPE DISC%s\n", "", 0);
        append(out, out_size, "LABEL %s\n", label, 0);
    } else {
        if (debug) {
            append(out, out_size, "DEVICE %s opened, no UDF volume read%.0ld\n", disc_device, 0);
        }
        append(out, out_size, "TYPE NONE%s\n", "", 0);
    }
    close(fd);
}

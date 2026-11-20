#pragma once
#include <stdbool.h>
#include <stddef.h>

// The drive the raw reader opens when the disc isn't mounted anywhere visible.
extern const char* disc_device;

// Describes the disc in the drive, one item per line:
//   TYPE BD | TYPE DVD | TYPE DISC | TYPE NONE
//   NAME <title>        (Blu-ray, from BDMV/META/DL/bdmt_*.xml)
//   FORMAT UHD          (4K Ultra HD Blu-ray)
//   LABEL <volume name> (read off the drive)
// With debug, also MOUNT/ROOT/DEVICE lines saying where it looked.
void disc_info(char* out, size_t out_size, bool debug);

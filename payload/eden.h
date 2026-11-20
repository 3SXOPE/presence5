#pragma once
#include <stddef.h>

// ProsperoEden (PPSA99008) runs every game inside its own title, so the
// console can only say "ProsperoEden is running". Its own files say the rest.
// Writes, one item per line:
//   STATE game | menu | unknown   from its session log (logs/stderr.log)
//   LAST <rom file name>          the game last started (config/prosperoeden.json)
//   AGE <seconds>                 since that was written, i.e. since the game started
void eden_info(char* out, size_t out_size);

// Folders ProsperoEden keeps its data in; the first that has the file is used.
extern const char* eden_config_files[];
extern const char* eden_log_files[];

// link_shim.h — minimal C facade over ableton::Link so Swift can drive
// tempo/beat/phase sync without C++ interop. One opaque handle per app.
//
// Licensing: Ableton Link is dual-licensed (GPLv2+ or a proprietary
// agreement from Ableton). Fine for internal dogfood; obtain the
// proprietary license before commercial distribution.

#ifndef JAMN_LINK_SHIM_H
#define JAMN_LINK_SHIM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct LinkHandle LinkHandle;

LinkHandle *link_create(double initial_bpm);
void link_destroy(LinkHandle *h);

void link_enable(LinkHandle *h, bool on);
bool link_is_enabled(const LinkHandle *h);
size_t link_num_peers(const LinkHandle *h);

double link_tempo(LinkHandle *h);
void link_set_tempo(LinkHandle *h, double bpm);

/// Link clock now, in microseconds.
int64_t link_clock_micros(LinkHandle *h);

/// Beat value at `micros` on the shared timeline for `quantum` beats.
double link_beat_at_time(LinkHandle *h, int64_t micros, double quantum);

/// Phase in [0, quantum) at `micros`.
double link_phase_at_time(LinkHandle *h, int64_t micros, double quantum);

/// Seconds from now until the next quantum (bar) boundary on the
/// shared timeline. 0 when already exactly on a boundary.
double link_seconds_to_next_quantum(LinkHandle *h, double quantum);

#ifdef __cplusplus
}
#endif

#endif  // JAMN_LINK_SHIM_H

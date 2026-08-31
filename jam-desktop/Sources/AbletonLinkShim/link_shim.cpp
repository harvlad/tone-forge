// link_shim.cpp — see include/link_shim.h.

#include "include/link_shim.h"

#include <ableton/Link.hpp>

#include <chrono>

struct LinkHandle {
    ableton::Link link;
    explicit LinkHandle(double bpm) : link(bpm) {}
};

extern "C" {

LinkHandle *link_create(double initial_bpm) {
    return new LinkHandle(initial_bpm);
}

void link_destroy(LinkHandle *h) { delete h; }

void link_enable(LinkHandle *h, bool on) { h->link.enable(on); }

bool link_is_enabled(const LinkHandle *h) { return h->link.isEnabled(); }

size_t link_num_peers(const LinkHandle *h) { return h->link.numPeers(); }

double link_tempo(LinkHandle *h) {
    return h->link.captureAppSessionState().tempo();
}

void link_set_tempo(LinkHandle *h, double bpm) {
    auto state = h->link.captureAppSessionState();
    state.setTempo(bpm, h->link.clock().micros());
    h->link.commitAppSessionState(state);
}

int64_t link_clock_micros(LinkHandle *h) {
    return h->link.clock().micros().count();
}

double link_beat_at_time(LinkHandle *h, int64_t micros, double quantum) {
    return h->link.captureAppSessionState().beatAtTime(
        std::chrono::microseconds(micros), quantum);
}

double link_phase_at_time(LinkHandle *h, int64_t micros, double quantum) {
    return h->link.captureAppSessionState().phaseAtTime(
        std::chrono::microseconds(micros), quantum);
}

double link_seconds_to_next_quantum(LinkHandle *h, double quantum) {
    auto state = h->link.captureAppSessionState();
    const auto now = h->link.clock().micros();
    const double beat = state.beatAtTime(now, quantum);
    const double phase = state.phaseAtTime(now, quantum);
    const double tempo = state.tempo();
    if (tempo <= 0.0) return 0.0;
    double remaining = quantum - phase;
    if (remaining >= quantum - 1e-9) remaining = 0.0;  // on the boundary
    (void)beat;
    return remaining * 60.0 / tempo;
}

}  // extern "C"

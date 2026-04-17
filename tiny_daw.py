from dataclasses import dataclass, field
import math, wave, struct, random
import dearpygui.dearpygui as dpg

SR = 44100
random.seed(0)


@dataclass
class Instrument:
    waveform: str = "sine"   # sine, square, saw, noise
    attack: float = 0.0
    decay: float = 0.1
    sustain: float = 0.0
    release: float = 0.0
    pitch_drop: float = 0.0  # semitones of downward sweep early in note
    noise: float = 0.0
    click: float = 0.0


@dataclass
class Note:
    step: int
    length: int
    pitch: int
    velocity: float = 1.0


@dataclass
class Track:
    name: str
    instrument: Instrument
    notes: list[Note] = field(default_factory=list)

    def add(self, step: int, length: int, pitch: int, velocity: float = 1.0):
        self.notes.append(Note(step, length, pitch, velocity))


@dataclass
class Song:
    bpm: float
    tracks: list[Track] = field(default_factory=list)

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    @property
    def seconds_per_step(self) -> float:
        return self.seconds_per_beat / 4.0


@dataclass
class DAW:
    song: Song


def midi_to_hz(m: int) -> float:
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


def osc_sample(waveform: str, phase: float) -> float:
    x = phase - math.floor(phase)
    if waveform == "sine":
        return math.sin(2 * math.pi * x)
    if waveform == "square":
        return 1.0 if x < 0.5 else -1.0
    if waveform == "saw":
        return 2.0 * x - 1.0
    if waveform == "noise":
        return random.uniform(-1.0, 1.0)
    return math.sin(2 * math.pi * x)


def adsr(inst: Instrument, t: float, hold: float) -> float:
    a, d, s, r = inst.attack, inst.decay, inst.sustain, inst.release

    if a > 0 and t < a:
        return t / a

    t2 = t - a
    if d > 0 and t2 < d:
        return 1.0 + (s - 1.0) * (t2 / d)

    if t < hold:
        return s

    if r <= 0:
        return 0.0

    rel_t = t - hold
    if rel_t < r:
        return s * (1.0 - rel_t / r)

    return 0.0


def render_note(song: Song, inst: Instrument, note: Note) -> list[float]:
    hold = max(0.0, note.length * song.seconds_per_step)
    total = hold + inst.release
    n = max(1, int(total * SR))
    out = [0.0] * n

    base_hz = midi_to_hz(note.pitch)
    phase = 0.0

    for i in range(n):
        t = i / SR
        env = adsr(inst, t, hold)
        if env <= 0.0:
            continue

        if inst.pitch_drop > 0.0:
            drop_window = min(0.08, total)
            frac = min(1.0, t / max(drop_window, 1e-9))
            semis = inst.pitch_drop * (1.0 - frac)
            hz = base_hz * (2.0 ** (semis / 12.0))
        else:
            hz = base_hz

        phase += hz / SR

        tonal = osc_sample(inst.waveform, phase)
        noise = random.uniform(-1.0, 1.0) * inst.noise
        click = (1.0 - min(1.0, t / 0.004)) * inst.click if t < 0.004 else 0.0

        out[i] = (0.9 * tonal + noise + click) * env * note.velocity

    return out


def render(song: Song, bars: int = 2) -> list[float]:
    total_steps = 16 * bars
    total_seconds = total_steps * song.seconds_per_step
    n_samples = int(total_seconds * SR)
    mix = [0.0] * n_samples

    for track in song.tracks:
        for note in track.notes:
            audio = render_note(song, track.instrument, note)
            for bar in range(bars):
                start = int((note.step + 16 * bar) * song.seconds_per_step * SR)
                end = min(n_samples, start + len(audio))
                for j in range(end - start):
                    mix[start + j] += audio[j]

    for i, x in enumerate(mix):
        mix[i] = math.tanh(1.4 * x)

    return mix


def write_wav(path: str, samples: list[float]) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        frames = bytearray()
        for s in samples:
            s = max(-1.0, min(1.0, s))
            frames += struct.pack("<h", int(s * 32767))
        wf.writeframes(frames)


def default_pitch(track: Track) -> int:
    name = track.name.lower()
    if "kick" in name:
        return 36
    if "snare" in name:
        return 38
    if "hat" in name:
        return 42
    if "808" in name or "bass" in name:
        return 36
    return 60


def note_at_step(track: Track, step: int) -> Note | None:
    for n in track.notes:
        if n.step == step:
            return n
    return None


def toggle_step(track: Track, step: int) -> None:
    n = note_at_step(track, step)
    if n is not None:
        track.notes.remove(n)
    else:
        track.notes.append(Note(step=step, length=1, pitch=default_pitch(track), velocity=1.0))


def rebuild_track_list(daw: DAW) -> None:
    dpg.delete_item("track_list", children_only=True)
    for i, track in enumerate(daw.song.tracks):
        with dpg.group(horizontal=True, parent="track_list"):
            dpg.add_text(f"{i}: {track.name}")


def rebuild_grid(daw: DAW) -> None:
    dpg.delete_item("grid_area", children_only=True)
    if not daw.song.tracks:
        dpg.add_text("no tracks", parent="grid_area")
        return

    with dpg.table(header_row=True, resizable=False, policy=dpg.mvTable_SizingFixedFit, parent="grid_area"):
        dpg.add_table_column(label="track")
        for step in range(16):
            dpg.add_table_column(label=str(step))

        for ti, track in enumerate(daw.song.tracks):
            with dpg.table_row():
                dpg.add_text(track.name)
                for step in range(16):
                    filled = note_at_step(track, step) is not None
                    dpg.add_button(
                        label="x" if filled else " ",
                        width=28,
                        callback=lambda s, a, u=(ti, step): on_grid_cell(daw, *u),
                    )


def rebuild_instrument_panel(daw: DAW) -> None:
    dpg.delete_item("instrument_panel", children_only=True)
    if not daw.song.tracks:
        dpg.add_text("no tracks", parent="instrument_panel")
        return

    for i, track in enumerate(daw.song.tracks):
        dpg.add_text(f"track {i}: {track.name}", parent="instrument_panel")
        dpg.add_input_text(
            label=f"name##{i}",
            default_value=track.name,
            parent="instrument_panel",
            callback=lambda s, a, u=track: on_track_name(daw, u, a),
            on_enter=True,
        )
        dpg.add_combo(
            ["sine", "square", "saw", "noise"],
            label=f"waveform##{i}",
            default_value=track.instrument.waveform,
            parent="instrument_panel",
            callback=lambda s, a, u=track: on_waveform(daw, u, a),
        )
        dpg.add_slider_float(
            label=f"decay##{i}",
            min_value=0.0,
            max_value=1.0,
            default_value=track.instrument.decay,
            parent="instrument_panel",
            callback=lambda s, a, u=track: on_inst_field(daw, u, "decay", a),
        )
        dpg.add_slider_float(
            label=f"noise##{i}",
            min_value=0.0,
            max_value=1.0,
            default_value=track.instrument.noise,
            parent="instrument_panel",
            callback=lambda s, a, u=track: on_inst_field(daw, u, "noise", a),
        )
        dpg.add_slider_float(
            label=f"click##{i}",
            min_value=0.0,
            max_value=1.0,
            default_value=track.instrument.click,
            parent="instrument_panel",
            callback=lambda s, a, u=track: on_inst_field(daw, u, "click", a),
        )
        dpg.add_slider_float(
            label=f"pitch_drop##{i}",
            min_value=0.0,
            max_value=36.0,
            default_value=track.instrument.pitch_drop,
            parent="instrument_panel",
            callback=lambda s, a, u=track: on_inst_field(daw, u, "pitch_drop", a),
        )
        dpg.add_separator(parent="instrument_panel")


def refresh_all(daw: DAW) -> None:
    rebuild_track_list(daw)
    rebuild_grid(daw)
    rebuild_instrument_panel(daw)
    if dpg.does_item_exist("bpm_input"):
        dpg.set_value("bpm_input", daw.song.bpm)


def on_grid_cell(daw: DAW, track_idx: int, step: int) -> None:
    toggle_step(daw.song.tracks[track_idx], step)
    refresh_all(daw)


def on_track_name(daw: DAW, track: Track, value: str) -> None:
    track.name = value or "track"
    refresh_all(daw)


def on_waveform(daw: DAW, track: Track, value: str) -> None:
    track.instrument.waveform = value
    refresh_all(daw)


def on_inst_field(daw: DAW, track: Track, field_name: str, value: float) -> None:
    setattr(track.instrument, field_name, value)


def on_bpm(daw: DAW, value: float) -> None:
    daw.song.bpm = max(1.0, float(value))


def on_add_track(daw: DAW) -> None:
    daw.song.tracks.append(Track("track", Instrument()))
    refresh_all(daw)


def on_render_wav(daw: DAW) -> None:
    audio = render(daw.song, bars=2)
    path = "tiny_trap.wav"
    write_wav(path, audio)
    dpg.set_value("status_text", f"wrote {path}")


def Render(daw: DAW) -> None:
    dpg.create_context()

    with dpg.window(label="tiny daw", tag="main", width=1000, height=720):
        with dpg.group(horizontal=True):
            dpg.add_input_float(
                label="bpm",
                tag="bpm_input",
                default_value=daw.song.bpm,
                min_value=1.0,
                min_clamped=True,
                step=1.0,
                width=120,
                callback=lambda s, a: on_bpm(daw, a),
            )
            dpg.add_button(label="add track", callback=lambda: on_add_track(daw))
            dpg.add_button(label="render wav", callback=lambda: on_render_wav(daw))
        dpg.add_text("", tag="status_text")
        dpg.add_separator()

        with dpg.group(horizontal=True):
            with dpg.child_window(width=220, autosize_y=True):
                dpg.add_text("tracks")
                dpg.add_separator()
                dpg.add_child_window(tag="track_list", autosize_x=True, autosize_y=True, border=False)

            with dpg.child_window(autosize_x=True, autosize_y=True):
                dpg.add_text("steps")
                dpg.add_separator()
                dpg.add_child_window(tag="grid_area", autosize_x=True, height=260, border=False)
                dpg.add_separator()
                dpg.add_text("instrument")
                dpg.add_separator()
                dpg.add_child_window(tag="instrument_panel", autosize_x=True, autosize_y=True, border=False)

    refresh_all(daw)

    dpg.create_viewport(title="tiny daw", width=1000, height=720)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main", True)
    dpg.start_dearpygui()
    dpg.destroy_context()


def make_demo_song() -> Song:
    song = Song(bpm=142)

    kick = Track("kick", Instrument(
        waveform="sine",
        decay=0.10,
        pitch_drop=24.0,
        click=0.20,
    ))

    snare = Track("snare", Instrument(
        waveform="square",
        decay=0.05,
        noise=0.65,
        click=0.08,
    ))

    hat = Track("hat", Instrument(
        waveform="noise",
        decay=0.02,
        noise=0.85,
    ))

    bass808 = Track("808", Instrument(
        waveform="sine",
        decay=0.20,
        sustain=0.85,
        release=0.08,
        pitch_drop=7.0,
        click=0.02,
    ))

    for step in [0, 6, 11]:
        kick.add(step, 1, 36)

    for step in [4, 12]:
        snare.add(step, 1, 38, 0.95)

    for step in range(16):
        hat.add(step, 1, 42, 0.35 if step % 4 else 0.65)

    for step, length, pitch in [
        (0, 4, 36),
        (6, 2, 36),
        (8, 4, 34),
        (12, 4, 31),
    ]:
        bass808.add(step, length, pitch)

    song.tracks += [kick, snare, hat, bass808]
    return song


if __name__ == "__main__":
    daw = DAW(make_demo_song())
    Render(daw)

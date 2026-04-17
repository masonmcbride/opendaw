import threading
import numpy as np
import sounddevice as sd
import dearpygui.dearpygui as dpg

SR = 48000
DUR = 1.0
N = int(SR * DUR)

DIMS = {
    "attack":      ("Attack",              0.0005, 0.0500, 0.0020),
    "burst":       ("Burst",               0.0000, 1.0000, 0.0000),
    "decay":       ("Decay",               0.0200, 1.5000, 0.3500),
    "center":      ("Center Hz",          40.0000, 5000.0, 600.0000),
    "tonality":    ("Tonality",            0.0000, 1.0000, 0.7000),
    "inharm":      ("Inharmonicity",       0.0000, 1.0000, 0.7000),
    "modes":       ("Mode Density",        1.0000, 12.000, 4.0000),
    "spread":      ("Spectral Spread",     0.0100, 1.0000, 0.3500),
    "drift":       ("Pitch Drift",        -1.0000, 1.0000, 0.0000),
    "noise_persist": ("Noise Persistence", 0.0100, 1.5000, 0.2000),
}

PRESETS = {
    "Cowbell": dict(attack=0.001, burst=0.0, decay=0.42, center=700, tonality=0.82, inharm=0.82, modes=4, spread=0.32, drift=0.0, noise_persist=0.12),
    "808": dict(attack=0.001, burst=0.0, decay=0.95, center=55, tonality=0.96, inharm=0.05, modes=2, spread=0.08, drift=0.85, noise_persist=0.05),
    "Hat": dict(attack=0.0005, burst=0.0, decay=0.10, center=3500, tonality=0.05, inharm=0.95, modes=10, spread=0.95, drift=0.0, noise_persist=0.35),
    "Snare": dict(attack=0.001, burst=0.0, decay=0.24, center=250, tonality=0.35, inharm=0.35, modes=4, spread=0.55, drift=0.1, noise_persist=0.50),
    "Steel": dict(attack=0.002, burst=0.0, decay=0.90, center=500, tonality=0.92, inharm=0.25, modes=5, spread=0.22, drift=-0.05, noise_persist=0.08),
}

class Audio:
    def __init__(self):
        self.lock = threading.Lock()
        self.buf = np.zeros(0, dtype=np.float32)
        self.pos = 0
        self.stream = sd.OutputStream(
            samplerate=SR,
            channels=1,
            dtype="float32",
            callback=self.callback,
            blocksize=256,
        )

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()

    def play(self, y):
        with self.lock:
            self.buf = y.astype(np.float32).copy()
            self.pos = 0

    def callback(self, outdata, frames, time, status):
        out = np.zeros(frames, dtype=np.float32)
        with self.lock:
            if self.pos < len(self.buf):
                take = min(frames, len(self.buf) - self.pos)
                out[:take] = self.buf[self.pos:self.pos + take]
                self.pos += take
        outdata[:, 0] = out

audio = Audio()

def get_params():
    return {k: dpg.get_value(k) for k in DIMS}

def set_params(p):
    for k, (_, _, _, default) in DIMS.items():
        dpg.set_value(k, p.get(k, default))

def exp_env(t, a, d):
    return (1.0 - np.exp(-t / max(a, 1e-6))) * np.exp(-t / max(d, 1e-6))

def onepole_lp(x, cutoff):
    cutoff = max(20.0, min(cutoff, SR * 0.45))
    a = np.exp(-2.0 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    z = 0.0
    for i, xi in enumerate(x):
        z = (1.0 - a) * xi + a * z
        y[i] = z
    return y

def onepole_hp(x, cutoff):
    return x - onepole_lp(x, cutoff)

def band_noise(n, center, spread, rng):
    x = rng.standard_normal(n)
    lo = max(20.0, center * (0.30 + 0.60 * (1.0 - spread)))
    hi = min(SR * 0.45, center * (1.20 + 8.00 * spread))
    y = onepole_hp(x, lo)
    y = onepole_lp(y, hi)
    m = np.max(np.abs(y))
    return y if m < 1e-9 else y / m

def synth(p):
    t = np.arange(N) / SR
    rng = np.random.default_rng()

    attack = p["attack"]
    burst = p["burst"]
    decay = p["decay"]
    center = p["center"]
    tonality = p["tonality"]
    inharm = p["inharm"]
    modes = int(round(p["modes"]))
    spread = p["spread"]
    drift = p["drift"]
    noise_persist = p["noise_persist"]

    env = exp_env(t, attack, decay)
    noise_env = exp_env(t, max(attack * 0.4, 1e-5), max(decay * noise_persist, 1e-4))

    if drift >= 0:
        f = center * (np.exp(-4.0 * drift * t) * (1.0 + drift) + (1.0 - np.exp(-4.0 * drift * t)) * (1.0 / (1.0 + drift)))
    else:
        f = center * (1.0 + 0.3 * drift * (1.0 - np.exp(-8.0 * t)))
    f = np.clip(f, 20.0, SR * 0.2)

    body = np.zeros_like(t)
    for k in range(1, modes + 1):
        stretch = 1.0 + inharm * 0.12 * (k - 1) ** 2
        fk = np.clip(f * k * stretch, 20.0, SR * 0.45)
        phase = 2.0 * np.pi * np.cumsum(fk) / SR
        body += np.sin(phase) / (k ** (0.7 + 1.8 * (1.0 - tonality)))
    body *= env

    noise = band_noise(N, center * (1.0 + 4.0 * spread), spread, rng) * noise_env

    click_len = max(8, int(SR * (0.0004 + 0.008 * (1.0 - attack / 0.05))))
    click = np.zeros(N)
    click[:click_len] = np.exp(-np.linspace(0, 12, click_len)) * rng.standard_normal(click_len)
    click = onepole_hp(click, 1000 + 8000 * (1.0 - tonality))
    click *= 0.25

    if burst > 0:
        d = int((0.004 + 0.025 * burst) * SR)
        click2 = np.zeros(N)
        click2[d:d + click_len] = 0.7 * click[:max(0, min(click_len, N - d))]
        click += click2

    y = tonality * body + (1.0 - tonality) * noise + click
    y = np.tanh(2.0 * y)
    y *= np.linspace(1.0, 0.0, N) ** 0.2

    m = np.max(np.abs(y))
    return (0.9 * y / max(m, 1e-8)).astype(np.float32)

def stft_mag(y, win=512, hop=128):
    w = np.hanning(win)
    frames = []
    for i in range(0, len(y) - win + 1, hop):
        frames.append(np.abs(np.fft.rfft(y[i:i + win] * w)))
    S = np.array(frames).T
    return S

def features(y):
    eps = 1e-9
    Y = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(len(y), 1 / SR)
    mag = Y + eps
    mag_n = mag / mag.sum()

    centroid = float((freqs * mag_n).sum())
    bandwidth = float(np.sqrt(((freqs - centroid) ** 2 * mag_n).sum()))
    zcr = float(np.mean(y[:-1] * y[1:] < 0))

    peak_idx = np.argpartition(mag, -8)[-8:]
    peak_idx = peak_idx[np.argsort(mag[peak_idx])[::-1]]
    peak_freqs = freqs[peak_idx]
    peak_count = int(np.sum(mag[peak_idx] > 0.10 * mag.max()))

    if len(peak_freqs) > 1 and peak_freqs[0] > 20:
        ratios = peak_freqs / peak_freqs[0]
        nearest = np.maximum(1, np.round(ratios))
        harmonicity = float(np.exp(-np.mean(np.abs(ratios - nearest))))
    else:
        harmonicity = 0.0

    a = int(0.03 * SR)
    b = int(0.15 * SR)
    e = y * y
    total = float(e.sum() + eps)
    transient = float(e[:a].sum() / total)
    body = float(e[a:b].sum() / total)
    tail = float(e[b:].sum() / total)

    S = stft_mag(y)
    Sn = S / np.maximum(S.sum(axis=0, keepdims=True), eps)
    cent_t = (np.fft.rfftfreq(512, 1 / SR)[:, None] * Sn).sum(axis=0)
    bw_t = np.sqrt((((np.fft.rfftfreq(512, 1 / SR)[:, None] - cent_t[None, :]) ** 2) * Sn).sum(axis=0))
    flux = float(np.mean(np.sqrt(np.sum(np.diff(S, axis=1) ** 2, axis=0))))

    return {
        "centroid": centroid,
        "bandwidth": bandwidth,
        "zcr": zcr,
        "peak_count": peak_count,
        "harmonicity": harmonicity,
        "transient": transient,
        "body": body,
        "tail": tail,
        "flux": flux,
        "centroid_t": cent_t,
        "bandwidth_t": bw_t,
    }

def spectrogram_rgba(y, h=256, w=256):
    S = stft_mag(y, win=512, hop=max(1, (len(y) - 512) // max(1, (w - 1))))
    S = np.log1p(S)
    S = S / max(S.max(), 1e-9)
    if S.shape[1] < w:
        S = np.pad(S, ((0, 0), (0, w - S.shape[1])))
    S = S[:S.shape[0], :w]
    rows = np.linspace(0, S.shape[0] - 1, h).astype(int)
    S = S[rows]
    S = np.flipud(S)
    rgba = np.zeros((h, w, 4), dtype=np.float32)
    rgba[..., 0] = S
    rgba[..., 1] = S * S
    rgba[..., 2] = 1.0 - 0.5 * S
    rgba[..., 3] = 1.0
    return rgba.reshape(-1)

def update_views(y):
    # waveform
    xw = np.arange(len(y)) / SR
    dpg.set_value("wave_series", [xw.tolist(), y.tolist()])

    # fft
    Y = np.abs(np.fft.rfft(y))
    f = np.fft.rfftfreq(len(y), 1 / SR)
    dpg.set_value("fft_series", [f.tolist(), Y.tolist()])

    # spectrogram
    dpg.set_value("spec_tex", spectrogram_rgba(y))

    # features
    feat = features(y)
    dpg.set_value("feat_text", (
        f"centroid: {feat['centroid']:.1f} Hz\n"
        f"bandwidth: {feat['bandwidth']:.1f} Hz\n"
        f"zcr: {feat['zcr']:.4f}\n"
        f"peak_count: {feat['peak_count']}\n"
        f"harmonicity: {feat['harmonicity']:.4f}\n"
        f"flux: {feat['flux']:.4f}"
    ))
    dpg.set_value("transient_bar", feat["transient"])
    dpg.set_value("body_bar", feat["body"])
    dpg.set_value("tail_bar", feat["tail"])

    xt = np.arange(len(feat["centroid_t"]))
    dpg.set_value("centroid_series", [xt.tolist(), feat["centroid_t"].tolist()])
    dpg.set_value("bandwidth_series", [xt.tolist(), feat["bandwidth_t"].tolist()])

def play_current():
    y = synth(get_params())
    audio.play(y)
    update_views(y)

def play_cb(sender=None, app_data=None, user_data=None):
    play_current()

def preset_cb(sender=None, app_data=None, user_data=None):
    set_params(PRESETS[dpg.get_value("preset")])

def random_cb(sender=None, app_data=None, user_data=None):
    rng = np.random.default_rng()
    for k, (_, lo, hi, _) in DIMS.items():
        dpg.set_value(k, float(rng.uniform(lo, hi)))

def build_ui():
    dpg.create_context()

    with dpg.texture_registry(show=False):
        blank = np.zeros((256, 256, 4), dtype=np.float32)
        blank[..., 3] = 1.0
        dpg.add_raw_texture(256, 256, blank.reshape(-1), format=dpg.mvFormat_Float_rgba, tag="spec_tex")

    with dpg.window(label="Percussion Cockpit", width=1400, height=900):
        with dpg.group(horizontal=True):
            with dpg.child_window(width=320, height=840, border=True):
                dpg.add_text("1. CONTROL SPACE")
                dpg.add_combo(list(PRESETS.keys()), default_value="Cowbell", tag="preset", callback=preset_cb)
                dpg.add_button(label="Load Preset", callback=preset_cb)
                dpg.add_button(label="Randomize", callback=random_cb)
                dpg.add_separator()
                for k, (label, lo, hi, default) in DIMS.items():
                    dpg.add_slider_float(tag=k, label=label, min_value=lo, max_value=hi, default_value=default)

            with dpg.child_window(width=220, height=840, border=True):
                dpg.add_text("2. AUDIO OUTPUT")
                dpg.add_button(label="Play", width=180, height=40, callback=play_cb)
                dpg.add_text("Spacebar = Play")
                dpg.add_separator()
                dpg.add_text("Current point lives in 10D control space.")
                dpg.add_text("Press play after moving sliders.")

            with dpg.child_window(width=500, height=840, border=True):
                dpg.add_text("3. SIGNAL VIEWS")
                with dpg.plot(height=180, width=470):
                    dpg.add_plot_axis(dpg.mvXAxis, label="time")
                    y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="amp")
                    dpg.add_line_series([], [], parent=y_axis, tag="wave_series")
                with dpg.plot(height=180, width=470):
                    dpg.add_plot_axis(dpg.mvXAxis, label="Hz")
                    y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="mag")
                    dpg.add_line_series([], [], parent=y_axis, tag="fft_series")
                dpg.add_text("spectrogram")
                dpg.add_image("spec_tex", width=470, height=300)

            with dpg.child_window(width=320, height=840, border=True):
                dpg.add_text("4. FEATURE SPACE")
                dpg.add_text("", tag="feat_text")
                dpg.add_text("transient")
                dpg.add_progress_bar(tag="transient_bar", default_value=0.0, width=280)
                dpg.add_text("body")
                dpg.add_progress_bar(tag="body_bar", default_value=0.0, width=280)
                dpg.add_text("tail")
                dpg.add_progress_bar(tag="tail_bar", default_value=0.0, width=280)
                dpg.add_separator()
                dpg.add_text("centroid over time")
                with dpg.plot(height=120, width=280):
                    dpg.add_plot_axis(dpg.mvXAxis, label="frame")
                    y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Hz")
                    dpg.add_line_series([], [], parent=y_axis, tag="centroid_series")
                dpg.add_text("bandwidth over time")
                with dpg.plot(height=120, width=280):
                    dpg.add_plot_axis(dpg.mvXAxis, label="frame")
                    y_axis = dpg.add_plot_axis(dpg.mvYAxis, label="Hz")
                    dpg.add_line_series([], [], parent=y_axis, tag="bandwidth_series")

    with dpg.handler_registry():
        dpg.add_key_press_handler(key=dpg.mvKey_Spacebar, callback=play_cb)

    dpg.create_viewport(title="Percussion Cockpit", width=1400, height=900)
    dpg.setup_dearpygui()
    dpg.show_viewport()

    set_params(PRESETS["Cowbell"])
    update_views(synth(get_params()))

    audio.start()
    try:
        dpg.start_dearpygui()
    finally:
        audio.stop()
        dpg.destroy_context()

if __name__ == "__main__":
    build_ui()
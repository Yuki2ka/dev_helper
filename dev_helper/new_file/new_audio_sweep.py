# === SETTINGS START ===
PATH = None
NUMBER = 2
FORMAT = 'opus'
VOLUME = 0.6  # 60%
DURATION_MS = 2000  # 2 seconds sweep duration
START_OCTAVE = 3  # Start at Octave 3 (A3 ~ 220Hz)
END_OCTAVE = 5    # End at Octave 5 (A5 ~ 880Hz)
BASE_A4 = 440.0   # Standard tuning reference
QUANTIZE = False   # True: Stepped pentatonic, False: Smooth sweep
# === SETTINGS END ===

import argparse
import os
import re
import numpy as np
from pydub import AudioSegment

# --- Helper Functions ---
import sys
from pathlib import Path

# Ensure project root is importable for path_args package
_resolved = Path(__file__).resolve()
if len(_resolved.parents) >= 3:
    sys.path.insert(0, str(_resolved.parents[2]))

from path_args import resolve_paths, choose_output_file

def get_closest_pentatonic_freq(target_freq):
    """Finds the closest A-minor pentatonic scale frequency (A, C, D, E, G)."""
    # Calculate midi note relative to A4 (midi 69)
    midi_note = 69 + 12 * np.log2(target_freq / BASE_A4)
    rounded_midi = round(midi_note)
    
    # Check distance to allowed pitch classes in A minor pentatonic: A(0), C(3), D(5), E(7), G(10)
    pitch_class = rounded_midi % 12
    allowed_classes = [0, 3, 5, 7, 10]
    
    # Find closest allowed pitch class
    closest_pc = min(allowed_classes, key=lambda x: min(abs(x - pitch_class), 12 - abs(x - pitch_class)))
    
    # Adjust midi note to the correct pitch class
    diff = closest_pc - pitch_class
    if diff > 6:
        diff -= 12
    elif diff < -6:
        diff += 12
    
    final_midi = rounded_midi + diff
    return BASE_A4 * (2 ** ((final_midi - 69) / 12))

def make_sweep(duration_ms, start_oct, end_oct, volume, quantize, sample_rate=44100):
    """Generates an audio segment with a continuous or quantized sweep."""
    total_samples = int(sample_rate * (duration_ms / 1000.0))
    t = np.linspace(0, duration_ms / 1000.0, total_samples, endpoint=False)
    
    # Frequencies corresponding to Octave roots (relative to A4 being Octave 4)
    # Octave 4 root = A4 (440Hz). Octave 3 root = A3 (220Hz), etc.
    f_start = BASE_A4 * (2 ** (start_oct - 4))
    f_end = BASE_A4 * (2 ** (end_oct - 4))
    
    # Exponential frequency interpolation over time
    frequencies = f_start * ((f_end / f_start) ** (t / (duration_ms / 1000.0)))
    
    if quantize:
        # Step through pentatonic scale notes instead of a smooth sweep
        frequencies = np.array([get_closest_pentatonic_freq(f) for f in frequencies])
        # Phase reconstruction for stepped frequencies to avoid popping artifacts
        dt = 1.0 / sample_rate
        phases = 2 * np.pi * np.cumsum(frequencies) * dt
        samples = np.sin(phases)
    else:
        # Standard exponential chirp formula phase profile
        k = (f_end / f_start) ** (1.0 / (duration_ms / 1000.0))
        phases = 2 * np.pi * f_start * ((k ** t - 1) / np.log(k))
        samples = np.sin(phases)
        
    # Apply fade-in and fade-out to prevent audio pops at the start and end boundaries
    fade_len = min(int(sample_rate * 0.02), total_samples // 10) # 20ms or 10%
    fade_in = np.linspace(0, 1, fade_len)
    fade_out = np.linspace(1, 0, fade_len)
    samples[:fade_len] *= fade_in
    samples[-fade_len:] *= fade_out

    # Normalize volume and convert float to 16-bit PCM array
    # 0.6 volume -> maps peak value accurately
    max_val = np.iinfo(np.int16).max
    audio_data = (samples * volume * max_val).astype(np.int16)
    
    # Construct PyDub AudioSegment
    return AudioSegment(
        audio_data.tobytes(),
        frame_rate=sample_rate,
        sample_width=2,  # 16-bit
        channels=1       # Mono
    )

# --- Argument Parsing ---
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Create high-quality audio sweeps with optional pentatonic quantization"
    )

    parser.add_argument("path", nargs="?", default=PATH,
                        help="Output filename or folder")
    parser.add_argument("-n", "--number", type=int, default=NUMBER,
                        help=f"Number of audio files (default: {NUMBER})")
    parser.add_argument("--format", default=FORMAT,
                        help=f"Format (default: {FORMAT})")
    parser.add_argument("--volume", type=float, default=VOLUME,
                        help=f"Volume 0.0-1.0 (default: {VOLUME})")
    parser.add_argument("--duration", type=int, default=DURATION_MS,
                        help=f"Sweep duration in ms (default: {DURATION_MS})")
    parser.add_argument("--start-octave", type=int, default=START_OCTAVE,
                        help=f"Starting octave relative to A4=4 (default: {START_OCTAVE})")
    parser.add_argument("--end-octave", type=int, default=END_OCTAVE,
                        help=f"Ending octave relative to A4=4 (default: {END_OCTAVE})")
    
    # Quantize flag processing (defaults to True)
    parser.add_argument("--quantize", type=str, default=str(QUANTIZE).lower(),
                        choices=["true", "false"],
                        help=f"Quantize sweep to pentatonic steps (default: {str(QUANTIZE).lower()})")

    return parser.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)
    is_quantize = args.quantize == "true"

    resolved = resolve_paths(
        args,
        arg_names=("path",),
        create="auto",
    )
    dir_path = resolved.first

    if dir_path.is_dir():
        base_name = "sweep"
    else:
        base_name = dir_path.stem or "sweep"

    m = re.match(r'^(.*?)(\d+)$', base_name)
    prefix = m.group(1) if m else base_name
    counter = int(m.group(2)) if m else 1

    for i in range(args.number):
        idx = counter + i
        stem = f"{prefix}{idx}"
        ext = f".{args.format}"

        candidate_path = dir_path / f"{stem}{ext}"

        final_path = choose_output_file(
            location=candidate_path,
            default_stem=stem,
            default_suffix=ext,
        )

        step_start_oct = args.start_octave + i
        step_end_oct = args.end_octave + i
        
        audio = make_sweep(
            duration_ms=args.duration,
            start_oct=step_start_oct,
            end_oct=step_end_oct,
            volume=args.volume,
            quantize=is_quantize
        )
        
        audio.export(str(final_path), format=args.format)
        print(f"Created: {final_path} (Octaves: {step_start_oct} -> {step_end_oct}, Quantize: {is_quantize})")

if __name__ == "__main__":
    main()

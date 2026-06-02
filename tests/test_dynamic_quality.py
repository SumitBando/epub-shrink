import pytest
import pathlib
from epub_shrink import estimate_next_quality, EpubContext

def test_first_lossy_pass_power_law():
    # 1. Starting at 100 with weighted_avg_quality = 90 and ratio = 2.0
    # q_est = 90 * (1 / 2.0) ^ 0.5 = 90 * 0.7071 = 63.64 -> 64
    # Clamped by MAX_DROP (25 points drop from 100) to 75
    history = [(100, 2000)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=95,
        weighted_avg_quality=90.0
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=1000,
        ratio=2.0,
        ctx=ctx
    )
    assert q_next == 75

    # 2. Starting at 100 with weighted_avg_quality = 95 and ratio = 1.21
    # Since weighted_avg_quality > 90, we use the high-quality JPEG linear drop model:
    # drop = 4.0 * (1.21 - 1.0) = 0.84
    # q_est = 95.0 - 0.84 = 94.16 -> 94
    history = [(100, 1210)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=95,
        weighted_avg_quality=95.0
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=1000,
        ratio=1.21,
        ctx=ctx
    )
    assert q_next == 94

    # 3. Starting at custom quality 80 with ratio = 1.44 (so target is 1000, size is 1440)
    # ref_q = 80
    # q_est = 80 * (1 / 1.44) ^ 0.5 = 80 * (1 / 1.2) = 66.67 -> 67
    history = [(80, 1440)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=0,
        weighted_avg_quality=90.0  # weighted_avg is 90, but we compressed at 80 so ref_q is 80
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=1000,
        ratio=1.44,
        ctx=ctx
    )
    assert q_next == 67


def test_secant_method_interpolation():
    # Correct secant estimation:
    # Point 1: q=95, size=2000
    # Point 2: q=90, size=1500
    # Slope = (1500 - 2000) / (90 - 95) = -500 / -5 = 100 bytes per quality point
    # Target = 1200 bytes.
    # q_est = 90 - (1500 - 1200) / 100 = 90 - 3 = 87
    history = [(95, 2000), (90, 1500)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=0,
        weighted_avg_quality=100.0
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=1200,
        ratio=1.25,
        ctx=ctx
    )
    assert q_next == 87


def test_secant_method_fallbacks():
    # Size difference is 0 (division by zero / no size change)
    # Ratio > 2.0 -> drop by 15 (clamped to: q_curr - 15 = 85 - 15 = 70)
    history = [(90, 1500), (85, 1500)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=0,
        weighted_avg_quality=100.0
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=700,
        ratio=2.1,
        ctx=ctx
    )
    assert q_next == 70  # max(15, min(85-2, 85-15)) = 70

    # Ratio > 1.5 -> drop by 10 (clamped to: q_curr - 10 = 85 - 10 = 75)
    q_next = estimate_next_quality(
        history=history,
        target_bytes=900,
        ratio=1.6,
        ctx=ctx
    )
    assert q_next == 75  # max(15, min(85-2, 85-10)) = 75

    # Ratio <= 1.5 -> drop by 5 (clamped to: q_curr - 5 = 85 - 5 = 80)
    q_next = estimate_next_quality(
        history=history,
        target_bytes=1200,
        ratio=1.25,
        ctx=ctx
    )
    assert q_next == 80  # max(15, min(85-2, 85-5)) = 80


def test_safety_clamps():
    # Clamp 1: Strict decrease (at least drop by 2)
    # Even if interpolation estimates q_est = 89, it must be clamped to 88 (when q_curr = 90)
    history = [(95, 1010), (90, 1000)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=0,
        weighted_avg_quality=100.0
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=999,
        ratio=1.001,
        ctx=ctx
    )
    assert q_next == 88

    # Clamp 2: Max drop per step (at most 25 points drop)
    # If slope is very flat, interpolation might estimate a huge drop.
    # Point 1: q=95, size=5000
    # Point 2: q=90, size=4990
    # Slope = (4990 - 5000) / (90 - 95) = -10 / -5 = 2 bytes per point.
    # Target = 1000.
    # q_est = 90 - (4990 - 1000) / 2 = 90 - 1995 = -1905
    # Max drop clamp: q_curr - 25 = 90 - 25 = 65.
    history = [(95, 5000), (90, 4990)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=0,
        weighted_avg_quality=100.0
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=1000,
        ratio=4.99,
        ctx=ctx
    )
    assert q_next == 65

    # Clamp 3: Minimum quality floor (never drop below 15)
    history = [(25, 2000), (20, 1800)]
    ctx = EpubContext(
        input_file=pathlib.Path("dummy.epub"),
        extract_dir=pathlib.Path("/tmp/dummy"),
        max_estimated_quality=0,
        weighted_avg_quality=100.0
    )
    q_next = estimate_next_quality(
        history=history,
        target_bytes=1000,
        ratio=1.8,
        ctx=ctx
    )
    assert q_next == 15

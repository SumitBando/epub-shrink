import pytest
from epub_shrink import estimate_next_quality

def test_first_lossy_pass_q100():
    # If initial quality was 100, ratio > 2.0 -> jump to 80
    history = [(100, 2000)]
    q_next = estimate_next_quality(history, target_bytes=900, ratio=2.2, max_estimated_quality=92)
    assert q_next == 80

    # If initial quality was 100, ratio <= 2.0, max_estimated_quality is set -> jump to max_estimated_quality - 1
    history = [(100, 1500)]
    q_next = estimate_next_quality(history, target_bytes=1000, ratio=1.5, max_estimated_quality=90)
    assert q_next == 89

    # If initial quality was 100, ratio <= 2.0, max_estimated_quality not set -> jump to 95
    history = [(100, 1500)]
    q_next = estimate_next_quality(history, target_bytes=1000, ratio=1.5, max_estimated_quality=0)
    assert q_next == 95


def test_first_lossy_pass_custom_q():
    # If custom starting quality < 100, ratio > 2.0 -> drop by 15 points
    history = [(90, 2000)]
    q_next = estimate_next_quality(history, target_bytes=900, ratio=2.2, max_estimated_quality=0)
    assert q_next == 75

    # If custom starting quality < 100, ratio <= 2.0 -> drop by 5 points
    history = [(90, 1500)]
    q_next = estimate_next_quality(history, target_bytes=1000, ratio=1.5, max_estimated_quality=0)
    assert q_next == 85


def test_secant_method_interpolation():
    # Correct secant estimation:
    # Point 1: q=95, size=2000
    # Point 2: q=90, size=1500
    # Slope = (1500 - 2000) / (90 - 95) = -500 / -5 = 100 bytes per quality point
    # Target = 1200 bytes.
    # q_est = 90 - (1500 - 1200) / 100 = 90 - 3 = 87
    history = [(95, 2000), (90, 1500)]
    q_next = estimate_next_quality(history, target_bytes=1200, ratio=1.25, max_estimated_quality=0)
    assert q_next == 87


def test_secant_method_fallbacks():
    # Size difference is 0 (division by zero / no size change)
    # Ratio > 2.0 -> drop by 15
    history = [(90, 1500), (85, 1500)]
    q_next = estimate_next_quality(history, target_bytes=700, ratio=2.1, max_estimated_quality=0)
    assert q_next == 70  # max(15, min(85-2, 85-15)) = 70

    # Ratio > 1.5 -> drop by 10
    q_next = estimate_next_quality(history, target_bytes=900, ratio=1.6, max_estimated_quality=0)
    assert q_next == 75  # max(15, min(85-2, 85-10)) = 75

    # Ratio <= 1.5 -> drop by 5
    q_next = estimate_next_quality(history, target_bytes=1200, ratio=1.25, max_estimated_quality=0)
    assert q_next == 80  # max(15, min(85-2, 85-5)) = 80


def test_safety_clamps():
    # Clamp 1: Strict decrease (at least drop by 2)
    # Even if interpolation estimates q_est = 89, it must be clamped to 88 (when q_curr = 90)
    # Point 1: q=95, size=1010
    # Point 2: q=90, size=1000
    # Slope = (1000 - 1010) / (90 - 95) = -10 / -5 = 2
    # Target = 999 bytes.
    # q_est = 90 - (1000 - 999) / 2 = 90 - 0.5 = 89.5 -> round to 90.
    # q_curr - 2 = 88.
    history = [(95, 1010), (90, 1000)]
    q_next = estimate_next_quality(history, target_bytes=999, ratio=1.001, max_estimated_quality=0)
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
    q_next = estimate_next_quality(history, target_bytes=1000, ratio=4.99, max_estimated_quality=0)
    assert q_next == 65

    # Clamp 3: Minimum quality floor (never drop below 15)
    # Point 1: q=25, size=2000
    # Point 2: q=20, size=1800
    # Slope = 200 / 5 = 40.
    # Target = 1000.
    # q_est = 20 - (1800 - 1000) / 40 = 20 - 20 = 0.
    # Floor: 15.
    history = [(25, 2000), (20, 1800)]
    q_next = estimate_next_quality(history, target_bytes=1000, ratio=1.8, max_estimated_quality=0)
    assert q_next == 15

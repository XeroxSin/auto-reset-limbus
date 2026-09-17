import logging
import math
import time

import numpy as np

log = logging.getLogger(__name__)

_KNOTS = (0.0, 300.0, 900.0, 2700.0, 8100.0)
_K = len(_KNOTS)

_P0 = 4.0 * np.ones((_K, _K)) + 0.25 * np.eye(_K)
_Q = 2e-3 * np.ones((_K, _K)) + 5e-4 * np.eye(_K)

_POINTER_STATE = {
    "gains": np.ones(_K, dtype=float),
    "P": _P0.copy(),
    "last_events": None,
}


def _basis(v):
    if v <= _KNOTS[0]:
        return 0, 0.0
    for k in range(_K - 1):
        if v < _KNOTS[k + 1]:
            return k, (v - _KNOTS[k]) / (_KNOTS[k + 1] - _KNOTS[k])
    return _K - 2, 1.0


def _project_monotone(gains, variances):
    weights = 1.0 / np.maximum(variances, 1e-6)
    blocks = []  # [value, weight, count]
    for value, weight in zip(gains, weights):
        blocks.append([value, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    out = []
    for value, _, count in blocks:
        out.extend([value] * count)
    return np.array(out)


def _raw_step(gains, screen_dist, dt):
    for k in range(_K - 1):
        if _KNOTS[k + 1] * dt * gains[k + 1] >= screen_dist:
            b = (gains[k + 1] - gains[k]) / (_KNOTS[k + 1] - _KNOTS[k])
            A = b / dt
            B = gains[k] - b * _KNOTS[k]
            if A <= 1e-12:
                return screen_dist / max(B, 1e-6)
            return (-B + math.sqrt(B * B + 4.0 * A * screen_dist)) / (2.0 * A)
    return screen_dist / gains[-1]


def update_pointer_scale(accumulated_raw, start_pos, current_pos):
    events = _POINTER_STATE["last_events"]
    _POINTER_STATE["last_events"] = None

    raw_delta = np.asarray(accumulated_raw, dtype=float)
    if not events or float(np.dot(raw_delta, raw_delta)) <= 64.0:
        return

    sum_dx = sum(e[1] for e in events)
    sum_dy = sum(e[2] for e in events)
    if (sum_dx, sum_dy) != (int(round(raw_delta[0])), int(round(raw_delta[1]))):
        return

    actual = np.asarray(current_pos, dtype=float) - np.asarray(start_pos, dtype=float)
    actual_norm = float(np.linalg.norm(actual))
    if actual_norm <= 0.0:
        return

    # Per-knot raw mass
    W = np.zeros((_K, 2))
    for dt, rdx, rdy in events:
        v = math.hypot(rdx, rdy) / dt
        k, w = _basis(v)
        W[k, 0] += (1.0 - w) * rdx
        W[k, 1] += (1.0 - w) * rdy
        W[k + 1, 0] += w * rdx
        W[k + 1, 1] += w * rdy

    gains = _POINTER_STATE["gains"]
    pred = W.T @ gains
    pred_norm = float(np.linalg.norm(pred))
    if pred_norm <= 0.0:
        return
    alignment = float(np.dot(actual, pred)) / (actual_norm * pred_norm)
    ratio = actual_norm / pred_norm
    if alignment < 0.85 or not (0.1 <= ratio <= 10.0):
        return
    if float(np.linalg.norm(actual - pred)) <= max(3.0, 0.04 * actual_norm):
        return

    P = _POINTER_STATE["P"] + _Q
    R = max(4.0, (0.03 * actual_norm) ** 2)
    for axis in (0, 1):
        h = W[:, axis]
        eps = float(actual[axis]) - float(np.dot(h, gains))
        if eps * eps > 6.25 * (float(h @ P @ h) + R):
            P = _P0.copy()
            break
    gains = gains.copy()
    for axis in (0, 1):
        h = W[:, axis]
        Ph = P @ h
        S = float(np.dot(h, Ph)) + R
        gains += Ph * ((float(actual[axis]) - float(np.dot(h, gains))) / S)
        P = P - np.outer(Ph, Ph) / S
    gains = _project_monotone(gains, np.diag(P))
    np.clip(gains, 0.02, 50.0, out=gains)
    _POINTER_STATE["gains"] = gains
    _POINTER_STATE["P"] = (P + P.T) * 0.5

    curve = ", ".join(f"{int(v)}:{g:.2f}" for v, g in zip(_KNOTS, gains))
    log.debug("adjusting pointer gain curve: %s", curve)


def execute_trajectory(dev, raw_path, times, emit_func=None):
    if not callable(emit_func):
        raise ValueError("emit_func must be a callable function")

    path = np.asarray(raw_path, dtype=float)
    times = np.asarray(times, dtype=float)
    n_points = min(len(path), len(times))

    if n_points <= 1:
        _POINTER_STATE["last_events"] = None
        return np.array([0.0, 0.0], dtype=float)

    gains = [float(g) for g in _POINTER_STATE["gains"]]
    flat = all(abs(g - gains[0]) <= 1e-12 for g in gains)
    inv_scale = 1.0 / gains[0] if flat else 0.0
    x0 = float(path[0, 0])
    y0 = float(path[0, 1])

    events = []
    planned_x = 0.0
    planned_y = 0.0
    emitted_x = 0
    emitted_y = 0
    start_time = time.perf_counter()
    last_emit_time = start_time

    for i in range(1, n_points):
        next_time = start_time + float(times[i])

        now = time.perf_counter()
        while now < next_time:
            remaining = next_time - now
            if remaining > 4e-4:
                time.sleep(remaining - 3e-4)
            now = time.perf_counter()

        if flat:
            planned_x = (float(path[i, 0]) - x0) * inv_scale
            planned_y = (float(path[i, 1]) - y0) * inv_scale
        else:
            step_x = float(path[i, 0] - path[i - 1, 0])
            step_y = float(path[i, 1] - path[i - 1, 1])
            dist = math.hypot(step_x, step_y)
            if dist > 0.0:
                dt = max(float(times[i] - times[i - 1]), 1e-4)
                r = _raw_step(gains, dist, dt)
                planned_x += step_x * (r / dist)
                planned_y += step_y * (r / dist)

        raw_dx = int(round(planned_x - emitted_x))
        raw_dy = int(round(planned_y - emitted_y))

        if raw_dx != 0 or raw_dy != 0:
            emit_func(dev, raw_dx, raw_dy)
            now = time.perf_counter()
            events.append((max(now - last_emit_time, 1e-4), raw_dx, raw_dy))
            last_emit_time = now
            emitted_x += raw_dx
            emitted_y += raw_dy

    _POINTER_STATE["last_events"] = events
    return np.array([float(emitted_x), float(emitted_y)], dtype=float)
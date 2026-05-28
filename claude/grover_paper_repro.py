#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reproduction of:
  Y. Wang and P. S. Krstic, Phys. Rev. A 102, 042609 (2020)

This script simulates Grover's search under noise using Qiskit Aer and
computes selectivity thresholds (S = 10 log10(Pt / Phn)).

Assumptions (due to missing Supplemental Material in the local folder):
  - MCTA uses MCX high-level synthesis with 1 clean ancilla when needed.
  - M1GA uses a global oracle and local diffusion on two partitions each iteration.
  - M2GA is modeled as two independent stages (prefix then suffix) whose
    distributions are combined by product, matching the measure/reset separation.
If you have the Supplemental Material, adjust the local diffusion routines
to match it exactly.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import MCXGate
from qiskit_aer import AerSimulator
from qiskit_aer.noise import (
    NoiseModel,
    amplitude_damping_error,
    depolarizing_error,
    pauli_error,
    phase_damping_error,
    thermal_relaxation_error,
)
from qiskit.transpiler.passes.synthesis.high_level_synthesis import HLSConfig

# -----------------------------
# Global config (tune as needed)
# -----------------------------

SHOTS = 1024
RUNS = 3
OPTIMIZATION_LEVEL = 1

QUBIT_COUNTS = [4, 6, 8, 10]
QUBIT_COUNTS = [10]
ERROR_TYPES = ["BF", "PF", "DEP", "AD", "PD"]
ERROR_SWEEP = np.logspace(-4.5, -1, 15)

RUN_ERROR_THRESHOLDS = False
RUN_THERMAL_THRESHOLDS = True

# Append outputs (one record per result) to avoid losing long runs.
APPEND_THRESHOLDS_JSONL = "grover_thresholds.jsonl"
APPEND_THRESHOLDS_CSV = "grover_thresholds.append.csv"
APPEND_THERMAL_JSONL = "grover_thermal_thresholds.jsonl"

# Thermal scan grid (microseconds). Increase density for smoother Fig. 3/5.
T1_T2_GRID_US = np.logspace(1, 4, 10)  # 10 us to 10,000 us

# Use target |11..1> as in the paper.
TARGET_STATE_ONE = "1"

# MCX HLS configuration (avoid deprecated QuantumCircuit.mcx(mode=...))
# Plugin names are listed by HighLevelSynthesisPluginManager in Qiskit.
MCX_NOANCILLA = "noaux_v24"
MCX_MCTA = "1_clean_kg24"  # 1 clean ancilla when needed

# Qiskit Aer basis and gate times (seconds)
BASIS_GATES = ["u", "cx"]
GATE_TIMES = {
    "u": 100e-9,      # use U3-like duration as in the paper
    "cx": 300e-9,
    "reset": 1000e-9,
    "measure": 1000e-9,
}


@dataclass(frozen=True)
class AlgorithmSpec:
    name: str
    kind: str
    use_mcta: bool


ALGORITHMS = [
    AlgorithmSpec("SGA", "standard", False),
    AlgorithmSpec("SGAA", "standard", True),
    AlgorithmSpec("M1GA", "local1", False),
    AlgorithmSpec("M1GAA", "local1", True),
    AlgorithmSpec("M2GA", "local2", False),
    AlgorithmSpec("M2GAA", "local2", True),
]


# -----------------------------
# Helper math and metrics
# -----------------------------


def optimal_iterations(num_qubits: int, num_marked: int = 1) -> int:
    """Standard Grover iteration count."""
    n = 2 ** num_qubits
    m = max(1, num_marked)
    theta = math.asin(math.sqrt(m / n))
    iters = math.floor(math.pi / (4 * theta))
    return max(1, iters)


def calculate_selectivity(
    probs: Dict[str, float], target_bits: str
) -> Tuple[float, float, float]:
    """Return (selectivity, pt, phn)."""
    pt = probs.get(target_bits, 0.0)
    phn = 0.0
    for state, p in probs.items():
        if state == target_bits:
            continue
        if p > phn:
            phn = p

    if pt > 0.0 and phn > 0.0:
        selectivity = 10.0 * math.log10(pt / phn)
    elif pt > 0.0 and phn == 0.0:
        selectivity = 100.0
    else:
        selectivity = -100.0
    return selectivity, pt, phn


def estimate_threshold(
    error_probs: Iterable[float], selectivities: Iterable[float], s_threshold: float = 3.0
) -> Optional[float]:
    """Linear interpolation of error probability at a selectivity threshold."""
    e = np.asarray(list(error_probs), dtype=float)
    s = np.asarray(list(selectivities), dtype=float)
    if len(e) == 0:
        return None
    sort_idx = np.argsort(s)
    s_sorted = s[sort_idx]
    e_sorted = e[sort_idx]
    if s_threshold < s_sorted.min() or s_threshold > s_sorted.max():
        return None
    return float(np.interp(s_threshold, s_sorted, e_sorted))


# -----------------------------
# Circuit building blocks
# -----------------------------


def _apply_oracle(
    qc: QuantumCircuit,
    qubits: List[int],
    target_bits: str,
) -> None:
    """Phase oracle for a single target state."""
    if len(qubits) < 2:
        raise ValueError("Oracle requires at least 2 qubits.")

    # Align target bits with qubit indices (bitstring is MSB->LSB).
    target_rev = target_bits[::-1]
    zero_qubits = [qubits[i] for i, bit in enumerate(target_rev) if bit == "0"]
    if zero_qubits:
        qc.x(zero_qubits)

    # Multi-controlled Z via H + MCX + H.
    qc.h(qubits[-1])
    qc.append(MCXGate(len(qubits) - 1), qubits)
    qc.h(qubits[-1])

    if zero_qubits:
        qc.x(zero_qubits)


def _apply_diffuser(
    qc: QuantumCircuit,
    qubits: List[int],
) -> None:
    """Standard diffusion operator on the given qubits."""
    if len(qubits) < 2:
        raise ValueError("Diffuser requires at least 2 qubits.")

    qc.h(qubits)
    qc.x(qubits)
    qc.h(qubits[-1])
    qc.append(MCXGate(len(qubits) - 1), qubits)
    qc.h(qubits[-1])
    qc.x(qubits)
    qc.h(qubits)


def build_standard_grover(
    num_qubits: int, target_bits: str, use_mcta: bool
) -> QuantumCircuit:
    """SGA / SGAA."""
    qc = QuantumCircuit(num_qubits + (1 if use_mcta else 0), num_qubits)
    data = list(range(num_qubits))

    qc.h(data)
    for _ in range(optimal_iterations(num_qubits)):
        _apply_oracle(qc, data, target_bits)
        _apply_diffuser(qc, data)

    qc.measure(data, data)
    return qc


def build_m1ga(
    num_qubits: int, target_bits: str, prefix_len: int, use_mcta: bool
) -> QuantumCircuit:
    """One-stage local diffusion (M1GA / M1GAA)."""
    qc = QuantumCircuit(num_qubits + (1 if use_mcta else 0), num_qubits)
    data = list(range(num_qubits))

    prefix = list(range(num_qubits - prefix_len, num_qubits))
    suffix = list(range(0, num_qubits - prefix_len))

    qc.h(data)
    for _ in range(optimal_iterations(num_qubits)):
        _apply_oracle(qc, data, target_bits)
        if prefix:
            _apply_diffuser(qc, prefix)
        if suffix:
            _apply_diffuser(qc, suffix)

    qc.measure(data, data)
    return qc


def build_stage_grover(
    num_qubits: int, target_bits: str, use_mcta: bool
) -> QuantumCircuit:
    """Single stage circuit used in M2GA/M2GAA."""
    return build_standard_grover(num_qubits, target_bits, use_mcta)


# -----------------------------
# Noise models
# -----------------------------


def _add_gate_error(
    noise_model: NoiseModel, err_1q, err_2q, gate_1q: List[str], gate_2q: List[str]
) -> None:
    noise_model.add_all_qubit_quantum_error(err_1q, gate_1q)
    noise_model.add_all_qubit_quantum_error(err_2q, gate_2q)


def create_noise_model(error_type: str, error_prob: float) -> NoiseModel:
    noise_model = NoiseModel()

    if error_type == "DEP":
        err_1q = depolarizing_error(error_prob, 1)
        err_2q = depolarizing_error(min(1.0, error_prob * 10.0), 2)
        _add_gate_error(noise_model, err_1q, err_2q, ["u"], ["cx"])

    elif error_type == "BF":
        err_1q = pauli_error([("X", error_prob), ("I", 1.0 - error_prob)])
        err_2q = pauli_error([("XX", min(1.0, error_prob * 10.0)), ("II", 1.0 - min(1.0, error_prob * 10.0))])
        _add_gate_error(noise_model, err_1q, err_2q, ["u"], ["cx"])

    elif error_type == "PF":
        err_1q = pauli_error([("Z", error_prob), ("I", 1.0 - error_prob)])
        err_2q = pauli_error([("ZZ", min(1.0, error_prob * 10.0)), ("II", 1.0 - min(1.0, error_prob * 10.0))])
        _add_gate_error(noise_model, err_1q, err_2q, ["u"], ["cx"])

    elif error_type == "AD":
        err_1q = amplitude_damping_error(error_prob)
        err_2q = err_1q.tensor(err_1q)
        _add_gate_error(noise_model, err_1q, err_2q, ["u"], ["cx"])

    elif error_type == "PD":
        err_1q = phase_damping_error(error_prob)
        err_2q = err_1q.tensor(err_1q)
        _add_gate_error(noise_model, err_1q, err_2q, ["u"], ["cx"])

    else:
        raise ValueError(f"Unknown error type: {error_type}")

    return noise_model


def create_thermal_noise_model(t1_us: float, t2_us: float) -> NoiseModel:
    t1 = t1_us * 1e-6
    t2 = min(t2_us * 1e-6, 2.0 * t1)
    noise_model = NoiseModel()

    err_u = thermal_relaxation_error(t1, t2, GATE_TIMES["u"])
    err_cx = err_u.tensor(err_u)

    noise_model.add_all_qubit_quantum_error(err_u, ["u"])
    noise_model.add_all_qubit_quantum_error(err_cx, ["cx"])
    return noise_model


# -----------------------------
# Simulation utilities
# -----------------------------


def run_circuit_probs(
    circuit: QuantumCircuit,
    noise_model: NoiseModel,
    shots: int,
    runs: int,
    mcx_method: str,
) -> Dict[str, float]:
    backend = AerSimulator(noise_model=noise_model)
    hls_config = HLSConfig()
    hls_config.set_methods("mcx", [mcx_method])
    # Avoid passing backend with basis_gates to keep Aer warnings away and
    # ensure a stable "u"/"cx" basis for the noise model.
    transpiled = transpile(
        circuit,
        basis_gates=BASIS_GATES,
        optimization_level=OPTIMIZATION_LEVEL,
        hls_config=hls_config,
    )

    counts_sum: Dict[str, int] = {}
    for _ in range(runs):
        result = backend.run(transpiled, shots=shots).result()
        counts = result.get_counts()
        for state, count in counts.items():
            counts_sum[state] = counts_sum.get(state, 0) + count

    total = shots * runs
    return {state: count / total for state, count in counts_sum.items()}


def combine_distributions(
    prefix: Dict[str, float], suffix: Dict[str, float]
) -> Dict[str, float]:
    combined: Dict[str, float] = {}
    for p_state, p_prob in prefix.items():
        for s_state, s_prob in suffix.items():
            combined[p_state + s_state] = p_prob * s_prob
    return combined


# -----------------------------
# Main analysis routines
# -----------------------------


def build_circuits_for_algorithm(
    algo: AlgorithmSpec, num_qubits: int, target_bits: str, prefix_len: int
) -> Tuple[Optional[QuantumCircuit], Optional[QuantumCircuit], Optional[QuantumCircuit]]:
    """Return (single, stage1, stage2). Only one of them is non-None."""
    if algo.kind == "standard":
        return build_standard_grover(num_qubits, target_bits, algo.use_mcta), None, None
    if algo.kind == "local1":
        return build_m1ga(num_qubits, target_bits, prefix_len, algo.use_mcta), None, None
    if algo.kind == "local2":
        # Stage circuits on prefix and suffix, combined later.
        prefix_bits = target_bits[:prefix_len]
        suffix_bits = target_bits[prefix_len:]
        stage1 = build_stage_grover(prefix_len, prefix_bits, algo.use_mcta)
        stage2 = build_stage_grover(num_qubits - prefix_len, suffix_bits, algo.use_mcta)
        return None, stage1, stage2
    raise ValueError(f"Unsupported algorithm: {algo.name}")


def run_error_thresholds() -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    csv_header = ["n_qubits", "algorithm", "error_type", "threshold_prob"]
    for n in QUBIT_COUNTS:
        print(f"[INFO] Starting error sweep for n={n} qubits...")
        target_bits = TARGET_STATE_ONE * n
        prefix_len = n // 2

        for algo in ALGORITHMS:
            print(f"[INFO]  Algorithm: {algo.name}")
            single, stage1, stage2 = build_circuits_for_algorithm(
                algo, n, target_bits, prefix_len
            )

            for err_type in ERROR_TYPES:
                print(f"[INFO]   Error type: {err_type}")
                selectivities = []
                start = time.time()
                for idx, prob in enumerate(ERROR_SWEEP, start=1):
                    noise_model = create_noise_model(err_type, prob)
                    if single is not None:
                        probs = run_circuit_probs(
                            single,
                            noise_model,
                            SHOTS,
                            RUNS,
                            MCX_MCTA if algo.use_mcta else MCX_NOANCILLA,
                        )
                    else:
                        probs1 = run_circuit_probs(
                            stage1,
                            noise_model,
                            SHOTS,
                            RUNS,
                            MCX_MCTA if algo.use_mcta else MCX_NOANCILLA,
                        )
                        probs2 = run_circuit_probs(
                            stage2,
                            noise_model,
                            SHOTS,
                            RUNS,
                            MCX_MCTA if algo.use_mcta else MCX_NOANCILLA,
                        )
                        probs = combine_distributions(probs1, probs2)

                    s_val, pt, phn = calculate_selectivity(probs, target_bits)
                    selectivities.append(s_val)
                    if idx == 1 or idx == len(ERROR_SWEEP) or idx % 5 == 0:
                        elapsed = time.time() - start
                        print(
                            f"[INFO]    Progress: {idx}/{len(ERROR_SWEEP)} "
                            f"(p={prob:.2e}, {elapsed:.1f}s)"
                        )

                threshold = estimate_threshold(ERROR_SWEEP, selectivities, s_threshold=3.0)
                row = {
                    "n_qubits": n,
                    "algorithm": algo.name,
                    "error_type": err_type,
                    "threshold_prob": threshold,
                    "selectivity_curve": selectivities,
                    "error_sweep": ERROR_SWEEP.tolist(),
                }
                results.append(row)
                append_jsonl(APPEND_THRESHOLDS_JSONL, row)
                append_csv_row(
                    APPEND_THRESHOLDS_CSV,
                    csv_header,
                    [
                        row["n_qubits"],
                        row["algorithm"],
                        row["error_type"],
                        row["threshold_prob"],
                    ],
                )
                print(
                    f"[INFO]   Checkpoint appended: {APPEND_THRESHOLDS_JSONL}, {APPEND_THRESHOLDS_CSV}"
                )
    return results


def run_thermal_thresholds() -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    for n in QUBIT_COUNTS:
        print(f"[INFO] Starting thermal sweep for n={n} qubits...")
        target_bits = TARGET_STATE_ONE * n
        prefix_len = n // 2

        for algo in ALGORITHMS:
            print(f"[INFO]  Algorithm: {algo.name}")
            single, stage1, stage2 = build_circuits_for_algorithm(
                algo, n, target_bits, prefix_len
            )

            accepted: List[Tuple[float, float, float]] = []
            total_points = len(T1_T2_GRID_US) * len(T1_T2_GRID_US)
            point_idx = 0
            start = time.time()
            for t1_us in T1_T2_GRID_US:
                for t2_us in T1_T2_GRID_US:
                    point_idx += 1
                    noise_model = create_thermal_noise_model(t1_us, t2_us)
                    if single is not None:
                        probs = run_circuit_probs(
                            single,
                            noise_model,
                            SHOTS,
                            RUNS,
                            MCX_MCTA if algo.use_mcta else MCX_NOANCILLA,
                        )
                    else:
                        probs1 = run_circuit_probs(
                            stage1,
                            noise_model,
                            SHOTS,
                            RUNS,
                            MCX_MCTA if algo.use_mcta else MCX_NOANCILLA,
                        )
                        probs2 = run_circuit_probs(
                            stage2,
                            noise_model,
                            SHOTS,
                            RUNS,
                            MCX_MCTA if algo.use_mcta else MCX_NOANCILLA,
                        )
                        probs = combine_distributions(probs1, probs2)

                    s_val, pt, phn = calculate_selectivity(probs, target_bits)
                    if 2.5 <= s_val <= 3.5:
                        accepted.append((t1_us, t2_us, s_val))
                    if point_idx == 1 or point_idx == total_points or point_idx % 20 == 0:
                        elapsed = time.time() - start
                        print(
                            f"[INFO]   Progress: {point_idx}/{total_points} "
                            f"({elapsed:.1f}s)"
                        )

            if accepted:
                t1_avg = float(np.mean([v[0] for v in accepted]))
                t2_avg = float(np.mean([v[1] for v in accepted]))
            else:
                t1_avg = None
                t2_avg = None

            row = {
                "n_qubits": n,
                "algorithm": algo.name,
                "t1_us_avg": t1_avg,
                "t2_us_avg": t2_avg,
                "accepted_points": accepted,
            }
            results.append(row)
            append_jsonl(APPEND_THERMAL_JSONL, row)
            print(f"[INFO]  Checkpoint appended: {APPEND_THERMAL_JSONL}")
    return results


def save_json(data: List[Dict[str, object]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def save_thresholds_csv(data: List[Dict[str, object]], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["n_qubits", "algorithm", "error_type", "threshold_prob"])
        for row in data:
            writer.writerow(
                [
                    row["n_qubits"],
                    row["algorithm"],
                    row["error_type"],
                    row["threshold_prob"],
                ]
            )


def _atomic_write(path: str, data: bytes) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def save_json_atomic(data: List[Dict[str, object]], path: str) -> None:
    payload = json.dumps(data, indent=2).encode("utf-8")
    _atomic_write(path, payload)


def save_thresholds_csv_atomic(data: List[Dict[str, object]], path: str) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["n_qubits", "algorithm", "error_type", "threshold_prob"])
    for row in data:
        writer.writerow(
            [
                row["n_qubits"],
                row["algorithm"],
                row["error_type"],
                row["threshold_prob"],
            ]
        )
    _atomic_write(path, buffer.getvalue().encode("utf-8"))


def append_jsonl(path: str, record: Dict[str, object]) -> None:
    line = json.dumps(record)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def append_csv_row(path: str, header: List[str], row: List[object]) -> None:
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def main() -> None:
    if RUN_ERROR_THRESHOLDS:
        thresholds = run_error_thresholds()
        save_json(thresholds, "grover_thresholds.json")
        save_thresholds_csv(thresholds, "grover_thresholds.csv")

    if RUN_THERMAL_THRESHOLDS:
        thermal = run_thermal_thresholds()
        save_json(thermal, "grover_thermal_thresholds.json")


if __name__ == "__main__":
    main()

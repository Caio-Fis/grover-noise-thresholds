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
from qiskit_aer.library import save_density_matrix
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

# Resolve all relative data paths to the repository's data/ directory,
# so the script can be launched from anywhere.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(_SRC_DIR), "data")
os.makedirs(DATA_DIR, exist_ok=True)
os.chdir(DATA_DIR)

# Choose Active Hardware Profile
# Options: "ibmq_cambridge_2020", "ibm_fez_2026", "ibm_kingston_2026"
ACTIVE_PROFILE = "ibm_fez_2026"

HARDWARE_PROFILES = {
    "ibmq_cambridge_2020": {
        "u": 100e-9,      # 100 ns
        "cx": 300e-9,     # 300 ns
        "reset": 1000e-9,
        "measure": 1000e-9,
    },
    "ibm_fez_2026": {
        "u": 35e-9,       # 35 ns
        "cx": 120e-9,     # 120 ns
        "reset": 250e-9,  # 250 ns
        "measure": 500e-9,# 500 ns
    },
    "ibm_kingston_2026": {
        "u": 35e-9,       # 35 ns
        "cx": 120e-9,     # 120 ns
        "reset": 250e-9,  # 250 ns
        "measure": 500e-9,# 500 ns
    }
}

GATE_TIMES = HARDWARE_PROFILES[ACTIVE_PROFILE]

# M1GA / M1GAA Configuration
# If True, M1GA uses alternating partition diffusion between iterations.
# If False, M1GA uses naive simultaneous partition diffusion (fails structurally).
USE_CORRECTED_M1GA = True

SHOTS = 512
RUNS = 2
OPTIMIZATION_LEVEL = 1

QUBIT_COUNTS = [4, 6, 8, 10]
ERROR_TYPES = ["BF", "PF", "DEP", "AD", "PD"]
ERROR_SWEEP = np.logspace(-4.5, -1, 15)

RUN_ERROR_THRESHOLDS = True
RUN_THERMAL_THRESHOLDS = True
SKIP_CHECKPOINTS = True

# Append outputs (one record per result) to avoid losing long runs.
APPEND_THRESHOLDS_JSONL = f"grover_thresholds_{ACTIVE_PROFILE}.jsonl"
APPEND_THRESHOLDS_CSV = f"grover_thresholds_{ACTIVE_PROFILE}.append.csv"
APPEND_THERMAL_JSONL = f"grover_thermal_thresholds_{ACTIVE_PROFILE}.jsonl"


def recompute_paths():
    """Recalcula os caminhos de arquivos e tempos de portas baseados no ACTIVE_PROFILE ativo."""
    global GATE_TIMES, APPEND_THRESHOLDS_JSONL, APPEND_THRESHOLDS_CSV, APPEND_THERMAL_JSONL
    GATE_TIMES = HARDWARE_PROFILES[ACTIVE_PROFILE]
    APPEND_THRESHOLDS_JSONL = f"grover_thresholds_{ACTIVE_PROFILE}.jsonl"
    APPEND_THRESHOLDS_CSV = f"grover_thresholds_{ACTIVE_PROFILE}.append.csv"
    APPEND_THERMAL_JSONL = f"grover_thermal_thresholds_{ACTIVE_PROFILE}.jsonl"


# Thermal scan grid (microseconds). Increase density for smoother Fig. 3/5.
T1_T2_GRID_US = np.logspace(1, 4, 10)  # 10 us to 10,000 us

# Use target |11..1> as in the paper.
TARGET_STATE_ONE = "1"

# MCX HLS configuration (avoid deprecated QuantumCircuit.mcx(mode=...))
# Plugin names are listed by HighLevelSynthesisPluginManager in Qiskit.
MCX_NOANCILLA = "noaux_v24"
MCX_MCTA = "1_clean_kg24"  # 1 clean ancilla when needed

# Qiskit Aer basis gates
BASIS_GATES = ["u", "cx"]

# Single global AerSimulator instance to avoid heavy C++ backend initialization overhead in sweep loops.
SIMULATOR = AerSimulator()
DENSITY_MATRIX_SIMULATOR = AerSimulator(method="density_matrix")


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
    for i in range(optimal_iterations(num_qubits)):
        _apply_oracle(qc, data, target_bits)
        if USE_CORRECTED_M1GA:
            # Alternating local diffusion partitions between iterations restores
            # amplitude amplification (solving the structural double-negative identity failure).
            if i % 2 == 0:
                if prefix:
                    _apply_diffuser(qc, prefix)
            else:
                if suffix:
                    _apply_diffuser(qc, suffix)
        else:
            # Naive simultaneous local diffusion (fails structurally due to (+I) tensor product component)
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
    err_cx_1q = thermal_relaxation_error(t1, t2, GATE_TIMES["cx"])
    err_cx = err_cx_1q.tensor(err_cx_1q)

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
    hls_config = HLSConfig()
    hls_config.set_methods("mcx", [mcx_method])
    # Evita passar o backend com basis_gates para manter os avisos do Aer longe e
    # garantir uma base estável "u"/"cx" para o modelo de ruído.
    transpiled = transpile(
        circuit,
        basis_gates=BASIS_GATES,
        optimization_level=OPTIMIZATION_LEVEL,
        hls_config=hls_config,
    )
    return run_transpiled_circuit_probs(transpiled, noise_model, shots, runs)


def run_transpiled_circuit_probs(
    transpiled_circuit: QuantumCircuit,
    noise_model: NoiseModel,
    shots: int,
    runs: int,
) -> Dict[str, float]:
    num_qubits = transpiled_circuit.num_qubits
    
    # Remove as medições finais para evitar o colapso do estado misto antes de salvar a matriz de densidade
    circuit_no_meas = transpiled_circuit.remove_final_measurements(inplace=False)
    
    # Adiciona a operação para salvar a matriz de densidade exata
    circuit_no_meas.save_density_matrix()
    
    # Executa a simulação exata analítica utilizando o simulador global reutilizável (shots e runs ignorados)
    result = DENSITY_MATRIX_SIMULATOR.run(circuit_no_meas, noise_model=noise_model).result()
    dm = result.data()["density_matrix"]
    
    # Obtém a distribuição de probabilidades exata sobre os 2^n estados da base
    probs_array = dm.probabilities()
    probs_dict = {}
    for i, p in enumerate(probs_array):
        # Filtra valores extremamente pequenos (ruído de precisão float) para manter o dicionário limpo e rápido
        if p > 1e-12:
            state_str = f"{i:0{num_qubits}b}"
            probs_dict[state_str] = float(p)
            
    return probs_dict


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


def load_existing_gate_threshold_keys(path: str) -> set:
    """Carrega as chaves (n_qubits, algorithm, error_type) já calculadas de um arquivo .jsonl."""
    keys = set()
    if not os.path.exists(path):
        return keys
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        n = record.get("n_qubits")
                        algo = record.get("algorithm")
                        err = record.get("error_type")
                        if n is not None and algo is not None and err is not None:
                            keys.add((int(n), str(algo), str(err)))
                    except Exception:
                        pass
    except Exception as e:
        print(f"[WARNING] Error reading checkpoint file {path}: {e}")
    return keys


def load_existing_thermal_threshold_keys(path: str) -> set:
    """Carrega as chaves (n_qubits, algorithm) já calculadas de um arquivo .jsonl."""
    keys = set()
    if not os.path.exists(path):
        return keys
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        record = json.loads(line)
                        n = record.get("n_qubits")
                        algo = record.get("algorithm")
                        if n is not None and algo is not None:
                            keys.add((int(n), str(algo)))
                    except Exception:
                        pass
    except Exception as e:
        print(f"[WARNING] Error reading checkpoint file {path}: {e}")
    return keys


def run_error_thresholds() -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    csv_header = ["n_qubits", "algorithm", "error_type", "threshold_prob"]
    
    computed_keys = set()
    if SKIP_CHECKPOINTS:
        computed_keys = load_existing_gate_threshold_keys(APPEND_THRESHOLDS_JSONL)
        if computed_keys:
            print(f"[INFO] Loaded {len(computed_keys)} existing gate threshold checkpoints from {APPEND_THRESHOLDS_JSONL}")

    for n in QUBIT_COUNTS:
        print(f"[INFO] Starting error sweep for n={n} qubits...")
        shots_to_use = SHOTS if n < 10 else 256
        target_bits = TARGET_STATE_ONE * n
        prefix_len = n // 2

        for algo in ALGORITHMS:
            errors_to_run = []
            for err_type in ERROR_TYPES:
                if (n, algo.name, err_type) in computed_keys:
                    print(f"[INFO]  Skipping n={n}, algorithm={algo.name}, error_type={err_type} (already computed)")
                else:
                    errors_to_run.append(err_type)

            if not errors_to_run:
                print(f"[INFO]  Algorithm {algo.name} on n={n} fully computed in checkpoints. Skipping.")
                continue

            print(f"[INFO]  Algorithm: {algo.name}")
            single, stage1, stage2 = build_circuits_for_algorithm(
                algo, n, target_bits, prefix_len
            )

            # Pre-transpile the circuits for this algorithm to avoid classical compilation overhead in loops.
            mcx_method = MCX_MCTA if algo.use_mcta else MCX_NOANCILLA
            hls_config = HLSConfig()
            hls_config.set_methods("mcx", [mcx_method])

            if single is not None:
                transpiled_single = transpile(
                    single,
                    basis_gates=BASIS_GATES,
                    optimization_level=OPTIMIZATION_LEVEL,
                    hls_config=hls_config,
                )
                transpiled_stage1, transpiled_stage2 = None, None
            else:
                transpiled_single = None
                transpiled_stage1 = transpile(
                    stage1,
                    basis_gates=BASIS_GATES,
                    optimization_level=OPTIMIZATION_LEVEL,
                    hls_config=hls_config,
                )
                transpiled_stage2 = transpile(
                    stage2,
                    basis_gates=BASIS_GATES,
                    optimization_level=OPTIMIZATION_LEVEL,
                    hls_config=hls_config,
                )

            for err_type in errors_to_run:
                print(f"[INFO]   Error type: {err_type}")
                selectivities = []
                start = time.time()
                for idx, prob in enumerate(ERROR_SWEEP, start=1):
                    noise_model = create_noise_model(err_type, prob)
                    if transpiled_single is not None:
                        probs = run_transpiled_circuit_probs(
                            transpiled_single,
                            noise_model,
                            shots_to_use,
                            RUNS,
                        )
                    else:
                        probs1 = run_transpiled_circuit_probs(
                            transpiled_stage1,
                            noise_model,
                            shots_to_use,
                            RUNS,
                        )
                        probs2 = run_transpiled_circuit_probs(
                            transpiled_stage2,
                            noise_model,
                            shots_to_use,
                            RUNS,
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
    
    computed_keys = set()
    if SKIP_CHECKPOINTS:
        computed_keys = load_existing_thermal_threshold_keys(APPEND_THERMAL_JSONL)
        if computed_keys:
            print(f"[INFO] Loaded {len(computed_keys)} existing thermal threshold checkpoints from {APPEND_THERMAL_JSONL}")

    for n in QUBIT_COUNTS:
        print(f"[INFO] Starting thermal sweep for n={n} qubits...")
        shots_to_use = SHOTS if n < 10 else 256
        target_bits = TARGET_STATE_ONE * n
        prefix_len = n // 2

        for algo in ALGORITHMS:
            if (n, algo.name) in computed_keys:
                print(f"[INFO]  Skipping n={n}, algorithm={algo.name} (already computed)")
                continue

            print(f"[INFO]  Algorithm: {algo.name}")
            single, stage1, stage2 = build_circuits_for_algorithm(
                algo, n, target_bits, prefix_len
            )

            # Pre-transpile the circuits for this algorithm to avoid classical compilation overhead in loops.
            mcx_method = MCX_MCTA if algo.use_mcta else MCX_NOANCILLA
            hls_config = HLSConfig()
            hls_config.set_methods("mcx", [mcx_method])

            if single is not None:
                transpiled_single = transpile(
                    single,
                    basis_gates=BASIS_GATES,
                    optimization_level=OPTIMIZATION_LEVEL,
                    hls_config=hls_config,
                )
                transpiled_stage1, transpiled_stage2 = None, None
            else:
                transpiled_single = None
                transpiled_stage1 = transpile(
                    stage1,
                    basis_gates=BASIS_GATES,
                    optimization_level=OPTIMIZATION_LEVEL,
                    hls_config=hls_config,
                )
                transpiled_stage2 = transpile(
                    stage2,
                    basis_gates=BASIS_GATES,
                    optimization_level=OPTIMIZATION_LEVEL,
                    hls_config=hls_config,
                )

            # Estimate circuit duration dynamically in microseconds to center the thermal sweep
            # exactly around the physical decoherence threshold (~0.5 * tau).
            def get_duration(t_qc):
                if t_qc is None:
                    return 0.0
                ops = t_qc.count_ops()
                num_u = ops.get("u", 0)
                num_cx = ops.get("cx", 0)
                num_measure = ops.get("measure", 0)
                num_reset = ops.get("reset", 0)
                return (
                    num_u * GATE_TIMES["u"] +
                    num_cx * GATE_TIMES["cx"] +
                    num_measure * GATE_TIMES["measure"] +
                    num_reset * GATE_TIMES["reset"]
                ) * 1e6

            if transpiled_single is not None:
                tau = get_duration(transpiled_single)
            else:
                tau = max(get_duration(transpiled_stage1), get_duration(transpiled_stage2))

            # Adaptive coherence grid: from 0.05 * tau to 20.0 * tau
            # This ensures the sweep is perfectly centered on the physical transition for any hardware profile,
            # algorithm complexity, and qubit count.
            min_grid = max(0.01, 0.05 * tau)
            max_grid = max(0.1, 20.0 * tau)
            grid_us = np.logspace(np.log10(min_grid), np.log10(max_grid), 10)

            accepted: List[Tuple[float, float, float]] = []
            grid_data: List[Tuple[float, float, float]] = []
            total_points = len(grid_us) * len(grid_us)
            point_idx = 0
            start = time.time()
            for t1_us in grid_us:
                for t2_us in grid_us:
                    point_idx += 1
                    noise_model = create_thermal_noise_model(t1_us, t2_us)
                    if transpiled_single is not None:
                        probs = run_transpiled_circuit_probs(
                            transpiled_single,
                            noise_model,
                            shots_to_use,
                            RUNS,
                        )
                    else:
                        probs1 = run_transpiled_circuit_probs(
                            transpiled_stage1,
                            noise_model,
                            shots_to_use,
                            RUNS,
                        )
                        probs2 = run_transpiled_circuit_probs(
                            transpiled_stage2,
                            noise_model,
                            shots_to_use,
                            RUNS,
                        )
                        probs = combine_distributions(probs1, probs2)

                    s_val, pt, phn = calculate_selectivity(probs, target_bits)
                    grid_data.append((t1_us, t2_us, s_val))
                    if 2.5 <= s_val <= 3.5:
                        accepted.append((t1_us, t2_us, s_val))
                    if point_idx == 1 or point_idx == total_points or point_idx % 20 == 0:
                        elapsed = time.time() - start
                        print(
                            f"[INFO]   Progress: {point_idx}/{total_points} "
                            f"({elapsed:.1f}s)"
                        )

            # Compute t1_avg and t2_avg using continuous grid interpolation to avoid discretization nulls
            crossing_points = []
            n_grid = len(grid_us)
            S_grid = np.zeros((n_grid, n_grid))
            for idx, (t1, t2, s) in enumerate(grid_data):
                i = idx // n_grid
                j = idx % n_grid
                S_grid[i, j] = s

            # Check horizontal crossings (interpolate along T2)
            for i in range(n_grid):
                for j in range(n_grid - 1):
                    s1 = S_grid[i, j]
                    s2 = S_grid[i, j+1]
                    if (s1 - 3.0) * (s2 - 3.0) <= 0 and s1 != s2:
                        t = (3.0 - s1) / (s2 - s1)
                        t1 = grid_us[i]
                        t2 = grid_us[j] + t * (grid_us[j+1] - grid_us[j])
                        crossing_points.append((t1, t2))

            # Check vertical crossings (interpolate along T1)
            for i in range(n_grid - 1):
                for j in range(n_grid):
                    s1 = S_grid[i, j]
                    s2 = S_grid[i+1, j]
                    if (s1 - 3.0) * (s2 - 3.0) <= 0 and s1 != s2:
                        t = (3.0 - s1) / (s2 - s1)
                        t1 = grid_us[i] + t * (grid_us[i+1] - grid_us[i])
                        t2 = grid_us[j]
                        crossing_points.append((t1, t2))

            if crossing_points:
                t1_avg = float(np.mean([p[0] for p in crossing_points]))
                t2_avg = float(np.mean([p[1] for p in crossing_points]))
            elif accepted:
                t1_avg = float(np.mean([v[0] for v in accepted]))
                t2_avg = float(np.mean([v[1] for v in accepted]))
            else:
                # No crossings and no accepted points: check if all points are above/below selectivity of 3.0
                all_s = [p[2] for p in grid_data]
                if all(s > 3.0 for s in all_s):
                    # Coherence threshold is extremely low, below the minimum swept
                    t1_avg = float(grid_us.min())
                    t2_avg = float(grid_us.min())
                elif all(s < 3.0 for s in all_s):
                    # Coherence threshold is extremely high, above the maximum swept
                    t1_avg = float(grid_us.max())
                    t2_avg = float(grid_us.max())
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
    global ACTIVE_PROFILE, QUBIT_COUNTS, RUN_ERROR_THRESHOLDS, RUN_THERMAL_THRESHOLDS, SKIP_CHECKPOINTS
    import argparse
    parser = argparse.ArgumentParser(description="Grover's Search Noise Benchmark reproduction script")
    parser.add_argument("--profile", type=str, choices=list(HARDWARE_PROFILES.keys()), default=ACTIVE_PROFILE,
                        help="Choose Active Hardware Profile")
    parser.add_argument("--qubits", type=str, default=None,
                        help="Comma-separated list of qubits to simulate (e.g. 4,6)")
    parser.add_argument("--only-error", action="store_true", help="Run only gate error thresholds")
    parser.add_argument("--only-thermal", action="store_true", help="Run only thermal thresholds")
    parser.add_argument("--no-skip", action="store_true", help="Disable skipping of already computed checkpoints")

    args = parser.parse_args()

    ACTIVE_PROFILE = args.profile
    recompute_paths()

    if args.qubits is not None:
        QUBIT_COUNTS = [int(q.strip()) for q in args.qubits.split(",") if q.strip()]

    if args.only_error:
        RUN_ERROR_THRESHOLDS = True
        RUN_THERMAL_THRESHOLDS = False
    elif args.only_thermal:
        RUN_ERROR_THRESHOLDS = False
        RUN_THERMAL_THRESHOLDS = True

    if args.no_skip:
        SKIP_CHECKPOINTS = False

    print(f"[INFO] Initializing run with Profile: {ACTIVE_PROFILE}")
    print(f"[INFO] Qubits to simulate: {QUBIT_COUNTS}")
    print(f"[INFO] Run Gate Errors: {RUN_ERROR_THRESHOLDS}, Run Thermal: {RUN_THERMAL_THRESHOLDS}")
    print(f"[INFO] Skipping computed checkpoints: {SKIP_CHECKPOINTS}")

    if RUN_ERROR_THRESHOLDS:
        print(f"[INFO] Starting gate error thresholds...")
        run_error_thresholds()

    if RUN_THERMAL_THRESHOLDS:
        print(f"[INFO] Starting thermal relaxation thresholds...")
        run_thermal_thresholds()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run Grover circuits on IBM Quantum hardware and store selectivity metrics plus gate counts.
Results are appended as JSON Lines (one record per configuration).

Credentials (privacy-safe):
  - Token is read from a separate file (default: ~/.config/ibm_quantum/token).
  - The token is never printed or stored in outputs.
Backend:
  - Uses real hardware (default: ibm_fez). No least-busy selection is used.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import MCXGate
from qiskit_ibm_runtime import QiskitRuntimeService, Sampler
from qiskit.transpiler.passes.synthesis.high_level_synthesis import HLSConfig


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


MCX_NOANCILLA = "noaux_v24"
MCX_MCTA = "1_clean_kg24"


def optimal_iterations(num_qubits: int, num_marked: int = 1) -> int:
    n = 2 ** num_qubits
    m = max(1, num_marked)
    theta = np.arcsin(np.sqrt(m / n))
    iters = int(np.floor(np.pi / (4 * theta)))
    return max(1, iters)


def calculate_selectivity(
    probs: Dict[str, float], target_bits: str
) -> Tuple[float, float, float]:
    pt = probs.get(target_bits, 0.0)
    phn = max((p for state, p in probs.items() if state != target_bits), default=0.0)

    if pt > 0.0 and phn > 0.0:
        selectivity = 10.0 * np.log10(pt / phn)
    elif pt > 0.0 and phn == 0.0:
        selectivity = 100.0
    else:
        selectivity = -100.0
    return float(selectivity), float(pt), float(phn)


def _apply_oracle(qc: QuantumCircuit, qubits: List[int], target_bits: str) -> None:
    target_rev = target_bits[::-1]
    zero_qubits = [qubits[i] for i, bit in enumerate(target_rev) if bit == "0"]
    if zero_qubits:
        qc.x(zero_qubits)

    qc.h(qubits[-1])
    qc.append(MCXGate(len(qubits) - 1), qubits)
    qc.h(qubits[-1])

    if zero_qubits:
        qc.x(zero_qubits)


def _apply_diffuser(qc: QuantumCircuit, qubits: List[int]) -> None:
    qc.h(qubits)
    qc.x(qubits)
    qc.h(qubits[-1])
    qc.append(MCXGate(len(qubits) - 1), qubits)
    qc.h(qubits[-1])
    qc.x(qubits)
    qc.h(qubits)


def build_standard_grover(num_qubits: int, target_bits: str, use_mcta: bool) -> QuantumCircuit:
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


def build_stage_grover(num_qubits: int, target_bits: str, use_mcta: bool) -> QuantumCircuit:
    return build_standard_grover(num_qubits, target_bits, use_mcta)


def build_circuits_for_algorithm(
    algo: AlgorithmSpec, num_qubits: int, target_bits: str, prefix_len: int
) -> Tuple[Optional[QuantumCircuit], Optional[QuantumCircuit], Optional[QuantumCircuit]]:
    if algo.kind == "standard":
        return build_standard_grover(num_qubits, target_bits, algo.use_mcta), None, None
    if algo.kind == "local1":
        return build_m1ga(num_qubits, target_bits, prefix_len, algo.use_mcta), None, None
    if algo.kind == "local2":
        prefix_bits = target_bits[:prefix_len]
        suffix_bits = target_bits[prefix_len:]
        stage1 = build_stage_grover(prefix_len, prefix_bits, algo.use_mcta)
        stage2 = build_stage_grover(num_qubits - prefix_len, suffix_bits, algo.use_mcta)
        return None, stage1, stage2
    raise ValueError(f"Unsupported algorithm: {algo.name}")


def _backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    return name() if callable(name) else str(name)


def get_backend(service: QiskitRuntimeService, backend_name: Optional[str]):
    if not backend_name:
        raise ValueError("A real hardware backend name is required.")
    return service.backend(backend_name)


def _backend_num_qubits(backend) -> int:
    num_qubits = getattr(backend, "num_qubits", None)
    if num_qubits is not None:
        return int(num_qubits)
    config = getattr(backend, "configuration", None)
    if callable(config):
        return int(config().n_qubits)
    raise RuntimeError("Unable to determine backend qubit count.")


def _hls_config(mcx_method: str) -> HLSConfig:
    hls = HLSConfig()
    hls.set_methods("mcx", [mcx_method])
    return hls


def count_gates(circuit: QuantumCircuit, backend, mcx_method: str) -> Tuple[Dict[str, int], int]:
    tqc = transpile(
        circuit,
        backend=backend,
        optimization_level=1,
        hls_config=_hls_config(mcx_method),
    )
    counts = {k: int(v) for k, v in tqc.count_ops().items()}
    total = int(sum(counts.values()))
    return counts, total


def _counts_to_probs(counts: Dict[str, int], num_qubits: int) -> Dict[str, float]:
    total = sum(counts.values())
    if total == 0:
        return {}
    probs: Dict[str, float] = {}
    for state, count in counts.items():
        state_str = str(state)
        if state_str.isdigit():
            state_str = format(int(state_str), f"0{num_qubits}b")
        if len(state_str) < num_qubits:
            state_str = state_str.zfill(num_qubits)
        probs[state_str] = count / total
    return probs


def run_sampler(
    sampler: Sampler,
    circuits: List[QuantumCircuit],
    num_qubits_list: List[int],
    shots: int,
    repeats: int,
) -> Tuple[List[Dict[str, float]], List[int], List[Dict[str, object]]]:
    counts_accum = [dict() for _ in circuits]
    shots_accum = [0 for _ in circuits]
    job_details: List[Dict[str, object]] = []

    for _ in range(repeats):
        start = time.time()
        job = sampler.run(circuits, shots=shots)
        result = job.result()
        elapsed_wall = time.time() - start
        try:
            metrics = job.metrics()
        except Exception:
            metrics = None
        job_details.append(
            {
                "job_id": job.job_id(),
                "elapsed_wall_s": elapsed_wall,
                "metrics": metrics,
            }
        )

        for idx, pub in enumerate(result):
            bit_array = pub.join_data()
            counts = bit_array.get_counts()
            shots_accum[idx] += bit_array.num_shots
            bucket = counts_accum[idx]
            for state, count in counts.items():
                bucket[state] = bucket.get(state, 0) + count

    probs_list: List[Dict[str, float]] = []
    for idx, counts in enumerate(counts_accum):
        probs_list.append(_counts_to_probs(counts, num_qubits_list[idx]))
    return probs_list, shots_accum, job_details


def combine_distributions(
    prefix: Dict[str, float], suffix: Dict[str, float]
) -> Dict[str, float]:
    combined: Dict[str, float] = {}
    for p_state, p_prob in prefix.items():
        for s_state, s_prob in suffix.items():
            combined[p_state + s_state] = p_prob * s_prob
    return combined


def run_hardware(
    backend_name: Optional[str],
    qubit_counts: List[int],
    algorithms: List[AlgorithmSpec],
    shots: int,
    repeats: int,
    output_path: str,
    token_file: str,
) -> None:
    token = _read_token_file(token_file)
    service = QiskitRuntimeService(channel="ibm_quantum", token=token)

    backend = get_backend(service, backend_name)
    backend_name_final = _backend_name(backend)
    if getattr(backend, "simulator", False):
        raise RuntimeError("The selected backend is a simulator. Please use real hardware.")
    backend_qubits = _backend_num_qubits(backend)
    if max(qubit_counts) > backend_qubits:
        raise RuntimeError(
            f"Backend {backend_name_final} has {backend_qubits} qubits, "
            f"but max requested is {max(qubit_counts)}."
        )
    sampler = Sampler(mode=backend)

    for algo in algorithms:
        print(f"[INFO] Running on backend {backend_name_final}: {algo.name}")
        for n in qubit_counts:
            print(f"[INFO]  Qubits: n={n}")
            target_bits = "1" * n
            prefix_len = n // 2
            mcx_method = MCX_MCTA if algo.use_mcta else MCX_NOANCILLA

            single, stage1, stage2 = build_circuits_for_algorithm(
                algo, n, target_bits, prefix_len
            )

            if single is not None:
                gate_counts, total_gates = count_gates(single, backend, mcx_method)
                probs_list, shots_list, job_details = run_sampler(
                    sampler, [single], [n], shots, repeats
                )
                probs = probs_list[0]
                shots_total = shots_list[0]
            else:
                gate_counts_1, total_1 = count_gates(stage1, backend, mcx_method)
                gate_counts_2, total_2 = count_gates(stage2, backend, mcx_method)
                probs_list, shots_list, job_details = run_sampler(
                    sampler, [stage1, stage2], [prefix_len, n - prefix_len], shots, repeats
                )
                probs = combine_distributions(probs_list[0], probs_list[1])
                shots_total = min(shots_list)
                gate_counts = {"stage1": gate_counts_1, "stage2": gate_counts_2}
                total_gates = int(total_1 + total_2)

            selectivity, pt, phn = calculate_selectivity(probs, target_bits)
            record = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "backend": backend_name_final,
                "shots": shots,
                "repeats": repeats,
                "algorithm": algo.name,
                "n_qubits": n,
                "target_state": target_bits,
                "success_prob": pt,
                "max_noise_prob": phn,
                "selectivity": selectivity,
                "shots_total": shots_total,
                "gate_counts": gate_counts,
                "total_gates": total_gates,
                "jobs": job_details,
            }
            _append_jsonl(output_path, record)
            print(f"[INFO]  Result appended: {output_path}")


def _append_jsonl(path: str, record: Dict[str, object]) -> None:
    line = json.dumps(record)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_token_file(token_file: str) -> str:
    token_path = Path(token_file).expanduser()
    if not token_path.exists():
        raise FileNotFoundError(f"Token file not found: {token_path}")
    mode = token_path.stat().st_mode & 0o777
    if mode & 0o077:
        print("[WARN] Token file permissions are too open; use chmod 600.")
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Token file is empty.")
    return token


def _parse_list(value: str) -> List[int]:
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def _parse_algorithms(value: str) -> List[AlgorithmSpec]:
    wanted = {v.strip().upper() for v in value.split(",") if v.strip()}
    return [algo for algo in ALGORITHMS if algo.name.upper() in wanted]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Grover circuits on IBM Quantum hardware with gate counts and selectivity."
    )
    parser.add_argument(
        "--backend",
        type=str,
        default="ibm_fez",
        help="Backend name (required, real hardware).",
    )
    parser.add_argument(
        "--qubits",
        type=str,
        default="4,6,8,10",
        help="Comma-separated qubit counts (e.g., 4,6,8,10).",
    )
    parser.add_argument(
        "--algorithms",
        type=str,
        default="SGA,SGAA,M1GA,M1GAA,M2GA,M2GAA",
        help="Comma-separated algorithm names.",
    )
    parser.add_argument("--shots", type=int, default=1024, help="Shots per circuit.")
    parser.add_argument("--repeats", type=int, default=1, help="Repeat each circuit run.")
    parser.add_argument(
        "--output",
        type=str,
        default="grover_hardware_results.jsonl",
        help="Output JSON Lines file (one record per configuration).",
    )
    parser.add_argument(
        "--token-file",
        type=str,
        default="~/.config/ibm_quantum/token",
        help="Path to the IBM Quantum token file.",
    )
    args = parser.parse_args()

    qubits = _parse_list(args.qubits)
    algos = _parse_algorithms(args.algorithms)
    if not qubits or not algos:
        raise SystemExit("No qubits or algorithms selected.")

    run_hardware(
        backend_name=args.backend,
        qubit_counts=qubits,
        algorithms=algos,
        shots=args.shots,
        repeats=args.repeats,
        output_path=args.output,
        token_file=args.token_file,
    )


if __name__ == "__main__":
    main()

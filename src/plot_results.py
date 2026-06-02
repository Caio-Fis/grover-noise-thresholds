#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera as figuras de resultados a partir dos dados consolidados em data/.

Produz, para um perfil de hardware, três figuras em figures/:
  1. Curvas de seletividade S vs. taxa de erro (estilo Fig. 3/5 do paper).
  2. Limiares de erro de porta por algoritmo (barras agrupadas).
  3. Escalonamento do limiar térmico de coerência (T1) vs. nº de qubits.

Uso:
  python src/plot_results.py --profile ibm_fez_2026
"""

from __future__ import annotations

import argparse
import json
import os

import numpy as np
import matplotlib

matplotlib.use("Agg")  # backend headless (sem display)
import matplotlib.pyplot as plt

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_SRC_DIR)
DATA_DIR = os.path.join(ROOT, "data")
FIG_DIR = os.path.join(ROOT, "figures")

ALGO_ORDER = ["SGA", "SGAA", "M1GA", "M1GAA", "M2GA", "M2GAA"]
ERR_ORDER = ["BF", "PF", "DEP", "AD", "PD"]
ERR_LABEL = {
    "BF": "Bit Flip",
    "PF": "Phase Flip",
    "DEP": "Depolarizing",
    "AD": "Amplitude Damping",
    "PD": "Phase Damping",
}
S_THRESHOLD = 3.0  # critério de sucesso (dB)
HERON_COHERENCE = (250.0, 300.0)  # faixa típica de coerência do Heron r2 (µs)

# Cores consistentes por algoritmo (padrão vs. local de 1 e 2 estágios).
ALGO_COLOR = {
    "SGA": "#1f77b4",
    "SGAA": "#5fa2dd",
    "M1GA": "#ff7f0e",
    "M1GAA": "#ffb066",
    "M2GA": "#2ca02c",
    "M2GAA": "#7bd17b",
}


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _index_last(records, key_fields):
    """Indexa registros por tupla de campos, mantendo o último (dedup de reruns)."""
    out = {}
    for r in records:
        out[tuple(r.get(k) for k in key_fields)] = r
    return out


def fig_selectivity_curves(gate_records, profile, n_qubits):
    idx = _index_last(gate_records, ("n_qubits", "algorithm", "error_type"))
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), sharey=True)
    axes = axes.ravel()

    for ax, err in zip(axes, ERR_ORDER):
        for algo in ALGO_ORDER:
            rec = idx.get((n_qubits, algo, err))
            if not rec or not rec.get("selectivity_curve"):
                continue
            x = np.asarray(rec["error_sweep"], dtype=float)
            y = np.asarray(rec["selectivity_curve"], dtype=float)
            ax.plot(x, y, marker="o", ms=3, lw=1.5, color=ALGO_COLOR[algo], label=algo)
        ax.axhline(S_THRESHOLD, color="k", ls="--", lw=1, alpha=0.7)
        ax.set_xscale("log")
        ax.set_title(f"{ERR_LABEL[err]} ({err})")
        ax.set_xlabel("Taxa de erro de porta")
        ax.grid(True, which="both", ls=":", alpha=0.4)
    axes[0].set_ylabel("Seletividade S (dB)")
    axes[3].set_ylabel("Seletividade S (dB)")

    # painel extra (6º) vira a legenda
    axes[5].axis("off")
    handles, labels = axes[0].get_legend_handles_labels()
    axes[5].legend(handles, labels, title="Algoritmo", loc="center", fontsize=12)
    axes[5].text(0.5, 0.12, f"Linha tracejada: limiar S = {S_THRESHOLD:.0f} dB",
                 ha="center", transform=axes[5].transAxes, fontsize=10, style="italic")

    fig.suptitle(f"Seletividade vs. ruído de porta — {n_qubits} qubits ({profile})",
                 fontsize=15, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return fig


def fig_gate_thresholds(gate_records, profile, n_qubits):
    idx = _index_last(gate_records, ("n_qubits", "algorithm", "error_type"))
    fig, ax = plt.subplots(figsize=(12, 6.5))
    n_algo = len(ALGO_ORDER)
    width = 0.8 / n_algo
    x = np.arange(len(ERR_ORDER))

    for i, algo in enumerate(ALGO_ORDER):
        vals = []
        for err in ERR_ORDER:
            rec = idx.get((n_qubits, algo, err))
            tp = rec.get("threshold_prob") if rec else None
            vals.append((tp * 100.0) if tp is not None else 0.0)
        ax.bar(x + i * width, vals, width, label=algo, color=ALGO_COLOR[algo])

    ax.set_xticks(x + 0.4 - width / 2)
    ax.set_xticklabels([f"{ERR_LABEL[e]}\n({e})" for e in ERR_ORDER])
    ax.set_ylabel("Limiar de erro de porta (%)  —  maior é mais robusto")
    ax.set_title(f"Limiares de erro de porta por algoritmo — {n_qubits} qubits ({profile})",
                 fontsize=14, fontweight="bold")
    ax.legend(title="Algoritmo", ncol=3)
    ax.grid(True, axis="y", ls=":", alpha=0.4)
    fig.tight_layout()
    return fig


def fig_thermal_scaling(thermal_records, profile):
    idx = _index_last(thermal_records, ("n_qubits", "algorithm"))
    qubits = sorted({r["n_qubits"] for r in thermal_records})
    fig, ax = plt.subplots(figsize=(11, 7))

    for algo in ALGO_ORDER:
        xs, ys = [], []
        for q in qubits:
            rec = idx.get((q, algo))
            t1 = rec.get("t1_us_avg") if rec else None
            if t1 is not None:
                xs.append(q)
                ys.append(t1)
        if xs:
            ax.plot(xs, ys, marker="o", lw=2, color=ALGO_COLOR[algo], label=algo)

    ax.axhspan(*HERON_COHERENCE, color="grey", alpha=0.25,
               label=f"Coerência física Heron r2 ({HERON_COHERENCE[0]:.0f}–{HERON_COHERENCE[1]:.0f} µs)")
    ax.set_yscale("log")
    ax.set_xticks(qubits)
    ax.set_xlabel("Número de qubits")
    ax.set_ylabel("T1 mínimo exigido para S ≥ 3 dB (µs)")
    ax.set_title(f"Escalonamento do limiar térmico de coerência ({profile})",
                 fontsize=14, fontweight="bold")
    ax.legend(title="Algoritmo", ncol=2)
    ax.grid(True, which="both", ls=":", alpha=0.4)
    fig.tight_layout()
    return fig


def main():
    parser = argparse.ArgumentParser(description="Gera figuras dos resultados de Grover")
    parser.add_argument("--profile", type=str, default="ibm_fez_2026",
                        help="Perfil de hardware (ex.: ibm_fez_2026, ibm_kingston_2026)")
    parser.add_argument("--selectivity-qubits", type=int, default=8,
                        help="Nº de qubits para o painel de curvas de seletividade")
    parser.add_argument("--bar-qubits", type=int, default=4,
                        help="Nº de qubits para o gráfico de barras de limiares de porta")
    parser.add_argument("--dpi", type=int, default=130)
    args = parser.parse_args()

    os.makedirs(FIG_DIR, exist_ok=True)
    gate = _load(os.path.join(DATA_DIR, f"grover_thresholds_{args.profile}.json"))
    thermal = _load(os.path.join(DATA_DIR, f"grover_thermal_thresholds_{args.profile}.json"))

    figs = {
        f"selectivity_curves_{args.profile}.png":
            fig_selectivity_curves(gate, args.profile, args.selectivity_qubits),
        f"gate_thresholds_{args.profile}.png":
            fig_gate_thresholds(gate, args.profile, args.bar_qubits),
        f"thermal_scaling_{args.profile}.png":
            fig_thermal_scaling(thermal, args.profile),
    }
    for name, fig in figs.items():
        out = os.path.join(FIG_DIR, name)
        fig.savefig(out, dpi=args.dpi, bbox_inches="tight")
        plt.close(fig)
        print(f"[OK] {out}")


if __name__ == "__main__":
    main()

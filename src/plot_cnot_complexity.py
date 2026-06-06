#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gera o gráfico de escalonamento de portas CNOT por algoritmo e qubits.
Salva em figures/cnot_complexity.png.
"""

import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Cores consistentes com o restante do projeto
ALGO_COLOR = {
    "SGA": "#1f77b4",
    "SGAA": "#5fa2dd",
    "M1GA": "#ff7f0e",
    "M1GAA": "#ffb066",
    "M2GA": "#2ca02c",
    "M2GAA": "#7bd17b",
}

# Dados computados exatos obtidos por transpilação
QUBITS = [4, 6, 8, 10]
CNOT_DATA = {
    "SGA": [84, 1008, 5280, 22200],
    "SGAA": [72, 288, 864, 2400],
    "M1GA": [45, 540, 2808, 12000],
    "M1GAA": [39, 180, 2622, 11586],
    "M2GA": [4, 48, 168, 576],
    "M2GAA": [4, 48, 144, 288],
}

def main():
    _SRC_DIR = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(_SRC_DIR)
    FIG_DIR = os.path.join(ROOT, "figures")
    os.makedirs(FIG_DIR, exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(8, 6))
    
    for algo, counts in CNOT_DATA.items():
        ax.plot(
            QUBITS, 
            counts, 
            marker="o", 
            ms=6, 
            lw=2, 
            color=ALGO_COLOR[algo], 
            label=algo
        )
        # Adicionar rótulos numéricos nos pontos de 10 qubits para destaque
        ax.text(
            10.15, 
            counts[-1] * (0.85 if algo in ["M1GAA", "M2GAA"] else 1.05), 
            f"{counts[-1]}", 
            color=ALGO_COLOR[algo], 
            fontweight="bold",
            va="center"
        )

    ax.set_yscale("log")
    ax.set_xticks(QUBITS)
    ax.set_xlabel("Número de Qubits ($N$)", fontsize=12, fontweight="bold")
    ax.set_ylabel("Contagem de Portas CNOT (Escala Log)", fontsize=12, fontweight="bold")
    ax.set_title("Escalonamento de Portas CNOT por Algoritmo", fontsize=14, fontweight="bold", pad=15)
    ax.grid(True, which="both", ls=":", alpha=0.4)
    ax.set_xlim(3.5, 11.2) # Abrir espaço na direita para os rótulos de texto
    
    # Customizar legenda
    ax.legend(title="Algoritmo", loc="upper left", frameon=True, shadow=False, fontsize=10)
    
    output_path = os.path.join(FIG_DIR, "cnot_complexity.png")
    plt.tight_layout()
    plt.savefig(output_path, dpi=300)
    plt.close()
    print(f"[SUCCESS] Saved CNOT complexity plot to: {output_path}")

if __name__ == "__main__":
    main()

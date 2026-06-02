# -*- coding: utf-8 -*-
"""
Projeto Final - Computação Quântica
Algoritmo de Grover - Análise de Limiar de Ruído (Threshold Analysis)
Baseado em: Phys. Rev. A 102, 042609 (2020)
"""

import os
import math
import numpy as np
import json
import pickle
import warnings
import csv
from datetime import datetime
from collections import defaultdict
from scipy.interpolate import interp1d
from qiskit import QuantumCircuit
from qiskit.circuit.library import ZGate, MCMTGate
from qiskit_aer import AerSimulator
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2 as Sampler
from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
from qiskit_aer.noise import NoiseModel, depolarizing_error, thermal_relaxation_error, pauli_error, ReadoutError
import matplotlib.pyplot as plt

# Resolve all relative data paths to the repository's data/ directory,
# so the script can be launched from anywhere.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(_SRC_DIR), "data")
os.makedirs(DATA_DIR, exist_ok=True)
os.chdir(DATA_DIR)

# Suprimir warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', category=UserWarning)

# Configurações Globais
SHOTS = 2048 # Aumentado para melhor resolução do P_hn
RUNS = 10
OPTIMIZATION_LEVEL = 1

print("="*80)
print("INICIALIZANDO EXPERIMENTO DE GROVER BASEADO EM WANG & KRSTIC (2020)")
print("="*80)

# =============================================================================
# FUNÇÃO DE MÉTRICA PRINCIPAL: SELETIVIDADE (SELECTIVITY)
# =============================================================================

def calculate_metrics(probs, marked_states, n_qubits, prob_ideal_ref=None):
    """
    Calcula métricas incluindo a Seletividade (S) definida no paper.

    Ref: "S = 10 log10(Pt / Phn)"
    Onde:
      - Pt: Probabilidade do estado alvo (Success Probability)
      - Phn: Probabilidade do maior sinal de ruído (Highest Noise)
    """
    N = 2 ** n_qubits
    M = len(marked_states)

    # 1. Probabilidade de Sucesso (Pt)
    success_prob = sum(probs.get(state, 0) for state in marked_states)

    # 2. Probabilidade do Maior Ruído (Phn) [cite: 36]
    # Encontrar a maior probabilidade entre os estados NÃO marcados
    max_noise_prob = 0.0
    all_possible_states = [format(i, f'0{n_qubits}b') for i in range(N)]

    for state in all_possible_states:
        if state not in marked_states:
            p = probs.get(state, 0)
            if p > max_noise_prob:
                max_noise_prob = p

    # 3. Cálculo da Seletividade (S)
    # Tratamento para evitar divisão por zero ou log de zero
    if max_noise_prob > 0 and success_prob > 0:
        ratio = success_prob / max_noise_prob
        selectivity = 10 * np.log10(ratio)
    elif max_noise_prob == 0 and success_prob > 0:
        selectivity = 100.0 # Valor alto arbitrário para "infinito" (separação perfeita)
    else:
        selectivity = -100.0 # Valor baixo para falha total

    # Outras métricas auxiliares
    if prob_ideal_ref is not None:
        deg_abs = prob_ideal_ref - success_prob
        deg_rel = (deg_abs / prob_ideal_ref * 100) if prob_ideal_ref > 0 else 0
    else:
        deg_abs = 0; deg_rel = 0

    # Fidelidade (Bhattacharyya distance squared)
    ideal_prob_per_marked = 1.0 / M if M > 0 else 0
    fidelity = 0
    for state in all_possible_states:
        p_ideal = ideal_prob_per_marked if state in marked_states else 0
        p_measured = probs.get(state, 0)
        fidelity += np.sqrt(p_ideal * p_measured)
    fidelity = fidelity ** 2

    return {
        'selectivity': selectivity,       # Métrica Principal
        'success_prob': success_prob,     # Pt [cite: 36]
        'max_noise_prob': max_noise_prob, # Phn [cite: 36]
        'fidelity': fidelity,
        'degradation_rel': deg_rel
    }

print("✓ Função calculate_metrics definida com Seletividade (Eq. 1)")

# =============================================================================
# ARTEFATO 2: GERAÇÃO DE CIRCUITOS E EXECUÇÃO
# =============================================================================

def grover_oracle_no_ancilla(marked_states, n):
    """Cria oráculo usando MCZ (Standard Grover - SGA)."""
    qc = QuantumCircuit(n)
    for target in marked_states:
        rev_target = target[::-1]
        zero_inds = [ind for ind in range(n) if rev_target[ind] == '0']
        if zero_inds: qc.x(zero_inds)
        qc.compose(MCMTGate(ZGate(), n - 1, 1), inplace=True)
        if zero_inds: qc.x(zero_inds)
    return qc

def create_grover_circuit(n, marked_states, use_ancilla=True):
    """
    Cria circuito de Grover.
    use_ancilla=True -> Simula 'SGAA' (com ancillas, menor profundidade teórica em decomposição)
    use_ancilla=False -> Simula 'SGA' (standard multicontrol) [cite: 7]
    """
    N = 2 ** n
    M = len(marked_states)
    optimal_iterations = math.floor(math.pi / (4 * math.asin(math.sqrt(M / N))))
    if optimal_iterations < 1: optimal_iterations = 1

    qc = QuantumCircuit(n + (1 if use_ancilla else 0), n)

    # Inicialização
    qc.h(range(n))
    if use_ancilla:
        ancilla = n
        qc.x(ancilla)
        qc.h(ancilla)

    qc.barrier()

    # Iterações de Grover
    for _ in range(optimal_iterations):
        # Oráculo
        if use_ancilla:
            for target in marked_states:
                rev = target[::-1]
                zero_inds = [i for i, b in enumerate(rev) if b == '0']
                if zero_inds: qc.x(zero_inds)
                qc.mcx(list(range(n)), n) # Alvo na ancilla
                if zero_inds: qc.x(zero_inds)
        else:
            qc.compose(grover_oracle_no_ancilla(marked_states, n), inplace=True)

        qc.barrier()

        # Difusor
        qc.h(range(n))
        qc.x(range(n))
        qc.h(n - 1)
        qc.mcx(list(range(n - 1)), n - 1)
        qc.h(n - 1)
        qc.x(range(n))
        qc.h(range(n))
        qc.barrier()

    qc.measure(list(range(n)), list(range(n)))
    return qc, optimal_iterations

def run_multiple_shots(backend, circuit, shots, runs):
    """Executa o circuito múltiplas vezes e media os resultados."""
    counts_sum = defaultdict(int)
    for _ in range(runs):
        job = backend.run(circuit, shots=shots)
        counts = job.result().get_counts()
        for state, c in counts.items():
            counts_sum[state] += c

    total = runs * shots
    return {state: count / total for state, count in counts_sum.items()}

def save_data(data, filename):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(f"{filename}_{timestamp}.pkl", 'wb') as f:
        pickle.dump(data, f)
    print(f"✓ Dados salvos em {filename}_{timestamp}.pkl")

print("✓ Funções de circuito e execução prontas.")

# =============================================================================
# ARTEFATO 3: EXPERIMENTO DE LIMIAR DE RUÍDO (THRESHOLD ANALYSIS)
# =============================================================================

# Definição do intervalo de varredura conforme Fig. 2 do paper (10^-4 a 10^-1)
# O paper analisa Bit Flip, Phase Flip, Depolarization (Gate Error) e Damping (T1/T2)
ERROR_RATES_SWEEP = np.logspace(-4.5, -1, 15) # 15 pontos de 3e-5 até 0.1

def create_specific_noise_model(error_type, error_prob, n_qubits):
    """
    Cria modelos de ruído específicos para isolar cada tipo de erro,
    conforme metodologia do paper[cite: 97, 106].
    """
    noise_model = NoiseModel()

    if error_type == 'DEP': # Depolarizing (Gate Error)
        # Erro aplicado a portas de 1 qubit (u1, u2, u3) e 2 qubits (cx)
        err_1q = depolarizing_error(error_prob, 1)
        err_2q = depolarizing_error(error_prob * 10, 2) # CX costuma ter erro 10x maior
        noise_model.add_all_qubit_quantum_error(err_1q, ['h', 'x', 'z', 'u1', 'u2', 'u3'])
        noise_model.add_all_qubit_quantum_error(err_2q, ['cx', 'mcx'])

    elif error_type == 'BF': # Bit Flip (Pauli X)
        # Define 1-qubit Pauli X error for 1-qubit gates
        err_1q = pauli_error([('X', error_prob), ('I', 1 - error_prob)])
        # Define 2-qubit Pauli error for 2-qubit gates (XX error)
        err_2q = pauli_error([('XX', error_prob * 10), ('II', 1 - error_prob * 10)])

        noise_model.add_all_qubit_quantum_error(err_1q, ['h', 'x', 'z', 'u1', 'u2', 'u3'])
        noise_model.add_all_qubit_quantum_error(err_2q, ['cx', 'mcx'])

    elif error_type == 'PF': # Phase Flip (Pauli Z)
        # Define 1-qubit Pauli Z error for 1-qubit gates
        err_1q = pauli_error([('Z', error_prob), ('I', 1 - error_prob)])
        # Define 2-qubit Pauli error for 2-qubit gates (ZZ error)
        err_2q = pauli_error([('ZZ', error_prob * 10), ('II', 1 - error_prob * 10)])

        noise_model.add_all_qubit_quantum_error(err_1q, ['h', 'x', 'z', 'u1', 'u2', 'u3'])
        noise_model.add_all_qubit_quantum_error(err_2q, ['cx', 'mcx'])

    elif error_type == 'T1': # Amplitude Damping
        # Emula T1 variando. Probabilidade de erro = dt/T1.
        # Aqui simplificamos tratando 'error_prob' como a probabilidade de decaimento por porta
        # Para ser fiel ao paper, convertemos prob em T1 assumindo gate_time ~ 100ns
        gate_time = 100e-9
        # Evitar divisão por zero
        if error_prob == 0: error_prob = 1e-10
        t1_val = gate_time / error_prob
        t2_val = 2 * t1_val # Assumindo T2 ideal
        err_1q = thermal_relaxation_error(t1_val, t2_val, gate_time)

        # Apply 1-qubit thermal relaxation error only to 1-qubit gates to avoid 'NoiseError'.
        # This is a simplification of the noise model for multi-qubit gates.
        noise_model.add_all_qubit_quantum_error(err_1q, ['h', 'x', 'z', 'u1', 'u2', 'u3'])

    return noise_model

# Configuração do Experimento
# Variamos Qubits (n) para ver o impacto no limiar [cite: 172]
qubit_counts = [4, 6, 8, 10]
error_types = ['DEP', 'BF', 'PF'] # Focando nos erros de porta principais
results_threshold = []

print("\nINICIANDO ANÁLISE DE LIMIAR (THRESHOLD ANALYSIS)")
print("Objetivo: Encontrar a probabilidade de erro onde Seletividade (S) = 3 [cite: 38]")

for n in qubit_counts:
    print(f"\n--- Analisando circuitos de {n} Qubits ---")

    # Usamos sempre o estado |1...1> como alvo para padronização, ou um aleatório
    marked_state = format(2**n - 1, f'0{n}b') # Estado |11..1>

    # Gerar os circuitos (SGA e SGAA)
    # SGA: Sem ancilla (decomposição direta)
    qc_sga, _ = create_grover_circuit(n, [marked_state], use_ancilla=False)
    # SGAA: Com ancilla (menor profundidade para n grande)
    qc_sgaa, _ = create_grover_circuit(n, [marked_state], use_ancilla=True)

    circuits = {'SGA': qc_sga, 'SGAA': qc_sgaa}

    for c_type, qc in circuits.items():
        print(f"  > Tipo de Circuito: {c_type}")

        for err_type in error_types:
            print(f"    > Varrendo erro: {err_type}...", end="")

            selectivity_curve = []

            for prob in ERROR_RATES_SWEEP:
                # 1. Criar Modelo de Ruído
                noise_model = create_specific_noise_model(err_type, prob, n)
                backend = AerSimulator(noise_model=noise_model)
                pm = generate_preset_pass_manager(backend=backend, optimization_level=OPTIMIZATION_LEVEL)

                # 2. Executar
                transpiled = pm.run(qc)
                probs = run_multiple_shots(backend, transpiled, SHOTS, RUNS)

                # 3. Calcular Seletividade
                metrics = calculate_metrics(probs, [marked_state], n)
                selectivity_curve.append(metrics['selectivity'])

            # 4. Determinar o Limiar (Threshold) por Interpolação Linear
            # Queremos encontrar 'prob' onde 'selectivity' == 3
            try:
                # Inverter para interpolação: x=Selectivity, y=Prob
                # Ordenar dados para interpolação funcionar
                sorted_indices = np.argsort(selectivity_curve)
                s_sorted = np.array(selectivity_curve)[sorted_indices]
                p_sorted = np.array(ERROR_RATES_SWEEP)[sorted_indices]

                # Criar função interpoladora
                f = interp1d(s_sorted, p_sorted, kind='linear', fill_value="extrapolate")
                threshold_prob = float(f(3.0)) # Limiar S=3 [cite: 38]

                # Validação simples
                if threshold_prob < 0: threshold_prob = 1e-5
                if threshold_prob > 0.5: threshold_prob = 0.5

            except Exception as e:
                threshold_prob = 0.0
                print(f"(Erro na interpolação: {e})", end="")

            results_threshold.append({
                'n_qubits': n,
                'circuit_type': c_type,
                'error_type': err_type,
                'threshold_prob': threshold_prob,
                'raw_curve_probs': ERROR_RATES_SWEEP.tolist(),
                'raw_curve_selectivity': selectivity_curve
            })
            print(f" Limiar estimado: {threshold_prob:.2e}")

save_data(results_threshold, "grover_threshold_analysis")

# =============================================================================
# ARTEFATO 4: VISUALIZAÇÃO E ANÁLISE (RÉPLICA FIG. 2)
# =============================================================================

print("\n" + "="*80)
print("GERANDO GRÁFICOS DE ANÁLISE DE LIMIAR")
print("="*80)

# Organizar dados para plotagem
# Estrutura: data[error_type][circuit_type] = ([qubits], [thresholds])
plot_data = defaultdict(lambda: defaultdict(lambda: {'x': [], 'y': []}))

for res in results_threshold:
    etype = res['error_type']
    ctype = res['circuit_type']
    n = res['n_qubits']
    thresh = res['threshold_prob']

    plot_data[etype][ctype]['x'].append(n)
    plot_data[etype][ctype]['y'].append(thresh)

# Configuração do Plot (Réplica da Fig. 2a do paper)
fig, ax = plt.subplots(1, 1, figsize=(10, 8))

colors = {'DEP': 'green', 'BF': 'red', 'PF': 'blue'}
markers = {'SGA': 'o', 'SGAA': '^'} # Círculo vazado (SGA) vs Triângulo cheio (SGAA) no paper
linestyles = {'SGA': '--', 'SGAA': '-'} # Tracejado vs Sólido no paper

for etype in error_types:
    for ctype in ['SGA', 'SGAA']:
        x = plot_data[etype][ctype]['x']
        y = plot_data[etype][ctype]['y']

        if not x: continue

        # Ordenar por qubits
        xy = sorted(zip(x, y))
        x = [val[0] for val in xy]
        y = [val[1] for val in xy]

        label = f"{ctype} - {etype}"

        # SGA usa marcadores vazios no paper, SGAA cheios.
        # Aqui usamos alpha para diferenciar ou fillstyle
        fill = 'none' if ctype == 'SGA' else 'full'

        ax.plot(x, y,
                marker=markers[ctype],
                linestyle=linestyles[ctype],
                color=colors[etype],
                label=label,
                linewidth=2,
                markersize=8,
                fillstyle=fill)

ax.set_yscale('log')
ax.set_xlabel('Número de Qubits (n)', fontsize=12)
ax.set_ylabel('Probabilidade de Erro Limiar (S=3)', fontsize=12) # [cite: 38]
ax.set_title('Limiar de Erro para Busca de Grover Bem-Sucedida', fontsize=14)
ax.grid(True, which="both", ls="-", alpha=0.2)
ax.legend(fontsize=10, loc='best')

# Anotações de contexto
plt.text(0.02, 0.02,
         "SGA: Standard Grover Algorithm\nSGAA: Grover com Ancilla\nS=3: Limiar de Seletividade",
         transform=ax.transAxes,
         bbox=dict(facecolor='white', alpha=0.8))

plt.tight_layout()
plt.show()

# =============================================================================
# EXPORTAÇÃO CSV
# =============================================================================
print("\nExportando dados processados para CSV...")

with open('grover_threshold_metrics.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['Error_Type', 'Circuit_Type', 'N_Qubits', 'Threshold_Prob_S3'])
    for res in results_threshold:
        writer.writerow([
            res['error_type'],
            res['circuit_type'],
            res['n_qubits'],
            res['threshold_prob']
        ])

print("✓ Dados exportados: grover_threshold_metrics.csv")
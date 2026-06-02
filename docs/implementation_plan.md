# Plano de Implementação: Varredura Quântica de Grover Incremental e Multi-QPU (IBM Fez & Kingston)

Este plano especifica as etapas necessárias para rodar benchmarks completos do algoritmo de Grover (limiares de porta e de coerência térmica) para todos os qubits estudados no paper original ($N \in [4, 6, 8, 10]$) sob dois computadores IBM baseados no chip Heron r2: **IBM Fez** e **IBM Kingston**. O script será altamente tolerante a falhas, permitindo retomar de forma incremental de onde parou.

---

## 1. Verificação de Abrangência do Paper Original

Após analisar o texto do paper original `physreva_102_042609.txt`, constatamos os seguintes pontos:
* **Registrador de Qubits ($N$)**: O paper original investigou registradores de $N = 4$ a $10$ qubits. As simulações nos computadores atuais devem abranger os tamanhos de $N \in [4, 6, 8, 10]$ qubits para fornecer a amostragem de escalabilidade necessária.
* **Algoritmos**: Foram aplicadas três variantes de Grover, com e sem qubits auxiliares (ancillas) de Toffoli de múltiplos controles:
  1. **SGA / SGAA**: Algoritmo de Grover padrão (Standard Grover's Algorithm / Standard Grover's Algorithm with Ancilla).
  2. **M1GA / M1GAA**: Algoritmo de Grover de estágio único com profundidade reduzida e difusão local (Modified 1-stage).
  3. **M2GA / M2GAA**: Algoritmo de Grover de dois estágios com profundidade reduzida e difusão local independente (Modified 2-stage).
* **Canais de Erro de Portas**: Varreduras de 15 pontos de probabilidade de erro ($10^{-4.5}$ a $10^{-1}$) para 5 canais de ruído:
  * Bit Flip (**BF**), Phase Flip (**PF**), Depolarizing (**DEP**), Amplitude Damping (**AD**), e Phase Damping (**PD**).
* **Limiares Coerentes Térmicos ($T_1$ / $T_2$)**: Varredura de relaxamento térmico e de desfasagem cobrindo os limites onde a seletividade de busca cai para o valor crítico $S = 3.0$ dB.

> [!NOTE]
> As estruturas de algoritmos, canais de erro, e sweeps logarítmicos já estão 100% representados in `grover_paper_repro.py`. Portanto, a execução dos qubits $N \in [4, 6, 8, 10]$ sob ambos os computadores atuais cobrirá com exatidão científica e completude todas as frentes de teste exploradas pelo paper original.

---

## 2. Mudanças Propostas

### A. Adicionar Perfil IBM Kingston em [grover_paper_repro.py](file:///home/crus/Documents/Projetos/Grover/antigravity/grover_paper_repro.py)
Como a IBM Kingston e a IBM Fez utilizam o mesmo processador **Heron r2**, as durações físicas de suas portas e operações elementares são idênticas. Adicionaremos o perfil `"ibm_kingston_2026"` para segmentar e registrar os resultados de forma isolada:

```python
    "ibm_kingston_2026": {
        "u": 35e-9,       # 35 ns
        "cx": 120e-9,     # 120 ns
        "reset": 250e-9,  # 250 ns
        "measure": 500e-9,# 500 ns
    }
```

### B. Implementar a Lógica de Checkpoint (Skip de Resultados Existentes)
Para evitar o retrabalho de rodar simulações que já possuem resultado (especialmente no Fez, onde já concluímos $N=4$ para portas e $N=4, 6, 8$ térmico), implementaremos funções para carregar o histórico de checkpoints dos arquivos `.jsonl` ativos e pular chaves computadas.

1. **Gate Thresholds (`run_error_thresholds`)**:
   * Ler `grover_thresholds_{perfil}.jsonl` se existir.
   * Carregar chaves computadas no formato `(n_qubits, algorithm, error_type)`.
   * Pular a simulação no loop interno se a chave estiver contida nesse conjunto.
2. **Thermal Thresholds (`run_thermal_thresholds`)**:
   * Ler `grover_thermal_thresholds_{perfil}.jsonl` se existir.
   * Carregar chaves computadas no formato `(n_qubits, algorithm)`.
   * Pular a simulação no loop interno se a chave estiver contida nesse conjunto.

### C. Interface de Linha de Comando (`argparse`)
Modificar o bloco `main()` de `grover_paper_repro.py` para receber argumentos de linha de comando:
* `--profile`: Nome do perfil a rodar (`ibm_fez_2026`, `ibm_kingston_2026`, `ibmq_cambridge_2020`).
* `--qubits`: Lista personalizada de contagem de qubits (ex: `4,6,8,10`).
* `--only-error`: Rodar apenas os sweeps de erros de portas.
* `--only-thermal`: Rodar apenas os sweeps térmicos de $T_1/T_2$.
* `--no-skip`: Forçar a execução completa ignorando checkpoints de skip existentes.

Isso tornará o script parametrizável e perfeito para orquestração em scripts de lote.

---

## 3. Criação do Script de Orquestração (`run_experimentos.py`)

Escreveremos um script automatizado [run_experimentos.py](file:///home/crus/Documents/Projetos/Grover/antigravity/run_experimentos.py) que cuidará de todo o ciclo de vida dos benchmarks:

1. **Consolidação Prévia do Fez**:
   * Anexará os dados das simulações térmicas concluídas de $N=6, 8$ qubits no Fez (`grover_thermal_thresholds_fez_2026_6_8.jsonl`) diretamente no arquivo oficial do perfil (`grover_thermal_thresholds_ibm_fez_2026.jsonl`).
   * Isso garantirá que o Fez reconheça esses qubits como computados e os pule na execução.
2. **Execução Sequencial Incremental**:
   * Invocar `grover_paper_repro.py --profile ibm_fez_2026 --qubits 4,6,8,10`
   * Invocar `grover_paper_repro.py --profile ibm_kingston_2026 --qubits 4,6,8,10`
3. **Consolidação e Geração de Resultados**:
   * Rodará uma rotina para mesclar todas as saídas `.jsonl` em arquivos unificados de formato `.json` estruturados e ordenados para posterior plotagem e análise comparativa.

---

## 4. Plano de Verificação

### Testes Automatizados e de Integração
* Executar o script `run_experimentos.py` com o ambiente virtual `.venv` ativo.
* Validar que as chamadas do Fez pulam instantaneamente as chaves já calculadas (como $N=4$ de portas e $N=4, 6, 8$ térmico) e prosseguem para os pontos restantes.
* Garantir que todas as portas e transições físicas de 10 qubits executem sem erros de compilação ou de memória.

### Verificação Manual
* Verificar visualmente se os arquivos `.jsonl` foram criados para `ibm_fez_2026` e `ibm_kingston_2026`.
* Confirmar a integridade dos dados finais gerados no formato `.json` unificado.

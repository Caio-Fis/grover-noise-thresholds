# Grover sob Ruído: Limiares de Seletividade em Hardware NISQ

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Qiskit](https://img.shields.io/badge/Qiskit-2.3-6929C4.svg)](https://www.ibm.com/quantum/qiskit)
[![DOI](https://img.shields.io/badge/DOI-10.1103%2FPhysRevA.102.042609-b31b1b.svg)](https://doi.org/10.1103/PhysRevA.102.042609)

Reprodução e modernização do estudo de limiares de ruído do algoritmo de busca de
Grover, baseado em:

> Y. Wang and P. S. Krstić, *"Multistate Grover search with noise"*,
> **Phys. Rev. A 102, 042609 (2020)**.
> DOI: [10.1103/PhysRevA.102.042609](https://doi.org/10.1103/PhysRevA.102.042609)

O projeto simula a busca de Grover sob diversos canais de ruído usando **Qiskit Aer**
e calcula o **limiar de seletividade**

$$S = 10 \log_{10}\!\left(\frac{P_t}{P_{hn}}\right)$$

onde `P_t` é a probabilidade de medir o estado-alvo e `P_hn` a maior probabilidade
entre os estados não-alvo. O critério de sucesso adotado é `S ≥ 3.0 dB`.

Além do perfil original do paper (`ibmq_cambridge_2020`), o estudo foi estendido para
processadores supercondutores modernos da IBM — **IBM Fez** e **IBM Kingston**
(arquitetura Heron r2) — comparando seis variantes do algoritmo de 4 a 10 qubits.

---

## Algoritmos avaliados

| Sigla   | Descrição                                                |
| :------ | :------------------------------------------------------- |
| `SGA`   | Grover padrão (Standard Grover Algorithm)                |
| `SGAA`  | Grover padrão com qubit auxiliar (ancilla)               |
| `M1GA`  | Grover local de 1 estágio (difusão local alternada)      |
| `M1GAA` | Grover local de 1 estágio com ancilla                    |
| `M2GA`  | Grover local de 2 estágios (prefixo + sufixo)            |
| `M2GAA` | Grover local de 2 estágios com ancilla                   |

Canais de ruído de porta: Bit Flip (`BF`), Phase Flip (`PF`), Depolarizing (`DEP`),
Amplitude Damping (`AD`) e Phase Damping (`PD`). Também é mapeado o limiar térmico
mínimo de coerência (`T1`/`T2`) necessário para manter `S ≥ 3.0 dB`.

---

## Resultados principais

Varredura de 4 a 10 qubits sob os perfis IBM Fez e IBM Kingston.

- **Vantagem exponencial do Grover de 2 estágios (M2GA / M2GAA).** A exigência de
  coerência térmica em relação ao Grover padrão cai de ~12× (4 qubits) para
  **120–130× (10 qubits)**, reduzindo o `T1`/`T2` necessário de mais de 10 ms para
  apenas ~76–84 µs.
- **Viabilidade no Heron r2.** Com coerência física típica de 250–300 µs, o Grover
  padrão (SGA) é inviável a partir de 8 qubits (>2,4 ms exigidos), enquanto a família
  M2GA opera dentro da zona de segurança física, com margens de 3× a 11× a 10 qubits.
- **Robustez a ruído de porta.** A 4 qubits, o M2GA tolera ~4,5% de erro de
  depolarização, contra apenas ~0,30% do SGA.


### Visualização dos Resultados

As figuras a seguir foram geradas a partir dos dados consolidados na pasta `data/` usando o script `src/plot_results.py`.

#### 1. Curvas de Seletividade vs. Taxa de Erro (8 Qubits)
Esta figura demonstra a degradação da seletividade do algoritmo ($S$ em dB) conforme a probabilidade de erro de porta ($p$) cresce, sob os 5 canais de ruído avaliados. A linha horizontal tracejada preta marca o limiar de sucesso crítico de **$S = 3.0\text{ dB}$**. O ponto exato onde a curva de um algoritmo intercepta essa linha indica a sua taxa limite de ruído tolerada; curvas que se mantêm acima e à direita por mais tempo representam algoritmos mais robustos. Fica nítido o rápido colapso dos algoritmos tradicionais (`SGA` e `SGAA`) em 8 qubits, enquanto a família de dois estágios (`M2GA`/`M2GAA`) mantém seletividade excelente até em taxas de ruído severas.

* **Perfil IBM Fez (`ibm_fez_2026`):**
  ![Curvas de Seletividade - IBM Fez](figures/selectivity_curves_ibm_fez_2026.png)
* **Perfil IBM Kingston (`ibm_kingston_2026`):**
  ![Curvas de Seletividade - IBM Kingston](figures/selectivity_curves_ibm_kingston_2026.png)

---

#### 2. Limiares de Erro de Porta por Algoritmo (4 Qubits)
Esta figura mapeia a taxa máxima tolerável de ruído de porta (em %) antes que o algoritmo falhe (ou seja, quando $S$ desce abaixo de $3.0\text{ dB}$). Cada bloco de barras coloridas representa um tipo de ruído, de modo que barras mais altas indicam algoritmos mais robustos. Os rótulos de texto no topo indicam o valor percentual exato. Os dados evidenciam a robustez monumental dos algoritmos locais de dois estágios: a família `M2GA`/`M2GAA` atinge limiares reais de **$12.4\% - 13.0\%$** em Amplitude Damping (AD) e de **$49.9\% - 50.4\%$** em Phase Damping (PD) (indicados com asterisco e limitados visualmente a $15.0\%$ no gráfico para preservar a escala visual do restante do gráfico).

* **Perfil IBM Fez (`ibm_fez_2026`):**
  ![Limiares de Erro - IBM Fez](figures/gate_thresholds_ibm_fez_2026.png)
* **Perfil IBM Kingston (`ibm_kingston_2026`):**
  ![Limiares de Erro - IBM Kingston](figures/gate_thresholds_ibm_kingston_2026.png)

---

#### 3. Escalonamento do Limiar Térmico de Coerência vs. Qubits (4 a 10 Qubits)
Esta figura indica o tempo mínimo de coerência térmica ($T_1$/$T_2$ em $\mu s$, escala logarítmica) que o hardware físico deve possuir para garantir a execução bem-sucedida do Grover à medida que o circuito escala em tamanho. O eixo horizontal indica o número de qubits, e o eixo vertical é o tempo de coerência. Curvas localizadas mais abaixo no gráfico indicam algoritmos mais viáveis, pois demandam menos coerência do hardware. A área cinza tracejada representa a janela física real oferecida pelas QPUs IBM Heron r2 ($250 - 300\ \mu s$). À medida que escalamos para 10 qubits, o Grover tradicional (`SGA`) exige tempos de coerência na escala de dezenas de milissegundos, ultrapassando os limites físicos do hardware moderno, enquanto as variantes locais (`M2GA`/`M2GAA`) demandam menos de $84\ \mu s$, operando de forma totalmente segura.

* **Perfil IBM Fez (`ibm_fez_2026`):**
  ![Escalonamento Térmico - IBM Fez](figures/thermal_scaling_ibm_fez_2026.png)
* **Perfil IBM Kingston (`ibm_kingston_2026`):**
  ![Escalonamento Térmico - IBM Kingston](figures/thermal_scaling_ibm_kingston_2026.png)

---

#### 4. Recursos de Circuito: Escalonamento de Portas CNOT (4 a 10 Qubits)
Esta figura apresenta o número total de portas CNOT (`cx`) executadas por cada algoritmo após a transpilação para a base nativa `["u", "cx"]`. A escala logarítmica evidencia como o Grover padrão (`SGA` e `SGAA`) sofre com uma explosão exponencial na quantidade de portas de dois qubits (passando de dezenas para dezenas de milhares de CNOTs), enquanto a família de dois estágios (`M2GA`/`M2GAA`) escala de forma extremamente otimizada e mantendo um número reduzido de CNOTs (apenas 288 portas para `M2GAA` em 10 qubits). Esse resultado justifica diretamente a menor exigência de coerência térmica e a maior tolerância a ruídos exibida pelos algoritmos locais.

* **Complexidade de Portas CNOT:**
  ![Escalonamento de CNOTs](figures/cnot_complexity.png)

---

## Formato dos dados (`data/`)

Para cada perfil (`ibm_fez_2026`, `ibm_kingston_2026`) há arquivos de duas famílias:

- `grover_thresholds_<perfil>.{jsonl,json,append.csv}` — limiares de erro de porta.
- `grover_thermal_thresholds_<perfil>.{jsonl,json}` — limiares térmicos (`T1`/`T2`).

Os `.jsonl` são append-only (um registro por configuração, robustos a interrupções em
execuções longas) e os `.json` são as versões consolidadas e ordenadas geradas pelo
pós-processamento.

---

## Estrutura do repositório

```
.
├── src/                     # Código-fonte
│   ├── grover_paper_repro.py    # Simulação principal (Qiskit Aer) — limiares de ruído e térmicos
│   ├── grover_paper_hardware.py # Execução em hardware real da IBM Quantum
│   ├── grover_origin.py         # Análise de limiar original do projeto
│   ├── run_experimentos.py      # Orquestrador multi-QPU (Fez + Kingston, com checkpoints)
│   ├── merge_results.py         # Pós-processamento e consolidação de resultados
│   └── plot_results.py          # Geração das figuras a partir dos dados consolidados
├── data/                    # Resultados (JSON / JSONL / CSV) por perfil de hardware
├── figures/                 # Figuras e gráficos gerados (PNG)
├── notebooks/               # Notebooks exploratórios
├── requirements.txt
└── README.md
```

Os scripts resolvem automaticamente os caminhos de leitura/escrita para `data/`,
independentemente do diretório de onde forem executados.

---

## Instalação

Requer Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Uso

### Simulação (Qiskit Aer)

Executar a varredura completa de limiares para um perfil de hardware:

```bash
# Perfil IBM Fez, 4 e 6 qubits
python src/grover_paper_repro.py --profile ibm_fez_2026 --qubits 4,6

# Apenas limiares de erro de porta
python src/grover_paper_repro.py --profile ibm_kingston_2026 --qubits 8 --only-error

# Apenas limiares térmicos (coerência T1/T2)
python src/grover_paper_repro.py --profile ibm_fez_2026 --qubits 10 --only-thermal
```

Perfis disponíveis: `ibmq_cambridge_2020`, `ibm_fez_2026`, `ibm_kingston_2026`.

### Orquestrador multi-QPU

Roda Fez e Kingston em sequência, com consolidação e checkpoints (pula configurações
já calculadas):

```bash
python src/run_experimentos.py --qubits 4,6,8,10
```

### Execução em hardware real (opcional)

O script `grover_paper_hardware.py` submete e executa os circuitos de Grover em uma QPU real da IBM Quantum (como `ibm_fez` ou `ibm_kingston`). 

As credenciais do IBM Quantum são resolvidas automaticamente através de 3 métodos (em ordem de prioridade), garantindo privacidade e flexibilidade:

1. **Variável de Ambiente:**
   ```bash
   export IBM_QUANTUM_TOKEN="seu_token_aqui"
   python src/grover_paper_hardware.py --backend ibm_fez
   ```
2. **Arquivo de Token (Padrão: `~/.config/ibm_quantum/token`):**
   Caso a variável de ambiente não esteja configurada, o script tentará ler o token deste arquivo (você pode alterar o caminho usando a flag `--token-file`):
   ```bash
   python src/grover_paper_hardware.py --backend ibm_kingston
   ```
3. **Credenciais Locais do Qiskit:**
   Se nenhum token direto for fornecido, o script tentará carregar qualquer conta IBM Quantum previamente salva em sua máquina local via biblioteca do Qiskit (salva anteriormente por meio do comando `QiskitRuntimeService.save_account()`).

---

## Licença

Distribuído sob a licença MIT — veja [`LICENSE`](LICENSE). O artigo de referência é
material de terceiros com direitos autorais e **não** está incluído no repositório;
consulte-o pelo DOI acima.

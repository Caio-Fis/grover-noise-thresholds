# Lista de Tarefas: Varredura Quântica de Grover Incremental e Multi-QPU

- `[x]` Modificar `grover_paper_repro.py` para incluir o perfil `ibm_kingston_2026`, lógica de checkpoint (skip) e interface de linha de comando (`argparse`)
- `[x]` Criar o script de orquestração `run_experimentos.py` que realiza a consolidação prévia dos dados de 6 e 8 qubits do Fez e executa as chamadas
- `[x]` Executar e validar o teste piloto de 4 qubits do zero para Fez e Kingston
- `[x]` Executar o sweep completo de 6 qubits (portas no Fez; portas + térmico no Kingston; térmico pulado no Fez via checkpoints)
- `[x]` Mesclar, pós-processar e atualizar relatórios e walkthrough com os dados de 6 qubits
- `[x]` Executar o sweep completo de 8 qubits (portas no Fez; portas + térmico no Kingston; térmico do Fez pulado por checkpoint)
- `[x]` Executar o sweep completo de 10 qubits (portas + térmico no Fez e Kingston)
- `[x]` Mesclar, pós-processar, atualizar relatórios unificados e atualizar walkthrough com os dados consolidados de 8 e 10 qubits


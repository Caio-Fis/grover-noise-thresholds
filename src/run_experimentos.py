#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orchestrator for running Grover simulation benchmarks incrementally on IBM Fez and Kingston.
"""

import sys
import os
import json
import argparse
import subprocess

# Resolve all relative data paths to the repository's data/ directory,
# so the orchestrator can be launched from anywhere. The simulation script
# lives in this same src/ directory and is invoked by absolute path below.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(_SRC_DIR), "data")
os.makedirs(DATA_DIR, exist_ok=True)
os.chdir(DATA_DIR)


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    results = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    results.append(json.loads(line))
                except Exception:
                    pass
    return results

def save_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

def consolidate_fez_thermal_checkpoints():
    """
    Consolida as simulações térmicas anteriores do Fez de 6 e 8 qubits 
    (grover_thermal_thresholds_fez_2026_6_8.jsonl) dentro do arquivo oficial 
    (grover_thermal_thresholds_ibm_fez_2026.jsonl) sem duplicar.
    """
    print("[INFO] Checking and consolidating past Fez thermal checkpoints...")
    official_path = "grover_thermal_thresholds_ibm_fez_2026.jsonl"
    partial_path = "grover_thermal_thresholds_fez_2026_6_8.jsonl"
    
    if not os.path.exists(partial_path):
        print(f"[INFO] No partial file '{partial_path}' found. Nothing to consolidate.")
        return
        
    official_data = load_jsonl(official_path)
    partial_data = load_jsonl(partial_path)
    
    # Criar um mapeamento das chaves existentes
    existing_keys = set()
    consolidated = []
    
    for item in official_data:
        key = (item.get("n_qubits"), item.get("algorithm"))
        if key not in existing_keys:
            existing_keys.add(key)
            consolidated.append(item)
            
    added_count = 0
    for item in partial_data:
        key = (item.get("n_qubits"), item.get("algorithm"))
        if key not in existing_keys:
            existing_keys.add(key)
            consolidated.append(item)
            added_count += 1
            
    if added_count > 0:
        save_jsonl(official_path, consolidated)
        print(f"[SUCCESS] Appended {added_count} thermal checkpoint records from '{partial_path}' to '{official_path}'.")
    else:
        print("[INFO] All past thermal checkpoints are already present in the official file.")

def run_repro_script(profile, qubits, run_flags=None):
    """Invoca o script grover_paper_repro.py usando subprocess com saída unbuffered."""
    cmd = [sys.executable, "-u", os.path.join(_SRC_DIR, "grover_paper_repro.py"), "--profile", profile, "--qubits", qubits]
    if run_flags:
        cmd.extend(run_flags)
        
    print(f"\n======================================================================")
    print(f"RUNNING: {' '.join(cmd)}")
    print(f"======================================================================")
    
    # Executa o comando de forma síncrona transmitindo a saída
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in process.stdout:
        print(line, end="")
    process.wait()
    if process.returncode != 0:
        print(f"[ERROR] Execution failed for profile {profile} with exit code {process.returncode}")
        sys.exit(process.returncode)
    print(f"[SUCCESS] Completed execution for profile {profile}.")

def consolidate_to_json_reports():
    """Consolida os arquivos .jsonl dos perfis em arquivos .json ordenados."""
    print("\n[INFO] Post-processing and generating structured JSON reports...")
    
    algo_order = {"SGA": 0, "SGAA": 1, "M1GA": 2, "M1GAA": 3, "M2GA": 4, "M2GAA": 5}
    error_order = {"BF": 0, "PF": 1, "DEP": 2, "AD": 3, "PD": 4}
    
    profiles = ["ibm_fez_2026", "ibm_kingston_2026"]
    
    for p in profiles:
        # 1. Gate Thresholds
        gate_jsonl = f"grover_thresholds_{p}.jsonl"
        gate_json = f"grover_thresholds_{p}.json"
        if os.path.exists(gate_jsonl):
            records = load_jsonl(gate_jsonl)
            # Ordenar por (n_qubits, algorithm, error_type)
            sorted_records = sorted(
                records,
                key=lambda r: (
                    int(r.get("n_qubits", 0)),
                    algo_order.get(r.get("algorithm"), 99),
                    error_order.get(r.get("error_type"), 99)
                )
            )
            with open(gate_json, "w", encoding="utf-8") as f:
                json.dump(sorted_records, f, indent=2)
            print(f"[REPORT] Saved ordered JSON for Gate Errors: '{gate_json}'")
            
        # 2. Thermal Thresholds
        thermal_jsonl = f"grover_thermal_thresholds_{p}.jsonl"
        thermal_json = f"grover_thermal_thresholds_{p}.json"
        if os.path.exists(thermal_jsonl):
            records = load_jsonl(thermal_jsonl)
            # Ordenar por (n_qubits, algorithm)
            sorted_records = sorted(
                records,
                key=lambda r: (
                    int(r.get("n_qubits", 0)),
                    algo_order.get(r.get("algorithm"), 99)
                )
            )
            with open(thermal_json, "w", encoding="utf-8") as f:
                json.dump(sorted_records, f, indent=2)
            print(f"[REPORT] Saved ordered JSON for Thermal: '{thermal_json}'")

def main():
    parser = argparse.ArgumentParser(description="Multi-QPU Grover benchmark orchestrator")
    parser.add_argument("--qubits", type=str, default="4", 
                        help="Comma-separated list of qubits to run (default: 4 for quick pilot)")
    parser.add_argument("--only-error", action="store_true", help="Run only gate error simulations")
    parser.add_argument("--only-thermal", action="store_true", help="Run only thermal simulations")
    parser.add_argument("--no-skip", action="store_true", help="Disable skip checkpoints")
    
    args = parser.parse_args()
    
    # 1. Consolidação do fez
    consolidate_fez_thermal_checkpoints()
    
    # Configurar flags de execução baseadas nas opções
    run_flags = []
    if args.only_error:
        run_flags.append("--only-error")
    elif args.only_thermal:
        run_flags.append("--only-thermal")
    if args.no_skip:
        run_flags.append("--no-skip")
        
    # 2. Executar para o Fez
    print(f"\n[INFO] Starting orchestrator queue for IBM Fez...")
    run_repro_script("ibm_fez_2026", args.qubits, run_flags)
    
    # 3. Executar para o Kingston
    print(f"\n[INFO] Starting orchestrator queue for IBM Kingston...")
    run_repro_script("ibm_kingston_2026", args.qubits, run_flags)
    
    # 4. Consolidação pós-execução
    consolidate_to_json_reports()
    
    print("\n======================================================================")
    print("[SUCCESS] All orchestrator jobs completed successfully!")
    print("======================================================================")

if __name__ == "__main__":
    main()

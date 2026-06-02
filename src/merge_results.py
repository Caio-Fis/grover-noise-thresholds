import json
import os

# Resolve all relative data paths to the repository's data/ directory.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(_SRC_DIR), "data")


def load_json(path):
    if not os.path.exists(path):
        print(f"File {path} does not exist. Returning empty list.")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error reading {path}: {e}")
        return []

def load_jsonl(path):
    if not os.path.exists(path):
        print(f"File {path} does not exist. Returning empty list.")
        return []
    results = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))
        return results
    except Exception as e:
        print(f"Error reading {path}: {e}")
        return []

def merge_thresholds(json_path, jsonl_path, output_path):
    print(f"Merging thresholds from {json_path} and {jsonl_path}...")
    json_data = load_json(json_path)
    jsonl_data = load_jsonl(jsonl_path)
    
    merged = {}
    
    # Process json first
    for item in json_data:
        key = (item.get("n_qubits"), item.get("algorithm"), item.get("error_type"))
        merged[key] = item
        
    # Process jsonl, overriding or adding
    for item in jsonl_data:
        key = (item.get("n_qubits"), item.get("algorithm"), item.get("error_type"))
        if key in merged:
            # If both exist, we can merge/override. Let's prefer the one with non-null threshold_prob, or the latest (from jsonl)
            existing = merged[key]
            if item.get("threshold_prob") is not None or existing.get("threshold_prob") is None:
                merged[key] = item
        else:
            merged[key] = item
            
    # Sort the results by (n_qubits, algorithm, error_type)
    # Define custom sort order for algorithms to keep standard first
    algo_order = {"SGA": 0, "SGAA": 1, "M1GA": 2, "M1GAA": 3, "M2GA": 4, "M2GAA": 5}
    error_order = {"BF": 0, "PF": 1, "DEP": 2, "AD": 3, "PD": 4}
    
    sorted_keys = sorted(
        merged.keys(),
        key=lambda k: (
            k[0],
            algo_order.get(k[1], 99),
            error_order.get(k[2], 99)
        )
    )
    
    sorted_data = [merged[k] for k in sorted_keys]
    
    print(f"Merged {len(json_data)} from JSON and {len(jsonl_data)} from JSONL into {len(sorted_data)} unique records.")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, indent=2)
    print(f"Saved merged thresholds to {output_path}")

def merge_thermal_thresholds(json_path, jsonl_path, output_path):
    print(f"Merging thermal thresholds from {json_path} and {jsonl_path}...")
    json_data = load_json(json_path)
    jsonl_data = load_jsonl(jsonl_path)
    
    merged = {}
    
    # Process json first
    for item in json_data:
        key = (item.get("n_qubits"), item.get("algorithm"))
        merged[key] = item
        
    # Process jsonl, overriding or adding
    for item in jsonl_data:
        key = (item.get("n_qubits"), item.get("algorithm"))
        if key in merged:
            existing = merged[key]
            # Prefer the one with actual data (t1_us_avg not None)
            if item.get("t1_us_avg") is not None or existing.get("t1_us_avg") is None:
                merged[key] = item
        else:
            merged[key] = item
            
    # Sort the results by (n_qubits, algorithm)
    algo_order = {"SGA": 0, "SGAA": 1, "M1GA": 2, "M1GAA": 3, "M2GA": 4, "M2GAA": 5}
    
    sorted_keys = sorted(
        merged.keys(),
        key=lambda k: (
            k[0],
            algo_order.get(k[1], 99)
        )
    )
    
    sorted_data = [merged[k] for k in sorted_keys]
    
    print(f"Merged {len(json_data)} from JSON and {len(jsonl_data)} from JSONL into {len(sorted_data)} unique records.")
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, indent=2)
    print(f"Saved merged thermal thresholds to {output_path}")

if __name__ == "__main__":
    workspace_dir = DATA_DIR
    
    # Merge standard thresholds
    t_json = os.path.join(workspace_dir, "grover_thresholds.json")
    t_jsonl = os.path.join(workspace_dir, "grover_thresholds.jsonl")
    merge_thresholds(t_json, t_jsonl, t_json)
    
    # Merge thermal thresholds
    th_json = os.path.join(workspace_dir, "grover_thermal_thresholds.json")
    th_jsonl = os.path.join(workspace_dir, "grover_thermal_thresholds.jsonl")
    merge_thermal_thresholds(th_json, th_jsonl, th_json)

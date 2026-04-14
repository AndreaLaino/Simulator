from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from common.cycle_pipeline import run_cycle_pipeline
from common.io_bootstrap import resolve_input_tsv
from common.params import ask_for_exact_k, choose_config


BASE_DIR = Path(__file__).resolve().parent
APPLIANCE_KEY = "computer"
INPUT_PATH = BASE_DIR / "computer_data.tsv"
OUTPUT_DIR = BASE_DIR / "computer_case_output"
PKL_PATH = OUTPUT_DIR / "cycles_raw_data.pkl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Computer clustering")
    parser.add_argument("--config", type=str, default=None, help="Percorso file JSON parametri custom")
    parser.add_argument("--exact-k", type=int, default=None, help="Numero cluster esatto richiesto")
    return parser.parse_args()


def choose_k_mode(args: argparse.Namespace, params: dict) -> int | None:
    if args.exact_k is not None:
        return int(args.exact_k)

    print("\nModalita clustering:")
    print("1) Automatico (k suggeriti)")
    print("2) K fisso esatto (scelto dall'utente)")

    while True:
        choice = input("Inserisci 1 o 2: ").strip()
        if choice == "1":
            return None
        if choice == "2":
            default_k = int(params.get("k", {}).get("human_k_min", 20))
            return ask_for_exact_k(default_k=default_k)
        print("Valore non valido. Inserisci solo 1 oppure 2.")


def main() -> None:
    args = parse_args()
    params = choose_config(APPLIANCE_KEY, custom_path_arg=args.config)
    exact_k = choose_k_mode(args, params)
    input_path = resolve_input_tsv(BASE_DIR, APPLIANCE_KEY, OUTPUT_DIR)

    run_cycle_pipeline(
        input_path=input_path,
        output_dir=OUTPUT_DIR,
        pkl_path=PKL_PATH,
        chart_title_prefix="computer",
        params=params,
        exact_k=exact_k,
    )


if __name__ == "__main__":
    main()

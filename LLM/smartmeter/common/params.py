from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_parameters.json"


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_update(out[key], value)
        else:
            out[key] = value
    return out


def load_default_config() -> dict[str, Any]:
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_appliance_config(appliance_key: str, custom_path: str | None = None) -> dict[str, Any]:
    full_default = load_default_config()
    default_cfg = full_default.get(appliance_key)
    if default_cfg is None:
        raise KeyError(f"Nessuna configurazione di default trovata per '{appliance_key}'.")

    if not custom_path:
        return deepcopy(default_cfg)

    custom_file = Path(custom_path)
    if not custom_file.exists():
        raise FileNotFoundError(f"File parametri custom non trovato: {custom_file}")

    with open(custom_file, "r", encoding="utf-8") as f:
        custom_cfg = json.load(f)

    # Consente sia file completo con chiave appliance, sia solo blocco appliance.
    appliance_custom = custom_cfg.get(appliance_key, custom_cfg)
    if not isinstance(appliance_custom, dict):
        raise ValueError("Il contenuto del file custom deve essere un oggetto JSON.")

    return _deep_update(default_cfg, appliance_custom)


def choose_config(appliance_key: str, custom_path_arg: str | None = None) -> dict[str, Any]:
    if custom_path_arg:
        return load_appliance_config(appliance_key, custom_path_arg)

    print("\nSeleziona configurazione parametri:")
    print("1) Parametri predefiniti")
    print("2) Carica parametri da file JSON")

    while True:
        choice = input("Inserisci 1 o 2: ").strip()
        if choice == "1":
            return load_appliance_config(appliance_key)
        if choice == "2":
            user_path = input("Percorso file JSON personalizzato: ").strip().strip('"')
            return load_appliance_config(appliance_key, user_path)
        print("Valore non valido. Inserisci solo 1 oppure 2.")


def ask_for_exact_k(default_k: int | None = None) -> int:
    prompt = "Numero cluster desiderato (k)"
    if default_k is not None:
        prompt += f" [default {default_k}]"
    prompt += ": "

    while True:
        raw = input(prompt).strip()
        if not raw and default_k is not None:
            return int(default_k)
        try:
            k = int(raw)
        except ValueError:
            print("Inserisci un intero valido.")
            continue
        if k < 2:
            print("k deve essere >= 2.")
            continue
        return k

# Simulator

Progetto di simulazione domestica con interfaccia Tkinter, sensori virtuali, logging eventi, grafici e supporto a profili LLM per Smart Meter.

## Obiettivi del progetto

- Costruire e modificare scenari domestici (punti, muri, porte, sensori, dispositivi).
- Simulare interazioni manuali e modalità automatica.
- Registrare eventi sensori/dispositivi e attività utente.
- Visualizzare log e grafici delle serie temporali.
- Stimare e riusare cicli di consumo con pipeline LLM Smart Meter.

## Requisiti

- Python 3.10 o superiore
- Ambiente desktop con Tkinter disponibile
- Dipendenze Python dichiarate in pyproject.toml

Dipendenze principali:
- numpy
- pandas
- scipy
- scikit-learn
- dtaidistance
- matplotlib
- Pillow
- boto3

## Installazione

1. Crea e attiva un ambiente virtuale.
2. Installa il progetto (editable consigliato):

    pip install -e .

In alternativa:

    pip install .

## Avvio applicazione

Dalla cartella del progetto:

    python main.py

L'app apre la UI principale con una griglia di sfondo e i menu File, Scenario e Simulation.

## Flusso rapido

1. Carica uno scenario esistente:
   - File > Open file
   - oppure File > Load default (saved.csv)
2. Avvia simulazione manuale:
   - Simulation > Manual
3. Visualizza output:
   - Simulation > Generate log
   - Simulation > Activity Log
   - Simulation > Generate graphs

## Come caricare una planimetria/scenario

Il progetto usa file CSV scenario (esempio: houses/prova1.csv) con sezioni testuali in questo ordine logico:

- Positions
- Walls
- Sensors
- Devices
- Doors

Ogni sezione contiene righe compatibili con il parser in read.py.

### Esempio pratico

- Apri houses/prova1.csv dal menu File > Open file.
- Lo scenario viene disegnato sulla canvas: punti, muri, sensori, dispositivi e porte.

### Costruzione scenario da zero

Dal menu Scenario:

- Add points
- Add walls
- Add doors
- Add sensors
- Add devices

Poi salva con File > Save oppure File > Save As.

## Modalità di simulazione

### Manual

- Menu: Simulation > Manual
- Le interazioni su canvas generano eventi sensori/dispositivi.
- Timer e monitor attività sono aggiornati in tempo simulato.

### Automatic

- Menu: Simulation > Automatic
- Include due tab:
  - Folder mode: grafici da file timestamp/state
  - User path mode: grafici/export da log interactions.csv

## Modulo LLM Smart Meter

Accesso da menu:

- Simulation > LLM Smart Meter

Funzionalità principali:

- Selezione appliance (Computer, Coffee Machine, Dishwasher, Refrigerator, Washing Machine)
- Selezione CSV sorgente
- Scelta parametri:
  - default
  - custom JSON
- Scelta k:
  - Best k (vote)
  - Human k
  - Custom k

Output principali:

- Catalogo profili runtime: LLM/smartmeter/llm_smartmeter_profiles.json
- Cartelle per appliance in LLM/smartmeter/<Appliance>/...
- Archivio run in saves/.../devices_k/...

Nota: i path nel catalogo profili sono gestiti in modo portabile (relativi alla cartella LLM/smartmeter quando possibile).

## Logging e output

Cartelle tipiche:

- logs per log runtime
- saves per salvataggi sessione
- devices per CSV acquisizioni dispositivi

Le esportazioni sono disponibili dal menu Simulation (Import/Export CSV, log e grafici).

## Struttura minima del progetto

- main.py: entrypoint UI
- app/: contesto applicativo, controller e UI
- sim.py: loop e aggiornamento sensori
- activity.py: detection attività
- sensor.py: logiche sensori e Smart Meter
- graph.py: grafici
- read.py: parsing scenari CSV
- LLM/smartmeter/: pipeline e artefatti LLM

## Troubleshooting

- Errore import Tkinter su Linux:
  - installa il pacchetto sistema python3-tk.
- Problemi con dipendenze scientifiche:
  - aggiorna pip/setuptools/wheel e reinstalla.
- Errori su AWS import:
  - verifica credenziali e connettività per boto3.

## Note per ricerca e riproducibilità

Per esperimenti riproducibili:

- salva il commit usato
- conserva input CSV e scenario CSV
- conserva output LLM in saves/devices_k
- documenta opzioni k e file parametri custom

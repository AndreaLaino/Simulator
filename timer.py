import tkinter as tk
from datetime import datetime, timedelta


class TimerApp:
    def __init__(self, parent, start_callback=None, stop_callback=None):
        self.start_callback = start_callback
        self.stop_callback = stop_callback

        self.on_advance_step = None

        self.timer_frame = tk.Frame(parent, width=400, height=500, bg="lightgrey", bd=2, relief="sunken")
        self.timer_frame.pack(side="right", fill="both", padx=15, pady=15, expand=True)

        self.is_running = False
        self.start_time = None
        self.elapsed_time = timedelta()
        self.simulated_start_time = None
        self.advanced = False
        self.current_date = datetime.today().strftime("%Y-%m-%d")

        self.timer_frame.columnconfigure(0, weight=1)

        self.label = tk.Label(
            self.timer_frame,
            text=f"Time: 00:00 \n Date: {self.current_date}",
            font=("Helvetica", 16),
            bg="lightgrey",
        )
        self.label.grid(row=0, column=0, pady=(20, 10), padx=10, sticky="ew")

        # Input for start time
        self.start_hour_label = tk.Label(
            self.timer_frame,
            text="Start Hour (HH:MM):",
            font=("Helvetica", 10),
            bg="lightgrey",
        )
        self.start_hour_label.grid(row=1, column=0, pady=5, padx=15, sticky="w")

        self.start_hour_entry = tk.Entry(self.timer_frame, font=("Helvetica", 12), width=10)
        self.start_hour_entry.grid(row=2, column=0, pady=5, padx=15)

        # Insert current time formatted properly
        self.start_hour_entry.insert(0, datetime.now().strftime("%H:%M"))

        # Start/Stop Button
        self.start_stop_button = tk.Button(self.timer_frame, text="Start", font=("Helvetica", 12), command=self.start_stop)
        self.start_stop_button.grid(row=3, column=0, pady=5, padx=15, sticky="ew")

        # Advance 15 sec Button
        self.advance_button = tk.Button(self.timer_frame, text="Advance 15 sec", font=("Helvetica", 12), command=self.advance_time)
        self.advance_button.grid(row=5, column=0, pady=5, padx=15, sticky="ew")

        # Reset Button
        self.reset_button = tk.Button(self.timer_frame, text="Reset", font=("Helvetica", 12), command=self.reset)
        self.reset_button.grid(row=6, column=0, pady=5, padx=15, sticky="ew")

        self.update_timer()

    def start_stop(self):
        if not self.is_running:
            if self.start_time is None:
                start_hour_str = self.start_hour_entry.get()
                try:
                    today = datetime.today()
                    simulated_start_time = datetime.strptime(start_hour_str, "%H:%M").time()
                    self.simulated_start_time = datetime.combine(today, simulated_start_time)

                    self.start_time = datetime.now()
                    self.elapsed_time = timedelta()
                    self.is_running = True
                    self.start_stop_button.config(text="Stop")
                    if self.start_callback:
                        self.start_callback()
                except ValueError:
                    print("Invalid time format. Use HH:MM.")
            else:
                self.start_time = datetime.now() - self.elapsed_time
                self.is_running = True
                self.start_stop_button.config(text="Stop")
                if self.start_callback:
                    self.start_callback()
        else:
            self.elapsed_time = datetime.now() - self.start_time
            self.is_running = False
            self.start_stop_button.config(text="Start")
            if self.stop_callback:
                self.stop_callback()

    def advance_time(self):
        """Advance the simulated time by 15 simulated minutes."""
        for _ in range(15): 
            if self.is_running:
                self.start_time -= timedelta(seconds=1)
                self.elapsed_time = datetime.now() - self.start_time  
            else:
                self.elapsed_time += timedelta(seconds=1)

            if callable(self.on_advance_step):
                self.on_advance_step(1)

        self.advanced = True
        simulated_time = self.get_simulated_time()
        self.label.config(text=f"Time: {simulated_time} \n Date: {self.current_date}")
        self.timer_frame.after(1000, self.reset_flag)

    def reset_flag(self):
        self.advanced = False

    def get_simulated_time(self):
        if self.simulated_start_time is None:
            return "00:00"

        total_seconds = self.elapsed_time.total_seconds()
        simulated_minutes = int(total_seconds)  # 1 sec reale = 1 min simulato
        simulated_time = self.simulated_start_time + timedelta(minutes=simulated_minutes)

        if simulated_time.date() != datetime.strptime(self.current_date, "%Y-%m-%d").date():
            self.current_date = simulated_time.date().strftime("%Y-%m-%d")

        return simulated_time.strftime("%H:%M")

    def reset(self):
        self.is_running = False
        self.start_time = None
        self.elapsed_time = timedelta()
        self.simulated_start_time = None
        self.current_date = datetime.today().strftime("%Y-%m-%d")

        self.start_hour_entry.delete(0, tk.END)
        self.start_hour_entry.insert(0, "00:00")

        self.start_stop_button.config(text="Start")
        self.label.config(text=f"Time: 00:00 \n Date: {self.current_date}")

    def update_timer(self):
        if self.is_running:
            self.elapsed_time = datetime.now() - self.start_time
            simulated_time = self.get_simulated_time()
            self.label.config(text=f"Time: {simulated_time} \n Date: {self.current_date}")

        self.timer_frame.after(100, self.update_timer)

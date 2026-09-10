"""
Drivers Manager Project - Graphical User Interface
====================================================
A tkinter-based desktop GUI with 5 tabs that walks the user through:
    1. Adding participants (name, location, optional car)
    2. Setting the destination
    3. Running the heuristic driver-selection scoring
    4. Running the APCA route optimisation
    5. Viewing final results
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
import threading
from typing import List, Optional

from core.config import (
    APP_TITLE, APP_WIDTH, APP_HEIGHT,
    FUEL_TYPES, FUEL_TYPE_LABELS,
    APCA_NUM_ANTS, APCA_NUM_ITERATIONS,
    APCA_ALPHA, APCA_BETA, APCA_RHO,
)
from core.models import Location, Car, Participant, DriverSet, TripResult
from core.driver_selection import DriverSelector
from core.apca import APCAAlgorithm
from core.utils import get_sample_data


class DriversManagerApp:
    """Main application window."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(f"{APP_WIDTH}x{APP_HEIGHT}")
        self.root.minsize(900, 600)

        # ---- State ----------------------------------------------------------
        self.participants: List[Participant] = []
        self.destination: Optional[Location] = None
        self.driver_sets: List[DriverSet] = []
        self.trip_results: List[TripResult] = []

        # ---- Style ----------------------------------------------------------
        style = ttk.Style()
        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Sub.TLabel", font=("Segoe UI", 10))
        style.configure("Big.TButton", font=("Segoe UI", 11))
        style.configure("Good.TLabel", foreground="green",
                        font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=26)

        # ---- Notebook (tabs) ------------------------------------------------
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_tab_participants()
        self._build_tab_destination()
        self._build_tab_selection()
        self._build_tab_optimisation()
        self._build_tab_results()

    # ==========================================================================
    # TAB 1 — Participants
    # ==========================================================================

    def _build_tab_participants(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  1. Participants  ")

        # -- Header -----------------------------------------------------------
        hdr = ttk.Frame(tab)
        hdr.pack(fill="x")
        ttk.Label(hdr, text="Add Participants",
                  style="Header.TLabel").pack(side="left")
        ttk.Button(hdr, text="🗂  Load Sample Data",
                   command=self._load_sample).pack(side="right", padx=4)

        ttk.Separator(tab, orient="horizontal").pack(fill="x", pady=6)

        # -- Form -------------------------------------------------------------
        form = ttk.LabelFrame(tab, text="New participant", padding=8)
        form.pack(fill="x")

        r = 0
        ttk.Label(form, text="Name:").grid(row=r, column=0, sticky="e", padx=4)
        self.e_name = ttk.Entry(form, width=18)
        self.e_name.grid(row=r, column=1, padx=4)

        ttk.Label(form, text="Latitude:").grid(row=r, column=2, sticky="e", padx=4)
        self.e_lat = ttk.Entry(form, width=12)
        self.e_lat.grid(row=r, column=3, padx=4)

        ttk.Label(form, text="Longitude:").grid(row=r, column=4, sticky="e", padx=4)
        self.e_lon = ttk.Entry(form, width=12)
        self.e_lon.grid(row=r, column=5, padx=4)

        ttk.Label(form, text="Location name:").grid(row=r, column=6, sticky="e", padx=4)
        self.e_locname = ttk.Entry(form, width=16)
        self.e_locname.grid(row=r, column=7, padx=4)

        r = 1
        self.var_has_car = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            form, text="Has car", variable=self.var_has_car,
            command=self._toggle_car_fields,
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=6)

        ttk.Label(form, text="Fuel type:").grid(row=r, column=2, sticky="e", padx=4)
        self.cb_fuel = ttk.Combobox(
            form, values=FUEL_TYPES, state="disabled", width=12
        )
        self.cb_fuel.grid(row=r, column=3, padx=4)
        self.cb_fuel.set("gasoline")

        ttk.Label(form, text="L/100 km:").grid(row=r, column=4, sticky="e", padx=4)
        self.e_cons = ttk.Entry(form, width=8, state="disabled")
        self.e_cons.grid(row=r, column=5, padx=4)

        ttk.Label(form, text="Total seats:").grid(row=r, column=6, sticky="e", padx=4)
        self.e_seats = ttk.Entry(form, width=6, state="disabled")
        self.e_seats.grid(row=r, column=7, padx=4)

        r = 2
        ttk.Label(form, text="Habit score:").grid(row=r, column=0, sticky="e", padx=4)
        self.e_habit = ttk.Entry(form, width=8)
        self.e_habit.insert(0, "0")
        self.e_habit.grid(row=r, column=1, padx=4)

        btn_frame = ttk.Frame(form)
        btn_frame.grid(row=r, column=3, columnspan=5, sticky="e")
        ttk.Button(btn_frame, text="➕ Add participant",
                   command=self._add_participant).pack(side="right", padx=4)

        # -- List -------------------------------------------------------------
        list_frame = ttk.Frame(tab)
        list_frame.pack(fill="both", expand=True, pady=8)

        cols = ("idx", "name", "location", "role", "car", "seats", "habit")
        self.tree_part = ttk.Treeview(
            list_frame, columns=cols, show="headings", height=12
        )
        for c, w, anch in [
            ("idx", 40, "center"), ("name", 110, "w"),
            ("location", 180, "w"), ("role", 80, "center"),
            ("car", 100, "center"), ("seats", 60, "center"),
            ("habit", 60, "center"),
        ]:
            self.tree_part.heading(c, text=c.title())
            self.tree_part.column(c, width=w, anchor=anch)
        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self.tree_part.yview)
        self.tree_part.configure(yscrollcommand=sb.set)
        self.tree_part.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        bot = ttk.Frame(tab)
        bot.pack(fill="x")
        ttk.Button(bot, text="🗑  Remove selected",
                   command=self._remove_participant).pack(side="left")
        self.lbl_count = ttk.Label(bot, text="0 participants")
        self.lbl_count.pack(side="right")

    def _toggle_car_fields(self):
        state = "normal" if self.var_has_car.get() else "disabled"
        for w in (self.cb_fuel, self.e_cons, self.e_seats):
            w.configure(state=state)

    def _add_participant(self):
        name = self.e_name.get().strip()
        if not name:
            messagebox.showwarning("Missing data", "Please enter a name.")
            return
        try:
            lat = float(self.e_lat.get())
            lon = float(self.e_lon.get())
        except ValueError:
            messagebox.showwarning("Missing data",
                                   "Please enter valid latitude and longitude.")
            return
        loc_name = self.e_locname.get().strip() or name

        car = None
        if self.var_has_car.get():
            fuel = self.cb_fuel.get()
            try:
                cons = float(self.e_cons.get()) if fuel != "electric" else 0.0
                seats = int(self.e_seats.get())
            except ValueError:
                messagebox.showwarning("Missing data",
                                       "Please fill in all car fields.")
                return
            if seats < 2:
                messagebox.showwarning("Invalid",
                                       "Total seats must be at least 2.")
                return
            car = Car(fuel_type=fuel, consumption_l_per_100km=cons,
                      total_seats=seats)

        try:
            habit = float(self.e_habit.get())
        except ValueError:
            habit = 0.0

        p = Participant(name=name, location=Location(loc_name, lat, lon),
                        car=car, habit_score=habit)
        self.participants.append(p)
        self._refresh_participant_list()

        # Clear inputs
        for w in (self.e_name, self.e_lat, self.e_lon, self.e_locname,
                  self.e_cons, self.e_seats, self.e_habit):
            w.delete(0, "end")
        self.e_habit.insert(0, "0")
        self.var_has_car.set(False)
        self._toggle_car_fields()

    def _remove_participant(self):
        sel = self.tree_part.selection()
        if not sel:
            return
        indices = sorted(
            [int(self.tree_part.item(s, "values")[0]) for s in sel],
            reverse=True,
        )
        for i in indices:
            if 0 <= i < len(self.participants):
                self.participants.pop(i)
        self._refresh_participant_list()

    def _refresh_participant_list(self):
        self.tree_part.delete(*self.tree_part.get_children())
        for i, p in enumerate(self.participants):
            role = "🚗 Driver" if p.can_drive else "👤 Passenger"
            car_str = p.car.fuel_type.title() if p.car else "—"
            seats = str(p.car.available_seats) if p.car else "—"
            self.tree_part.insert("", "end", values=(
                i, p.name, p.location.name, role, car_str, seats,
                f"{p.habit_score:+.1f}",
            ))
        n = len(self.participants)
        d = sum(1 for p in self.participants if p.can_drive)
        self.lbl_count.config(
            text=f"{n} participants  ({d} potential drivers, "
                 f"{n - d} passengers only)"
        )

    def _load_sample(self):
        if self.participants:
            if not messagebox.askyesno(
                "Replace?", "This will replace existing participants. Continue?"
            ):
                return
        sample_p, sample_d = get_sample_data()
        self.participants = sample_p
        self.destination = sample_d
        self._refresh_participant_list()
        # Also fill destination tab
        self.e_dest_name.delete(0, "end")
        self.e_dest_name.insert(0, sample_d.name)
        self.e_dest_lat.delete(0, "end")
        self.e_dest_lat.insert(0, str(sample_d.latitude))
        self.e_dest_lon.delete(0, "end")
        self.e_dest_lon.insert(0, str(sample_d.longitude))
        self.lbl_dest_status.config(text=f"✅ Destination set: {sample_d}")
        messagebox.showinfo("Sample loaded",
                            f"Loaded {len(sample_p)} participants and "
                            f"destination '{sample_d.name}'.")

    # ==========================================================================
    # TAB 2 — Destination
    # ==========================================================================

    def _build_tab_destination(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  2. Destination  ")

        ttk.Label(tab, text="Set Trip Destination",
                  style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            tab, text="All participants will be heading to this single destination.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        ttk.Separator(tab, orient="horizontal").pack(fill="x", pady=4)

        form = ttk.Frame(tab, padding=8)
        form.pack(fill="x")

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="e", padx=4)
        self.e_dest_name = ttk.Entry(form, width=25)
        self.e_dest_name.grid(row=0, column=1, padx=4)

        ttk.Label(form, text="Latitude:").grid(row=0, column=2, sticky="e", padx=4)
        self.e_dest_lat = ttk.Entry(form, width=14)
        self.e_dest_lat.grid(row=0, column=3, padx=4)

        ttk.Label(form, text="Longitude:").grid(row=0, column=4, sticky="e", padx=4)
        self.e_dest_lon = ttk.Entry(form, width=14)
        self.e_dest_lon.grid(row=0, column=5, padx=4)

        ttk.Button(form, text="✅ Set Destination",
                   command=self._set_destination, style="Big.TButton"
                   ).grid(row=1, column=0, columnspan=6, pady=12)

        self.lbl_dest_status = ttk.Label(tab, text="No destination set.",
                                         style="Sub.TLabel")
        self.lbl_dest_status.pack(anchor="w", pady=8)

    def _set_destination(self):
        name = self.e_dest_name.get().strip()
        try:
            lat = float(self.e_dest_lat.get())
            lon = float(self.e_dest_lon.get())
        except ValueError:
            messagebox.showwarning("Missing data",
                                   "Please enter destination coordinates.")
            return
        if not name:
            name = "Destination"
        self.destination = Location(name, lat, lon)
        self.lbl_dest_status.config(
            text=f"✅ Destination set: {self.destination}",
            style="Good.TLabel",
        )

    # ==========================================================================
    # TAB 3 — Driver Selection
    # ==========================================================================

    def _build_tab_selection(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  3. Driver Selection  ")

        ttk.Label(tab, text="Heuristic Driver Scoring & Selection",
                  style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            tab,
            text="Scores each potential driver on 5 criteria and selects "
                 "candidate driver sets.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 6))

        ttk.Button(tab, text="▶  Run Driver Selection",
                   command=self._run_selection, style="Big.TButton"
                   ).pack(pady=6)

        ttk.Separator(tab, orient="horizontal").pack(fill="x", pady=4)

        # Scores table
        ttk.Label(tab, text="Individual driver scores:",
                  style="Sub.TLabel").pack(anchor="w")

        cols_s = ("name", "SDP", "SEI", "SDD", "SAS", "SH", "Total")
        self.tree_scores = ttk.Treeview(
            tab, columns=cols_s, show="headings", height=6
        )
        for c, w in [("name", 120), ("SDP", 80), ("SEI", 80), ("SDD", 80),
                     ("SAS", 80), ("SH", 80), ("Total", 90)]:
            self.tree_scores.heading(c, text=c)
            self.tree_scores.column(c, width=w, anchor="center")
        self.tree_scores.pack(fill="x", pady=4)

        # Sets
        ttk.Label(tab, text="Selected driver sets:",
                  style="Sub.TLabel").pack(anchor="w", pady=(8, 2))
        self.txt_sets = tk.Text(tab, height=10, wrap="word", state="disabled",
                                font=("Consolas", 10))
        self.txt_sets.pack(fill="both", expand=True)

    def _run_selection(self):
        if len(self.participants) < 2:
            messagebox.showwarning("Not enough data",
                                   "Add at least 2 participants.")
            return
        if not any(p.can_drive for p in self.participants):
            messagebox.showwarning("No drivers",
                                   "At least one participant must have a car.")
            return
        if self.destination is None:
            messagebox.showwarning("No destination",
                                   "Please set a destination first (Tab 2).")
            return

        selector = DriverSelector(self.participants, self.destination)
        scores = selector.calculate_all_scores()
        self.driver_sets = selector.select_driver_sets()

        # Populate scores tree
        self.tree_scores.delete(*self.tree_scores.get_children())
        for s in scores:
            self.tree_scores.insert("", "end", values=(
                s.name,
                f"{s.score_distance_passengers:.3f}",
                f"{s.score_environmental:.3f}",
                f"{s.score_distance_destination:.3f}",
                f"{s.score_available_seats:.3f}",
                f"{s.score_habit:+.3f}",
                f"{s.total_score:.3f}",
            ))

        # Populate sets text
        self.txt_sets.config(state="normal")
        self.txt_sets.delete("1.0", "end")
        for ds in self.driver_sets:
            names = [self.participants[i].name for i in ds.driver_indices]
            self.txt_sets.insert("end",
                f"━━━  {ds.name}  ━━━\n"
                f"  Drivers:  {', '.join(names)}\n"
                f"  Total available seats: {ds.total_seats}\n"
                f"  Passengers to serve: "
                f"{len(self.participants) - len(ds.driver_indices)}\n\n"
            )
        if not self.driver_sets:
            self.txt_sets.insert("end", "No driver sets could be generated.\n")
        self.txt_sets.config(state="disabled")
        messagebox.showinfo("Done",
                            f"Selection complete — {len(self.driver_sets)} "
                            f"driver set(s) generated.")

    # ==========================================================================
    # TAB 4 — Route Optimisation (APCA)
    # ==========================================================================

    def _build_tab_optimisation(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  4. Route Optimisation  ")

        ttk.Label(tab, text="APCA Route Optimisation (Ant Colony)",
                  style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            tab,
            text="Runs the APCA algorithm on each driver set to find the "
                 "best passenger-to-driver assignment and pickup routes.",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 6))

        # Parameters
        pf = ttk.LabelFrame(tab, text="ACO Parameters", padding=8)
        pf.pack(fill="x", pady=4)

        params = [
            ("Ants:", APCA_NUM_ANTS), ("Iterations:", APCA_NUM_ITERATIONS),
            ("α (pheromone):", APCA_ALPHA), ("β (heuristic):", APCA_BETA),
            ("ρ (evaporation):", APCA_RHO),
        ]
        self.param_entries: dict = {}
        for col, (label, default) in enumerate(params):
            ttk.Label(pf, text=label).grid(row=0, column=col * 2, sticky="e",
                                           padx=2)
            e = ttk.Entry(pf, width=7)
            e.insert(0, str(default))
            e.grid(row=0, column=col * 2 + 1, padx=2)
            self.param_entries[label] = e

        # Run button + progress
        bf = ttk.Frame(tab)
        bf.pack(fill="x", pady=6)
        self.btn_run_apca = ttk.Button(
            bf, text="▶  Run APCA Optimisation",
            command=self._run_apca, style="Big.TButton",
        )
        self.btn_run_apca.pack(side="left")
        self.lbl_apca_status = ttk.Label(bf, text="")
        self.lbl_apca_status.pack(side="left", padx=12)

        self.progress = ttk.Progressbar(tab, mode="determinate")
        self.progress.pack(fill="x", pady=4)

        # Log
        ttk.Label(tab, text="Optimisation log:",
                  style="Sub.TLabel").pack(anchor="w", pady=(6, 2))
        self.txt_log = tk.Text(tab, height=14, wrap="word", state="disabled",
                               font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True)

    def _run_apca(self):
        if not self.driver_sets:
            messagebox.showwarning("No driver sets",
                                   "Run Driver Selection first (Tab 3).")
            return

        # Read parameters
        try:
            ants = int(self.param_entries["Ants:"].get())
            iters = int(self.param_entries["Iterations:"].get())
            alpha = float(self.param_entries["α (pheromone):"].get())
            beta = float(self.param_entries["β (heuristic):"].get())
            rho = float(self.param_entries["ρ (evaporation):"].get())
        except ValueError:
            messagebox.showwarning("Invalid", "Check parameter values.")
            return

        self.btn_run_apca.config(state="disabled")
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.config(state="disabled")
        self.progress["value"] = 0

        total_steps = iters * len(self.driver_sets)
        self.progress["maximum"] = total_steps

        def _worker():
            results: List[TripResult] = []
            step_offset = 0

            for ds in self.driver_sets:
                self._log(f"\n{'='*60}\n"
                          f"Running APCA on driver set: {ds.name}\n"
                          f"  Drivers: "
                          f"{[self.participants[i].name for i in ds.driver_indices]}\n"
                          f"{'='*60}\n")

                apca = APCAAlgorithm(
                    self.participants, ds.driver_indices, self.destination,
                    num_ants=ants, num_iterations=iters,
                    alpha=alpha, beta=beta, rho=rho,
                )

                base_offset = step_offset

                def _cb(it, total, best_f, _bo=base_offset):
                    self.root.after(0, self._update_progress,
                                   _bo + it, total_steps, it, total, best_f)

                result = apca.solve(progress_callback=_cb)
                result.driver_set_name = ds.name
                results.append(result)

                self._log(
                    f"  ✅ Best fitness: {result.fitness:.4f}\n"
                    f"     Total distance: {result.total_distance_km:.2f} km\n"
                    f"     Total cost: €{result.total_cost_eur:.2f}\n"
                )

                step_offset += iters

            self.trip_results = results
            self.root.after(0, self._apca_finished)

        threading.Thread(target=_worker, daemon=True).start()

    def _log(self, text: str):
        def _do():
            self.txt_log.config(state="normal")
            self.txt_log.insert("end", text)
            self.txt_log.see("end")
            self.txt_log.config(state="disabled")
        self.root.after(0, _do)

    def _update_progress(self, step, total, it, it_total, best_f):
        self.progress["value"] = step
        self.lbl_apca_status.config(
            text=f"Iteration {it}/{it_total}  |  Best fitness: {best_f:.3f}"
        )

    def _apca_finished(self):
        self.btn_run_apca.config(state="normal")
        self.progress["value"] = self.progress["maximum"]
        self.lbl_apca_status.config(text="✅ Optimisation complete!")
        self._populate_results()
        messagebox.showinfo("Done",
                            f"Optimisation finished — "
                            f"{len(self.trip_results)} set(s) evaluated.\n"
                            f"Check the Results tab.")
        self.notebook.select(4)  # switch to Results tab

    # ==========================================================================
    # TAB 5 — Results
    # ==========================================================================

    def _build_tab_results(self):
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  5. Results  ")

        ttk.Label(tab, text="Trip Results", style="Header.TLabel").pack(anchor="w")

        self.txt_results = tk.Text(
            tab, wrap="word", state="disabled",
            font=("Consolas", 10),
        )
        sb = ttk.Scrollbar(tab, orient="vertical",
                           command=self.txt_results.yview)
        self.txt_results.configure(yscrollcommand=sb.set)
        self.txt_results.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _populate_results(self):
        self.txt_results.config(state="normal")
        self.txt_results.delete("1.0", "end")

        if not self.trip_results:
            self.txt_results.insert("end", "No results yet.\n")
            self.txt_results.config(state="disabled")
            return

        # Find best overall
        best = max(self.trip_results, key=lambda r: r.fitness)

        self.txt_results.insert("end",
            "╔══════════════════════════════════════════════════════════╗\n"
            "║              DRIVERS MANAGER PROJECT — RESULTS          ║\n"
            "╚══════════════════════════════════════════════════════════╝\n\n"
        )

        for tr in self.trip_results:
            is_best = (tr is best)
            tag = "  ⭐ BEST SOLUTION" if is_best else ""
            self.txt_results.insert("end",
                f"{'━'*58}\n"
                f"  Driver Set: {tr.driver_set_name}{tag}\n"
                f"  Fitness score: {tr.fitness:.4f}\n"
                f"  Total distance: {tr.total_distance_km:.2f} km\n"
                f"  Total cost: €{tr.total_cost_eur:.2f}\n"
                f"{'━'*58}\n\n"
            )

            for ra in tr.assignments:
                drv = self.participants[ra.driver_index]
                self.txt_results.insert("end",
                    f"  🚗 Driver: {drv.name}  "
                    f"({drv.car.fuel_type.title()}, "
                    f"{drv.car.available_seats} seats)\n"
                )
                if ra.passenger_indices:
                    names = [self.participants[pi].name
                             for pi in ra.passenger_indices]
                    self.txt_results.insert("end",
                        f"     Passengers ({len(names)}): "
                        f"{', '.join(names)}\n"
                    )
                else:
                    self.txt_results.insert("end",
                        "     Passengers: (none assigned)\n"
                    )

                self.txt_results.insert("end", "     Route:\n")
                for step in ra.route_nodes:
                    self.txt_results.insert("end", f"       → {step}\n")

                self.txt_results.insert("end",
                    f"     Distance: {ra.route_distance_km:.2f} km\n"
                    f"     Fuel cost: €{ra.route_cost_eur:.2f}\n"
                    f"     Cost per person: €{ra.cost_per_person_eur:.2f}\n\n"
                )

            if tr.unassigned_passenger_indices:
                names = [self.participants[i].name
                         for i in tr.unassigned_passenger_indices]
                self.txt_results.insert("end",
                    f"  ⚠  Unassigned passengers: {', '.join(names)}\n\n"
                )

        # Summary
        self.txt_results.insert("end",
            f"\n{'═'*58}\n"
            f"  🏆 RECOMMENDED SOLUTION: {best.driver_set_name}\n"
            f"     Fitness: {best.fitness:.4f}\n"
            f"     Total distance: {best.total_distance_km:.2f} km\n"
            f"     Total cost: €{best.total_cost_eur:.2f}\n"
            f"{'═'*58}\n"
        )

        self.txt_results.config(state="disabled")

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import queue
import re
import os
import pandas as pd
import color_def
import concurrent.futures
# 需要 openpyxl 來處理 Excel
try:
    import openpyxl
except ImportError:
    messagebox.showerror("Error", "Missing required library: openpyxl.\nPlease install it using: pip install openpyxl")

# =====================================================================
#  PingPanel - Final Version (Leader Only, Master Summary Update Only)
# =====================================================================
class PingPanel(tk.Frame):
    def __init__(self, parent, ser, log_func, response_queue):
        super().__init__(parent, bg=color_def.COLOR_BG_PANEL)
        self.ser = ser
        self.log = log_func
        self.response_queue = response_queue
        
        self.is_working = False
        self.work_thread = None
        self.mesh_prefix = ""

        self.setup_ui()
        # 確保結果目錄存在
        if not os.path.exists("results"):
            os.makedirs("results")
        
        # [關鍵] 定義固定的主總結檔案名稱
        self.master_summary_file = os.path.join("results", "ping_master_summary.xlsx")

    def setup_ui(self):
        # --- Top Panel: Settings & Actions ---
        top_frame = tk.LabelFrame(self, text="Ping Configuration & Actions", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, font=("Arial", 10, "bold"))
        top_frame.pack(fill="x", padx=10, pady=10)

        # Settings
        settings_frame = tk.Frame(top_frame, bg=color_def.COLOR_BG_PANEL)
        settings_frame.pack(side=tk.LEFT, padx=10, pady=10)
        
        tk.Label(settings_frame, text="Size:", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT).grid(row=0, column=0, padx=5)
        self.entry_size = tk.Entry(settings_frame, width=5); self.entry_size.insert(0, "64"); self.entry_size.grid(row=0, column=1)
        tk.Label(settings_frame, text="Count:", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT).grid(row=0, column=2, padx=5)
        self.entry_count = tk.Entry(settings_frame, width=5); self.entry_count.insert(0, "50"); self.entry_count.grid(row=0, column=3)
        tk.Label(settings_frame, text="Timeout/Pkt(s):", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT).grid(row=0, column=4, padx=5)
        self.entry_timeout = tk.Entry(settings_frame, width=5); self.entry_timeout.insert(0, "2"); self.entry_timeout.grid(row=0, column=5)

        # Actions
        action_frame = tk.Frame(top_frame, bg=color_def.COLOR_BG_PANEL)
        action_frame.pack(side=tk.RIGHT, padx=10, pady=10)
        self.btn_discover = tk.Button(action_frame, text="1. Discover Nodes", bg=color_def.COLOR_ACCENT_YELLOW, fg="black", command=self.start_discovery_thread)
        self.btn_discover.pack(side=tk.LEFT, padx=5)
        self.btn_ping_sel = tk.Button(action_frame, text="2. Ping Selected", bg=color_def.COLOR_BTN_BG, fg=color_def.COLOR_BTN_FG, command=self.start_ping_selected_thread)
        self.btn_ping_sel.pack(side=tk.LEFT, padx=5)
        self.btn_ping_all = tk.Button(action_frame, text="3. Ping All", bg=color_def.COLOR_ACCENT_GREEN, fg=color_def.COLOR_BTN_FG, command=self.start_ping_all_thread)
        self.btn_ping_all.pack(side=tk.LEFT, padx=5)
        
        stop_btn_style = {"bg": color_def.COLOR_ACCENT_RED, "fg": "white", "font": ("Arial", 9, "bold")}
        self.btn_stop = tk.Button(action_frame, text="Stop Ping", command=self.stop_ping_process, state="disabled", **stop_btn_style)
        self.btn_stop.pack(side=tk.LEFT, padx=(20, 5))

        # --- Middle Panel: Combined Node List & Status (Treeview) ---
        list_frame = tk.LabelFrame(self, text="Node List & Status", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT)
        list_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        
        cols = ("Index", "Role", "IP Address", "Status")
        self.combined_tree = ttk.Treeview(list_frame, columns=cols, show='headings', selectmode='extended', height=12)
        
        self.combined_tree.heading("Index", text="Index", anchor="center")
        self.combined_tree.column("Index", width=60, anchor="center")
        self.combined_tree.heading("Role", text="Role", anchor="center")
        self.combined_tree.column("Role", width=100, anchor="center")
        self.combined_tree.heading("IP Address", text="IP Address", anchor="w")
        self.combined_tree.column("IP Address", width=300, anchor="w")
        self.combined_tree.heading("Status", text="Ping Status", anchor="w")
        self.combined_tree.column("Status", width=350, anchor="w")
        
        scrollbar_tree = ttk.Scrollbar(list_frame, orient="vertical", command=self.combined_tree.yview)
        self.combined_tree.configure(yscroll=scrollbar_tree.set)
        self.combined_tree.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar_tree.pack(side=tk.RIGHT, fill="y")

    # ================= Helper Functions =================
    def send_cmd(self, cmd):
        if self.ser.is_open:
            self.ser.write((cmd + "\r\n").encode())

    def wait_for_response(self, match_func, timeout=5.0):
        start_time = time.time()
        collected_lines = []
        while time.time() - start_time < timeout:
            try:
                line = self.response_queue.get(timeout=0.1)
                collected_lines.append(line)
                result = match_func(line, collected_lines)
                if result:
                    return result 
            except queue.Empty:
                if not self.ser.is_open: break 
                continue
        return None 

    def set_buttons_state(self, state):
        self.btn_discover.config(state=state)
        self.btn_ping_sel.config(state=state)
        self.btn_ping_all.config(state=state)
        stop_state = "normal" if state == "disabled" else "disabled"
        self.btn_stop.config(state=stop_state)
    
    def update_tree_status(self, item_id, status_text):
        self.after(0, lambda: self.combined_tree.set(item_id, column="Status", value=status_text))

    def send_silence_command(self):
        """發送 'ot log level 0' 讓裝置靜音"""
        if self.ser and self.ser.is_open:
            try:
                self.log("[PingTool] Sending: ot log level 0 (silence device)")
                self.ser.write(b"ot log level 0\r\n")
                time.sleep(0.2)
            except Exception as e:
                self.log(f"[PingTool] Failed to send 'ot log level 0': {e}")

    # [新增] 檢查當前裝置是否為 Leader 的輔助函式
    def check_is_leader(self):
        if not self.ser.is_open:
            messagebox.showwarning("Error", "Not connected."); return False
        
        self.log("[PingTool] Checking device role (must be Leader)...")
        with self.response_queue.mutex: self.response_queue.queue.clear()
        self.send_cmd("ot state")

        def match_state(line, all_lines):
            # 匹配常見的狀態回應
            if line.strip() in ["leader", "router", "child", "detached", "disabled"]:
                return line.strip()
            return None

        state = self.wait_for_response(match_state, timeout=2.0)
        
        if state == "leader":
            self.log("[PingTool] Role confirmed: Leader. Proceeding.")
            return True
        elif state is None:
             self.log("[PingTool] Failed to get device role (timeout).")
             messagebox.showerror("Error", "Failed to get device role (timeout).\nPlease check connection.")
             return False
        else:
            self.log(f"[PingTool] Role mismatch. Current role: {state}. Action aborted.")
            messagebox.showwarning("Role Restriction", f"Current device role is '{state}'.\nOnly 'leader' can perform this action.")
            return False

    # [保留] 用於「原地更新」主總結檔案的輔助函式
    def update_master_summary(self, new_data_list):
        if not new_data_list: return
        filename = self.master_summary_file
        try:
            new_df = pd.DataFrame(new_data_list)
            
            if not os.path.exists(filename):
                new_df.to_excel(filename, index=False, engine='openpyxl')
            else:
                existing_df = pd.read_excel(filename, engine='openpyxl')
                existing_df.set_index("IP Address", inplace=True)
                new_df.set_index("IP Address", inplace=True)
                existing_df.update(new_df)
                new_ips = new_df.index.difference(existing_df.index)
                final_df = pd.concat([existing_df, new_df.loc[new_ips]])
                final_df.reset_index(inplace=True)
                with pd.ExcelWriter(filename, engine='openpyxl', mode='w') as writer:
                    final_df.to_excel(writer, index=False)
                    
            self.log(f"[PingTool] Updated master summary file: {filename}")
        except Exception as e:
            self.log(f"[PingTool] Error updating master summary: {e}")

    # [新增] 計算當前統計資料的輔助函式
    def calculate_current_stats(self, target_item_ids, node_results, node_rtts, current_total_rounds):
        stats_list = []
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        size_str = self.entry_size.get()

        for item_id in target_item_ids:
            success_count = node_results[item_id]
            rtt_list = node_rtts[item_id]
            avg_rtt = sum(rtt_list) / len(rtt_list) if rtt_list else 0.0
            
            actual_rounds = current_total_rounds if current_total_rounds > 0 else 1
            loss_rate = ((actual_rounds - success_count) / actual_rounds) * 100
            rtt_info = f"Avg RTT: {avg_rtt:.1f}ms" if success_count > 0 else "Avg RTT: N/A"

            if success_count == actual_rounds:
                final_status = f"Success (Loss: 0.0%) - OK:{success_count}/{actual_rounds} - {rtt_info}"
            elif success_count > 0:
                final_status = f"Partial (Loss: {loss_rate:.1f}%) - OK:{success_count}/{actual_rounds} - {rtt_info}"
            else:
                final_status = f"Failed (Loss: 100%) - OK:0/{actual_rounds}"
            
            self.update_tree_status(item_id, final_status)
            
            current_values = self.combined_tree.item(item_id)['values']
            stats_list.append({
                "Test Time": timestamp, "Node Index": current_values[0], "Role": current_values[1],
                "IP Address": current_values[2], "Packet Size": size_str, 
                "Total Rounds": actual_rounds, 
                "Success Count": success_count, "Loss Rate (%)": f"{loss_rate:.1f}",
                "Avg RTT (ms)": f"{avg_rtt:.1f}" if success_count > 0 else "N/A", "Status": final_status
            })
        return stats_list

    # ================= Discovery Logic =================
    def start_discovery_thread(self):
        # [需求] 加入角色檢查
        if not self.check_is_leader(): return
        
        if self.is_working: return
        self.set_buttons_state("disabled")
        self.is_working = True
        for item in self.combined_tree.get_children(): self.combined_tree.delete(item)
        self.work_thread = threading.Thread(target=self.discovery_task, daemon=True)
        self.work_thread.start()

    def discovery_task(self):
        self.log("[PingTool] Starting node discovery...")
        try:
            # --- Step 1: Get Prefix ---
            self.log("[PingTool] Getting mesh prefix...")
            with self.response_queue.mutex: self.response_queue.queue.clear()
            self.send_cmd("ot ipaddr mleid")
            
            found_prefix = None
            def match_prefix_done(line, all_lines):
                nonlocal found_prefix
                if "fd" in line and ":" in line and "Done" not in line and ">" not in line:
                    parts = line.strip().split(':')
                    if len(parts) >= 4:
                        found_prefix = ":".join(parts[:4]) + ":"
                if "Done" in line: return found_prefix if found_prefix else "NOT_FOUND"
                return None

            prefix_result = self.wait_for_response(match_prefix_done, timeout=3.0)
            if not prefix_result or prefix_result == "NOT_FOUND":
                 raise Exception("Failed to get prefix (timeout or not found).")
            self.mesh_prefix = prefix_result
            self.log(f"[PingTool] Prefix found: {self.mesh_prefix}")
            time.sleep(0.5)

            # --- Step 2: Get Node List & Parse Index ---
            self.log("[PingTool] Getting node list...")
            with self.response_queue.mutex: self.response_queue.queue.clear()
            self.send_cmd("app node list")

            def match_node_list_ok(line, all_lines):
                if "Done" in line: return all_lines
                return None

            node_lines = self.wait_for_response(match_node_list_ok, timeout=10.0)
            if not node_lines: raise Exception("Failed to get node list (Done timeout).")

            self.log("[PingTool] Parsing node list...")
            parse_count = 0
            
            regex_pattern = r'\[(\d+)\]\s+(router|child|detached)\s+(?:[0-9A-Fa-f]{4}\s+){2}([0-9A-Fa-f]{16})'
            
            for line in node_lines:
                match = re.search(regex_pattern, line)
                if match:
                    index_str = match.group(1)
                    role_str = match.group(2)
                    full_ext_addr = match.group(3)
                    
                    rloc16_prefix = full_ext_addr[:4] 
                    target_ip = f"{self.mesh_prefix}{rloc16_prefix}:0000:0000:0000"
                    
                    self.after(0, lambda idx=index_str, r=role_str, ip=target_ip: 
                               self.combined_tree.insert('', tk.END, values=(idx, r, ip, "Ready")))
                    parse_count += 1

            self.log(f"[PingTool] Discovery complete. Found {parse_count} nodes.")

        except Exception as e:
            self.log(f"[PingTool] Discovery stopped: {e}")
            messagebox.showerror("Discovery Error", str(e))
        finally:
            self.is_working = False
            self.after(0, lambda: self.set_buttons_state("normal"))

    # ================= Ping Logic (Multi-threaded Round-Robin) =================
    def start_ping_selected_thread(self):
        # [需求] 加入角色檢查
        if not self.check_is_leader(): return

        selected_items = self.combined_tree.selection()
        if not selected_items: messagebox.showwarning("Info", "Please select node(s) to ping."); return
        self.send_silence_command()
        self.run_ping_task(selected_items, is_ping_all=False)

    def start_ping_all_thread(self):
        # [需求] 加入角色檢查
        if not self.check_is_leader(): return

        all_items = self.combined_tree.get_children()
        if not all_items: messagebox.showwarning("Info", "No nodes available. Please discover first."); return
        self.send_silence_command()
        self.run_ping_task(all_items, is_ping_all=True)

    def stop_ping_process(self):
        if self.is_working:
            self.log("[PingTool] Stop requested by user...")
            self.is_working = False

    def stop_all_tasks(self):
        if self.is_working:
            self.log("[PingTool] Stopping tasks due to tab change...")
            self.is_working = False

    def run_single_ot_ping(self, target_ip, size_str, timeout_per_pkt):
        cmd = f"ot ping {target_ip} {size_str} 1 1 15 {int(timeout_per_pkt)}"
        with self.response_queue.mutex: self.response_queue.queue.clear()
        self.send_cmd(cmd)

        def match_ping_result(line, all_lines):
            rtt_match = re.search(r"time=(\d+)ms", line)
            if rtt_match: return ("OK", int(rtt_match.group(1)))
            summary_match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+packets received", line)
            if summary_match and int(summary_match.group(1)) == 1 and int(summary_match.group(2)) == 0:
                return ("Timeout", None)
            if "Error" in line and "ot ping" not in line: return ("Error", None)
            return None

        return self.wait_for_response(match_ping_result, timeout=timeout_per_pkt + 1.0)

    def run_ping_task(self, target_item_ids, is_ping_all=False):
        if not self.ser.is_open: messagebox.showwarning("Error", "Not connected."); return
        if self.is_working: return
        self.set_buttons_state("disabled")
        self.is_working = True
        self.work_thread = threading.Thread(target=self.ping_task_loop, args=(target_item_ids, is_ping_all), daemon=True)
        self.work_thread.start()

    def ping_task_loop(self, target_item_ids, is_ping_all):
        try:
            size_str = self.entry_size.get()
            target_count = int(self.entry_count.get())
            timeout_per_pkt = float(self.entry_timeout.get())
            total_nodes = len(target_item_ids)
            
            node_results = {item_id: 0 for item_id in target_item_ids}
            node_rtts = {item_id: [] for item_id in target_item_ids}

            mode_str = "Ping All (Updating Master Summary)" if is_ping_all else "Ping Selected"
            self.log(f"[PingTool] Starting Multi-threaded {mode_str} (Rounds: {target_count}, Nodes: {total_nodes})")

            workers = min(total_nodes, 32) 
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)

            # --- 外層迴圈控制「輪數」 ---
            for round_num in range(1, target_count + 1):
                if not self.ser.is_open or not self.is_working: break
                self.log(f"[PingTool] --- Starting Round {round_num}/{target_count} ---")

                for item_id in target_item_ids:
                    self.update_tree_status(item_id, f"Round {round_num}/{target_count}: Pinging...")

                future_to_item = {}
                for item_id in target_item_ids:
                    target_ip = self.combined_tree.item(item_id)['values'][2]
                    future = executor.submit(self.run_single_ot_ping, target_ip, size_str, timeout_per_pkt)
                    future_to_item[future] = item_id
                
                for future in concurrent.futures.as_completed(future_to_item):
                    if not self.is_working: break
                    item_id = future_to_item[future]
                    try:
                        result = future.result()
                        round_result_str = "Timeout"
                        if result:
                            status, rtt = result
                            if status == "OK":
                                node_results[item_id] += 1
                                if rtt is not None:
                                    node_rtts[item_id].append(rtt)
                                    round_result_str = f"OK ({rtt}ms)"
                            elif status == "Error":
                                round_result_str = "Error"
                        
                        self.update_tree_status(item_id, f"Round {round_num}/{target_count}: {round_result_str}")
                    except Exception as e:
                        self.update_tree_status(item_id, f"Error: {e}")

                if not self.is_working: break

                # [需求] Ping All 模式下，每 10 輪更新一次主總結檔案
                if is_ping_all and round_num % 10 == 0:
                    self.log(f"[PingTool] Auto-updating master summary file for rounds up to {round_num}...")
                    current_stats = self.calculate_current_stats(target_item_ids, node_results, node_rtts, round_num)
                    self.update_master_summary(current_stats)

                time.sleep(0.5)

            executor.shutdown(wait=False)

            if not self.is_working: self.log("[PingTool] Ping task stopped by user.")
            else: self.log("[PingTool] All rounds completed.")
            
            # 處理剩餘的資料
            if is_ping_all:
                # 更新最後一次主總結
                final_stats = self.calculate_current_stats(target_item_ids, node_results, node_rtts, target_count)
                self.update_master_summary(final_stats)
                messagebox.showinfo("Success", f"Ping All finished.\nMaster summary updated: {self.master_summary_file}")

            elif not is_ping_all:
                 self.calculate_current_stats(target_item_ids, node_results, node_rtts, target_count)
                 messagebox.showinfo("Success", "Ping Selected finished. See UI for results.")

        except Exception as e:
            self.log(f"[PingTool] Ping task error: {e}")
            messagebox.showerror("Ping Error", str(e))
        finally:
            self.is_working = False
            self.after(0, lambda: self.set_buttons_state("normal"))
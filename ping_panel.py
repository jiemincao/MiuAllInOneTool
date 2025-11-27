import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import queue
import re
import os
import pandas as pd
import color_def

# =====================================================================
#  全新的 PingPanel 類別定義 (Final Version with RTT & Excel)
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
        self.entry_count = tk.Entry(settings_frame, width=5); self.entry_count.insert(0, "5"); self.entry_count.grid(row=0, column=3)
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
        self.combined_tree.column("Status", width=350, anchor="w") # 加寬以顯示 RTT
        
        scrollbar_tree = ttk.Scrollbar(list_frame, orient="vertical", command=self.combined_tree.yview)
        self.combined_tree.configure(yscroll=scrollbar_tree.set)
        self.combined_tree.pack(side=tk.LEFT, fill="both", expand=True)
        scrollbar_tree.pack(side=tk.RIGHT, fill="y")

    # ================= Helper Functions for Threading =================
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
    
    def update_tree_status(self, item_id, status_text):
        self.after(0, lambda: self.combined_tree.set(item_id, column="Status", value=status_text))

    # ================= Discovery Logic =================
    def start_discovery_thread(self):
        if not self.ser.is_open: messagebox.showwarning("Error", "Not connected."); return
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
                if "+Ok" in line: return all_lines
                return None

            node_lines = self.wait_for_response(match_node_list_ok, timeout=10.0)
            if not node_lines: raise Exception("Failed to get node list (+Ok timeout).")

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

    # ================= Ping Logic (Round-Robin with RTT) =================
    def start_ping_selected_thread(self):
        selected_items = self.combined_tree.selection()
        if not selected_items: messagebox.showwarning("Info", "Please select node(s) to ping."); return
        self.run_ping_task(selected_items)

    def start_ping_all_thread(self):
        all_items = self.combined_tree.get_children()
        if not all_items: messagebox.showwarning("Info", "No nodes available. Please discover first."); return
        self.run_ping_task(all_items)

    def stop_all_tasks(self):
        if self.is_working:
            self.log("[PingTool] Stopping tasks due to tab change...")
            self.is_working = False

    def run_ping_task(self, target_item_ids):
        if not self.ser.is_open: messagebox.showwarning("Error", "Not connected."); return
        if self.is_working: return
        self.set_buttons_state("disabled")
        self.is_working = True
        self.work_thread = threading.Thread(target=self.ping_task_loop, args=(target_item_ids,), daemon=True)
        self.work_thread.start()

    def ping_task_loop(self, target_item_ids):
        try:
            size_str = self.entry_size.get()
            target_count_str = self.entry_count.get()
            target_count = int(target_count_str)
            timeout_per_pkt_str = self.entry_timeout.get()
            timeout_per_pkt = float(timeout_per_pkt_str)

            # 指令中的 count 固定為 1
            base_cmd_single = f"ot ping {{}} {size_str} 1 1 64 {int(timeout_per_pkt)}"
            
            total_nodes = len(target_item_ids)
            
            # --- [新增] 初始化結果與 RTT 追蹤字典 ---
            node_results = {item_id: 0 for item_id in target_item_ids}
            node_rtts = {item_id: [] for item_id in target_item_ids}

            self.log(f"[PingTool] Starting Round-Robin Ping (Total Rounds: {target_count}, Nodes: {total_nodes})")

            # --- [修改] 新的匹配函式，優先抓取 RTT ---
            def match_single_ping_result(line, all_lines):
                # 1. 優先嘗試抓取 RTT，例如 "time=57ms"
                rtt_match = re.search(r"time=(\d+)ms", line)
                if rtt_match:
                    rtt = int(rtt_match.group(1))
                    return ("OK", rtt)
                
                # 2. 如果沒抓到 RTT，檢查是否為 Timeout 的統計行 (1 transmitted, 0 received)
                summary_match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+packets received", line)
                if summary_match:
                    tx = int(summary_match.group(1))
                    rx = int(summary_match.group(2))
                    if tx == 1 and rx == 0:
                         return ("Timeout", None)

                # 3. 檢查是否有明確錯誤
                if "Error" in line and "ot ping" not in line:
                    return ("Error", None)
                
                return None

            # --- 外層迴圈控制「輪數」 ---
            for round_num in range(1, target_count + 1):
                if not self.ser.is_open: break
                self.log(f"[PingTool] --- Starting Round {round_num}/{target_count} ---")

                # --- 內層迴圈遍歷所有目標節點 ---
                for i, item_id in enumerate(target_item_ids):
                    if not self.ser.is_open: break

                    current_values = self.combined_tree.item(item_id)['values']
                    target_ip = current_values[2]

                    seq_status = f"Round {round_num}/{target_count}: Pinging..."
                    self.update_tree_status(item_id, seq_status)
                    
                    with self.response_queue.mutex: self.response_queue.queue.clear()
                    self.send_cmd(base_cmd_single.format(target_ip))

                    result = self.wait_for_response(match_single_ping_result, timeout=timeout_per_pkt + 1.0)
                    
                    round_result_str = "Timeout"
                    
                    if result:
                        status, rtt = result
                        if status == "OK":
                            node_results[item_id] += 1
                            if rtt is not None:
                                node_rtts[item_id].append(rtt) # 儲存 RTT
                                round_result_str = f"OK ({rtt}ms)"
                            else:
                                round_result_str = "OK" # 理論上不會發生
                        elif status == "Error":
                             round_result_str = "Error"
                    
                    self.update_tree_status(item_id, f"Round {round_num}/{target_count}: {round_result_str}")
                    time.sleep(0.1) 
                
                time.sleep(0.5)

            # --- 所有輪數結束，計算統計並儲存 ---
            self.log("[PingTool] rounds completed. Calculating final statistics...")
            
            excel_data = []
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

            for item_id in target_item_ids:
                success_count = node_results[item_id]
                rtt_list = node_rtts[item_id]
                
                # 計算平均 RTT
                avg_rtt = 0.0
                if rtt_list:
                    avg_rtt = sum(rtt_list) / len(rtt_list)
                
                final_status = "Internal Error"
                loss_rate = 0.0
                # 準備顯示用的 RTT 字串
                rtt_info = f"Avg RTT: {avg_rtt:.1f}ms" if success_count > 0 else "Avg RTT: N/A"

                if target_count > 0:
                    loss_rate = ((target_count - success_count) / target_count) * 100
                    if success_count == target_count:
                        final_status = f"Success (Loss: 0.0%) - OK:{success_count}/{target_count} - {rtt_info}"
                    elif success_count > 0:
                        final_status = f"Partial (Loss: {loss_rate:.1f}%) - OK:{success_count}/{target_count} - {rtt_info}"
                    else:
                        final_status = f"Failed (Loss: 100%) - OK:0/{target_count}"
                
                self.update_tree_status(item_id, final_status)
                
                current_values = self.combined_tree.item(item_id)['values']
                excel_data.append({
                    "Test Time": timestamp,
                    "Node Index": current_values[0],
                    "Role": current_values[1],
                    "IP Address": current_values[2],
                    "Packet Size": size_str,
                    "Total Count": target_count,
                    "Success Count": success_count,
                    "Loss Rate (%)": f"{loss_rate:.1f}",
                    "Avg RTT (ms)": f"{avg_rtt:.1f}" if success_count > 0 else "N/A", # 新增 RTT 欄位
                    "Status": final_status
                })

            # --- 將結果寫入 Excel 檔案 ---
            if excel_data:
                try:
                    log_dir = "results"
                    if not os.path.exists(log_dir):
                        os.makedirs(log_dir)
                    
                    filename_ts = time.strftime("%Y%m%d_%H%M%S")
                    excel_filename = os.path.join(log_dir, f"ping_results_{filename_ts}.xlsx")
                    
                    df = pd.DataFrame(excel_data)
                    df.to_excel(excel_filename, index=False, engine='openpyxl')
                    self.log(f"[PingTool] Results saved to: {excel_filename}")
                    messagebox.showinfo("Success", f"Ping test finished.\nResults saved to: {excel_filename}")
                except Exception as e:
                    self.log(f"[PingTool] Error saving Excel: {e}")
                    messagebox.showerror("Error", f"Failed to save Excel file:\n{e}")
            else:
                self.log("[PingTool] No data to save.")


        except Exception as e:
            self.log(f"[PingTool] Ping task error: {e}")
            messagebox.showerror("Ping Error", str(e))
        finally:
            self.is_working = False
            self.after(0, lambda: self.set_buttons_state("normal"))
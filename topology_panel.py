import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import queue
import re
import os
import tempfile
import networkx as nx
from pyvis.network import Network
import webbrowser
import shutil
import color_def

class TopologyPanel(tk.Frame):
    def __init__(self, parent, ser, log_func, response_queue):
        super().__init__(parent, bg=color_def.COLOR_BG_PANEL)
        self.ser = ser
        self.log = log_func
        self.response_queue = response_queue
        
        self.is_working = False
        self.work_thread = None
        self.my_rloc16 = None
        self.prefix = ""

        self.G = nx.Graph()
        self.node_details = {} 
        
        # 定義一個固定的暫存檔案路徑，用於自動刷新
        self.temp_dir = tempfile.gettempdir()
        self.fixed_html_path = os.path.join(self.temp_dir, "miu_topology_live_view.html")

        # --- 自動刷新相關變數 ---
        self.auto_refresh_enabled = tk.BooleanVar(value=False)
        self.refresh_interval_var = tk.StringVar(value="10")
        self.refresh_timer_id = None
        # [移除] 不再需要 browser_opened_once 旗標
        # self.browser_opened_once = False

        self.setup_ui()

    def setup_ui(self):
        # --- 左側：控制面板 ---
        control_frame = tk.LabelFrame(self, text="Controls", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, width=220)
        control_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        control_frame.pack_propagate(False)

        # 1. 手動刷新按鈕
        self.btn_refresh = tk.Button(control_frame, text="Refresh Topology Now", bg=color_def.COLOR_ACCENT_BLUE, fg=color_def.COLOR_BTN_FG, command=self.start_refresh_thread, height=2)
        self.btn_refresh.pack(fill=tk.X, padx=10, pady=(20, 10))

        # --- 自動刷新控制區 ---
        auto_frame = tk.LabelFrame(control_frame, text="Auto Refresh", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT)
        auto_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # [修改] checkbutton 的 command 改為 toggle_auto_refresh_handler
        tk.Checkbutton(auto_frame, text="Enable", variable=self.auto_refresh_enabled, command=self.toggle_auto_refresh_handler,
                       bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, selectcolor=color_def.COLOR_BG_PANEL, activebackground=color_def.COLOR_BG_PANEL, activeforeground=color_def.COLOR_TEXT).pack(anchor="w", padx=5)
        
        int_frame = tk.Frame(auto_frame, bg=color_def.COLOR_BG_PANEL)
        int_frame.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(int_frame, text="Interval (s):", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT).pack(side=tk.LEFT)
        tk.Entry(int_frame, textvariable=self.refresh_interval_var, width=5).pack(side=tk.RIGHT)
        
        tk.Label(auto_frame, text="Note: Browser reloads page automatically.", font=("Arial", 8), bg=color_def.COLOR_BG_PANEL, fg="gray", wraplength=200).pack(anchor="w", padx=5, pady=(0,5))


        # --- 儲存按鈕 ---
        self.btn_save = tk.Button(control_frame, text="Save HTML...", bg=color_def.COLOR_BTN_BG, fg=color_def.COLOR_BTN_FG, command=self.save_html_file, state="disabled")
        self.btn_save.pack(fill=tk.X, padx=10, pady=20)

        # 狀態標籤
        self.status_label = tk.Label(control_frame, text="Status: Idle", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, wraplength=200, justify="left")
        self.status_label.pack(fill=tk.X, padx=10, pady=(0, 20), side=tk.BOTTOM)

        # --- 右側：提示區域 ---
        right_frame = tk.Frame(self, bg=color_def.COLOR_BG_SUB, relief=tk.RIDGE, bd=2)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 10), pady=10)
        
        msg = "Topology visualization will open in your\ndefault web browser.\n\nWhen Auto-Refresh is enabled,\nthe browser page reloads itself."
        tk.Label(right_frame, text=msg, bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, font=("Arial", 12), justify="center").pack(expand=True)

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
                if result: return result 
            except queue.Empty:
                if not self.ser.is_open: break 
                continue
        return None

    def set_status(self, text, color=color_def.COLOR_TEXT):
        self.status_label.config(text=f"Status: {text}", fg=color)

    # --- 停止任務 (用於分頁切換) ---
    def stop_all_tasks(self):
        self.log("[Topo] Stopping all tasks...")
        self.auto_refresh_enabled.set(False)
        if self.refresh_timer_id:
            self.after_cancel(self.refresh_timer_id)
            self.refresh_timer_id = None
        self.is_working = False
        self.set_status("Stopped.", color_def.COLOR_ACCENT_YELLOW)
        self.btn_refresh.config(state="normal")
        # [移除] browser_opened_once 重置

    # ================= Auto Refresh Logic =================
    def toggle_auto_refresh_handler(self):
        """ 處理 Checkbutton 的點擊事件 """
        if self.auto_refresh_enabled.get():
            self.log("[Topo] Auto-refresh enabled.")
            # [關鍵] 啟用時立即觸發一次「手動」刷新。
            # 這會生成帶有 Meta 標籤的 HTML，並強制瀏覽器開啟/重新載入它。
            if not self.is_working and self.ser.is_open:
                 self.start_refresh_thread(is_auto=False) 
            # 開始後台計時器迴圈
            self.auto_refresh_loop()
        else:
            self.log("[Topo] Auto-refresh disabled.")
            # 停止計時器
            if self.refresh_timer_id:
                self.after_cancel(self.refresh_timer_id)
                self.refresh_timer_id = None
            # [關鍵] 停用時也立即觸發一次「手動」刷新。
            # 這會生成不帶 Meta 標籤的靜態 HTML，並強制瀏覽器重新載入它，從而停止瀏覽器的自動刷新。
            if not self.is_working and self.ser.is_open:
                 self.start_refresh_thread(is_auto=False)

    def auto_refresh_loop(self):
        if not self.auto_refresh_enabled.get(): return
        
        # 計算間隔
        try:
            interval_sec = int(self.refresh_interval_var.get())
            if interval_sec < 5: interval_sec = 5
        except ValueError:
            interval_sec = 30
        
        # 如果當前沒有在忙，就觸發一次後台刷新 (is_auto=True)
        if not self.is_working and self.ser.is_open:
            self.start_refresh_thread(is_auto=True)

        # 排程下一次
        self.refresh_timer_id = self.after(interval_sec * 1000, self.auto_refresh_loop)

    # ================= Save HTML Logic =================
    def save_html_file(self):
        if not self.fixed_html_path or not os.path.exists(self.fixed_html_path):
            messagebox.showwarning("Warning", "No topology data available to save. Please refresh first.")
            return
        
        file_path = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML Files", "*.html"), ("All Files", "*.*")],
            title="Save Topology HTML"
        )
        
        if file_path:
            try:
                shutil.copy2(self.fixed_html_path, file_path)
                self.log(f"[Topo] HTML saved to: {file_path}")
                messagebox.showinfo("Success", f"Topology saved successfully to:\n{file_path}")
            except Exception as e:
                self.log(f"[Topo] Error saving file: {e}")
                messagebox.showerror("Error", f"Failed to save file:\n{e}")

    # ================= Core Logic: Data Collection =================
    def start_refresh_thread(self, is_auto=False):
        if not self.ser.is_open: 
            if not is_auto: messagebox.showwarning("Error", "Not connected.")
            return
        if self.is_working: return
        
        if not is_auto: # 只有手動刷新時才禁用按鈕
            self.btn_refresh.config(state="disabled")
            self.btn_save.config(state="disabled")

        self.is_working = True
        prefix = "Auto-" if is_auto else ""
        self.set_status(f"{prefix}Refreshing data...", color_def.COLOR_ACCENT_YELLOW)
        
        self.work_thread = threading.Thread(target=self.refresh_task, args=(is_auto,), daemon=True)
        self.work_thread.start()

    def refresh_task(self, is_auto):
        # ... (資料收集邏輯保持不變，省略以節省篇幅) ...
        try:
            self.G.clear()
            self.node_details.clear()
            self.prefix = ""
            self.my_rloc16 = None
            my_role = "unknown"

            # --- Step 1: 獲取 Prefix ---
            with self.response_queue.mutex: self.response_queue.queue.clear()
            self.send_cmd("ot ipaddr mleid")
            def match_prefix(line, all_lines):
                if "fd" in line and ":" in line and "Done" not in line and ">" not in line:
                    parts = line.strip().split(':')
                    if len(parts) >= 4:
                        self.prefix = ":".join(parts[:3]) + ":"
                if "Done" in line: return self.prefix if self.prefix else "ERROR"
                return None
            prefix_result = self.wait_for_response(match_prefix, timeout=2.0)

            # --- Step 2: 獲取 RLOC16 ---
            with self.response_queue.mutex: self.response_queue.queue.clear()
            self.send_cmd("ot rloc16")
            def match_rloc(line, all_lines):
                line = line.strip().upper()
                if re.match(r"^[0-9A-F]{4}$", line) and line != "DONE":
                    self.my_rloc16 = line
                if "DONE" in line: return self.my_rloc16 if self.my_rloc16 else "ERROR"
                return None
            rloc_result = self.wait_for_response(match_rloc, timeout=2.0)
            if not rloc_result or rloc_result == "ERROR":
                raise Exception("Failed to get RLOC16")

            # --- Step 3: 獲取 Role ---
            with self.response_queue.mutex: self.response_queue.queue.clear()
            self.send_cmd("ot state")
            def match_state(line, all_lines):
                nonlocal my_role
                line = line.strip().lower()
                if line in ["leader", "router", "child", "detached", "disabled"]:
                    my_role = line
                if "Done" in line: return my_role if my_role != "unknown" else "ERROR"
                return None
            state_result = self.wait_for_response(match_state, timeout=3.0)
            if state_result and state_result != "ERROR":
                my_role = state_result


            if my_role == "leader":
                self.log("Role confirmed: Leader. Proceeding.")
            else:
                self.log(f"Role mismatch. Current role: {my_role}. Action aborted.")
                messagebox.showwarning("Role Restriction", f"Current device role is '{my_role}'.\nOnly 'leader' can perform this action.")
                return False
            
            # --- Step 4: 獲取 Extaddr ---
            with self.response_queue.mutex: self.response_queue.queue.clear()
            self.send_cmd("ot extaddr")
            def match_state(line, all_lines):
                nonlocal my_extaddr
                lline = line.strip().upper()
                if re.match(r"^[0-9A-F]{16}$", lline) and lline != "DONE":
                    my_extaddr = lline
                if "DONE" in line: return my_extaddr if my_extaddr else "ERROR"
                return None
            state_result = self.wait_for_response(match_state, timeout=2.0)
            if state_result and state_result != "ERROR":
                my_extaddr = state_result

            # --- Step 5: 建立「自己」這個節點 ---
            self.G.add_node(self.my_rloc16)
            self.node_details[self.my_rloc16] = {'role': my_role, 'ext_addr': my_extaddr if my_extaddr else 'N/A', 'rssi': 0}

            # --- Step 6: 獲取並解析 app node list ---
            with self.response_queue.mutex: self.response_queue.queue.clear()
            self.send_cmd("app node list")
            def match_list_done(line, all_lines):
                if "Done" in line: return all_lines
                return None
            list_lines = self.wait_for_response(match_list_done, timeout=8.0)
            if not list_lines: raise Exception("Node list timeout")

            node_pattern = r"\[\d+\]\s+(router|child|leader)\s+([0-9A-Fa-f]{4})\s+([0-9A-Fa-f]{4})\s+([0-9A-Fa-f]{16})\s+(-?\d+)"
            
            for line in list_lines:
                match = re.search(node_pattern, line)
                if match:
                    role = match.group(1)
                    parent_rloc = match.group(2).upper()
                    self_rloc = match.group(3).upper()
                    ext_addr = match.group(4)
                    rssi = int(match.group(5))

                    if self_rloc == self.my_rloc16:
                        self.node_details[self.my_rloc16]['ext_addr'] = ext_addr
                        if parent_rloc != self_rloc:
                             self.G.add_edge(parent_rloc, self_rloc, rssi=rssi)
                    else:
                        self.G.add_node(self_rloc)
                        self.node_details[self_rloc] = {'role': role, 'ext_addr': ext_addr, 'rssi': rssi}
                        if parent_rloc != self_rloc:
                            self.G.add_edge(parent_rloc, self_rloc, rssi=rssi)
                    if parent_rloc not in self.G:
                            self.G.add_node(parent_rloc)
                    if parent_rloc not in self.node_details:
                        self.node_details[parent_rloc] = {'role': 'unknown', 'ext_addr': '?', 'rssi': 0}

            if not is_auto:
                self.log(f"[Topo] Data collected. Nodes: {len(self.G.nodes)}, Edges: {len(self.G.edges)}")
            
            self.after(0, lambda: self.generate_and_show_pyvis(is_auto))
            
            status_msg = "Data updated (Auto)." if is_auto else "Data updated. Browser opened."
            self.after(0, lambda: self.set_status(status_msg, color_def.COLOR_ACCENT_GREEN))

        except Exception as e:
            self.log(f"[Topo] Error: {e}")
            self.after(0, lambda: self.set_status(f"Error: {e}", color_def.COLOR_ACCENT_RED))
        finally:
            self.is_working = False
            if not is_auto:
                self.after(0, lambda: self.btn_refresh.config(state="normal"))
                self.after(0, lambda: self.btn_save.config(state="normal"))

    def generate_and_show_pyvis(self, is_auto):
        net = Network(height="860px", width="100%", bgcolor="#ffffff", font_color="#000000", notebook=False, cdn_resources='in_line', select_menu=True)
        
        # --- 轉換節點 ---
        vis_nodes_data = []
        for node_rloc in self.G.nodes:
            details = self.node_details.get(node_rloc, {})
            role = str(details.get('role', 'unknown'))
            ext = str(details.get('ext_addr', 'N/A'))
            rssi = str(details.get('rssi', 0))

            role_lower = role.lower()
            color, size = ("#FF0000", 20) if role_lower=="leader" else ("#0055FF", 15) if role_lower=="router" else ("#00FF00", 10) if role_lower=="child" else ("gray", 8)
            shape = "dot"

            title = f"RLOC16: {node_rloc}\nRole: {role.title()}\nExt: {ext}\nRSSI: {rssi} dBm"
            
            vis_nodes_data.append({
                'n_id': node_rloc, 'label': node_rloc, 'color': color, 'size': size, 'shape': shape,
                'title': title, 'font': {'size': 16, 'strokeWidth': 2, 'strokeColor': '#000000'}
            })

        for node_data in vis_nodes_data:
            net.add_node(
                node_data['n_id'], label=node_data['label'], color=node_data['color'], size=node_data['size'],
                shape=node_data['shape'], title=node_data['title'], font=node_data['font']
            )

        # --- 轉換邊 ---
        vis_edges = []
        for u, v, data in self.G.edges(data=True):
            rssi_val = data.get('rssi', 0)
            try: rssi_int = int(rssi_val)
            except (ValueError, TypeError): rssi_int = 0

            if rssi_int >= -60:
                weight = 1.5 # very close
            elif rssi_int >= -80:
                weight = 1.2 # medium
            else:
                weight = 0.85 # very far
            
            vis_edges.append({
                'from': u, 'to': v, 'label': str(rssi_int), 'color': {'color': "#000000", 'highlight': "#000000"},
                'width': weight, 'font': {'color': "#000000", 'strokeWidth': 0, 'align': 'top'}
            })

        for e in vis_edges: net.add_edge(e['from'], e['to'], label=e['label'], color=e['color'], width=e['width'], font=e['font'])

        net.set_options("""
        var options = {
          "physics": {
            "forceAtlas2Based": { "gravitationalConstant": -100, "springLength": 150, "damping": 0.9 },
            "maxVelocity": 50, "solver": "forceAtlas2Based", "timestep": 0.35, "stabilization": {"enabled": true, "iterations": 150}
          },
          "interaction": { "hover": true, "navigationButtons": true, "keyboard": true }
        }
        """)

        try:
            # 1. 生成原始 HTML 字串
            html_content = net.generate_html()
            
            # 2. [關鍵] 根據自動刷新狀態，決定是否注入 Meta Refresh 標籤
            if self.auto_refresh_enabled.get():
                try:
                    interval = int(self.refresh_interval_var.get())
                    if interval < 5: interval = 5
                except:
                    interval = 30
                # 啟用：注入 meta refresh 標籤
                meta_tag = f'<meta http-equiv="refresh" content="{interval}">'
                html_content = html_content.replace('</head>', f'{meta_tag}\n</head>')
            
            # 3. 準備要插入的摘要 HTML (Total Nodes)
            total_nodes = len(self.G.nodes)
            summary_html = f"""
            <div style="
                position: absolute;
                top: 10%; 
                right: 20px;
                background: rgba(255,255,255,0.9);
                padding: 10px 15px;
                border-radius: 8px;
                border: 1px solid #ccc;
                font-size: 16px;
                font-weight: bold;
                color: black;
                z-index: 9999;
                box-shadow: 2px 2px 5px rgba(0,0,0,0.2);
            ">
                Total Nodes: {total_nodes}
            </div>
            """
            
            # 4. 將摘要 HTML 插入到 </body> 之前
            html_content = html_content.replace('</body>', f'{summary_html}\n</body>')

            # 5. 強制 UTF-8 寫入固定的暫存檔案
            with open(self.fixed_html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
                
            if not is_auto:
                self.log(f"[Topo] Generated HTML at: {self.fixed_html_path}")
            
            # [關鍵] 只要是手動觸發的 (包含切換開關時的那一次)，都呼叫 webbrowser.open
            # 這確保了瀏覽器會被打開，或者重新載入新的 HTML 內容 (包含或移除 Meta 標籤)
            if not is_auto:
                url = 'file://' + self.fixed_html_path
                webbrowser.open(url)
                self.log(f"[Topo] Opened external browser with URL: {url}")
                # [移除] browser_opened_once 標記

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log(f"[Topo] Error generating/loading HTML: {e}")
            if not is_auto:
                messagebox.showerror("Error", f"Failed to generate view:\n{e}")

    def __del__(self):
        # 清理固定的暫存檔
        if self.fixed_html_path and os.path.exists(self.fixed_html_path):
            try: os.remove(self.fixed_html_path)
            except: pass
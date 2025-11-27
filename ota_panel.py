import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import os
import struct
import lzma
import tempfile
import color_def
import binascii

# =============================================
# Constants & Definitions
# =============================================
OTA_CHUNK_SIZE = 221 # Maximum data payload per packet
OTA_MAX_RETRIES = 5  # Max retries for packet sending
OTA_TIMEOUT = 3.0    # Timeout for waiting for ACK (seconds)
ERASE_TIMEOUT = 8.0  # Longer timeout for erase operation
# [新增] 封包間的延遲時間，讓設備有時間寫入 Flash
INTER_PACKET_DELAY = 0.1 # 100ms

class OTAPanel(tk.Frame):
    # 建構子接收 main_app 實體
    def __init__(self, main_app, ser, log_func, ota_queue):
        super().__init__(main_app.notebook, bg=color_def.COLOR_BG_PANEL)
        self.main_app = main_app # 保存主程式引用，用於呼叫 start/stop_ota_mode
        self.ser = ser
        self.log = log_func
        self.ota_queue = ota_queue
        
        self.original_file_path = ""
        self.temp_fota_path = None
        self.is_running = False
        
        self.setup_ui()

    # ... (setup_ui, select_file, process_firmware_file 保持不變) ...
    def setup_ui(self):
        # --- Control Panel (Left) ---
        control_frame = tk.LabelFrame(self, text="OTA Controls", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, width=280)
        control_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        control_frame.pack_propagate(False)

        # 1. File Selection
        file_frame = tk.Frame(control_frame, bg=color_def.COLOR_BG_PANEL)
        file_frame.pack(fill=tk.X, padx=10, pady=(20, 10))
        
        tk.Label(file_frame, text="Select Original Firmware:", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT).pack(anchor="w", pady=(0, 5))
        
        self.file_label = tk.Label(file_frame, text="No file selected", bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, relief=tk.SUNKEN, anchor="w", height=2)
        self.file_label.pack(fill=tk.X, side=tk.LEFT, expand=True)
        
        self.btn_select = tk.Button(file_frame, text="Browse...", bg=color_def.COLOR_BTN_BG, fg=color_def.COLOR_BTN_FG, command=self.select_file)
        self.btn_select.pack(side=tk.RIGHT, padx=(5, 0))
        
        # 2. File Info Display
        info_frame = tk.LabelFrame(control_frame, text="FOTA File Info", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT)
        info_frame.pack(fill=tk.X, padx=10, pady=20)
        
        self.info_label = tk.Label(info_frame, text="Select file to auto-extract\nversion/type and generate image.", 
                                   bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT, justify="left", anchor="w", font=("Courier", 9))
        self.info_label.pack(fill=tk.X, padx=10, pady=10)

        # 3. Start Button
        self.btn_start = tk.Button(control_frame, text="Start OTA Update", bg=color_def.COLOR_ACCENT_GREEN, fg=color_def.COLOR_BTN_FG, command=self.start_ota, height=2, state="disabled")
        self.btn_start.pack(fill=tk.X, padx=10, pady=(0, 20), side=tk.BOTTOM)

        # --- Status & Progress (Right) ---
        right_frame = tk.Frame(self, bg=color_def.COLOR_BG_PANEL)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.status_label = tk.Label(right_frame, text="Status: Idle", bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, font=("Arial", 12), anchor="w", padx=10, pady=10, relief=tk.RIDGE)
        self.status_label.pack(fill=tk.X, pady=(0, 10))
        self.progress = ttk.Progressbar(right_frame, orient=tk.HORIZONTAL, length=100, mode='determinate')
        self.progress.pack(fill=tk.X, pady=(0, 10))
        log_frame = tk.LabelFrame(right_frame, text="OTA Progress Log", bg=color_def.COLOR_BG_PANEL, fg=color_def.COLOR_TEXT)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.ota_log = tk.Text(log_frame, bg=color_def.COLOR_BG_SUB, fg=color_def.COLOR_TEXT, state="disabled", wrap=tk.WORD)
        self.ota_log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

    def select_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("BIN files", "*.bin"), ("All files", "*.*")])
        if file_path:
            self.original_file_path = file_path
            self.file_label.config(text=os.path.basename(file_path))
            self.process_firmware_file()

    def process_firmware_file(self):
        try:
            self.log(f"[OTA] Processing firmware: {self.original_file_path}")
            self.set_status("Processing file & extracting info...", color_def.COLOR_ACCENT_YELLOW)
            self.btn_select.config(state="disabled")
            self.btn_start.config(state="disabled")
            self.info_label.config(text="Analyzing binary and generating image...")

            threading.Thread(target=self._generate_fota_bin, args=(self.original_file_path,), daemon=True).start()

        except Exception as e:
            self._handle_process_error(e)

    def _generate_fota_bin(self, original_path):
        """ Background thread to extract info, compress, and add header. """
        try:
            # 1. Read original file
            with open(original_path, 'rb') as f:
                original_data = f.read()
            orig_size = len(original_data)

            # === 解析原始 Binary ===
            self.log("[OTA] Searching for 'VerGet' marker...")
            marker = b'VerGet'
            marker_idx = original_data.find(marker)

            if marker_idx == -1:
                raise Exception("Marker 'VerGet' not found.")
            
            # +1 跳過 'VerGet' 後面的 null byte
            data_start_idx = marker_idx + len(marker) + 1
            
            # 提取 Version (4 bytes)
            version_bytes = original_data[data_start_idx : data_start_idx + 4]
            if len(version_bytes) < 4: raise Exception("Unexpected end reading version.")
            # 直接將 raw bytes 轉為 hex 字串顯示
            extracted_version_str = binascii.hexlify(version_bytes).decode('ascii').upper()
            
            # 提取 Type (12 bytes)
            type_bytes = original_data[data_start_idx + 4 : data_start_idx + 4 + 12]
            if len(type_bytes) < 12: raise Exception("Unexpected end reading type.")
            extracted_type_str = type_bytes.decode('ascii', errors='ignore').strip('\x00')

            self.log(f"[OTA] Extracted - Version: 0x{extracted_version_str}, Type: {extracted_type_str}")
            # ========================

            # 2. Compress (LZMA) - 使用明確的嵌入式標準參數
            self.log("[OTA] Compressing data (LZMA standard embedded params: dict=8MB)...")
            embedded_filters = [
                {
                    "id": lzma.FILTER_LZMA1,
                    "dict_size": 8 * 1024 * 1024, # 8MB
                    "lc": 3, "lp": 0, "pb": 2,
                    "mode": lzma.MODE_NORMAL,
                }
            ]
            compressor = lzma.LZMACompressor(format=lzma.FORMAT_ALONE, filters=embedded_filters)
            compressed_data = compressor.compress(original_data) + compressor.flush()
            compressed_size = len(compressed_data)
            self.log(f"[OTA] Compression done. Size: {compressed_size} bytes (Target: ~276632)")

            # 3. Generate Header
            self.log("[OTA] Generating 32-byte header...")
            # 使用標準 binascii.crc32
            checksum = binascii.crc32(compressed_data) & 0xFFFFFFFF
            self.log(f"[OTA Debug] Calculated Standard CRC32: {checksum:#010x} (Target: 0xedddacf9)")
            checksum_bytes = struct.pack('<I', checksum)
            
            # Field 4: Start Address (0x00000000)
            start_bytes = struct.pack('<I', 0x00000000)
            # Field 5: End Address (compressed size)
            end_bytes = struct.pack('<I', compressed_size)
            # Field 6: Reserved
            reserved_bytes = b'\0' * 4
            
            header = version_bytes + type_bytes + checksum_bytes + start_bytes + end_bytes + reserved_bytes

            # 4. Combine into final FOTA image
            fota_image = header + compressed_data
            final_size = len(fota_image)
            
            # 5. Save to temp file
            if self.temp_fota_path and os.path.exists(self.temp_fota_path):
                os.remove(self.temp_fota_path)
            tmp_fd, self.temp_fota_path = tempfile.mkstemp(suffix=".bin")
            os.close(tmp_fd)
            with open(self.temp_fota_path, 'wb') as f:
                f.write(fota_image)
                
            # 6. Update UI
            self.after(0, lambda: self._update_ui_after_process(orig_size, compressed_size, final_size, checksum, extracted_version_str, extracted_type_str))

        except Exception as e:
            self.after(0, lambda: self._handle_process_error(e))

    def _update_ui_after_process(self, orig_size, comp_size, final_size, checksum, version_str, type_str):
        info_text = (
            f"Version:   0x{version_str}\n"
            f"Type:      {type_str}\n"
            f"Original:  {orig_size:,} bytes\n"
            f"Compressed:{comp_size:,} bytes\n"
            f"Total FOTA:{final_size:,} bytes\n"
            f"Checksum:  {checksum:#010x} (CRC32)"
        )
        self.info_label.config(text=info_text)
        self.btn_start.config(state="normal")
        self.btn_select.config(state="normal")
        self.log(f"[OTA] FOTA binary generated successfully: {self.temp_fota_path}")
        self.set_status("FOTA image ready. Click 'Start OTA' to begin.", color_def.COLOR_ACCENT_GREEN)

    def _handle_process_error(self, error):
        self.log(f"[OTA] Error processing file: {error}")
        messagebox.showerror("Error", f"Failed to process firmware:\n{error}")
        self.info_label.config(text=f"Error: {error}")
        self.btn_start.config(state="disabled")
        self.btn_select.config(state="normal")
        self.set_status("Processing failed.", color_def.COLOR_ACCENT_RED)

    # ================= OTA Execution Logic =================
    def start_ota(self):
        """ Triggers the background OTA transmission task. """
        if not self.ser.is_open:
            messagebox.showwarning("Error", "Serial port not connected."); return
        if not self.temp_fota_path or not os.path.exists(self.temp_fota_path):
            messagebox.showerror("Error", "FOTA file not found. Please regenerate."); return
            
        if self.is_running:
            self.is_running = False 
            self.set_status("Stopping OTA...", color_def.COLOR_ACCENT_YELLOW)
        else:
            self.is_running = True
            self.btn_start.config(text="Stop OTA", bg=color_def.COLOR_ACCENT_RED)
            self.btn_select.config(state="disabled")
            self.set_status("Starting OTA process...", color_def.COLOR_ACCENT_BLUE)
            self.progress['value'] = 0
            self.ota_log.config(state="normal"); self.ota_log.delete(1.0, tk.END); self.ota_log.config(state="disabled")
            threading.Thread(target=self.ota_transmission_task, daemon=True).start()

    def ota_transmission_task(self):
        success = False
        try: # 外層 Try
            self.log_ota("--- OTA Transmission Started ---")
            self.main_app.start_ota_mode()
            time.sleep(0.2)
            transmission_success = False
            
            try: # 內層 Try
                # === Step 0: Silence Logs (等待 Done) ===
                self.log_ota("[0/4] Silencing device logs: ot log level 0")
                if not self.send_and_wait_ascii("ot log level 0", "Done"):
                     raise Exception("Failed to set log level (no 'Done').")
                self.log_ota("Log level set to 0.")

                # === Step 1: Send Start Command (等待 +Ok) ===
                self.log_ota("[1/4] Sending start command: ota download")
                if not self.send_and_wait_ascii("ota download", "+Ok", timeout=5.0):
                     raise Exception("Failed to enter OTA mode (no '+Ok').")
                
                self.log_ota("OTA mode confirmed (+Ok). Waiting for readiness...")
                time.sleep(1.0) 
                
                self.log_ota("Clearing buffer for binary transfer...")
                self.ser.reset_input_buffer()

                # === Step 2: Send Erase Command (Hex String) ===
                self.log_ota("[2/4] Sending Flash Erase command...")
                erase_cmd_hex = "FFFCFCFF07000000F000000008"
                erase_ack_hex = "FFFCFCFF0B008000F00000000000000084"
                if not self.send_and_wait_raw(erase_cmd_hex, erase_ack_hex, timeout=ERASE_TIMEOUT):
                    raise Exception("Flash Erase failed or timed out.")
                self.log_ota("Flash erased successfully.")

                # === Step 3: Stream Firmware (Binary Packets) ===
                self.log_ota("[3/4] Starting firmware streaming...")
                if not self.stream_firmware():
                    raise Exception("Firmware streaming failed.")
                transmission_success = True

            except Exception as e:
                self.log_ota(f"Transmission Process Error: {e}", error=True)
                transmission_success = False

            finally:
                # === Step 4: Send Finish Command (Cleanup) ===
                if self.ser.is_open:
                    self.log_ota("[4/4] Sending Finish command (cleanup)...")
                    finish_cmd_hex = "FFFCFCFF07020000F000000006"
                    finish_ack_hex = "FFFCFCFF0B008000F00000000000000084"
                    if not self.send_and_wait_raw(finish_cmd_hex, finish_ack_hex, timeout=2.0, retries=1):
                         self.log_ota("Warning: Finish command verification failed.", error=True)
                    else:
                         self.log_ota("Finish command sent and acknowledged.")

            success = transmission_success
            if success:
                self.log_ota("--- OTA transmission complete! ---", color=color_def.COLOR_ACCENT_GREEN)

        except Exception as e:
            self.log_ota(f"Initialization Error: {e}", error=True)
            self.log(f"[OTA Error] {e}")
        finally:
            self.main_app.stop_ota_mode()
            self.after(0, lambda: self.finish_ota(success))

    def stream_firmware(self):
        try:
            with open(self.temp_fota_path, 'rb') as f:
                fota_data = f.read()
            
            total_size = len(fota_data)
            file_version = struct.unpack('<I', fota_data[0:4])[0]
            file_type = 0x0001
            manufacturer_code = 0x1234
            total_packets = (total_size + OTA_CHUNK_SIZE - 1) // OTA_CHUNK_SIZE
            self.log_ota(f"Total size: {total_size} bytes, Packets: {total_packets}")
            expected_ack_hex = "FFFCFCFF0B008000F00000000000000084"

            for i in range(total_packets):
                if not self.is_running: return False
                start_idx = i * OTA_CHUNK_SIZE
                end_idx = min((i + 1) * OTA_CHUNK_SIZE, total_size)
                chunk = fota_data[start_idx:end_idx]
                current_index = i
                packet_bin = self.build_ota_packet(file_type, manufacturer_code, file_version, 
                                                   total_size, total_packets, i, chunk)
                
                # [重點修改] 在這裡增加延遲
                if not self.send_and_wait_bin(packet_bin, expected_ack_hex, retries=OTA_MAX_RETRIES):
                    self.log_ota(f"Failed to send packet {current_index}/{total_packets}", error=True)
                    return False
                
                # 成功發送一個封包後，等待設備寫入 Flash
                # self.log_ota(f"Packet {current_index} ACKed. Waiting for flash write...") # Debug
                time.sleep(INTER_PACKET_DELAY)

                progress_percent = int((current_index / total_packets) * 100)
                self.after(0, lambda p=progress_percent: self.progress.configure(value=p))
                if current_index % 10 == 0 or current_index == total_packets:
                    self.log_ota(f"Sent packet {current_index}/{total_packets} ({len(chunk)} bytes)")
            return True
        except Exception as e:
            self.log_ota(f"Streaming error: {e}", error=True)
            return False

    # build_ota_packet (保持上一次的修正，使用 4-byte Address 和 1-byte Length)
    def build_ota_packet(self, f_type, m_code, f_ver, f_size, t_idx, c_idx, data_chunk):
        data_len = len(data_chunk)
        # 1. Payload (Little-Endian)
        payload_struct = struct.pack('<HHIIIIH', f_type, m_code, f_ver, f_size, t_idx, c_idx, data_len)
        full_payload = payload_struct + data_chunk

        # 2. Header Parts
        header_fixed = b'\xFF\xFC\xFC\xFF'
        cmd_id_bytes = bytes.fromhex('010000F0') # Little-Endian 0xF0000001
        # 使用 2-byte Address
        addr_bytes = struct.pack('<H', 0x0001) 
        mode_byte = b'\x00'

        # Length Calculation: Cmd(4)+Addr(2)+Mode(1) + Payload
        length_val = 4 + 2 + 1 + len(full_payload)
        
        # Length 打包: 強制轉換為單個 byte
        length_bytes = bytes([length_val])

        # 3. Combine for Checksum
        packet_without_checksum = length_bytes + cmd_id_bytes + addr_bytes + mode_byte + full_payload
        
        # 4. Calculate Checksum
        checksum_sum = sum(packet_without_checksum) & 0xFF
        checksum_val = (~checksum_sum) & 0xFF
        
        # 5. Final Packet
        return header_fixed + packet_without_checksum + struct.pack('B', checksum_val)

    def send_and_wait_raw(self, data_hex, expected_ack_hex, timeout=OTA_TIMEOUT, retries=3):
        data_bin = binascii.unhexlify(data_hex)
        return self.send_and_wait_bin(data_bin, expected_ack_hex, timeout, retries)

    # send_and_wait_bin (保持上一次的修正)
    def send_and_wait_bin(self, data_bin, expected_ack_hex, timeout=OTA_TIMEOUT, retries=3):
        expected_ack_bin = binascii.unhexlify(expected_ack_hex)
        ack_len = len(expected_ack_bin)
        
        header_debug = binascii.hexlify(data_bin[:300]).decode('ascii').upper()
        self.log_ota(f"[OTA Debug] TX ({len(data_bin)} bytes): {header_debug}"+("..." if len(data_bin)>300 else ""), color="gray")

        for attempt in range(retries + 1):
            if not self.is_running: return False
            try:
                self.ser.reset_input_buffer()
                self.ser.write(data_bin)
                self.ser.flush()
                time.sleep(0.02)

                start_time = time.time()
                rx_buffer = b""
                while time.time() - start_time < timeout:
                    if self.ser.in_waiting > 0:
                        byte = self.ser.read(1)
                        rx_buffer += byte
                        if len(rx_buffer) > ack_len * 2: rx_buffer = rx_buffer[-ack_len:]
                        if expected_ack_bin in rx_buffer: return True
                    else:
                        time.sleep(0.005)

                if attempt < retries:
                    self.log_ota(f"Timeout waiting for ACK. Retrying ({attempt+1}/{retries})...", error=True)
                    remaining = self.ser.read(self.ser.in_waiting) if self.ser.in_waiting > 0 else b""
                    full_rx = rx_buffer + remaining
                    if full_rx:
                        try:
                            rx_text = full_rx.decode('ascii', errors='ignore').strip()
                            if rx_text and len(rx_text) > 1:
                                self.log_ota(f"[OTA Debug] RX (Device Log): {rx_text}", error=True)
                            else:
                                raise ValueError("Empty or tiny text")
                        except:
                            rx_debug = binascii.hexlify(full_rx).decode('ascii').upper()
                            self.log_ota(f"[OTA Debug] RX (Hex): {rx_debug}", error=True)
                    else:
                        self.log_ota(f"[OTA Debug] RX buffer empty.", error=True)
                    time.sleep(0.5)

            except Exception as e:
                 self.log_ota(f"Serial error: {e}", error=True)
                 return False
        return False

    # send_and_wait_ascii (保持上一次的修正)
    def send_and_wait_ascii(self, command, expected_response, timeout=3.0, retries=3):
        """ Sends an ASCII command and waits for a text response using readline. """
        cmd_bytes = command.encode('utf-8') + b'\r\n'
        for attempt in range(retries + 1):
            if not self.is_running: return False
            try:
                self.ser.reset_input_buffer()
                self.ser.write(cmd_bytes)
                self.ser.flush()
                start_time = time.time()
                while time.time() - start_time < timeout:
                    if self.ser.in_waiting > 0:
                        line_bytes = self.ser.readline()
                        try:
                            line_text = line_bytes.decode('utf-8', errors='ignore').strip()
                            if expected_response in line_text:
                                return True
                        except:
                            pass
                    else:
                        time.sleep(0.05)
                if attempt < retries:
                     self.log_ota(f"Timeout waiting for '{expected_response}'. Retrying...", error=True)
                     time.sleep(0.5)
            except Exception as e:
                 self.log_ota(f"Serial error: {e}", error=True)
                 return False
        return False

    def finish_ota(self, success):
        self.is_running = False
        self.btn_start.config(text="Start OTA Update", bg=color_def.COLOR_ACCENT_GREEN)
        self.btn_select.config(state="normal")
        if success:
            self.set_status("OTA Update Completed Successfully!", color_def.COLOR_ACCENT_GREEN)
            self.progress['value'] = 100
            messagebox.showinfo("OTA Finish", "Firmware update completed successfully.")
        else:
            self.set_status("OTA Update Failed.", color_def.COLOR_ACCENT_RED)

    # ================= Helper Functions =================
    # ... (log_ota, set_status, stop_all_tasks, __del__ 保持不變) ...
    def log_ota(self, message, error=False, color=None):
        """ Logs messages to the OTA-specific text area with optional coloring. """
        if not self.is_running and not error and color is None: return # 避免停止後還在寫 log

        self.ota_log.config(state="normal")
        tag = "normal"
        if error: 
            tag = "error"
            self.ota_log.tag_config("error", foreground="red")
        elif color:
            tag = "custom_color"
            self.ota_log.tag_config("custom_color", foreground=color)
            
        self.ota_log.insert(tk.END, message + "\n", tag)
        self.ota_log.see(tk.END)
        self.ota_log.config(state="disabled")
        # 同步寫入主 Log
        if error: self.log(f"[OTA Error] {message}")
        else: self.log(f"[OTA] {message}")

    def set_status(self, text, color):
        self.status_label.config(text=f"Status: {text}", fg=color)

    def stop_all_tasks(self):
        """ Called when switching away from this tab. """
        if self.is_running:
            self.log("[OTA] Stopping tasks due to tab change...")
            self.is_running = False
            self.btn_start.config(text="Start OTA Update", bg=color_def.COLOR_ACCENT_GREEN)
            self.btn_select.config(state="normal")
            self.set_status("Stopped by tab change.", color_def.COLOR_ACCENT_YELLOW)
            # 確保在意外切換分頁時也能恢復主程式讀取
            self.main_app.stop_ota_mode()

    def __del__(self):
        if self.temp_fota_path and os.path.exists(self.temp_fota_path):
            try: os.remove(self.temp_fota_path)
            except: pass
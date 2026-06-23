"""Apply three changes to ebo_trend_builder.py - no heredocs needed"""
import os

path = os.path.join(os.path.dirname(__file__), 'ebo_trend_builder.py')

with open(path) as f:
    lines = f.read().split('\n')

# Find line indices
btn_start = None
for i, l in enumerate(lines):
    if l.strip() == '# Export button':
        btn_start = i
        break

btn_end = btn_start
for i in range(btn_start, len(lines)):
    if lines[i].strip() == '' and i > btn_start + 8:
        btn_end = i
        break

enable_idx = -1
for i, l in enumerate(lines):
    if 'self.export_btn.config(state="normal")' in l:
        enable_idx = i
        break

export_start = None
for i, l in enumerate(lines):
    if l.strip() == 'def _export(self):':
        export_start = i
        break

export_end = None
for i, l in enumerate(lines):
    if l.strip().startswith('def _set_status') and i > export_start:
        export_end = i
        break

print(f'btn: {btn_start+1}-{btn_end}, enable: {enable_idx+1}, export: {export_start+1}-{export_end}')

# Verify
assert btn_start is not None
assert btn_end > btn_start
assert enable_idx > 0
assert export_start is not None
assert export_end > export_start

# Change 1: Button block
btn_new = [
    '            # Export buttons',
    '            self.export_group_btn = tk.Button(options_frame, text="Export XML - Group",',
    '                                        font=("Segoe UI", 10, "bold"),',
    '                                        bg="#009624", fg=self.FG_WHITE,',
    '                                        relief="flat", padx=20, pady=6,',
    '                                        activebackground=self.HIGHLIGHT,',
    '                                        cursor="hand2",',
    '                                        state="disabled",',
    '                                        command=self._export_group)',
    '            self.export_group_btn.pack(padx=12, pady=(0, 5), fill="x")',
    '',
    '            self.export_single_btn = tk.Button(options_frame, text="Export XML - Single",',
    '                                         font=("Segoe UI", 10, "bold"),',
    '                                         bg=self.SELECT_BG, fg=self.FG_WHITE,',
    '                                         relief="flat", padx=20, pady=6,',
    '                                         activebackground=self.HIGHLIGHT,',
    '                                         cursor="hand2",',
    '                                         state="disabled",',
    '                                         command=self._export_single)',
    '            self.export_single_btn.pack(padx=12, pady=(0, 10), fill="x")',
]

lines[btn_start:btn_end] = btn_new

# Change 2: Enable line (indices shifted after insert, re-find)
for i, l in enumerate(lines):
    if l.strip().startswith('self.export_group_btn.config') and 'load_backup' in globals().__str__():
        pass  # just find the right line

# Actually just search for the pattern by text
enable_idx = None
for i, l in enumerate(lines):
    if l.strip() == 'self.export_btn.config(state="normal")':
        enable_idx = i
        break

if enable_idx is not None:
    lines[enable_idx] = '                self.export_group_btn.config(state="normal")'
    lines.insert(enable_idx + 1, '                self.export_single_btn.config(state="normal")')

# Change 3: Replace _export method (re-find after inserts)
export_start = None
for i, l in enumerate(lines):
    if l.strip() == 'def _export(self):':
        export_start = i
        break

export_end = None
for i, l in enumerate(lines):
    if l.strip().startswith('def _set_status') and i > export_start:
        export_end = i
        break

assert export_start is not None
assert export_end > export_start

# Build the replacement
D = chr(9472)

new_methods = []

def a(s):
    new_methods.append(s)

a('        def _build_report(self, output_path, stats):')
a('            report = []')
a(f'            report.append(f"{D*50}")')
a('            report.append("  EBO TREND BUILDER - EXPORT COMPLETE")')
a(f'            report.append(f"{D*50}")')
a('            report.append(f"  Output File:  {output_path}")')
a('            report.append(f"  Server:       {self.db.server_name}  v{self.db.server_version}")')
a('            report.append(f"  Controllers:  {len(stats[\'controllers_found\'])} / {stats[\'total_controllers\']}")')
a('            report.append(f"  Total Points: {stats[\'total_points\']}")')
a(f'            report.append(f"{D*50}")')
a('            report.append(f"  Trends Created:")')
a('            report.append(f"    BACnet Trend Logs:     {stats[\'trend_logs_created\']}")')
a('            report.append(f"    Extended Trend Logs:   {stats[\'ext_logs_created\']}")')
a('            report.append(f"    Trend Charts:          {stats[\'trend_charts_created\']}")')
a(f'            report.append(f"{D*29}")')
a('            report.append(f"    TOTAL Objects:         {stats[\'trend_logs_created\'] + stats[\'ext_logs_created\'] + stats[\'trend_charts_created\']}")')
a(f'            report.append(f"{D*50}")')
a('            report.append(f"  File size: {os.path.getsize(output_path):,} bytes")')
a(f'            report.append(f"{D*50}")')
a('            return "\\n".join(report)')
a('')
a('        def _export_group(self):')
a('            """Export all selected controllers into a single XML file."""')
a('            if not self.db:')
a('                messagebox.showwarning("No Data", "Load a backup file first.")')
a('                return')
a('')
a('            selected = set(name for name, var in self.controller_vars.items() if var.get())')
a('            if not selected:')
a('                messagebox.showwarning("No Selection", "Select at least one controller.")')
a('                return')
a('')
a('            default_name = f"trends_{self.db.server_name}.xml"')
a('            output_path = filedialog.asksaveasfilename(')
a('                title="Save Group XML Export",')
a('                defaultextension=".xml",')
a('                initialfile=default_name,')
a('                filetypes=[("XML Files", "*.xml"), ("All Files", "*.*")]')
a('            )')
a('            if not output_path:')
a('                return')
a('')
a('            self.root.config(cursor="watch")')
a('            self.root.update()')
a('')
a('            try:')
a('                gen = XmlGenerator(')
a('                    db=self.db,')
a('                    include_trend_logs=self.trend_log_var.get(),')
a('                    include_ext_logs=self.ext_log_var.get(),')
a('                    include_trend_charts=self.chart_var.get(),')
a('                    selected_controllers=selected,')
a('                )')
a('                xml_str, stats = gen.generate(output_path)')
a('                report_text = self._build_report(output_path, stats)')
a('                self._set_status(report_text, color=self.FG_GREEN)')
a('            except Exception as e:')
a('                self._set_status(f"Export error: {e}", color=self.FG_RED)')
a('                messagebox.showerror("Export Error", str(e))')
a('            finally:')
a('                self.root.config(cursor="")')
a('')
a('        def _export_single(self):')
a('            """Export one XML file per selected controller into a chosen directory."""')
a('            if not self.db:')
a('                messagebox.showwarning("No Data", "Load a backup file first.")')
a('                return')
a('')
a('            selected = set(name for name, var in self.controller_vars.items() if var.get())')
a('            if not selected:')
a('                messagebox.showwarning("No Selection", "Select at least one controller.")')
a('                return')
a('')
a('            output_dir = filedialog.askdirectory(')
a('                title="Select Export Directory for Individual Files"')
a('            )')
a('            if not output_dir:')
a('                return')
a('')
a('            self.root.config(cursor="watch")')
a('            self.root.update()')
a('')
a('            total_tl = 0')
a('            total_el = 0')
a('            total_tc = 0')
a('            created_files = []')
a('')
a('            try:')
a('                for ctrl_name in sorted(selected):')
a('                    safe_name = ctrl_name.replace("(", "").replace(")", "").replace(" ", "_")')
a('                    filename = f"trends_{self.db.server_name}_{safe_name}.xml"')
a('                    filepath = os.path.join(output_dir, filename)')
a('')
a('                    gen = XmlGenerator(')
a('                        db=self.db,')
a('                        include_trend_logs=self.trend_log_var.get(),')
a('                        include_ext_logs=self.ext_log_var.get(),')
a('                        include_trend_charts=self.chart_var.get(),')
a('                        selected_controllers={ctrl_name},')
a('                    )')
a('                    xml_str, stats = gen.generate(filepath)')
a('                    total_tl += stats["trend_logs_created"]')
a('                    total_el += stats["ext_logs_created"]')
a('                    total_tc += stats["trend_charts_created"]')
a('                    created_files.append(filepath)')
a('')
a('                report = []')
a(f'                report.append(f"{D*50}")')
a('                report.append("  EBO TREND BUILDER - SINGLE EXPORT COMPLETE")')
a(f'                report.append(f"{D*50}")')
a('                report.append(f"  Export Directory:  {output_dir}")')
a('                report.append(f"  Server:            {self.db.server_name}  v{self.db.server_version}")')
a('                report.append(f"  Files Created:     {len(created_files)} / {len(selected)} selected")')
a(f'                report.append(f"{D*50}")')
a('                report.append(f"  Trends Created:")')
a('                report.append(f"    BACnet Trend Logs:     {total_tl}")')
a('                report.append(f"    Extended Trend Logs:   {total_el}")')
a('                report.append(f"    Trend Charts:          {total_tc}")')
a(f'                report.append(f"{D*29}")')
a('                report.append(f"    TOTAL Objects:         {total_tl + total_el + total_tc}")')
a(f'                report.append(f"{D*50}")')
a('                report.append("  Files:")')
a('                for f in created_files:')
a('                    size = os.path.getsize(f)')
a('                    report.append(f"    {os.path.basename(f)}  ({size:,} bytes)")')
a(f'                report.append(f"{D*50}")')
a('                self._set_status("\\n".join(report), color=self.FG_GREEN)')
a('')
a('            except Exception as e:')
a('                self._set_status(f"Export error: {e}", color=self.FG_RED)')
a('                messagebox.showerror("Export Error", str(e))')
a('            finally:')
a('                self.root.config(cursor="")')

lines[export_start:export_end] = new_methods

# Write
content = '\n'.join(lines)
with open(path, 'w') as f:
    f.write(content)

# Verify
compile(content, path, 'exec')
print('OK - all 3 changes applied, file compiles clean')
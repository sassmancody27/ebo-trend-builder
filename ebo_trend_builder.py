#!/usr/bin/env python3
"""
EBO Trend Builder
Standalone GUI tool for automatically creating BACnet trends from EBO .xbk backup files.

Generates EBO import XML with:
  - BACnet Trend Logs (bacnet.mpx.TrendLog)
  - Extended Trend Logs (trend.ETLog)
  - Trend Charts (trend.view.GraphicalTrendView)

Usage:
  python ebo_trend_builder.py

PyInstaller:
  pyinstaller --onefile --name EBO_Trend_Builder ebo_trend_builder.py
"""

import os
import sys
import re
import zipfile
import sqlite3
import tempfile
import shutil
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    tk = None


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────
RSTP_NETWORK_PARENT_NAME = "BACnet Interface"
RSTP_NETWORK_NAME = "RSTP Network"
CONTROLLER_TYPES_FILTER = ("bacnet.mpx.udt.MPC36A", "bacnet.mpx.udt.MPC24A")
APPLICATION_TYPE = "bacnet.mpx.MPXApplicationProxy"
POINT_TYPE_PREFIXES = ("bacnet.mpx.point.", "bacnet.mpx.value.")

# Analog types -> LogInterval=30000, LoggingType=0
ANALOG_POINT_TYPES = frozenset({
    "bacnet.mpx.point.VoltageInput",
    "bacnet.mpx.point.CurrentInput",
    "bacnet.mpx.point.TemperatureInput",
    "bacnet.mpx.point.RTD2WireInput",
    "bacnet.mpx.point.ResistiveInput",
    "bacnet.mpx.point.AnalogValue",
    "bacnet.mpx.point.CurrentOutput",
    "bacnet.mpx.point.VoltageOutput",
    "bacnet.mpx.point.CounterInput",
    "bacnet.mpx.point.DigitalPulsedOutput",
    "bacnet.mpx.point.PulseWidthOutput",
    "bacnet.mpx.point.TristatePulsedOutput",
    "bacnet.mpx.value.AnalogValue",
    "bacnet.mpx.value.AnalogConsumerValue",
    "bacnet.mpx.value.DateTimeValue",
    "bacnet.mpx.point.CurrentInput",
})

# Binary/digital types -> LogInterval=0, LoggingType=1
BINARY_POINT_TYPES = frozenset({
    "bacnet.mpx.point.DigitalInput",
    "bacnet.mpx.point.DigitalOutput",
    "bacnet.mpx.point.DigitalValue",
    "bacnet.mpx.point.SupervisedInput",
    "bacnet.mpx.point.TristateOutput",
    "bacnet.mpx.value.DigitalValue",
    "bacnet.mpx.value.DigitalConsumerValue",
    "bacnet.mpx.value.MultistateValue",
    "bacnet.mpx.value.CharacterString",
})


# ──────────────────────────────────────────────────────────────────────
# XBK Database Reader
# ──────────────────────────────────────────────────────────────────────
class XbkDatabase:
    """Reads .xbk backup files and extracts station hierarchy."""

    def __init__(self, xbk_path):
        self.xbk_path = xbk_path
        self._tmpdir = None
        self._conn = None
        self.server_name = ""
        self.server_version = ""
        self.server_full_path = ""
        self.controllers = []  # list of ControllerInfo

    def open(self):
        """Extract .xbk zip and open SQLite database."""
        self._tmpdir = tempfile.mkdtemp(prefix="ebo_trend_")
        with zipfile.ZipFile(self.xbk_path, 'r') as zf:
            zf.extractall(self._tmpdir)
        db_path = os.path.join(self._tmpdir, "Configuration", "configuration.db")
        if not os.path.exists(db_path):
            # Try alternate path
            alt = os.path.join(self._tmpdir, "configuration.db")
            if os.path.exists(alt):
                db_path = alt
            else:
                raise FileNotFoundError(f"configuration.db not found in {self.xbk_path}")
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._parse_metadata()
        self._parse_hierarchy()

    def close(self):
        if self._conn:
            self._conn.close()
        if self._tmpdir and os.path.exists(self._tmpdir):
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _parse_metadata(self):
        """Extract server name and version from DB and filename."""
        cursor = self._conn.cursor()
        # Server name and full path
        cursor.execute("SELECT stringvalue FROM PropertyInstance WHERE name='FullPath' AND objectinstanceid IN (SELECT id FROM ObjectInstance WHERE name='~')")
        row = cursor.fetchone()
        if row and row[0]:
            self.server_full_path = row[0]
            self.server_name = self.server_full_path.strip("/").split("/")[0] if self.server_full_path else ""
        else:
            # Fallback: extract from filename
            basename = os.path.basename(self.xbk_path)
            m = re.match(r'^(.+?)_(\d{8})[_-]', basename)
            if m:
                self.server_name = m.group(1)
            else:
                self.server_name = os.path.splitext(basename)[0].split("_")[0]

        # Version from etc/versions.properties or SoftwareConfig table or filename
        with zipfile.ZipFile(self.xbk_path, 'r') as zf:
            try:
                ver_data = zf.read("etc/versions.properties").decode("utf-8")
                for line in ver_data.splitlines():
                    if line.startswith("dbVersion:"):
                        self.server_version = line.split(":", 1)[1].strip()
                        break
            except KeyError:
                pass

        if not self.server_version:
            cursor.execute("SELECT row, value FROM SoftwareConfig WHERE row=1")
            row = cursor.fetchone()
            if row:
                v = row[1]
                major = v // 100
                minor = (v % 100) // 10
                patch = v % 10
                self.server_version = f"{major}.0.{minor}.{patch}"
            else:
                basename = os.path.basename(self.xbk_path)
                m = re.search(r'(\d+\.\d+\.\d+\.\d+)', basename)
                if m:
                    self.server_version = m.group(1)
                else:
                    self.server_version = "5.0.3.117"

    def _parse_hierarchy(self):
        """Discover controllers and their points."""
        cursor = self._conn.cursor()

        # Find BACnet Interface and RSTP Network IDs
        cursor.execute("SELECT id FROM ObjectInstance WHERE name='BACnet Interface' AND typename='bacnet.Device'")
        bacnet_row = cursor.fetchone()
        if not bacnet_row:
            raise ValueError("BACnet Interface not found in database")
        bacnet_id = bacnet_row[0]

        cursor.execute("SELECT id FROM ObjectInstance WHERE parentid=? AND name='RSTP Network'", (bacnet_id,))
        rstp_row = cursor.fetchone()
        if not rstp_row:
            raise ValueError("RSTP Network not found under BACnet Interface")
        rstp_id = rstp_row[0]

        # Find controllers (non-Nonexist) under RSTP Network
        cursor.execute(
            "SELECT id, typename, name FROM ObjectInstance WHERE parentid=? AND typename NOT LIKE '%Nonexist%'",
            (rstp_id,)
        )
        controller_rows = cursor.fetchall()

        for ctrl_id, ctrl_type, ctrl_name in controller_rows:
            ctrl = ControllerInfo(name=ctrl_name, type_name=ctrl_type)

            # Find Application folder
            cursor.execute(
                "SELECT id FROM ObjectInstance WHERE parentid=? AND typename=?",
                (ctrl_id, APPLICATION_TYPE)
            )
            app_row = cursor.fetchone()
            if not app_row:
                continue
            app_id = app_row[0]

            # Find subfolders (Inputs, Outputs, Values, ValuesRemote, etc.)
            cursor.execute(
                "SELECT id, name FROM ObjectInstance WHERE parentid=? AND typename='system.base.Folder'",
                (app_id,)
            )
            subfolder_rows = cursor.fetchall()

            for sf_id, sf_name in subfolder_rows:
                # Skip folders that don't contain points
                if sf_name in ("Alarms", "Programs", "Trends", "Graphics",
                               "Programming Notes", "Functions", "Schedules"):
                    continue

                # Find points in this subfolder
                cursor.execute(
                    "SELECT id, typename, name FROM ObjectInstance WHERE parentid=?",
                    (sf_id,)
                )
                point_rows = cursor.fetchall()

                for p_id, p_type, p_name in point_rows:
                    # Only include point/value types
                    if not p_type.startswith(POINT_TYPE_PREFIXES):
                        continue

                    point = PointInfo(
                        name=p_name,
                        type_name=p_type,
                        subfolder=sf_name,
                        controller_name=ctrl_name
                    )

                    # Fetch properties
                    self._load_point_properties(cursor, p_id, point)
                    ctrl.points.append(point)

            if ctrl.points:
                self.controllers.append(ctrl)

    def _load_point_properties(self, cursor, point_id, point):
        """Load properties for a point from PropertyInstance."""
        cursor.execute(
            "SELECT name, unitid, realvalue, stringvalue, intvalue FROM PropertyInstance WHERE objectinstanceid=?",
            (point_id,)
        )
        for row in cursor.fetchall():
            name = row[0]
            unitid = row[1]
            realval = row[2]
            strval = row[3]
            intval = row[4]

            if name == "BACnetName":
                point.bacnet_name = strval if strval else point.name
            elif name == "COVIncrement":
                point.cov_unit_id = unitid
                point.cov_value = realval
            elif name == "ForeignAddress":
                # Skip for trend log generation, but store for reference
                point.foreign_address = strval


# ──────────────────────────────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────────────
class PointInfo:
    """Represents a BACnet point or value."""

    __slots__ = (
        "name", "type_name", "subfolder", "controller_name",
        "bacnet_name", "cov_unit_id", "cov_value", "foreign_address",
    )

    def __init__(self, name="", type_name="", subfolder="", controller_name=""):
        self.name = name
        self.type_name = type_name
        self.subfolder = subfolder
        self.controller_name = controller_name
        self.bacnet_name = name
        self.cov_unit_id = None
        self.cov_value = None
        self.foreign_address = None

    @property
    def is_analog(self):
        """True if this is an analog point type."""
        return self.type_name in ANALOG_POINT_TYPES

    @property
    def is_binary(self):
        """True if this is a binary/digital point type."""
        return self.type_name in BINARY_POINT_TYPES

    @property
    def log_interval(self):
        return 30000 if self.is_analog else 0

    @property
    def logging_type(self):
        return 0 if self.is_analog else 1

    @property
    def unit_hex(self):
        """Return unit as hex string like '0x10001'."""
        if self.cov_unit_id is not None:
            # Handle negative unit IDs from signed int storage
            u = self.cov_unit_id
            if u < 0:
                u = u & 0xFFFFFFFF
            return f"0x{u:X}"
        return "0x10001"


class ControllerInfo:
    """Represents a controller with its points."""

    __slots__ = ("name", "type_name", "points")

    def __init__(self, name="", type_name=""):
        self.name = name
        self.type_name = type_name
        self.points = []


# ──────────────────────────────────────────────────────────────────────
# XML Generator
# ──────────────────────────────────────────────────────────────────────
class XmlGenerator:
    """Generates EBO import XML from parsed station data."""

    def __init__(self, db, include_trend_logs=True, include_ext_logs=True,
                 include_trend_charts=True, selected_controllers=None,
                 selected_points=None):
        self.db = db
        self.include_trend_logs = include_trend_logs
        self.include_ext_logs = include_ext_logs
        self.include_trend_charts = include_trend_charts
        self.selected_controllers = selected_controllers  # set of names, or None for all
        self.selected_points = selected_points  # set of (ctrl_name, point_name) tuples, or None

    def generate(self, output_path=None):
        """Generate XML and optionally write to file. Returns (xml_string, stats_dict)."""
        stats = {
            "total_controllers": len(self.db.controllers),
            "total_points": sum(len(c.points) for c in self.db.controllers),
            "trend_logs_created": 0,
            "ext_logs_created": 0,
            "trend_charts_created": 0,
            "controllers_found": [],
        }

        controllers = self.db.controllers
        if self.selected_controllers is not None:
            controllers = [c for c in controllers if c.name in self.selected_controllers]

        # Build XML
        root = ET.Element("ObjectSet")
        root.set("ExportMode", "Special")
        root.set("Note", "TypesFirst")
        root.set("SemanticsFilter", "Special")
        root.set("Version", self.db.server_version)

        # MetaInformation
        meta = ET.SubElement(root, "MetaInformation")
        _add_simple(meta, "ExportMode", "Value", "Special")
        _add_simple(meta, "SemanticsFilter", "Value", "None")
        _add_simple(meta, "RuntimeVersion", "Value", self.db.server_version)
        _add_simple(meta, "SourceVersion", "Value", self.db.server_version)
        _add_simple(meta, "ServerFullPath", "Value", self.db.server_full_path or f"/{self.db.server_name}")

        # Types (empty - just a placeholder)
        ET.SubElement(root, "Types")

        # ExportedObjects
        exported = ET.SubElement(root, "ExportedObjects")

        trend_addr_counter = {}  # per-controller sequential counter

        for ctrl in controllers:
            if not ctrl.points:
                continue
            stats["controllers_found"].append(ctrl.name)

            # Start foreign address from a base per controller
            if ctrl.name not in trend_addr_counter:
                trend_addr_counter[ctrl.name] = 244

            # Create Trends_<Controller> folder
            trends_folder_name = f"Trends_{ctrl.name}"
            folder_oi = ET.SubElement(exported, "OI")
            folder_oi.set("NAME", trends_folder_name)
            folder_oi.set("TYPE", "system.base.Folder")

            if self.include_trend_logs:
                self._add_trend_logs(folder_oi, ctrl, trend_addr_counter, stats)

            if self.include_ext_logs:
                self._add_ext_logs(folder_oi, ctrl, stats)

            if self.include_trend_charts:
                self._add_trend_charts(folder_oi, ctrl, stats)

        # Pretty-print XML
        rough_string = ET.tostring(root, encoding="unicode")
        dom = minidom.parseString(rough_string)
        xml_str = dom.toprettyxml(indent="  ", encoding=None)

        # Post-process: fix self-closing tags - minidom uses <foo/> but EBO expects <foo />
        # Actually minidom outputs <foo/> which is fine.

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xml_str)

        return xml_str, stats

    def _add_trend_logs(self, parent_oi, ctrl, addr_counter, stats):
        """Add BACnet Trend Log entries."""
        points = [p for p in ctrl.points
                  if self.selected_points is None or (ctrl.name, p.name) in self.selected_points]
        for point in points:
            addr = addr_counter[ctrl.name]
            trend_name = self._make_trend_name(point)
            bacnet_name = self._truncate_bacnet_name(trend_name)

            addr_counter[ctrl.name] += 1
            stats["trend_logs_created"] += 1

            tl_oi = ET.SubElement(parent_oi, "OI")
            tl_oi.set("NAME", trend_name)
            tl_oi.set("TYPE", "bacnet.mpx.TrendLog")

            # BACnetName
            pi = ET.SubElement(tl_oi, "PI")
            pi.set("Name", "BACnetName")
            pi.set("Value", bacnet_name)

            # ForeignAddress
            pi = ET.SubElement(tl_oi, "PI")
            pi.set("Name", "ForeignAddress")
            pi.set("Value", f"<trend-log,     {addr}>")

            # LogArray (unit only, for analog points)
            if point.is_analog:
                pi = ET.SubElement(tl_oi, "PI")
                pi.set("Name", "LogArray")
                pi.set("Unit", point.unit_hex)

            # LogDeviceObjectProperty with Reference
            ref_path = f"~/BACnet Interface/RSTP Network/{ctrl.name}/Application/{point.subfolder}/{point.name}"
            pi = ET.SubElement(tl_oi, "PI")
            pi.set("Name", "LogDeviceObjectProperty")
            pi.set("Unit", point.unit_hex)
            ref = ET.SubElement(pi, "Reference")
            ref.set("DeltaFilter", "0")
            ref.set("Locked", "1")
            ref.set("Object", ref_path)
            ref.set("Property", "Value")
            ref.set("Retransmit", "0")
            ref.set("TransferRate", "10")

            # LogInterval
            pi = ET.SubElement(tl_oi, "PI")
            pi.set("Name", "LogInterval")
            pi.set("Value", str(point.log_interval))

            # LoggingType
            pi = ET.SubElement(tl_oi, "PI")
            pi.set("Name", "LoggingType")
            pi.set("Value", str(point.logging_type))

            # Start_Time (null)
            pi = ET.SubElement(tl_oi, "PI")
            pi.set("Name", "Start_Time")
            pi.set("Null", "1")

            # StopTime (null)
            pi = ET.SubElement(tl_oi, "PI")
            pi.set("Name", "StopTime")
            pi.set("Null", "1")

    def _add_ext_logs(self, parent_oi, ctrl, stats):
        """Add Extended Trend Log entries in Ext Logs subfolder."""
        ext_folder = ET.SubElement(parent_oi, "OI")
        ext_folder.set("NAME", "Ext Logs")
        ext_folder.set("TYPE", "system.base.Folder")

        points = [p for p in ctrl.points
                  if self.selected_points is None or (ctrl.name, p.name) in self.selected_points]
        for point in points:
            trend_name = self._make_trend_name(point)
            ext_name = f"{trend_name} - Extended Trend Log"
            stats["ext_logs_created"] += 1

            et_oi = ET.SubElement(ext_folder, "OI")
            et_oi.set("NAME", ext_name)
            et_oi.set("TYPE", "trend.ETLog")

            # ForceReadTimeout
            pi = ET.SubElement(et_oi, "PI")
            pi.set("Name", "ForceReadTimeout")
            pi.set("Value", "900000")

            # LogArray
            pi = ET.SubElement(et_oi, "PI")
            pi.set("Name", "LogArray")
            pi.set("Unit", point.unit_hex)

            # Meter timestamps (use current time placeholder)
            now_hex = f"Tx{datetime.now().strftime('%Y%m%d%H%M%S')}"
            for met in ("MeterEndTime", "MeterStartTime", "MeterTime"):
                pi = ET.SubElement(et_oi, "PI")
                pi.set("Name", met)
                pi.set("Value", now_hex)

            # MonitoredLog (references ../../<trend name>)
            pi = ET.SubElement(et_oi, "PI")
            pi.set("Name", "MonitoredLog")
            ref = ET.SubElement(pi, "Reference")
            ref.set("DeltaFilter", "0")
            ref.set("Object", f"../../{trend_name}")
            ref.set("Retransmit", "0")
            ref.set("TransferRate", "10")

            # SmartLogEnabled
            pi = ET.SubElement(et_oi, "PI")
            pi.set("Name", "SmartLogEnabled")
            pi.set("Value", "0")

            # LastTransferredTimestamp
            pi = ET.SubElement(et_oi, "PI")
            pi.set("Name", "LastTransferredTimestamp")
            pi.set("Value", "Tx0000000000000000")

    def _add_trend_charts(self, parent_oi, ctrl, stats):
        """Add Trend Chart views in Trend Charts subfolder."""
        charts_folder = ET.SubElement(parent_oi, "OI")
        charts_folder.set("NAME", "Trend Charts")
        charts_folder.set("TYPE", "system.base.Folder")

        points = [p for p in ctrl.points
                  if self.selected_points is None or (ctrl.name, p.name) in self.selected_points]
        for point in points:
            trend_name = self._make_trend_name(point)
            stats["trend_charts_created"] += 1

            chart_oi = ET.SubElement(charts_folder, "OI")
            chart_oi.set("NAME", point.name)
            chart_oi.set("TYPE", "trend.view.GraphicalTrendView")

            # DisplayStartTime
            now_hex = f"Tx{datetime.now().strftime('%Y%m%d%H%M%S')}29"
            pi = ET.SubElement(chart_oi, "PI")
            pi.set("Name", "DisplayStartTime")
            pi.set("Value", now_hex)

            # YAxisMaximum1
            pi = ET.SubElement(chart_oi, "PI")
            pi.set("Name", "YAxisMaximum1")
            pi.set("Value", "0.10000000000000001")

            # YAxisMaximum2
            pi = ET.SubElement(chart_oi, "PI")
            pi.set("Name", "YAxisMaximum2")
            pi.set("Value", "0.10000000000000001")

            # TrendLogSeriesProperties (hidden)
            series_oi = ET.SubElement(chart_oi, "OI")
            series_oi.set("NAME", trend_name)
            series_oi.set("TYPE", "trend.view.TrendLogSeriesProperties")
            series_oi.set("hidden", "1")

            # Color
            pi = ET.SubElement(series_oi, "PI")
            pi.set("Name", "Color")
            pi.set("Value", "-11179217")

            # CustomCalculationPeriodStart
            pi = ET.SubElement(series_oi, "PI")
            pi.set("Name", "CustomCalculationPeriodStart")
            pi.set("Value", "Tx0000000000000000")

            # DisplayLog
            pi = ET.SubElement(series_oi, "PI")
            pi.set("Name", "DisplayLog")
            ref = ET.SubElement(pi, "Reference")
            ref.set("DeltaFilter", "0")
            ref.set("Object", f"../../../{trend_name}")
            ref.set("Retransmit", "0")
            ref.set("TransferRate", "10")

    @staticmethod
    def _make_trend_name(point):
        """Create the trend log name from a point name."""
        return f"{point.name} - BACnet Trend Log"

    @staticmethod
    def _truncate_bacnet_name(name):
        """Truncate BACnetName to 20 chars, replacing last chars with ~ if needed."""
        if len(name) <= 20:
            return name
        # Truncate to 20 chars, replacing the 20th with ~
        return name[:19] + "~"


def _add_simple(parent, tag, attr_name, attr_value):
    """Add a simple element with one attribute."""
    el = ET.SubElement(parent, tag)
    el.set(attr_name, attr_value)
    return el


# ──────────────────────────────────────────────────────────────────────
# Tkinter GUI (fallback to CLI if tkinter unavailable)
# ──────────────────────────────────────────────────────────────────────
if tk is not None:
    class TrendBuilderGUI:
        """Dark-themed tkinter GUI for the EBO Trend Builder."""

        # Colors
        BG_DARK = "#0D1B2A"
        BG_MED = "#1B2838"
        BG_INPUT = "#243447"
        FG_WHITE = "#E0E6ED"
        FG_GREEN = "#00E676"
        FG_RED = "#FF5252"
        SELECT_BG = "#2A3F54"
        HIGHLIGHT = "#00E676"

        def __init__(self):
            self.root = tk.Tk()
            self.root.title("EBO Trend Builder")
            self.root.geometry("900x700")
            self.root.configure(bg=self.BG_DARK)
            self.root.minsize(750, 600)

            self.db = None
            self.xbk_path = None
            self.controller_vars = {}  # name -> tk.BooleanVar
            self.controller_tree_refs = {}  # name -> tree item ID
            self.point_vars = {}  # (ctrl_name, point_name) -> tk.BooleanVar
            self.point_tree_refs = {}  # tree item ID -> (ctrl_name, point_name)

            self._setup_styles()
            self._build_ui()

        def _setup_styles(self):
            style = ttk.Style()
            style.theme_use("clam")

            style.configure("TFrame", background=self.BG_DARK)
            style.configure("TLabel", background=self.BG_DARK, foreground=self.FG_WHITE)
            style.configure("TButton", background=self.SELECT_BG, foreground=self.FG_WHITE,
                            borderwidth=1, focusthickness=0, font=("Segoe UI", 10))
            style.map("TButton",
                      background=[("active", self.HIGHLIGHT), ("pressed", "#009624")],
                      foreground=[("active", "#000000")])

            style.configure("Green.TButton", background="#009624", foreground=self.FG_WHITE,
                            font=("Segoe UI", 10, "bold"))
            style.map("Green.TButton",
                      background=[("active", self.HIGHLIGHT), ("pressed", "#009624")],
                      foreground=[("active", "#000000")])

            style.configure("Treeview", background=self.BG_INPUT, foreground=self.FG_WHITE,
                            fieldbackground=self.BG_INPUT, borderwidth=0,
                            font=("Segoe UI", 9))
            style.configure("Treeview.Heading", background=self.BG_MED, foreground=self.FG_WHITE,
                            font=("Segoe UI", 9, "bold"))
            style.map("Treeview", background=[("selected", self.SELECT_BG)])

            style.configure("TCheckbutton", background=self.BG_DARK, foreground=self.FG_WHITE,
                            font=("Segoe UI", 10))
            style.map("TCheckbutton",
                      background=[("active", self.BG_DARK)],
                      foreground=[("active", self.FG_WHITE)])

            style.configure("Green.TLabel", background=self.BG_DARK, foreground=self.FG_GREEN,
                            font=("Segoe UI", 10))

            style.configure("Accent.TLabelframe", background=self.BG_MED, foreground=self.FG_GREEN)
            style.configure("Accent.TLabelframe.Label", background=self.BG_MED, foreground=self.FG_GREEN,
                            font=("Segoe UI", 10, "bold"))

        def _build_ui(self):
            # Header
            header = tk.Frame(self.root, bg=self.BG_DARK)
            header.pack(fill="x", padx=15, pady=(15, 5))
            title = tk.Label(header, text="EBO Trend Builder",
                             font=("Segoe UI", 18, "bold"),
                             fg=self.FG_GREEN, bg=self.BG_DARK)
            title.pack(side="left")
            subtitle = tk.Label(header, text="Generate BACnet trends from .xbk backups",
                                font=("Segoe UI", 9),
                                fg="#7A8B9B", bg=self.BG_DARK)
            subtitle.pack(side="left", padx=(12, 0))

            # Separator
            sep = tk.Frame(self.root, height=1, bg=self.SELECT_BG)
            sep.pack(fill="x", padx=15, pady=(0, 10))

            # File selection
            file_frame = tk.Frame(self.root, bg=self.BG_DARK)
            file_frame.pack(fill="x", padx=15, pady=(0, 8))
            tk.Label(file_frame, text="XBK Backup File:",
                     font=("Segoe UI", 10),
                     fg=self.FG_WHITE, bg=self.BG_DARK).pack(side="left")
            self.file_path_var = tk.StringVar()
            self.file_entry = tk.Entry(file_frame, textvariable=self.file_path_var,
                                       font=("Segoe UI", 9),
                                       bg=self.BG_INPUT, fg=self.FG_WHITE,
                                       insertbackground=self.FG_WHITE,
                                       relief="flat", bd=3)
            self.file_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
            browse_btn = tk.Button(file_frame, text="Browse...",
                                   font=("Segoe UI", 9),
                                   bg=self.SELECT_BG, fg=self.FG_WHITE,
                                   relief="flat", padx=12, pady=2,
                                   activebackground=self.HIGHLIGHT,
                                   cursor="hand2",
                                   command=self._browse_file)
            browse_btn.pack(side="left")
            load_btn = tk.Button(file_frame, text="Load",
                                 font=("Segoe UI", 9, "bold"),
                                 bg="#009624", fg=self.FG_WHITE,
                                 relief="flat", padx=16, pady=2,
                                 activebackground=self.HIGHLIGHT,
                                 cursor="hand2",
                                 command=self._load_backup)
            load_btn.pack(side="left", padx=(8, 0))

            # Server info
            self.server_info_label = tk.Label(self.root, text="",
                                              font=("Segoe UI", 9),
                                              fg=self.FG_GREEN, bg=self.BG_DARK)
            self.server_info_label.pack(fill="x", padx=15, pady=(0, 5))

            # Main content area with tree + controls
            main_frame = tk.Frame(self.root, bg=self.BG_DARK)
            main_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

            # Left: controller tree
            tree_frame = tk.LabelFrame(main_frame, text="Controllers & Points",
                                       font=("Segoe UI", 10, "bold"),
                                       fg=self.FG_GREEN, bg=self.BG_MED,
                                       relief="flat", bd=1)
            tree_frame.pack(side="left", fill="both", expand=True)

            # Select/Deselect all buttons
            select_frame = tk.Frame(tree_frame, bg=self.BG_MED)
            select_frame.pack(fill="x", padx=5, pady=(5, 0))
            tk.Button(select_frame, text="Select All",
                      font=("Segoe UI", 8),
                      bg=self.SELECT_BG, fg=self.FG_WHITE,
                      relief="flat", padx=8,
                      cursor="hand2",
                      command=self._select_all).pack(side="left", padx=(0, 5))
            tk.Button(select_frame, text="Deselect All",
                      font=("Segoe UI", 8),
                      bg=self.SELECT_BG, fg=self.FG_WHITE,
                      relief="flat", padx=8,
                      cursor="hand2",
                      command=self._deselect_all).pack(side="left")

            # Treeview
            tree_container = tk.Frame(tree_frame, bg=self.BG_INPUT)
            tree_container.pack(fill="both", expand=True, padx=5, pady=(5, 5))

            self.tree = ttk.Treeview(tree_container, columns=("points", "selected"),
                                     show="tree headings",
                                     height=15)
            self.tree.heading("#0", text="Controller / Point")
            self.tree.heading("points", text="Points")
            self.tree.heading("selected", text="")
            self.tree.column("#0", minwidth=220, width=280)
            self.tree.column("points", minwidth=60, width=80, anchor="center")
            self.tree.column("selected", minwidth=50, width=50, anchor="center")

            vsb = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=vsb.set)
            self.tree.pack(side="left", fill="both", expand=True)
            vsb.pack(side="right", fill="y")

            self.tree.bind("<ButtonRelease-1>", self._on_tree_click)

            # Right: trend options
            options_frame = tk.LabelFrame(main_frame, text="Trend Options",
                                          font=("Segoe UI", 10, "bold"),
                                          fg=self.FG_GREEN, bg=self.BG_MED,
                                          relief="flat", bd=1, width=200)
            options_frame.pack(side="right", fill="y", padx=(10, 0))
            options_frame.pack_propagate(False)

            pad_opts = {"padx": 12, "pady": 3, "anchor": "w"}

            tk.Label(options_frame, text="Generate:",
                     font=("Segoe UI", 10),
                     fg=self.FG_WHITE, bg=self.BG_MED).pack(**pad_opts)

            self.trend_log_var = tk.BooleanVar(value=True)
            tk.Checkbutton(options_frame, text="BACnet Trend Logs",
                           variable=self.trend_log_var,
                           font=("Segoe UI", 9),
                           fg=self.FG_WHITE, bg=self.BG_MED,
                           selectcolor=self.BG_DARK,
                           activebackground=self.BG_MED,
                           activeforeground=self.FG_GREEN).pack(**pad_opts)

            self.ext_log_var = tk.BooleanVar(value=True)
            tk.Checkbutton(options_frame, text="Extended Trend Logs",
                           variable=self.ext_log_var,
                           font=("Segoe UI", 9),
                           fg=self.FG_WHITE, bg=self.BG_MED,
                           selectcolor=self.BG_DARK,
                           activebackground=self.BG_MED,
                           activeforeground=self.FG_GREEN).pack(**pad_opts)

            self.chart_var = tk.BooleanVar(value=True)
            tk.Checkbutton(options_frame, text="Trend Charts",
                           variable=self.chart_var,
                           font=("Segoe UI", 9),
                           fg=self.FG_WHITE, bg=self.BG_MED,
                           selectcolor=self.BG_DARK,
                           activebackground=self.BG_MED,
                           activeforeground=self.FG_GREEN).pack(**pad_opts)

            # Separator
            sep2 = tk.Frame(options_frame, height=1, bg=self.SELECT_BG)
            sep2.pack(fill="x", padx=12, pady=10)

            # Export buttons
            self.export_group_btn = tk.Button(options_frame, text="Export XML - Group",
                                        font=("Segoe UI", 10, "bold"),
                                        bg="#009624", fg=self.FG_WHITE,
                                        relief="flat", padx=20, pady=6,
                                        activebackground=self.HIGHLIGHT,
                                        cursor="hand2",
                                        state="disabled",
                                        command=self._export_group)
            self.export_group_btn.pack(padx=12, pady=(0, 5), fill="x")

            self.export_single_btn = tk.Button(options_frame, text="Export XML - Single",
                                         font=("Segoe UI", 10, "bold"),
                                         bg=self.SELECT_BG, fg=self.FG_WHITE,
                                         relief="flat", padx=20, pady=6,
                                         activebackground=self.HIGHLIGHT,
                                         cursor="hand2",
                                         state="disabled",
                                         command=self._export_single)
            self.export_single_btn.pack(padx=12, pady=(0, 10), fill="x")

            # Status
            status_frame = tk.Frame(self.root, bg=self.BG_DARK)
            status_frame.pack(fill="x", padx=15, pady=(0, 12))

            self.status_text = tk.Text(status_frame, height=6,
                                       font=("Consolas", 9),
                                       bg=self.BG_INPUT, fg=self.FG_WHITE,
                                       relief="flat", bd=3,
                                       state="disabled")
            self.status_text.pack(fill="both")

        def _browse_file(self):
            path = filedialog.askopenfilename(
                title="Select EBO Backup File",
                filetypes=[("XBK Backup", "*.xbk"), ("All Files", "*.*")]
            )
            if path:
                self.file_path_var.set(path)

        def _load_backup(self):
            path = self.file_path_var.get()
            if not path:
                messagebox.showwarning("No File", "Please select an .xbk backup file first.")
                return
            if not os.path.exists(path):
                messagebox.showerror("File Not Found", f"File not found:\n{path}")
                return

            # Clear existing data
            for item in self.tree.get_children():
                self.tree.delete(item)
            self.controller_vars.clear()
            self.controller_tree_refs.clear()
            self.point_vars.clear()
            self.point_tree_refs.clear()
            self.server_info_label.config(text="")
            self._set_status("Loading backup...", color="#FFA726")

            # Close previous DB if any
            if self.db:
                self.db.close()

            self.root.config(cursor="watch")
            self.root.update()

            try:
                self.db = XbkDatabase(path)
                self.db.open()
                self.xbk_path = path
                self._populate_tree()
                self.export_group_btn.config(state="normal")
                self.export_single_btn.config(state="normal")

                info = f"Server: {self.db.server_name}  |  Version: {self.db.server_version}  |  " \
                       f"Controllers: {len(self.db.controllers)}  |  Total Points: {sum(len(c.points) for c in self.db.controllers)}"
                self.server_info_label.config(text=info)
                self._set_status(f"Loaded successfully. Found {len(self.db.controllers)} controllers.",
                                 color=self.FG_GREEN)

            except Exception as e:
                self._set_status(f"Error loading backup: {e}", color=self.FG_RED)
                messagebox.showerror("Load Error", str(e))
            finally:
                self.root.config(cursor="")

        def _populate_tree(self):
            for ctrl in self.db.controllers:
                var = tk.BooleanVar(value=True)
                self.controller_vars[ctrl.name] = var

                item = self.tree.insert("", "end", text=f"  {ctrl.name}",
                                        values=(len(ctrl.points), "[X]" if var.get() else "[ ]"),
                                        tags=("controller",))
                self.controller_tree_refs[ctrl.name] = item

                # Add child points
                for pt in ctrl.points:
                    pt_var = tk.BooleanVar(value=True)
                    self.point_vars[(ctrl.name, pt.name)] = pt_var
                    pt_item = self.tree.insert(item, "end", text=f"    {pt.name}",
                                     values=(pt.type_name.split(".")[-1], "[X]"),
                                     tags=("point",))
                    self.point_tree_refs[pt_item] = (ctrl.name, pt.name)

        def _on_tree_click(self, event):
            """Toggle controller or point checkbox on click."""
            item = self.tree.identify_row(event.y)
            if not item:
                return
            # Check if it is a controller row
            for ctrl_name, ref_id in self.controller_tree_refs.items():
                if ref_id == item:
                    new_val = not self.controller_vars[ctrl_name].get()
                    self.controller_vars[ctrl_name].set(new_val)
                    self.tree.set(item, "selected", "[X]" if new_val else "[ ]")
                    # Toggle all child points to match
                    for child in self.tree.get_children(item):
                        if child in self.point_tree_refs:
                            key = self.point_tree_refs[child]
                            self.point_vars[key].set(new_val)
                            self.tree.set(child, "selected", "[X]" if new_val else "[ ]")
                    return
            # Check if it is a point row
            if item in self.point_tree_refs:
                ctrl_name, pt_name = self.point_tree_refs[item]
                key = (ctrl_name, pt_name)
                var = self.point_vars[key]
                var.set(not var.get())
                self.tree.set(item, "selected", "[X]" if var.get() else "[ ]")

        def _select_all(self):
            for ctrl_name, var in self.controller_vars.items():
                var.set(True)
                ref = self.controller_tree_refs.get(ctrl_name)
                if ref:
                    self.tree.set(ref, "selected", "[X]")
                    for child in self.tree.get_children(ref):
                        if child in self.point_tree_refs:
                            key = self.point_tree_refs[child]
                            self.point_vars[key].set(True)
                            self.tree.set(child, "selected", "[X]")

        def _deselect_all(self):
            for ctrl_name, var in self.controller_vars.items():
                var.set(False)
                ref = self.controller_tree_refs.get(ctrl_name)
                if ref:
                    self.tree.set(ref, "selected", "[ ]")
                    for child in self.tree.get_children(ref):
                        if child in self.point_tree_refs:
                            key = self.point_tree_refs[child]
                            self.point_vars[key].set(False)
                            self.tree.set(child, "selected", "[ ]")

        def _build_report(self, output_path, stats):
            report = []
            report.append(f"──────────────────────────────────────────────────")
            report.append("  EBO TREND BUILDER - EXPORT COMPLETE")
            report.append(f"──────────────────────────────────────────────────")
            report.append(f"  Output File:  {output_path}")
            report.append(f"  Server:       {self.db.server_name}  v{self.db.server_version}")
            report.append(f"  Controllers:  {len(stats['controllers_found'])} / {stats['total_controllers']}")
            report.append(f"  Total Points: {stats['total_points']}")
            report.append(f"──────────────────────────────────────────────────")
            report.append(f"  Trends Created:")
            report.append(f"    BACnet Trend Logs:     {stats['trend_logs_created']}")
            report.append(f"    Extended Trend Logs:   {stats['ext_logs_created']}")
            report.append(f"    Trend Charts:          {stats['trend_charts_created']}")
            report.append(f"─────────────────────────────")
            report.append(f"    TOTAL Objects:         {stats['trend_logs_created'] + stats['ext_logs_created'] + stats['trend_charts_created']}")
            report.append(f"──────────────────────────────────────────────────")
            report.append(f"  File size: {os.path.getsize(output_path):,} bytes")
            report.append(f"──────────────────────────────────────────────────")
            return "\n".join(report)

        def _export_group(self):
            """Export all selected controllers into a single XML file."""
            if not self.db:
                messagebox.showwarning("No Data", "Load a backup file first.")
                return

            selected = set(name for name, var in self.controller_vars.items() if var.get())
            selected_points = set()
            for (cname, pname), var in self.point_vars.items():
                if var.get() and cname in selected:
                    selected_points.add((cname, pname))
            if not selected:
                messagebox.showwarning("No Selection", "Select at least one controller.")
                return

            default_name = f"trends_{self.db.server_name}.xml"
            output_path = filedialog.asksaveasfilename(
                title="Save Group XML Export",
                defaultextension=".xml",
                initialfile=default_name,
                filetypes=[("XML Files", "*.xml"), ("All Files", "*.*")]
            )
            if not output_path:
                return

            self.root.config(cursor="watch")
            self.root.update()

            try:
                gen = XmlGenerator(
                    db=self.db,
                    include_trend_logs=self.trend_log_var.get(),
                    include_ext_logs=self.ext_log_var.get(),
                    include_trend_charts=self.chart_var.get(),
                    selected_controllers=selected,
                    selected_points=selected_points,
                )
                xml_str, stats = gen.generate(output_path)
                report_text = self._build_report(output_path, stats)
                self._set_status(report_text, color=self.FG_GREEN)
            except Exception as e:
                self._set_status(f"Export error: {e}", color=self.FG_RED)
                messagebox.showerror("Export Error", str(e))
            finally:
                self.root.config(cursor="")

        def _export_single(self):
            """Export one XML file per selected controller into a chosen directory."""
            if not self.db:
                messagebox.showwarning("No Data", "Load a backup file first.")
                return

            selected = set(name for name, var in self.controller_vars.items() if var.get())
            if not selected:
                messagebox.showwarning("No Selection", "Select at least one controller.")
                return

            output_dir = filedialog.askdirectory(
                title="Select Export Directory for Individual Files"
            )
            if not output_dir:
                return

            self.root.config(cursor="watch")
            self.root.update()

            total_tl = 0
            total_el = 0
            total_tc = 0
            created_files = []

            try:
                for ctrl_name in sorted(selected):
                    safe_name = ctrl_name.replace("(", "").replace(")", "").replace(" ", "_")
                    filename = f"trends_{self.db.server_name}_{safe_name}.xml"
                    filepath = os.path.join(output_dir, filename)

                    pts_for_ctrl = set()
                    for (cname, pname), var in self.point_vars.items():
                        if var.get() and cname == ctrl_name:
                            pts_for_ctrl.add((cname, pname))

                    gen = XmlGenerator(
                        db=self.db,
                        include_trend_logs=self.trend_log_var.get(),
                        include_ext_logs=self.ext_log_var.get(),
                        include_trend_charts=self.chart_var.get(),
                        selected_controllers={ctrl_name},
                        selected_points=pts_for_ctrl,
                    )
                    xml_str, stats = gen.generate(filepath)
                    total_tl += stats["trend_logs_created"]
                    total_el += stats["ext_logs_created"]
                    total_tc += stats["trend_charts_created"]
                    created_files.append(filepath)

                report = []
                report.append(f"──────────────────────────────────────────────────")
                report.append("  EBO TREND BUILDER - SINGLE EXPORT COMPLETE")
                report.append(f"──────────────────────────────────────────────────")
                report.append(f"  Export Directory:  {output_dir}")
                report.append(f"  Server:            {self.db.server_name}  v{self.db.server_version}")
                report.append(f"  Files Created:     {len(created_files)} / {len(selected)} selected")
                report.append(f"──────────────────────────────────────────────────")
                report.append(f"  Trends Created:")
                report.append(f"    BACnet Trend Logs:     {total_tl}")
                report.append(f"    Extended Trend Logs:   {total_el}")
                report.append(f"    Trend Charts:          {total_tc}")
                report.append(f"─────────────────────────────")
                report.append(f"    TOTAL Objects:         {total_tl + total_el + total_tc}")
                report.append(f"──────────────────────────────────────────────────")
                report.append("  Files:")
                for f in created_files:
                    size = os.path.getsize(f)
                    report.append(f"    {os.path.basename(f)}  ({size:,} bytes)")
                report.append(f"──────────────────────────────────────────────────")
                self._set_status("\n".join(report), color=self.FG_GREEN)

            except Exception as e:
                self._set_status(f"Export error: {e}", color=self.FG_RED)
                messagebox.showerror("Export Error", str(e))
            finally:
                self.root.config(cursor="")
        def _set_status(self, text, color=None):
            self.status_text.config(state="normal")
            self.status_text.delete("1.0", tk.END)
            if color:
                self.status_text.tag_configure("color", foreground=color)
                self.status_text.insert("1.0", text, "color")
            else:
                self.status_text.insert("1.0", text)
            self.status_text.config(state="disabled")

        def run(self):
            self.root.mainloop()

        def close(self):
            if self.db:
                self.db.close()
            self.root.destroy()


# ──────────────────────────────────────────────────────────────────────
# CLI Mode (when tkinter is not available)
# ──────────────────────────────────────────────────────────────────────
def run_cli():
    """CLI fallback when tkinter is not available."""
    import argparse

    parser = argparse.ArgumentParser(description="EBO Trend Builder - Generate BACnet trends from .xbk backups")
    parser.add_argument("xbk_file", nargs="?", help="Path to .xbk backup file")
    parser.add_argument("-o", "--output", default=None, help="Output XML file path")
    parser.add_argument("--no-trend-logs", action="store_false", dest="trend_logs", default=True,
                        help="Skip BACnet Trend Log generation")
    parser.add_argument("--no-ext-logs", action="store_false", dest="ext_logs", default=True,
                        help="Skip Extended Trend Log generation")
    parser.add_argument("--no-charts", action="store_false", dest="charts", default=True,
                        help="Skip Trend Chart generation")
    parser.add_argument("-c", "--controllers", nargs="*", default=None,
                        help="Only process specified controllers")

    args = parser.parse_args()

    if not args.xbk_file:
        parser.print_help()
        print("\nError: No .xbk file specified.")
        sys.exit(1)

    if not os.path.exists(args.xbk_file):
        print(f"Error: File not found: {args.xbk_file}")
        sys.exit(1)

    output_path = args.output
    if not output_path:
        basename = os.path.basename(args.xbk_file)
        name = os.path.splitext(basename)[0].split("_")[0]
        output_path = f"trends_{name}.xml"

    try:
        db = XbkDatabase(args.xbk_file)
        db.open()
    except Exception as e:
        print(f"Error opening backup: {e}")
        sys.exit(1)

    # If --controllers specified, validate names
    selected = set(args.controllers) if args.controllers else None
    if selected is not None:
        ctrl_names = {c.name for c in db.controllers}
        unknown = selected - ctrl_names
        if unknown:
            print(f"Warning: Unknown controllers: {', '.join(sorted(unknown))}")
            print(f"Available: {', '.join(sorted(ctrl_names))}")

    print(f"Server: {db.server_name}  v{db.server_version}")
    print(f"Found {len(db.controllers)} controllers, {sum(len(c.points) for c in db.controllers)} points")

    gen = XmlGenerator(
        db=db,
        include_trend_logs=args.trend_logs,
        include_ext_logs=args.ext_logs,
        include_trend_charts=args.charts,
        selected_controllers=selected,
    )

    print(f"Generating XML...")
    xml_str, stats = gen.generate(output_path)
    print(f"Written to: {output_path}")
    print(f"  Trend Logs: {stats['trend_logs_created']}")
    print(f"  Ext Logs:   {stats['ext_logs_created']}")
    print(f"  Charts:     {stats['trend_charts_created']}")
    print(f"  Total:      {stats['trend_logs_created'] + stats['ext_logs_created'] + stats['trend_charts_created']}")

    db.close()


# ──────────────────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if tk is not None and len(sys.argv) <= 1:
        app = TrendBuilderGUI()
        try:
            app.run()
        finally:
            app.close()
    else:
        run_cli()
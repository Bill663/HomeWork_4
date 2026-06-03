from __future__ import annotations

import hashlib
import io
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "weeklysum.sqlite"


st.set_page_config(
    page_title="WeeklySum",
    page_icon="WS",
    layout="wide",
    initial_sidebar_state="expanded",
)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def connect_db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                report_date TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                batch TEXT,
                source TEXT,
                pph_hours REAL,
                total_production REAL,
                working_people REAL,
                average_pph REAL,
                ended_groups REAL
            );

            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
                report_date TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                batch TEXT,
                group_name TEXT,
                group_ids TEXT,
                people REAL,
                work_status TEXT,
                start_time TEXT,
                pph_hours REAL,
                production REAL,
                production_text TEXT,
                pph REAL,
                pph_text TEXT,
                note TEXT,
                production_status TEXT
            );
            """
        )


def get_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def require_password() -> bool:
    password = get_secret("APP_PASSWORD", "")
    if not password:
        return True
    if st.session_state.get("authenticated"):
        return True
    st.title("WeeklySum")
    entered = st.text_input("Password", type="password")
    if st.button("Enter", type="primary"):
        if entered == password:
            st.session_state.authenticated = True
            st.rerun()
        st.error("Incorrect password.")
    return False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def read_xlsx_rows(data: bytes) -> list[list[Any]]:
    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    sheet = workbook.active
    rows: list[list[Any]] = []
    for row in sheet.iter_rows(values_only=True):
        values = [clean_cell(value) for value in row]
        while values and values[-1] == "":
            values.pop()
        rows.append(values)
    return rows


def read_xml_spreadsheet_rows(data: bytes) -> list[list[Any]]:
    text = data.decode("utf-8-sig", errors="replace")
    root = ET.fromstring(text)
    ns = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}
    rows: list[list[Any]] = []
    for row in root.findall(".//ss:Worksheet/ss:Table/ss:Row", ns):
        values: list[Any] = []
        for cell in row.findall("ss:Cell", ns):
            index = cell.attrib.get("{urn:schemas-microsoft-com:office:spreadsheet}Index")
            if index and index.isdigit():
                while len(values) < int(index) - 1:
                    values.append("")
            data_node = cell.find("ss:Data", ns)
            values.append(data_node.text if data_node is not None and data_node.text is not None else "")
        while values and values[-1] == "":
            values.pop()
        rows.append(values)
    return rows


def read_workbook_rows(file_name: str, data: bytes) -> list[list[Any]]:
    if data[:2] == b"PK":
        return read_xlsx_rows(data)
    return read_xml_spreadsheet_rows(data)


def normalized_row(row: list[Any]) -> list[str]:
    return [str(value or "").strip().lower() for value in row]


def find_header(rows: list[list[Any]], required: list[str]) -> int:
    for index, row in enumerate(rows):
        normalized = normalized_row(row)
        if all(any(req in cell for cell in normalized) for req in required):
            return index
    return -1


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.upper() == "DNE":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y, %I:%M:%S %p",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def text_at(row: list[Any], index: int) -> str:
    return str(row[index] if index < len(row) else "").strip()


def number_at(row: list[Any], index: int) -> float | None:
    return parse_number(text_at(row, index))


def build_index_map(header: list[Any]) -> dict[str, int]:
    return {str(name or "").strip().lower(): index for index, name in enumerate(header)}


def first_index(index_map: dict[str, int], names: list[str], fallback: int) -> int:
    for name in names:
        key = name.lower()
        if key in index_map:
            return index_map[key]
    return fallback


def parse_pph_report(file_name: str, data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = read_workbook_rows(file_name, data)
    summary_header = find_header(rows, ["time", "batch", "total production", "average pph"])
    group_header = find_header(rows, ["group", "group id", "production", "pph"])
    if summary_header < 0 or group_header < 0:
        raise ValueError("Could not find PPH summary and group sections in this workbook.")

    snapshots: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    summary_map = build_index_map(rows[summary_header])
    group_map = build_index_map(rows[group_header])

    idx_time = first_index(summary_map, ["time"], 0)
    idx_batch = first_index(summary_map, ["batch"], 1)
    idx_source = first_index(summary_map, ["source"], 2)
    idx_hours = first_index(summary_map, ["pph hours"], 3)
    idx_total = first_index(summary_map, ["total production"], 4)
    idx_people = first_index(summary_map, ["working people"], 5)
    idx_avg = first_index(summary_map, ["average pph"], 6)
    idx_ended = first_index(summary_map, ["ended groups"], 7)

    file_hash = sha256_bytes(data)
    uploaded_at = datetime.now().isoformat(timespec="seconds")

    for row in rows[summary_header + 1 : group_header]:
        if not any(str(cell or "").strip() for cell in row):
            continue
        collected_at = parse_datetime_value(text_at(row, idx_time))
        if not collected_at:
            continue
        batch = text_at(row, idx_batch)
        snapshot_id = sha256_bytes(f"{file_hash}|{collected_at.isoformat()}|{batch}".encode("utf-8"))
        snapshots.append(
            {
                "snapshot_id": snapshot_id,
                "file_name": file_name,
                "file_hash": file_hash,
                "uploaded_at": uploaded_at,
                "report_date": collected_at.date().isoformat(),
                "collected_at": collected_at.isoformat(sep=" ", timespec="seconds"),
                "batch": batch,
                "source": text_at(row, idx_source),
                "pph_hours": number_at(row, idx_hours),
                "total_production": number_at(row, idx_total),
                "working_people": number_at(row, idx_people),
                "average_pph": number_at(row, idx_avg),
                "ended_groups": number_at(row, idx_ended),
            }
        )

    snapshot_by_key = {(item["collected_at"], item["batch"]): item["snapshot_id"] for item in snapshots}

    idx_g_time = first_index(group_map, ["time"], 0)
    idx_g_batch = first_index(group_map, ["batch"], 1)
    idx_g_name = first_index(group_map, ["group"], 2)
    idx_g_ids = first_index(group_map, ["group id(s)", "group ids"], 3)
    idx_g_people = first_index(group_map, ["people"], 4)
    idx_g_status = first_index(group_map, ["work status"], 5)
    idx_g_start = first_index(group_map, ["start time"], 6)
    idx_g_hours = first_index(group_map, ["pph hours"], 7)
    idx_g_prod = first_index(group_map, ["production"], 8)
    idx_g_pph = first_index(group_map, ["pph"], 9)
    idx_g_note = first_index(group_map, ["note"], 10)

    for row in rows[group_header + 1 :]:
        if not any(str(cell or "").strip() for cell in row):
            continue
        collected_at_dt = parse_datetime_value(text_at(row, idx_g_time))
        if not collected_at_dt:
            continue
        collected_at = collected_at_dt.isoformat(sep=" ", timespec="seconds")
        batch = text_at(row, idx_g_batch)
        snapshot_id = snapshot_by_key.get((collected_at, batch))
        if not snapshot_id:
            snapshot_id = sha256_bytes(f"{file_hash}|{collected_at}|{batch}".encode("utf-8"))
            snapshot_by_key[(collected_at, batch)] = snapshot_id
            snapshots.append(
                {
                    "snapshot_id": snapshot_id,
                    "file_name": file_name,
                    "file_hash": file_hash,
                    "uploaded_at": uploaded_at,
                    "report_date": collected_at_dt.date().isoformat(),
                    "collected_at": collected_at,
                    "batch": batch,
                    "source": "group-only",
                    "pph_hours": None,
                    "total_production": None,
                    "working_people": None,
                    "average_pph": None,
                    "ended_groups": None,
                }
            )

        production_text = text_at(row, idx_g_prod)
        pph_text = text_at(row, idx_g_pph)
        note = text_at(row, idx_g_note)
        is_dne = "DNE" in {production_text.upper(), pph_text.upper()} or note.upper().startswith("DNE")
        groups.append(
            {
                "snapshot_id": snapshot_id,
                "report_date": collected_at_dt.date().isoformat(),
                "collected_at": collected_at,
                "batch": batch,
                "group_name": text_at(row, idx_g_name),
                "group_ids": text_at(row, idx_g_ids),
                "people": number_at(row, idx_g_people),
                "work_status": text_at(row, idx_g_status),
                "start_time": text_at(row, idx_g_start),
                "pph_hours": number_at(row, idx_g_hours),
                "production": None if is_dne else number_at(row, idx_g_prod),
                "production_text": production_text,
                "pph": None if is_dne else number_at(row, idx_g_pph),
                "pph_text": pph_text,
                "note": note,
                "production_status": "DNE" if is_dne else "OK",
            }
        )

    return snapshots, groups


def save_report(file_name: str, data: bytes) -> tuple[int, int]:
    snapshots, groups = parse_pph_report(file_name, data)
    if not snapshots:
        raise ValueError("No snapshots found in workbook.")
    with connect_db() as conn:
        for snapshot in snapshots:
            conn.execute("DELETE FROM groups WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    snapshot_id, file_name, file_hash, uploaded_at, report_date, collected_at,
                    batch, source, pph_hours, total_production, working_people, average_pph, ended_groups
                ) VALUES (
                    :snapshot_id, :file_name, :file_hash, :uploaded_at, :report_date, :collected_at,
                    :batch, :source, :pph_hours, :total_production, :working_people, :average_pph, :ended_groups
                )
                """,
                snapshot,
            )
        conn.executemany(
            """
            INSERT INTO groups (
                snapshot_id, report_date, collected_at, batch, group_name, group_ids,
                people, work_status, start_time, pph_hours, production, production_text,
                pph, pph_text, note, production_status
            ) VALUES (
                :snapshot_id, :report_date, :collected_at, :batch, :group_name, :group_ids,
                :people, :work_status, :start_time, :pph_hours, :production, :production_text,
                :pph, :pph_text, :note, :production_status
            )
            """,
            groups,
        )
    return len(snapshots), len(groups)


def load_df(table: str) -> pd.DataFrame:
    with connect_db() as conn:
        return pd.read_sql_query(f"SELECT * FROM {table}", conn)


def latest_daily_snapshots(snapshots: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if snapshots.empty:
        return snapshots
    if not isinstance(start, date):
        start = pd.to_datetime(start).date()
    if not isinstance(end, date):
        end = pd.to_datetime(end).date()
    frame = snapshots.copy()
    frame["report_date_dt"] = pd.to_datetime(frame["report_date"]).dt.date
    frame["collected_at_dt"] = pd.to_datetime(frame["collected_at"], errors="coerce")
    frame = frame[(frame["report_date_dt"] >= start) & (frame["report_date_dt"] <= end)]
    if frame.empty:
        return frame
    return frame.sort_values("collected_at_dt").groupby("report_date", as_index=False).tail(1).sort_values("report_date")


def groups_for_snapshots(groups: pd.DataFrame, snapshot_ids: list[str]) -> pd.DataFrame:
    if groups.empty or not snapshot_ids:
        return pd.DataFrame()
    return groups[groups["snapshot_id"].isin(snapshot_ids)].copy()


def summarize_groups(groups: pd.DataFrame) -> pd.DataFrame:
    if groups.empty:
        return groups
    frame = groups.copy()
    frame["production_value"] = pd.to_numeric(frame["production"], errors="coerce").fillna(0)
    frame["pph_value"] = pd.to_numeric(frame["pph"], errors="coerce")
    frame["people_value"] = pd.to_numeric(frame["people"], errors="coerce").fillna(0)
    frame["is_dne"] = frame["production_status"].eq("DNE")
    rows = []
    for group_name, data in frame.groupby("group_name", dropna=False):
        valid = data[~data["is_dne"]]
        weighted = (valid["pph_value"].fillna(0) * valid["people_value"]).sum()
        people_weight = valid["people_value"].sum()
        rows.append(
            {
                "Group": group_name,
                "Group IDs": ", ".join(sorted(set(str(v) for v in data["group_ids"].dropna() if str(v).strip()))),
                "Days": data["report_date"].nunique(),
                "Total volume": int(data["production_value"].sum()),
                "Avg PPH": round(weighted / people_weight) if people_weight else 0,
                "Avg people": round(data["people_value"].mean(), 1) if len(data) else 0,
                "DNE count": int(data["is_dne"].sum()),
                "Notes": " | ".join(str(v) for v in data["note"].dropna() if str(v).strip())[:500],
            }
        )
    return pd.DataFrame(rows).sort_values(["Total volume", "Avg PPH"], ascending=False)


def make_weekly_workbook(daily: pd.DataFrame, group_summary: pd.DataFrame, dne_rows: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        daily.to_excel(writer, index=False, sheet_name="Daily Summary")
        group_summary.to_excel(writer, index=False, sheet_name="Group Summary")
        dne_rows.to_excel(writer, index=False, sheet_name="DNE Notes")
    output.seek(0)
    return output.read()


def reset_database() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def restore_database(uploaded_file: Any) -> None:
    ensure_dirs()
    DB_PATH.write_bytes(uploaded_file.read())
    init_db()


def render_upload_page() -> None:
    st.header("Upload Daily PPH Excel")
    uploaded = st.file_uploader(
        "Upload PPH Reporter Excel files",
        type=["xls", "xlsx"],
        accept_multiple_files=True,
        help="Use the Export Excel / Download Excel files from PPH Reporter.",
    )
    if uploaded and st.button("Import uploaded files", type="primary"):
        total_snapshots = 0
        total_groups = 0
        errors = []
        for file in uploaded:
            data = file.read()
            try:
                snapshot_count, group_count = save_report(file.name, data)
                total_snapshots += snapshot_count
                total_groups += group_count
            except Exception as exc:
                errors.append(f"{file.name}: {exc}")
        if total_snapshots:
            st.success(f"Imported {total_snapshots} snapshots and {total_groups} group rows.")
        for error in errors:
            st.error(error)


def render_backup_tools() -> None:
    st.header("Data Backup")
    col1, col2 = st.columns(2)
    with col1:
        if DB_PATH.exists():
            st.download_button(
                "Download database backup",
                data=DB_PATH.read_bytes(),
                file_name=f"weeklysum-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite",
                mime="application/octet-stream",
            )
        else:
            st.info("No database yet.")
    with col2:
        backup = st.file_uploader("Restore database backup", type=["sqlite", "db"], key="restore_db")
        if backup and st.button("Restore backup"):
            restore_database(backup)
            st.success("Backup restored.")
            st.rerun()
    with st.expander("Danger zone"):
        confirm = st.checkbox("I understand this deletes all imported WeeklySum data.")
        if st.button("Reset database", disabled=not confirm):
            reset_database()
            st.success("Database reset.")
            st.rerun()


def render_dashboard() -> None:
    snapshots = load_df("snapshots")
    groups = load_df("groups")
    if snapshots.empty:
        st.info("Upload PPH Reporter Excel files to start.")
        return

    all_dates = pd.to_datetime(snapshots["report_date"]).dt.date
    default_end = max(all_dates)
    default_start = default_end - timedelta(days=6)

    with st.sidebar:
        st.subheader("Week Filter")
        selected = st.date_input("Date range", value=(default_start, default_end))
        if isinstance(selected, tuple) and len(selected) == 2:
            start_date, end_date = selected
        else:
            start_date, end_date = default_start, default_end
        st.caption("Default view uses the latest snapshot for each day.")

    daily = latest_daily_snapshots(snapshots, start_date, end_date)
    selected_groups = groups_for_snapshots(groups, daily["snapshot_id"].tolist() if not daily.empty else [])

    st.header("Weekly Dashboard")
    if daily.empty:
        st.warning("No saved reports in this date range.")
        return

    daily_view = daily[
        [
            "report_date",
            "batch",
            "collected_at",
            "total_production",
            "working_people",
            "average_pph",
            "pph_hours",
            "ended_groups",
        ]
    ].copy()
    daily_view = daily_view.rename(
        columns={
            "report_date": "Date",
            "batch": "Batch",
            "collected_at": "Latest snapshot",
            "total_production": "Total volume",
            "working_people": "Working people",
            "average_pph": "Average PPH",
            "pph_hours": "PPH hours",
            "ended_groups": "Ended groups",
        }
    )

    total_volume = int(pd.to_numeric(daily_view["Total volume"], errors="coerce").fillna(0).sum())
    avg_pph = int(pd.to_numeric(daily_view["Average PPH"], errors="coerce").fillna(0).mean())
    dne_count = int(selected_groups["production_status"].eq("DNE").sum()) if not selected_groups.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Weekly total volume", f"{total_volume:,}")
    c2.metric("Average daily PPH", f"{avg_pph:,}")
    c3.metric("Days imported", f"{daily_view['Date'].nunique()}")
    c4.metric("DNE rows", f"{dne_count}")

    chart_data = daily_view[["Date", "Average PPH", "Total volume"]].copy()
    chart_data["Date"] = pd.to_datetime(chart_data["Date"])
    left, right = st.columns(2)
    with left:
        st.subheader("PPH Trend")
        st.line_chart(chart_data.set_index("Date")["Average PPH"])
    with right:
        st.subheader("Total Volume Trend")
        st.line_chart(chart_data.set_index("Date")["Total volume"])

    st.subheader("Daily Summary")
    st.dataframe(daily_view, use_container_width=True, hide_index=True)

    st.subheader("Group Weekly Summary")
    group_summary = summarize_groups(selected_groups)
    st.dataframe(group_summary, use_container_width=True, hide_index=True)

    st.subheader("DNE Notes")
    dne_rows = selected_groups[selected_groups["production_status"].eq("DNE")].copy() if not selected_groups.empty else pd.DataFrame()
    if dne_rows.empty:
        st.caption("No DNE rows in this week.")
    else:
        dne_view = dne_rows[["report_date", "batch", "group_name", "group_ids", "note"]].rename(
            columns={
                "report_date": "Date",
                "batch": "Batch",
                "group_name": "Group",
                "group_ids": "Group IDs",
                "note": "Note",
            }
        )
        st.dataframe(dne_view, use_container_width=True, hide_index=True)

    export_bytes = make_weekly_workbook(daily_view, group_summary, dne_rows)
    st.download_button(
        "Export weekly summary Excel",
        data=export_bytes,
        file_name=f"weeklysum-{start_date}-to-{end_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )


def main() -> None:
    init_db()
    if not require_password():
        return

    st.title("WeeklySum")
    st.caption("Weekly PPH and total volume dashboard from PPH Reporter Excel exports.")

    page = st.sidebar.radio("Page", ["Weekly Dashboard", "Upload Reports", "Backup / Restore"])
    if page == "Upload Reports":
        render_upload_page()
    elif page == "Backup / Restore":
        render_backup_tools()
    else:
        render_dashboard()


if __name__ == "__main__":
    main()

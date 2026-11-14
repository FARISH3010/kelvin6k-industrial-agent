import { useEffect, useState } from "react";
import { getAttendanceReport, getOperators } from "../api";
import type { AttendanceReportResponse, Operator } from "../types";
import "./AttendanceReportDashboard.css";

interface Props {
  onClose: () => void;
}

export function AttendanceReportDashboard({ onClose }: Props) {
  const [reportData, setReportData] = useState<AttendanceReportResponse | null>(null);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [selectedDate, setSelectedDate] = useState<string>(() => {
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, "0");
    const dd = String(today.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  });
  const [selectedOp, setSelectedOp] = useState<string>("");
  const [searchTerm, setSearchTerm] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchOps();
  }, []);

  useEffect(() => {
    fetchReport();
  }, [selectedDate, selectedOp]);

  const fetchOps = async () => {
    try {
      const res = await getOperators();
      setOperators(res.operators || []);
    } catch (err) {
      console.error("Failed to fetch operators for filter:", err);
    }
  };

  const fetchReport = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAttendanceReport(selectedDate || undefined, selectedOp || undefined);
      setReportData(data);
    } catch (err: any) {
      setError(err?.message || "Failed to load attendance report.");
    } finally {
      setLoading(false);
    }
  };

  const filteredRecords = (reportData?.report || []).filter((r) => {
    if (!searchTerm) return true;
    const term = searchTerm.toLowerCase();
    return (
      r.name.toLowerCase().includes(term) ||
      (r.id && r.id.toLowerCase().includes(term)) ||
      (r.code && r.code.toLowerCase().includes(term)) ||
      r.date.includes(term) ||
      (r.presence && r.presence.toLowerCase().includes(term)) ||
      r.status.toLowerCase().includes(term)
    );
  });

  return (
    <div className="rep-container">
      {/* Top Banner / Navigation Bar */}
      <div className="rep-header">
        <div className="rep-header-title-group">
          <div className="rep-icon-badge">📊</div>
          <div>
            <div className="rep-title-row">
              <h1 className="rep-title">Attendance &amp; Shift Report</h1>
              <span className="rep-live-badge">SQLITE DATABASE</span>
            </div>
            <p className="rep-subtitle">
              Comprehensive report of employee shift hours, clock-in, clock-out, overtime, and work duration.
            </p>
          </div>
        </div>

        <div className="rep-header-actions">
          <button
            type="button"
            className="rep-btn rep-btn-refresh"
            onClick={fetchReport}
            title="Refresh database records"
          >
            ↻ Refresh
          </button>
          <button
            type="button"
            className="rep-btn rep-btn-close"
            onClick={onClose}
            title="Return to operational floor"
          >
            ✕ Back to Assistant
          </button>
        </div>
      </div>

      {/* Controls & Filter Bar */}
      <div className="rep-filter-bar">
        <div className="rep-filter-group">
          <label className="rep-filter-label" htmlFor="rep-search">Search Employee</label>
          <input
            id="rep-search"
            type="text"
            className="rep-input rep-search-input"
            placeholder="Search by name, ID, code..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
          />
        </div>

        <div className="rep-filter-group">
          <label className="rep-filter-label" htmlFor="rep-op-filter">Filter by Employee</label>
          <select
            id="rep-op-filter"
            className="rep-select"
            value={selectedOp}
            onChange={(e) => setSelectedOp(e.target.value)}
          >
            <option value="">All Employees</option>
            {operators.map((op) => (
              <option key={op.operator_id} value={op.Employee_id || op.name}>
                {op.name} {op.Employee_id ? `(${op.Employee_id})` : op.employee_code ? `(Code: ${op.employee_code})` : ""}
              </option>
            ))}
          </select>
        </div>

        <div className="rep-filter-group">
          <label className="rep-filter-label" htmlFor="rep-date-filter">Filter by Date</label>
          <input
            id="rep-date-filter"
            type="date"
            className="rep-input rep-date-input"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
          />
        </div>

        {(selectedDate || selectedOp || searchTerm) && (
          <button
            type="button"
            className="rep-btn rep-btn-clear"
            onClick={() => {
              setSelectedDate("");
              setSelectedOp("");
              setSearchTerm("");
            }}
          >
            Clear Filters
          </button>
        )}
      </div>

      {/* Error / Loading banner */}
      {error && (
        <div className="rep-alert rep-alert-error">
          <span>⚠️ {error}</span>
        </div>
      )}

      {/* Report Table Card */}
      <div className="rep-table-card">
        <div className="rep-table-header">
          <div className="rep-table-title">
            <span>📋</span>
            <span>Employee Shift &amp; Attendance Records</span>
            <span className="rep-count-badge">{filteredRecords.length} records</span>
          </div>
        </div>

        <div className="rep-table-wrapper">
          {loading ? (
            <div className="rep-loading-state">
              <span className="rep-spinner" />
              <span>Fetching latest attendance records from database...</span>
            </div>
          ) : filteredRecords.length === 0 ? (
            <div className="rep-empty-state">
              <span>📭</span>
              <p>No attendance records match the selected filters.</p>
            </div>
          ) : (
            <table className="rep-table">
              <thead>
                <tr>
                  <th>Employee ID</th>
                  <th>Employee Code</th>
                  <th>Employee Name</th>
                  <th>Present / Absent</th>
                  <th>Start Time</th>
                  <th>End Time</th>
                  <th>Overtime</th>
                  <th>Date</th>
                  <th>Total Hours</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredRecords.map((item) => (
                  <tr
                    key={item.shift_id}
                    className={
                      item.status === "ACTIVE"
                        ? "rep-row-active"
                        : item.presence === "Absent"
                        ? "rep-row-absent"
                        : ""
                    }
                  >
                    {/* 1. ID */}
                    <td className="rep-cell-id">
                      {item.id && item.id !== "-" ? (
                        <span className="rep-badge-id">{item.id}</span>
                      ) : (
                        <span className="rep-text-muted">-</span>
                      )}
                    </td>

                    {/* 2. Code */}
                    <td className="rep-cell-code">
                      {item.code && item.code !== "-" ? (
                        <span className="rep-badge-code">{item.code}</span>
                      ) : (
                        <span className="rep-text-muted">-</span>
                      )}
                    </td>

                    {/* 3. Name */}
                    <td className="rep-cell-name">
                      <strong>{item.name}</strong>
                    </td>

                    {/* 4. Present / Absent */}
                    <td className="rep-cell-presence">
                      <span
                        className={`rep-presence-badge ${
                          item.presence === "Present"
                            ? "rep-presence-present"
                            : "rep-presence-absent"
                        }`}
                      >
                        {item.presence === "Present" ? "✓ Present" : "✕ Absent"}
                      </span>
                    </td>

                    {/* 5. Start Time */}
                    <td className="rep-cell-time rep-cell-start">
                      <div className="rep-time-content">
                        {item.start_time !== "-" && (
                          <span className="rep-time-dot rep-dot-start" />
                        )}
                        <span>{item.start_time}</span>
                      </div>
                    </td>

                    {/* 6. End Time */}
                    <td className="rep-cell-time rep-cell-end">
                      <div className="rep-time-content">
                        {item.end_time !== "-" && (
                          <span
                            className={`rep-time-dot ${
                              item.status === "ACTIVE" ? "rep-dot-active" : "rep-dot-end"
                            }`}
                          />
                        )}
                        <span>{item.end_time}</span>
                      </div>
                    </td>

                    {/* 7. Overtime */}
                    <td className="rep-cell-overtime">
                      {item.overtime_num > 0 ? (
                        <span className="rep-badge-ot">{item.overtime}</span>
                      ) : (
                        <span className="rep-text-muted">0.00 hrs</span>
                      )}
                    </td>

                    {/* 8. Date */}
                    <td className="rep-cell-date">{item.date}</td>

                    {/* 9. Total Hours */}
                    <td className="rep-cell-hours">
                      <strong>{item.total_hours}</strong>
                    </td>

                    {/* 10. Status */}
                    <td className="rep-cell-status">
                      <span className={`rep-status-pill rep-status-${item.status.toLowerCase()}`}>
                        {item.status === "ACTIVE"
                          ? "● ON SHIFT"
                          : item.status === "ABSENT"
                          ? "✕ ABSENT"
                          : "✓ COMPLETED"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

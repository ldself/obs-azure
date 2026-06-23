// Ingestion Monitoring admin screen (Phase 2, Build Plan v1.2 §4.2).
// Administrator-only view. RULE 3: display layer only — no business logic.

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Paper from "@mui/material/Paper";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import RefreshIcon from "@mui/icons-material/Refresh";

import { useAuth } from "../../auth/AuthContext";
import { listIngestionRecords } from "../../api/ingestion";
import type { IngestionRecord } from "../../types";
import { IngestionStatusChip } from "./IngestionStatusChip";
import { QuarantineDrawer } from "./QuarantineDrawer";

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

function fmtDate(v: string | null): string {
  if (!v) return "—";
  return new Date(v).toLocaleString();
}

export function IngestionMonitor(): JSX.Element {
  const { user } = useAuth();
  const [records, setRecords] = useState<IngestionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drawerIngestionId, setDrawerIngestionId] = useState<string | null>(null);
  const [drawerFileName, setDrawerFileName] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listIngestionRecords()
      .then(setRecords)
      .catch((err: unknown) => setError(String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (!user?.is_administrator) {
    return (
      <Alert severity="error">
        Access denied. This page requires Administrator role.
      </Alert>
    );
  }

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", mb: 2, gap: 2 }}>
        <Typography variant="h5">Ingestion Monitor</Typography>
        <Tooltip title="Refresh">
          <span>
            <Button
              variant="outlined"
              size="small"
              startIcon={<RefreshIcon />}
              onClick={load}
              disabled={loading}
            >
              Refresh
            </Button>
          </span>
        </Tooltip>
        {loading && <CircularProgress size={20} />}
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <TableContainer component={Paper}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>File Name</TableCell>
              <TableCell>File Type</TableCell>
              <TableCell>Status</TableCell>
              <TableCell align="right">Total Rows</TableCell>
              <TableCell align="right">Promoted</TableCell>
              <TableCell align="right">Quarantined</TableCell>
              <TableCell align="right">Error Rate</TableCell>
              <TableCell>Started At</TableCell>
              <TableCell>Completed At</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {records.map((r) => (
              <TableRow
                key={r.ingestion_id}
                hover
                sx={{
                  cursor: (r.quarantined_rows ?? 0) > 0 ? "pointer" : "default",
                }}
                onClick={() => {
                  if ((r.quarantined_rows ?? 0) > 0) {
                    setDrawerIngestionId(r.ingestion_id);
                    setDrawerFileName(r.file_name);
                  }
                }}
              >
                <TableCell>
                  <Tooltip title={r.ingestion_id} placement="top-start">
                    <span>{r.file_name}</span>
                  </Tooltip>
                </TableCell>
                <TableCell>{r.file_type}</TableCell>
                <TableCell>
                  <IngestionStatusChip status={r.status} />
                </TableCell>
                <TableCell align="right">{r.total_rows ?? "—"}</TableCell>
                <TableCell align="right">{r.promoted_rows ?? "—"}</TableCell>
                <TableCell align="right">{r.quarantined_rows ?? "—"}</TableCell>
                <TableCell align="right">{fmtPct(r.error_rate)}</TableCell>
                <TableCell>{fmtDate(r.started_at)}</TableCell>
                <TableCell>{fmtDate(r.completed_at)}</TableCell>
              </TableRow>
            ))}
            {records.length === 0 && !loading && (
              <TableRow>
                <TableCell colSpan={9} align="center">
                  <Typography variant="body2" color="text.secondary">
                    No ingestion records found.
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <QuarantineDrawer
        ingestionId={drawerIngestionId}
        fileName={drawerFileName}
        onClose={() => setDrawerIngestionId(null)}
      />
    </Box>
  );
}

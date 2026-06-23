// Right-anchored drawer showing quarantine rows for a selected ingestion run.
// Lazy-loads quarantine data when opened (RULE 3: display layer only).

import { useEffect, useState } from "react";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Toolbar from "@mui/material/Toolbar";
import Typography from "@mui/material/Typography";
import CloseIcon from "@mui/icons-material/Close";

import { getQuarantineRecords } from "../../api/ingestion";
import type { QuarantineRecord } from "../../types";

interface Props {
  ingestionId: string | null;
  fileName: string;
  onClose: () => void;
}

export function QuarantineDrawer({ ingestionId, fileName, onClose }: Props): JSX.Element {
  const [records, setRecords] = useState<QuarantineRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ingestionId) return;
    setLoading(true);
    setError(null);
    getQuarantineRecords(ingestionId)
      .then(setRecords)
      .catch((err: unknown) => setError(String(err)))
      .finally(() => setLoading(false));
  }, [ingestionId]);

  return (
    <Drawer anchor="right" open={Boolean(ingestionId)} onClose={onClose} sx={{ zIndex: 1300 }}>
      <Box sx={{ width: 600, p: 0 }}>
        <Toolbar
          sx={{
            display: "flex",
            justifyContent: "space-between",
            bgcolor: "background.paper",
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Box>
            <Typography variant="subtitle1" fontWeight="bold">
              Quarantine Records
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap>
              {fileName}
            </Typography>
          </Box>
          <IconButton onClick={onClose} size="small" aria-label="close drawer">
            <CloseIcon />
          </IconButton>
        </Toolbar>

        <Box sx={{ p: 2 }}>
          {loading && <CircularProgress size={24} />}
          {error && (
            <Typography color="error" variant="body2">
              Failed to load quarantine records: {error}
            </Typography>
          )}
          {!loading && !error && records.length === 0 && (
            <Typography variant="body2" color="text.secondary">
              No quarantine records found.
            </Typography>
          )}
          {!loading && !error && records.length > 0 && (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Row #</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Source Data</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {records.map((r) => (
                  <TableRow key={r.quarantine_id}>
                    <TableCell>{r.source_row_number}</TableCell>
                    <TableCell>{r.quarantine_reason}</TableCell>
                    <TableCell>{r.quarantine_status}</TableCell>
                    <TableCell>
                      <Typography
                        variant="caption"
                        component="pre"
                        sx={{ fontSize: "0.65rem", whiteSpace: "pre-wrap", wordBreak: "break-all" }}
                      >
                        {JSON.stringify(r.row_data, null, 2)}
                      </Typography>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Box>
      </Box>
    </Drawer>
  );
}

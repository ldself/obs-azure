// User Management screen (design-derived from Build Plan v1.2 §4.1.2; the repo's
// UX_v1_5.docx carries only the v1.5 report-authoring amendment, not the full
// §8.2 layout — flagged for later reconciliation). Administrator-only surface:
// list users, invite a user, set capability flags / is_active, and manage cost
// center grants. The server enforces all authorization (RULE 3 / RULE 5); a
// non-Administrator who reaches this route sees the inline 403 message.

import { useCallback, useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { DataGrid, type GridColDef, type GridRenderCellParams } from "@mui/x-data-grid";

import { ApiError } from "../../api/client";
import { listUsers } from "../../api/users";
import { useAuth } from "../../auth/AuthContext";
import type { User } from "../../types";
import { EditUserDrawer } from "./EditUserDrawer";
import { InviteUserDialog } from "./InviteUserDialog";

// Full Glossary v1.11 role terms — no abbreviations (RULE 1).
const FLAG_CHIPS: { key: keyof User; label: string }[] = [
  { key: "is_administrator", label: "Administrator" },
  { key: "is_system_modeler", label: "System Modeler" },
  { key: "is_report_developer", label: "Report Developer" },
  { key: "is_finance_reviewer", label: "Finance Reviewer" },
];

function CapabilityChips({ user }: { user: User }): JSX.Element {
  const active = FLAG_CHIPS.filter(({ key }) => Boolean(user[key]));
  if (active.length === 0) return <Chip label="No capabilities" size="small" variant="outlined" />;
  return (
    <Stack direction="row" spacing={0.5}>
      {active.map(({ key, label }) => (
        <Chip key={key} label={label} size="small" color="primary" variant="outlined" />
      ))}
    </Stack>
  );
}

export function UserManagement(): JSX.Element {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      setUsers(await listUsers());
      setForbidden(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setForbidden(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Keep the open edit drawer in sync with refreshed data.
  useEffect(() => {
    if (!editing) return;
    const fresh = users.find((u) => u.user_id === editing.user_id);
    if (fresh && fresh !== editing) setEditing(fresh);
  }, [users, editing]);

  if (!currentUser?.is_administrator || forbidden) {
    return (
      <Alert severity="error">
        You do not have permission to view this page (HTTP 403). Administrator access is required.
      </Alert>
    );
  }

  const columns: GridColDef<User>[] = [
    { field: "display_name", headerName: "Name", flex: 1, minWidth: 160 },
    { field: "email", headerName: "Email", flex: 1, minWidth: 200 },
    {
      field: "is_active",
      headerName: "Active",
      width: 110,
      renderCell: (params: GridRenderCellParams<User, boolean>) => (
        <Chip
          label={params.value ? "Active" : "Inactive"}
          size="small"
          color={params.value ? "success" : "default"}
        />
      ),
    },
    {
      field: "capabilities",
      headerName: "Capabilities",
      flex: 1.5,
      minWidth: 240,
      sortable: false,
      renderCell: (params: GridRenderCellParams<User>) => <CapabilityChips user={params.row} />,
    },
  ];

  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>
          User Management
        </Typography>
        <Button variant="contained" onClick={() => setInviteOpen(true)}>
          Invite user
        </Button>
      </Stack>

      <Box sx={{ height: 540, width: "100%" }}>
        <DataGrid
          rows={users}
          columns={columns}
          getRowId={(row) => row.user_id}
          loading={loading}
          onRowClick={(params) => setEditing(params.row as User)}
          disableRowSelectionOnClick
          initialState={{ sorting: { sortModel: [{ field: "display_name", sort: "asc" }] } }}
        />
      </Box>

      <InviteUserDialog open={inviteOpen} onClose={() => setInviteOpen(false)} onCreated={() => void load()} />
      <EditUserDrawer user={editing} onClose={() => setEditing(null)} onChanged={() => void load()} />
    </Box>
  );
}

// Edit a user: toggle capability flags + is_active (PATCH /api/v1/users/{id})
// and manage cost center / rollup grants. Surfaces the last-active-Administrator
// guard (HTTP 409) as an inline message (AC-CAP-08). Display layer only (RULE 3):
// all enforcement happens server-side; this UI just reflects server results.

import { useEffect, useState } from "react";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import Drawer from "@mui/material/Drawer";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import DeleteIcon from "@mui/icons-material/Delete";

import { ApiError } from "../../api/client";
import { addGrant, addRollupGrant, listGrants, removeGrant, updateUser } from "../../api/users";
import type {
  ConfidentialGrant,
  CostCenterGrant,
  StandardGrant,
  User,
  UserUpdate,
} from "../../types";

const CAPABILITY_FIELDS: { key: keyof UserUpdate; label: string }[] = [
  { key: "is_active", label: "Active (login permission)" },
  { key: "is_administrator", label: "Administrator" },
  { key: "is_system_modeler", label: "System Modeler" },
  { key: "is_report_developer", label: "Report Developer" },
  { key: "is_finance_reviewer", label: "Finance Reviewer" },
];

interface EditUserDrawerProps {
  user: User | null;
  onClose: () => void;
  onChanged: () => void;
}

export function EditUserDrawer({ user, onClose, onChanged }: EditUserDrawerProps): JSX.Element {
  const [flagError, setFlagError] = useState<string | null>(null);
  const [grants, setGrants] = useState<CostCenterGrant[]>([]);
  const [costCenterId, setCostCenterId] = useState("");
  const [standardGrant, setStandardGrant] = useState<StandardGrant>("ALL_READ");
  const [confidentialGrant, setConfidentialGrant] = useState<ConfidentialGrant>("ALL_NONE");
  const [grantError, setGrantError] = useState<string | null>(null);
  const [rollupId, setRollupId] = useState("");
  const [rollupStandardGrant, setRollupStandardGrant] = useState<StandardGrant>("ALL_READ");
  const [rollupMessage, setRollupMessage] = useState<string | null>(null);

  useEffect(() => {
    setFlagError(null);
    setGrantError(null);
    setRollupMessage(null);
    if (!user) {
      setGrants([]);
      return;
    }
    void listGrants(user.user_id).then(setGrants).catch(() => setGrants([]));
  }, [user]);

  async function toggleFlag(field: keyof UserUpdate, value: boolean): Promise<void> {
    if (!user) return;
    setFlagError(null);
    try {
      await updateUser(user.user_id, { [field]: value });
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setFlagError("Cannot deactivate or remove Administrator from the last active Administrator.");
      } else if (err instanceof ApiError && err.status === 403) {
        setFlagError("You are not authorized to change this user.");
      } else {
        setFlagError("Could not save the change. Please try again.");
      }
    }
  }

  async function handleAddGrant(): Promise<void> {
    if (!user || !costCenterId.trim()) return;
    setGrantError(null);
    try {
      await addGrant(user.user_id, {
        cost_center_id: costCenterId.trim(),
        standard_grant: standardGrant,
        confidential_grant: confidentialGrant,
      });
      setCostCenterId("");
      setGrants(await listGrants(user.user_id));
    } catch {
      setGrantError("Could not add the grant. Please try again.");
    }
  }

  async function handleRemoveGrant(grant: CostCenterGrant): Promise<void> {
    if (!user) return;
    setGrantError(null);
    try {
      await removeGrant(user.user_id, grant.cost_center_id);
      setGrants(await listGrants(user.user_id));
    } catch {
      setGrantError("Could not remove the grant. Please try again.");
    }
  }

  async function handleAddRollupGrant(): Promise<void> {
    if (!user || !rollupId.trim()) return;
    setRollupMessage(null);
    try {
      await addRollupGrant(user.user_id, {
        rollup_id: rollupId.trim(),
        default_standard_grant: rollupStandardGrant,
      });
      setRollupId("");
      setRollupMessage("Rollup grant added.");
    } catch {
      setRollupMessage("Could not add the rollup grant. Please try again.");
    }
  }

  return (
    <Drawer anchor="right" open={Boolean(user)} onClose={onClose}>
      <Box sx={{ width: 420, p: 3 }} role="presentation">
        {user && (
          <Stack spacing={2}>
            <Typography variant="h6">{user.display_name}</Typography>
            <Typography variant="body2" color="text.secondary">
              {user.email} · {user.user_id}
            </Typography>

            <Divider />
            <Typography variant="subtitle1">Capability flags</Typography>
            {flagError && <Alert severity="error">{flagError}</Alert>}
            {CAPABILITY_FIELDS.map(({ key, label }) => (
              <FormControlLabel
                key={key}
                control={
                  <Switch
                    checked={Boolean(user[key as keyof User])}
                    onChange={(e) => void toggleFlag(key, e.target.checked)}
                  />
                }
                label={label}
              />
            ))}

            <Divider />
            <Typography variant="subtitle1">Cost center grants</Typography>
            {grantError && <Alert severity="error">{grantError}</Alert>}
            {grants.length === 0 && (
              <Typography variant="body2" color="text.secondary">
                No cost center grants.
              </Typography>
            )}
            {grants.map((grant) => (
              <Stack key={grant.cost_center_id} direction="row" alignItems="center" spacing={1}>
                <Typography variant="body2" sx={{ flexGrow: 1 }}>
                  {grant.cost_center_id} — {grant.standard_grant} / {grant.confidential_grant}
                </Typography>
                <IconButton
                  size="small"
                  aria-label={`remove grant ${grant.cost_center_id}`}
                  onClick={() => void handleRemoveGrant(grant)}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Stack>
            ))}

            <Stack spacing={1}>
              <TextField
                label="Cost center id"
                size="small"
                value={costCenterId}
                onChange={(e) => setCostCenterId(e.target.value)}
              />
              <TextField
                label="Standard grant"
                size="small"
                select
                value={standardGrant}
                onChange={(e) => setStandardGrant(e.target.value as StandardGrant)}
              >
                <MenuItem value="ALL_READ">ALL_READ</MenuItem>
                <MenuItem value="ALL_WRITE">ALL_WRITE</MenuItem>
              </TextField>
              <TextField
                label="Confidential grant"
                size="small"
                select
                value={confidentialGrant}
                onChange={(e) => setConfidentialGrant(e.target.value as ConfidentialGrant)}
              >
                <MenuItem value="ALL_NONE">ALL_NONE</MenuItem>
                <MenuItem value="ALL_READ">ALL_READ</MenuItem>
                <MenuItem value="ALL_WRITE">ALL_WRITE</MenuItem>
              </TextField>
              <Button
                variant="outlined"
                onClick={() => void handleAddGrant()}
                disabled={!costCenterId.trim()}
              >
                Add grant
              </Button>
            </Stack>

            <Divider />
            <Typography variant="subtitle1">Rollup grants</Typography>
            {rollupMessage && <Alert severity="info">{rollupMessage}</Alert>}
            <Stack spacing={1}>
              <TextField
                label="Rollup id"
                size="small"
                value={rollupId}
                onChange={(e) => setRollupId(e.target.value)}
              />
              <TextField
                label="Default standard grant"
                size="small"
                select
                value={rollupStandardGrant}
                onChange={(e) => setRollupStandardGrant(e.target.value as StandardGrant)}
              >
                <MenuItem value="ALL_READ">ALL_READ</MenuItem>
                <MenuItem value="ALL_WRITE">ALL_WRITE</MenuItem>
              </TextField>
              <Button
                variant="outlined"
                onClick={() => void handleAddRollupGrant()}
                disabled={!rollupId.trim()}
              >
                Add rollup grant
              </Button>
            </Stack>
          </Stack>
        )}
      </Box>
    </Drawer>
  );
}

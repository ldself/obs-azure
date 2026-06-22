// Invite/create a user registry record (POST /api/v1/users). An Administrator
// provides the Entra ID object id (user_id), display name, and email; capability
// flags default to FALSE and are set afterwards in the edit drawer (Security
// Spec v1.4 §6.2).

import { useState } from "react";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";

import { ApiError } from "../../api/client";
import { createUser } from "../../api/users";

interface InviteUserDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export function InviteUserDialog({ open, onClose, onCreated }: InviteUserDialogProps): JSX.Element {
  const [userId, setUserId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function reset(): void {
    setUserId("");
    setDisplayName("");
    setEmail("");
    setError(null);
  }

  async function handleSubmit(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      await createUser({ user_id: userId.trim(), display_name: displayName.trim(), email: email.trim() });
      reset();
      onCreated();
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError("A user with this user_id already exists.");
      } else if (err instanceof ApiError && err.status === 422) {
        setError("Please provide a valid user id, display name, and email address.");
      } else {
        setError("Could not create the user. Please try again.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Invite user</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="User ID (Entra ID object id)"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            required
            fullWidth
          />
          <TextField
            label="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
            fullWidth
          />
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            fullWidth
            error={Boolean(error)}
            helperText={error ?? " "}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => void handleSubmit()}
          disabled={saving || !userId.trim() || !displayName.trim() || !email.trim()}
        >
          Create
        </Button>
      </DialogActions>
    </Dialog>
  );
}

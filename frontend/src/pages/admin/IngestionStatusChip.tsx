// MUI Chip displaying ingestion run status with colour coding.
// RULE 3: display layer only — no business logic.

import Chip from "@mui/material/Chip";
import type { IngestionStatus } from "../../types";

interface Props {
  status: IngestionStatus;
}

const STATUS_COLOR: Record<
  IngestionStatus,
  "success" | "error" | "info" | "warning" | "default"
> = {
  COMPLETED: "success",
  FAILED: "error",
  RUNNING: "info",
  QUARANTINED: "warning",
  PARTIAL: "warning",
};

export function IngestionStatusChip({ status }: Props): JSX.Element {
  return <Chip label={status} color={STATUS_COLOR[status] ?? "default"} size="small" />;
}
